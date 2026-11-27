"""요청 예상 시간(src/shared/eta.py) — 실제 걸린 시간을 기록해 예상치를 맞추는지."""
from src.shared import eta


def test_eta_learns_from_finished_jobs(tmp_path, monkeypatch):
    monkeypatch.setattr(eta, "FILE", tmp_path / "eta.json")
    key = eta.key_of({"action": "start", "body": {"start": {"start_date": "2026-11-28", "end_date": "2026-12-01",
                                                            "waypoints": ["a"] * 11}}})
    assert key == "start:n3:w2"
    assert eta.total(key) == 150 + 90 + 80          # 기록 전 기본값: 박수·꼭 갈 곳 반영
    for dur in (200, 240):
        j = eta.begin(key)
        j["t0"] -= dur
        j["steps"] = [("generate_plan", 10), ("regenerate", dur - 60)]
        eta.finish(j)
    assert eta.total(key) == 220                    # 실제 기록의 중앙값
    j = eta.begin(key)
    j["t0"] -= 100
    j["steps"] = [("regenerate", 90)]               # 재생성 단계에 들어오면 그 단계 기준 남은 시간
    st = eta.status(j)
    assert st["elapsed_sec"] == 100 and st["eta_sec"] >= 150 and st["stage"].startswith("일정 다시 짜기")


def test_eta_never_shows_past_estimate(tmp_path, monkeypatch):
    monkeypatch.setattr(eta, "FILE", tmp_path / "eta.json")
    j = eta.begin("edit")
    j["t0"] -= 60                                   # 기본 15초를 넘겨도 남은 시간이 음수가 되지 않음
    st = eta.status(j)
    assert st["eta_sec"] > st["elapsed_sec"]
