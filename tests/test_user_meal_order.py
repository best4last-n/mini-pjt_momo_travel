"""직접 순서를 정한 날의 식사 (2026-11-27 사용자 테스트: 순천만국가정원 바로 뒤에 순천만습지가 안 들어감).
정한 순서의 식당은 하나도 빼거나 옮기지 않고, 식사 시간 미스는 위반이 아니라 안내(plan.warnings)다. LLM·네트워크 없음."""
from datetime import date

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.counsel import agent
from src.planning import brochure, planner
from src.shared import data
from src.memory import store
from src.planning.schemas import DayPick, PickItem, PlanPick

c = TestClient(app)
REGION = "suncheon_gurye"


def _id(title):
    return next(pid for pid, p in data.places(REGION).items() if p["title"].startswith(title))


def _lodging():
    return next(pid for pid, p in data.places(REGION).items() if p.get("kind") == "lodging" and p.get("lat"))


@pytest.fixture()
def db(tmp_path, monkeypatch):
    f = tmp_path / "m.sqlite"
    monkeypatch.setattr(store, "STATE_DB", f)
    monkeypatch.setattr(agent, "STATE_DB", f)
    monkeypatch.setattr(agent, "_GRAPH", None)
    yield f
    agent._GRAPH = None


def _setup(sid):
    spec = planner.TripSpec(region=REGION, start=date(2026, 11, 28), nights=1)
    g, w, y = _id("순천만국가정원"), _id("순천만습지"), _id("용산전망대")
    sushi, bap = _id("고구려초밥"), _id("순천만전라도밥상")
    p = lambda pid, role="place", m=90: PickItem(place_id=pid, role=role, stay_minutes=m, reason="r")
    rows = [[p(g), p(sushi, "lunch", 60), p(w), p(y), p(bap, "dinner", 60), p(_id("스카이큐브")), p(_lodging(), "stay", 0)],
            [p(_id("화엄사")), p(_id("사성암"))]]
    items = planner.schedule(spec, PlanPick(days=[DayPick(day=i + 1, items=r) for i, r in enumerate(rows)]), {})
    agent.graph().update_state({"configurable": {"thread_id": sid}}, {
        "itinerary": items, "spec": agent.spec_to_dict(spec), "draft_version": "v1", "violations": [], "session_id": sid}, as_node="guard_out")
    return g, w, y, sushi, bap, items


def _day(sid, n):
    ds = f"2026-11-{27 + n}"
    return [it for it in agent.graph().get_state({"configurable": {"thread_id": sid}}).values["itinerary"]
            if it["date"] == ds and it["type"] != "move"]


def test_user_order_keeps_wetland_right_after_garden_and_keeps_both_meals(db):
    g, w, y, sushi, bap, before = _setup("m1")
    day2_before = [it["place_id"] for it in before if it["date"] == "2026-11-29" and it["type"] != "move" and it["place_id"]]
    r = c.post("/query", json={"question": "순서", "session_id": "m1", "action": "edit", "edit": {
        "op": "order", "days": [{"day": 1, "place_ids": [g, w, y, sushi, bap, _lodging()]}]}}).json()
    day1 = [it["place_id"] for it in _day("m1", 1)]
    assert day1[:5] == [g, w, y, sushi, bap]                          # 습지는 국가정원 바로 뒤, 두 식당 모두 정한 자리 그대로
    assert [it["place_id"] for it in _day("m1", 1)].count(sushi) == 1 and bap in day1      # 식당을 빼지 않음
    assert [it["place_id"] for it in _day("m1", 2) if it["place_id"]] == day2_before            # 다른 날의 장소·순서는 그대로
    st = agent.graph().get_state({"configurable": {"thread_id": "m1"}}).values
    bad = [v for v in st["violations"] if v["type"] in ("식사 누락", "식사 중복") and v["date"] == "2026-11-28"]
    assert not bad                                                    # 정한 순서의 날은 식사 시간 미스가 위반이 아님
    assert "뺐어요" not in r["answer"] or "순천만전라도밥상" not in r["answer"].split("■")[0]


def test_meal_time_miss_on_user_ordered_day_is_only_a_warning(db):
    g, w, y, sushi, bap, _ = _setup("m2")
    spec = agent.spec_from_dict(agent.graph().get_state({"configurable": {"thread_id": "m2"}}).values["spec"])
    spec.user_ordered_days = [1]
    p = lambda pid, role="place": PickItem(place_id=pid, role=role, stay_minutes=60, reason="r")
    items = planner.schedule(spec, PlanPick(days=[DayPick(day=1, items=[p(g), p(w)]), DayPick(day=2, items=[p(_id("화엄사"))])]), {})
    v = planner.validate(spec, items, {g, w, _id("화엄사")})
    assert not [x for x in v if x["type"] == "식사 누락" and x["date"] == "2026-11-28"]
    plan = brochure.build(spec, items, [], v, [])
    assert planner.meal_advice(spec, items) and any("식사" in x for x in plan["warnings"])   # 안내로는 보임


def test_user_placed_restaurant_does_not_wait_for_meal_window(db):
    """사용자가 정한 날: 습지 뒤에 둔 식당은 도착 직후(운영·준비시간만 제외) 시작 — 저녁 시간대까지 기다리지 않는다."""
    g, w, y, sushi, bap, _ = _setup("m3")
    cube = _id("스카이큐브")
    c.post("/query", json={"question": "순서", "session_id": "m3", "action": "edit", "edit": {
        "op": "order", "days": [{"day": 1, "place_ids": [g, cube, w, sushi, y, bap, _lodging()]}]}})
    day = _day("m3", 1)
    ids = [it["place_id"] for it in day]
    assert ids[:6] == [g, cube, w, sushi, y, bap]                       # 정한 순서·식당 둘 다 그대로
    hm = planner.hours.parse_hm
    ws, ss = next(it for it in day if it["place_id"] == w), next(it for it in day if it["place_id"] == sushi)
    spec = agent.spec_from_dict(agent.graph().get_state({"configurable": {"thread_id": "m3"}}).values["spec"])
    info = planner.hours.parse_open(data.places(REGION)[sushi], spec.days[0])
    gap = hm(ss["start_time"]) - hm(ws["end_time"])
    brk_end = max((b for a, b in info.breaks if a < hm(ss["start_time"]) + 60), default=0)
    assert gap <= 40 or hm(ss["start_time"]) == brk_end             # 이동·여유 외에는 준비시간(브레이크)만 기다림
