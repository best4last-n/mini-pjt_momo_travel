"""규칙 검사에 걸린 일정도 사용자가 확인하면 '무시하고 확정'할 수 있다(2026-11-27 요청)."""
from src.counsel import agent
from src.memory import store


def _state(viol):
    return {"session_id": "force-test", "itinerary": [{"hours_status": "확인됨"}], "draft_version": "dv1",
            "draft_version_in": "dv1", "violations": viol, "trace": [],
            "spec": {"region": "suncheon_gurye", "nights": 1, "start": "2026-11-28", "departure": None},
            "plan": None}


def test_violation_blocks_without_force(monkeypatch):
    monkeypatch.setattr(store, "save_plan", lambda *a, **k: (_ for _ in ()).throw(AssertionError("저장되면 안 됨")))
    out = agent.approve_node(_state([{"place_name": "순천만습지", "detail": "운영시간 밖"}]))
    assert out["status"] == "승인 대기" and out["extra"]["can_force"] is True


def test_force_approve_saves_with_acknowledged(monkeypatch):
    saved = {}
    monkeypatch.setattr(store, "save_plan", lambda sid, dv, payload: saved.update(payload) or "p123")
    st = _state([{"place_name": "순천만습지", "detail": "운영시간 밖"}]) | {"force_approve": True}
    out = agent.approve_node(st)
    assert out["status"] == "확정" and out["plan_id"] == "p123"
    assert saved["acknowledged_violations"][0]["place_name"] == "순천만습지"
    assert "그대로 두었어요" in out["answer"]
