"""시작 카드(action=start) 흐름을 끝까지 — src/ 재구성 때 문자열로 남은 옛 모듈 경로('momo.options')가
시작 카드 요청마다 실패하던 문제(2026-11-27). LLM 장소 고르기·검색만 모의, 나머지는 실제 코드."""
import pytest

from src.counsel import agent
from src.memory import store
from src.planning import planner
from src.planning.schemas import DayPick, PickItem, PlanPick
from src.shared import data

REGION = "gyeongju"


def _id(title):
    return next(pid for pid, p in data.places(REGION).items() if p["title"].startswith(title))


@pytest.fixture()
def db(tmp_path, monkeypatch):
    f = tmp_path / "s.sqlite"
    monkeypatch.setattr(store, "STATE_DB", f)
    monkeypatch.setattr(agent, "STATE_DB", f)
    monkeypatch.setattr(agent, "_GRAPH", None)
    yield f
    agent._GRAPH = None


@pytest.mark.parametrize("preset", [None, "부모님 효도"])
def test_start_card_makes_plan(db, monkeypatch, preset):
    names = ["경주 첨성대", "국립경주박물관", "경주 동궁과 월지"]
    cands = {_id(n): data.places(REGION)[_id(n)] for n in names}
    monkeypatch.setattr(planner, "candidates", lambda spec, request: dict(cands))
    seen = {}

    def fake_pick(spec, cands, request, feedback=None, base=None, indoor=False):
        seen["request"] = request
        return PlanPick(days=[DayPick(day=1, items=[PickItem(place_id=_id(n), stay_minutes=60, reason="r") for n in names])])
    monkeypatch.setattr(planner, "pick", fake_pick)
    monkeypatch.setattr(agent.tools, "weather", lambda region, days: {"kind": "seasonal", "rain": False, "text": "계절 평년 안내"})
    start = {"region": REGION, "start_date": "2026-11-28", "end_date": "2026-11-28"}
    if preset:
        start["preset"] = preset
    r = agent.handle_query("", action="start", start_req=start)
    assert r["status"] in ("승인 대기", "정보"), r.get("answer")
    assert {"경주 첨성대", "국립경주박물관"} <= {it["place_name"] for it in r.get("itinerary") or []}
    if preset:
        assert "여행 스타일: 부모님 효도" in seen["request"]
