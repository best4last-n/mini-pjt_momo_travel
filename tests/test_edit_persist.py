"""고치기 모드에서 사용자가 넣은 장소가 이후 편집·조건 변경·채팅 재수립에서 조용히 사라지던 문제의 회귀 테스트(2026-11-26) — LLM 없이.

E1 조건 변경(예산 등, action=set)이 repair로 '규칙 위반 항목'을 지우면서 방금 넣은 순천만습지(17:00 마감)가 사라짐
   (세션 0f23ae5e… 메시지 579→583: 추가 직후 [조건 변경] 뒤 일정에서 빠짐)
E2 채팅이 plan 의도로 해석되면 plan_node가 새 TripSpec을 만들어 화면에서 넣은 장소(waypoint_ids·user_waypoints)가 통째로 사라짐
E3 채팅 재수립(modify)에서 LLM이 개인 시간·숙소 다시 들르기 블록을 빠뜨림 → 원래 날짜에 되돌림
E4 동선 편집 'order' 가 낡은 목록으로 오면 그 사이 넣은 장소가 지워짐 → 남겨 둠
"""
from datetime import date

import pytest

from src.counsel import agent
from src.shared import data
from src.planning import planner
from src.memory import store
from src.planning.schemas import DayPick, PickItem, PlanPick
from src.counsel.schemas import ParsedRequest

REGION = "suncheon_gurye"


def _id(title):
    return next(pid for pid, p in data.places(REGION).items() if p["title"].startswith(title))


def _seed(sid, spec, rows):
    items = planner.schedule(spec, PlanPick(days=[DayPick(day=i + 1, items=r) for i, r in enumerate(rows)]), {})
    agent.graph().update_state({"configurable": {"thread_id": sid}}, {
        "itinerary": items, "spec": agent.spec_to_dict(spec), "draft_version": "v1", "violations": [],
        "session_id": sid}, as_node="guard_out")
    return items


@pytest.fixture()
def db(tmp_path, monkeypatch):
    f = tmp_path / "p.sqlite"
    monkeypatch.setattr(store, "STATE_DB", f)
    monkeypatch.setattr(agent, "STATE_DB", f)
    monkeypatch.setattr(agent, "_GRAPH", None)
    yield f
    agent._GRAPH = None


def _late_day():
    """17시를 넘겨 끝나는 하루 — 마지막에 17:00 마감인 순천만습지를 넣으면 운영시간 위반."""
    return [PickItem(place_id=_id("사성암"), stay_minutes=150, reason="r"),
            PickItem(place_id=_id("화엄사"), stay_minutes=150, reason="r"),
            PickItem(place_id=_id("순천만국가정원"), stay_minutes=120, reason="r"),
            PickItem(place_id=_id("순천만습지"), stay_minutes=90, reason="직접 추가하신 곳")]


def test_e1_condition_change_keeps_user_added_place_even_if_it_violates(db):
    spec = planner.TripSpec(region=REGION, start=date(2026, 11, 29), nights=0,
                            waypoint_ids=[_id("순천만습지")])        # 화면에서 '일정에 넣기' → waypoint_ids에 들어감
    items = _seed("e1", spec, [_late_day()])
    allowed = {it.get("place_id") for it in items} | {planner.MEAL_ID}
    viol = planner.validate(spec, items, allowed)
    assert any(v["place_name"].startswith("순천만습지") for v in viol), "재현 전제: 습지가 운영시간 밖"
    r = agent.handle_query("", "e1", "set", set_req={"key": "budget", "value": "100만 원"})
    assert any(it["place_name"].startswith("순천만습지") for it in r["itinerary"])        # 지우지 않음
    assert "뺐어요" in r["answer"]                                  # 대신 덜어 낸 다른 곳을 알림(조용한 삭제 없음)
    assert not any(v["place_name"].startswith("순천만습지") for v in r["violations"])


def test_e1_repair_still_trims_non_user_places():
    """사용자가 넣지 않은 위반 장소는 기존처럼 보정이 덜어 낸다."""
    spec = planner.TripSpec(region=REGION, start=date(2026, 11, 29), nights=0)
    row = _late_day()
    items = planner.schedule(spec, PlanPick(days=[DayPick(day=1, items=row)]), {})
    allowed = {it.get("place_id") for it in items} | {planner.MEAL_ID}
    viol = planner.validate(spec, items, allowed)
    out = planner.repair(spec, items, viol, allowed, {})
    assert not any(it["place_name"].startswith("순천만습지") for it in out)


def test_e2_chat_replan_carries_user_edits(monkeypatch):
    wid = _id("순천만습지")
    up = {"place_id": "user:abc12345", "title": "바빈스커피", "lat": 34.95, "lng": 127.52, "kind": "restaurant"}
    old = planner.TripSpec(region=REGION, start=date(2026, 11, 28), nights=1, waypoint_ids=[wid],
                           user_waypoints=[up], kind_override={wid: "관광지"}, title="내 제목", booked_stay=_id("호텔") if False else None)
    got = {}

    def fake_run(state, spec, request, notes, base, **kw):
        got["spec"], got["notes"] = spec, notes
        return {"status": "승인 대기"}
    monkeypatch.setattr(agent, "_run_plan", fake_run)
    state = {"parsed": ParsedRequest(intent="plan", region_text="순천", month=11, day=28, nights=1).model_dump(),
             "question": "순천 11월 28일 1박 2일", "itinerary": [{"x": 1}], "spec": agent.spec_to_dict(old), "replan": "update"}
    agent.plan_node(state)
    s = got["spec"]
    assert wid in s.waypoint_ids and any(w["place_id"] == "user:abc12345" for w in s.user_waypoints)
    assert s.kind_override == {wid: "관광지"} and s.title == "내 제목"
    assert any("직접 넣으신" in n for n in got["notes"])


def test_e2_other_region_does_not_carry(monkeypatch):
    old = planner.TripSpec(region=REGION, start=date(2026, 11, 28), nights=1, waypoint_ids=[_id("순천만습지")])
    got = {}
    monkeypatch.setattr(agent, "_run_plan", lambda state, spec, request, notes, base, **kw: got.update(spec=spec) or {})
    r = agent._carry_user_edits(agent.spec_to_dict(old), planner.TripSpec(region="other", start=date(2026, 11, 28), nights=1))
    assert r  # 호출하면 이어 붙임 — 지역이 다르면 plan_node가 호출하지 않는다(아래 확인)
    state = {"parsed": ParsedRequest(intent="plan", region_text="순천", month=11, day=28, nights=1).model_dump(),
             "question": "q", "itinerary": [{"x": 1}], "spec": {**agent.spec_to_dict(old), "region": "gyeongju"}, "replan": "update"}
    agent.plan_node(state)
    assert not got["spec"].waypoint_ids


def test_e3_restore_free_and_visit_blocks_after_llm_replan():
    spec = planner.TripSpec(region=REGION, start=date(2026, 11, 29), nights=0)
    free = f"{planner.FREE_PREFIX}ab12cd34"
    base = planner.schedule(spec, PlanPick(days=[DayPick(day=1, items=[
        PickItem(place_id=_id("사성암"), stay_minutes=90, reason="r"),
        PickItem(place_id=free, stay_minutes=45, reason="낮잠")])]), {})
    llm = PlanPick(days=[DayPick(day=1, items=[PickItem(place_id=_id("화엄사"), stay_minutes=90, reason="r")])])
    out = planner.restore_user_blocks(spec, llm, base)
    ids = [x.place_id for x in out.days[0].items]
    assert free in ids and _id("화엄사") in ids
    assert next(x for x in out.days[0].items if x.place_id == free).stay_minutes == 45
    assert planner.restore_user_blocks(spec, llm, None) is llm


def test_e4_stale_order_does_not_drop_new_place(db):
    spec = planner.TripSpec(region=REGION, start=date(2026, 11, 29), nights=0)
    a, b, c = _id("사성암"), _id("화엄사"), _id("순천만국가정원")
    _seed("e4", spec, [[PickItem(place_id=x, stay_minutes=90, reason="r") for x in (a, b, c)]])
    # 동선 편집 창을 연 뒤 c를 따로 넣었다고 가정: 낡은 목록(a, b)만 와도 c는 남는다
    r = agent.handle_query("", "e4", "edit", edit={"op": "order", "days": [{"day": 1, "place_ids": [b, a]}]})
    names = [it["place_name"] for it in r["itinerary"] if it["type"] != "move"]
    assert any(n.startswith("순천만국가정원") for n in names)
    assert names.index(next(n for n in names if n.startswith("화엄사"))) < names.index(next(n for n in names if n.startswith("사성암")))
    # 명시적으로 삭제한 것은 지워진다
    r2 = agent.handle_query("", "e4", "edit", edit={"op": "order", "days": [{"day": 1, "place_ids": [b, a]}], "remove": [c]})
    assert not any(it["place_name"].startswith("순천만국가정원") for it in r2["itinerary"])
