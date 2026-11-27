"""먼 장소→가는 길 들를 곳 · 자정 초과 경고 · 체크인 뒤 야간 일정 (2026-11-27) — LLM·네트워크 없이."""
from datetime import date

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.counsel import agent
from src.shared import data
from src.planning import planner
from src.memory import store
from src.planning.schemas import DayPick, PickItem, PlanPick

c = TestClient(app)
REGION = "suncheon_gurye"
PANGYO = [37.39, 127.1]


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


def _spec(nights=1):
    return planner.TripSpec(region=REGION, start=date(2026, 11, 28), nights=nights, departure="경기",
                            origin_coords=PANGYO, origin_label="경기도 성남시 분당구", depart_time=7 * 60)


def _seed(sid, spec, rows):
    items = planner.schedule(spec, PlanPick(days=[DayPick(day=i + 1, items=r) for i, r in enumerate(rows)]), {})
    agent.graph().update_state({"configurable": {"thread_id": sid}}, {
        "itinerary": items, "spec": agent.spec_to_dict(spec), "draft_version": "v1", "violations": [],
        "session_id": sid}, as_node="guard_out")
    return items


def _edit(sid, edit):
    return c.post("/query", json={"question": "편집", "session_id": sid, "action": "edit", "edit": edit}).json()


def _state(sid):
    return agent.graph().get_state({"configurable": {"thread_id": sid}}).values


def _basic_rows():
    return [[PickItem(place_id=_id("사성암"), stay_minutes=60, reason="r"),
             PickItem(place_id=_lodging(), role="stay", stay_minutes=0, reason="숙소")],
            [PickItem(place_id=_id("화엄사"), stay_minutes=60, reason="r")]]


def test_far_place_near_origin_becomes_departure_route_stop(db):
    spec = _spec()
    _seed("g1", spec, _basic_rows())
    out = _edit("g1", {"op": "add", "manual": {"name": "버거킹 판교", "lat": 37.40, "lng": 127.107}, "day": 1})
    st = _state("g1")
    sp = agent.spec_from_dict(st["spec"])
    assert [s["name"] for s in sp.depart_stops] == ["버거킹 판교"]
    assert not sp.user_waypoints                                   # 현지 장소로는 남지 않음
    assert "가는 길 들를 곳" in out["answer"] and "여행지에서 멀어" in out["answer"]
    day1 = [it for it in st["itinerary"] if it["date"] == "2026-11-28" and it["type"] != "move"]
    assert all(it["place_name"] != "버거킹 판교" for it in day1)
    assert all(it["travel_minutes_from_prev"] < 120 for it in day1)   # 300km 운전이 현지 구간으로 잡히지 않음
    assert any(it["type"] == "move" and it["place_name"] == "버거킹 판교" for it in st["itinerary"])


def test_far_place_on_last_day_becomes_return_route_stop(db):
    spec = _spec()
    _seed("g2", spec, _basic_rows())
    _edit("g2", {"op": "add", "manual": {"name": "분당 카페", "lat": 37.38, "lng": 127.12}, "day": 2})
    sp = agent.spec_from_dict(_state("g2")["spec"])
    assert [s["name"] for s in sp.return_stops] == ["분당 카페"] and not sp.depart_stops


def test_existing_far_local_stop_is_fixed_on_next_edit(db):
    """사용자 세션 모양: 판교 버거킹이 1일차 현지 식사로 들어가 있고 이동이 340분으로 잡힌 상태."""
    spec = _spec()
    bk = {"contentid": "user:bk", "place_id": "user:bk", "title": "버거킹 판교", "lat": 37.40, "lng": 127.107,
          "kind": "restaurant", "source": "사용자 지정", "addr": "경기 성남시 분당구"}
    spec.user_waypoints = [bk]
    spec.waypoint_ids = ["user:bk"]
    rows = _basic_rows()
    rows[0].insert(0, PickItem(place_id="user:bk", role="lunch", stay_minutes=60, reason="r"))
    items = _seed("g3", spec, rows)
    assert any(it["travel_minutes_from_prev"] >= 300 for it in items if it["type"] != "move")       # 고쳐지기 전 모양
    _edit("g3", {"op": "stay", "place_id": _id("사성암"), "minutes": 45})
    st = _state("g3")
    sp = agent.spec_from_dict(st["spec"])
    assert [s["key"] for s in sp.depart_stops] == ["user:bk"]
    assert all(it["travel_minutes_from_prev"] < 120 for it in st["itinerary"] if it["type"] != "move")


def test_far_from_both_region_and_route_keeps_local_confirmation(db):
    spec = _spec()
    _seed("g4", spec, _basic_rows())
    _edit("g4", {"op": "add", "manual": {"name": "제주 어딘가", "lat": 33.5, "lng": 126.5}, "day": 1})
    sp = agent.spec_from_dict(_state("g4")["spec"])
    assert not sp.depart_stops and not sp.return_stops
    assert any(w["title"] == "제주 어딘가" for w in sp.user_waypoints)


def test_midnight_violation_and_user_place_kept(db):
    spec = _spec(nights=0)
    spec.departure = None
    spec.origin_coords = None
    a, b = _id("사성암"), _id("화엄사")
    spec.waypoint_ids = [a, b]
    spec.fixed_starts = {f"1:{b}": 23 * 60}
    items = planner.schedule(spec, PlanPick(days=[DayPick(day=1, items=[
        PickItem(place_id=a, stay_minutes=60, reason="r"), PickItem(place_id=b, stay_minutes=90, reason="r")])]), {})
    viol = planner.validate(spec, items, {a, b})
    m = [v for v in viol if v["type"] == "자정 초과"]
    assert m and "1일차 일정이 자정을 넘어요" in m[0]["detail"] and "곳 줄이기 권장" in m[0]["detail"]
    fixed = planner.repair(spec, items, viol, {a, b}, {})
    assert {it["place_id"] for it in fixed if it["type"] != "move"} >= {a, b}       # 사용자가 넣은 곳은 조용히 지우지 않음


def test_evening_insert_after_stay_persists_and_returns_to_lodging(db):
    spec = _spec()
    _seed("n1", spec, _basic_rows())
    stay = _lodging()
    night_pid = next(p for p, v in data.places(REGION).items()
                     if v.get("kind") == "attraction" and v.get("lat") and p not in (_id("사성암"), _id("화엄사")))
    _edit("n1", {"op": "add", "place_id": night_pid, "after": stay, "insert": True})
    _edit("n1", {"op": "stay", "place_id": _id("사성암"), "minutes": 45})          # 뒤이은 편집에도 남아야 함
    st = _state("n1")
    day1 = [it for it in st["itinerary"] if it["date"] == "2026-11-28"]
    types = [(it["type"], it["place_id"], (it.get("notes") or [None])[0]) for it in day1 if it["type"] != "move" or (it.get("notes") or [""])[0] == "숙소 복귀"]
    si = next(i for i, t in enumerate(types) if t[0] == "stay")
    ni = next(i for i, t in enumerate(types) if t[1] == night_pid)
    assert ni > si                                                         # 체크인 뒤에 일정
    night_it = next(it for it in day1 if it.get("place_id") == night_pid)
    stay_it = next(it for it in day1 if it["type"] == "stay")
    assert "야간" in night_it["notes"] and night_it["start_time"] >= stay_it["start_time"]
    ret = [it for it in day1 if it["type"] == "move" and it["notes"][:1] == ["숙소 복귀"]]
    assert len(ret) == 1 and ret[0]["place_name"].startswith("숙소로 돌아가기")
    assert not [v for v in st["violations"] if v["type"] in ("숙박 누락", "시간 창 밖") and v.get("place_name") == night_it["place_name"]]
    pl = st["plan"]["itinerary"][0]["stops"]
    assert [s["name"] for s in pl][-1].startswith("숙소로 돌아가기") and pl[-1]["lodge_return"]
    assert next(s for s in pl if s["place_id"] == night_pid).get("night")


def test_evening_insert_uses_after_day_when_same_lodging_each_night(db):
    spec = _spec(nights=2)
    stay = _lodging()
    rows = [[PickItem(place_id=_id("사성암"), stay_minutes=60, reason="r"), PickItem(place_id=stay, role="stay", stay_minutes=0, reason="숙소")],
            [PickItem(place_id=_id("화엄사"), stay_minutes=60, reason="r"), PickItem(place_id=stay, role="stay", stay_minutes=0, reason="숙소")],
            []]
    _seed("n2", spec, rows)
    pid = next(p for p, v in data.places(REGION).items() if v.get("kind") == "attraction" and v.get("lat")
               and p not in (_id("사성암"), _id("화엄사")))
    _edit("n2", {"op": "add", "place_id": pid, "after": stay, "after_day": 2, "insert": True})
    it = next(x for x in _state("n2")["itinerary"] if x.get("place_id") == pid)
    assert it["date"] == "2026-11-29" and "야간" in it["notes"]
