"""재생성이 빈 결과로 무너져도 더 나은 앞 시도를 보정해 쓴다 (2차 평가 #15, 2026-11-27).
1차 시도는 관광지가 있고 위반 1건, 2·3차는 LLM이 빈 선택을 돌려준 상황 — 예전에는 마지막(빈) 시도를 보정해
관광지 0곳 일정이 '승인 대기'로 나갔다. LLM·네트워크 없이(pick·candidates 모의)."""
from datetime import date

from src.planning import planner
from src.planning.schemas import DayPick, PickItem, PlanPick
from src.shared import data

REGION = "gyeongju"


def _id(title):
    return next(pid for pid, p in data.places(REGION).items() if p["title"].startswith(title))


def _lodging():
    return next(pid for pid, p in data.places(REGION).items() if p.get("kind") == "lodging" and p.get("lat"))


def test_empty_regeneration_does_not_replace_better_attempt(monkeypatch):
    spec = planner.TripSpec(region=REGION, start=date(2026, 11, 28), nights=1)
    names = ["경주 첨성대", "경주 동궁과 월지", "국립경주박물관", "경주 불국사", "경주 석굴암"]
    cands = {_id(n): data.places(REGION)[_id(n)] for n in names}
    cands[_lodging()] = data.places(REGION)[_lodging()]
    monkeypatch.setattr(planner, "candidates", lambda spec, request: dict(cands))
    good = PlanPick(days=[
        DayPick(day=1, items=[PickItem(place_id=_id("경주 첨성대"), stay_minutes=60, reason="r"),
                              PickItem(place_id=_id("국립경주박물관"), stay_minutes=90, reason="r"),
                              PickItem(place_id="no-such-place", stay_minutes=60, reason="없는 장소 → 위반"),
                              PickItem(place_id=_lodging(), role="stay", stay_minutes=0, reason="숙소")]),
        DayPick(day=2, items=[PickItem(place_id=_id("경주 불국사"), stay_minutes=90, reason="r"),
                              PickItem(place_id=_id("경주 석굴암"), stay_minutes=60, reason="r")])])
    calls = []

    def fake_pick(spec, cands, request, feedback=None, base=None, indoor=False):
        calls.append(feedback)
        return good.model_copy(deep=True) if len(calls) == 1 else PlanPick(days=[])
    monkeypatch.setattr(planner, "pick", fake_pick)

    out = planner.build_plan(spec, "경주 1박 2일 최대한 많이")
    places = [it["place_name"] for it in out["itinerary"] if it["type"] == "place"]
    assert len(calls) == planner.MAX_REGEN + 1
    assert {"경주 첨성대", "국립경주박물관"} <= set(places)           # 1차 시도의 관광지가 살아 있음
    assert not out["violations"]
    assert "no-such-place" not in {it.get("place_id") for it in out["itinerary"]}
    # 빈 시도의 위반(식사·숙박 누락)이 아니라 1차 시도의 위반을 다시 알려 준다
    assert all("no-such-place" in (fb or "") or "없는 장소" in (fb or "") for fb in calls[1:])
