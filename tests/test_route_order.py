"""출발지 근처 장소 → 가는 길/오는 길 들를 곳: 구간별(바로 앞 지점 → 다음 지점) 시간·순서·편집·끼워 넣기 규칙 (2026-11-27).
roads.drive는 모의(좌표 기록) — 네트워크·LLM 없이."""
from datetime import date

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.counsel import agent
from src.planning import brochure, planner
from src.shared import data, travel
from src.mobility import roads
from src.memory import store
from src.planning.schemas import DayPick, PickItem, PlanPick

c = TestClient(app)
REGION = "suncheon_gurye"
PANGYO = [37.39, 127.1]
BK = {"contentid": "user:bk", "place_id": "user:bk", "title": "버거킹 판교", "lat": 37.40, "lng": 127.107,
      "kind": "restaurant", "source": "사용자 지정", "addr": "경기 성남시 분당구"}
HM = planner.hours.parse_hm


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


@pytest.fixture()
def calls(monkeypatch):
    rec = []

    def fake(a, b, d, t, outbound):
        rec.append((tuple(a), tuple(b), outbound))
        m = int(travel.km(a, b) * 0.75) + 5
        return {"total_min": m, "free_min": m, "reason": "모의", "factor": 1.0, "rests": 0, "distance_km": round(travel.km(a, b) * 1.25, 1),
                "source": "모의", "live": False, "path": [list(a), list(b)], "segments": []}
    monkeypatch.setattr(roads, "drive", fake)
    return rec


def _spec(nights=1, **kw):
    return planner.TripSpec(region=REGION, start=date(2026, 11, 28), nights=nights, departure="경기", origin_coords=PANGYO,
                            origin_label="경기도 성남시 분당구", depart_time=kw.pop("depart_time", 8 * 60), breakfast="안 먹음", **kw)


def _rows():
    return [[PickItem(place_id=_id("사성암"), stay_minutes=60, reason="r"), PickItem(place_id=_lodging(), role="stay", stay_minutes=0, reason="숙소")],
            [PickItem(place_id=_id("화엄사"), stay_minutes=60, reason="r")]]


def _seed(sid, spec, rows):
    items = planner.schedule(spec, PlanPick(days=[DayPick(day=i + 1, items=r) for i, r in enumerate(rows)]), {})
    agent.graph().update_state({"configurable": {"thread_id": sid}}, {
        "itinerary": items, "spec": agent.spec_to_dict(spec), "draft_version": "v1", "violations": [], "session_id": sid}, as_node="guard_out")


def _edit(sid, edit):
    return c.post("/query", json={"question": "편집", "session_id": sid, "action": "edit", "edit": edit}).json()


def _state(sid):
    return agent.graph().get_state({"configurable": {"thread_id": sid}}).values


def _sp(sid):
    return agent.spec_from_dict(_state(sid)["spec"])


def _bk_state(sid, spec=None):
    spec = spec or _spec()
    spec.user_waypoints = [BK]
    spec.waypoint_ids = ["user:bk"]
    rows = _rows()
    rows[0].insert(0, PickItem(place_id="user:bk", role="lunch", stay_minutes=60, reason="r"))
    _seed(sid, spec, rows)


def _order_bk_first(sid):
    return _edit(sid, {"op": "order", "days": [{"day": 1, "place_ids": ["user:bk", _id("사성암"), _lodging()]},
                                               {"day": 2, "place_ids": [_id("화엄사")]}]})


def test_order_op_makes_origin_meal_a_depart_stop_and_legs_use_previous_point(db, calls):
    _bk_state("r1", _spec(long_override={"out": 175}))
    sa = _id("사성암")
    r = _order_bk_first("r1")
    sp = _sp("r1")
    assert [s["key"] for s in sp.depart_stops] == ["user:bk"]
    st = _state("r1")
    road = [it for it in st["itinerary"] if it["date"] == "2026-11-28" and it["type"] == "move"]
    assert [it["notes"][0] for it in road][:2] == ["출발", "가는 길"]
    assert "→ 버거킹 판교" in road[0]["place_name"]
    assert HM(road[0]["end_time"]) - HM(road[0]["start_time"]) < 20                  # 출발지 → 버거킹은 짧게
    day1 = [it for it in st["itinerary"] if it["date"] == "2026-11-28" and it["type"] != "move"]
    assert day1[0]["place_id"] == sa and not any(it["place_name"] == "도착 지점" for it in day1)
    # 구간 계산에 쓴 좌표: (출발지 → 버거킹), (버거킹 → 첫 현지 장소 사성암) — 여행지 중심(도착 지점)이 아니다
    sa_xy = (data.places(REGION)[sa]["lat"], data.places(REGION)[sa]["lng"])
    legs = [(a, b) for a, b, out in calls if out]
    assert (tuple(PANGYO), (BK["lat"], BK["lng"])) in legs and ((BK["lat"], BK["lng"]), sa_xy) in legs
    bk_item = next(it for it in road if it["notes"][0] == "가는 길")
    assert HM(day1[0]["start_time"]) >= HM(bk_item["end_time"]) + int(travel.km((BK["lat"], BK["lng"]), sa_xy) * 0.75)
    # 직접 정한 운전 시간(출발지→여행지 한 구간)은 구간이 나뉘며 해제 + 안내
    assert "해제" in r["answer"] and not sp.long_override.get("out")


def test_long_override_after_split_applies_to_last_segment_only(db, calls):
    _bk_state("r2")
    _order_bk_first("r2")
    _edit("r2", {"op": "long_time", "leg": "out", "minutes": 200})
    sp = _sp("r2")
    lp = planner.leg_plan(sp, "out", sp.depart_time)
    assert lp["last_min"] == 200 and lp["events"][0]["minutes"] < 20           # 첫 구간(출발지→버거킹)은 그대로
    d0 = brochure.build(sp, _state("r2")["itinerary"], [], [], [])["itinerary"][0]
    assert d0["start_point"]["leg"]["segmented"] and not d0["start_point"]["leg"]["drive_custom"]


def test_edits_on_route_stop_keep_working(db, calls):
    _bk_state("r3")
    _edit("r3", {"op": "kind", "place_id": "user:bk", "kind": "자동"})          # 예전: '그 장소는 일정에 없어요'
    assert [s["key"] for s in _sp("r3").depart_stops] == ["user:bk"]
    r = _edit("r3", {"op": "kind", "place_id": "user:bk", "kind": "카페"})
    assert "없어요" not in r["answer"] and _sp("r3").depart_stops[0]["kind"] == "카페"
    _edit("r3", {"op": "stay", "place_id": "user:bk", "minutes": 25})
    assert _sp("r3").depart_stops[0]["stay_min"] == 25
    _edit("r3", {"op": "time", "place_id": "user:bk", "time": "09:30"})
    bk = next(it for it in _state("r3")["itinerary"] if it.get("route_key") == "user:bk")
    assert bk["start_time"] == "09:30" and bk["end_time"] == "09:55"
    _edit("r3", {"op": "remove", "place_id": "user:bk"})
    sp = _sp("r3")
    assert not sp.depart_stops and "user:bk" not in sp.waypoint_ids


def test_return_day_mirror_last_local_to_stop_to_home(db, calls):
    _seed("r4", _spec(), _rows())
    _edit("r4", {"op": "add", "manual": {"name": "분당 카페", "lat": 37.38, "lng": 127.12}, "day": 2})
    assert [s["name"] for s in _sp("r4").return_stops] == ["분당 카페"]
    st = _state("r4")
    back = [it for it in st["itinerary"] if it["type"] == "move" and it["notes"][0] in ("오는 길", "귀가")]
    assert [it["notes"][0] for it in back] == ["오는 길", "귀가"] and back[1]["place_name"].startswith("분당 카페 →")
    ha = data.places(REGION)[_id("화엄사")]
    legs = [(a, b) for a, b, out in calls if not out]
    assert ((ha["lat"], ha["lng"]), (37.38, 127.12)) in legs and ((37.38, 127.12), tuple(PANGYO)) in legs
    assert HM(back[1]["start_time"]) == HM(back[0]["end_time"])


def test_route_stops_sorted_by_progress_but_user_order_kept(db, calls):
    _seed("r5", _spec(), _rows())
    far = {"name": "대전 맛집", "lat": 36.35, "lng": 127.38, "key": "far"}
    near = {"name": "수원 카페", "lat": 37.27, "lng": 127.01, "key": "near"}
    for cd in (far, near):                       # 먼 곳을 먼저 넣어도 길 위 진행 순서(출발지에서 가까운 순)
        _edit("r5", {"op": "route_stop", "leg": "out", "action": "add", "candidate": cd})
    assert [s["key"] for s in _sp("r5").depart_stops] == ["near", "far"]
    _edit("r5", {"op": "route_stop", "leg": "out", "action": "move", "key": "near", "dir": 1})       # 사용자가 ↓로 바꾼 순서
    assert [s["key"] for s in _sp("r5").depart_stops] == ["far", "near"]
    _edit("r5", {"op": "route_stop", "leg": "out", "action": "add", "candidate": {"name": "천안 휴게소", "lat": 36.8, "lng": 127.15, "key": "mid"}})
    keys = [s["key"] for s in _sp("r5").depart_stops]
    assert keys[0] == "far" and set(keys) == {"far", "near", "mid"}             # 직접 정한 순서(far 먼저)는 지켜짐
    mv = [it for it in _state("r5")["itinerary"] if it["type"] == "move" and it["notes"][0] == "가는 길"]
    assert [it["place_name"] for it in mv if it.get("route_key")] == [s["name"] for s in _sp("r5").depart_stops]
    ts = [HM(it["start_time"]) for it in mv]
    assert ts == sorted(ts)                                                      # 시각도 그 순서대로


def test_meal_route_stop_counts_as_lunch(db, calls):
    spec = _spec(depart_time=11 * 60 + 30)
    spec.depart_stops = [{"key": "user:bk", "name": "버거킹 판교", "lat": 37.40, "lng": 127.107, "detour_min": 5, "stay_min": 50,
                          "kind": "식당", "km_from_start": 1}]
    assert planner.route_meal_covers(spec, 0, planner.LUNCH)
    picks = [DayPick(day=1, items=[PickItem(place_id=_id("사성암"), stay_minutes=60, reason="r")]),
             DayPick(day=2, items=[PickItem(place_id=_id("화엄사"), stay_minutes=60, reason="r")])]
    items = planner.schedule(spec, PlanPick(days=picks), {})
    v = planner.validate(spec, items, {_id("사성암"), _id("화엄사")})
    assert not [x for x in v if x["type"] == "식사 누락" and "점심" in x["detail"] and x["date"] == "2026-11-28"]
    days = agent._fill_meals(spec, [[PickItem(place_id=_id("사성암"), stay_minutes=60, reason="r")], []])
    assert not any(x.role == "lunch" for x in days[0])                      # 점심 자리표시자를 또 넣지 않음


def test_insert_at_every_position_lands_in_order(db, calls):
    """출발지 ↔ 가는 길 들를 곳 ↔ 첫 현지 장소 ↔ … ↔ 오는 길 들를 곳 ↔ 복귀지 어느 자리든 끼워 넣기가 되고 순서대로 들어간다."""
    _seed("r7", _spec(), _rows())

    def ins(nm, lat, lng, route, idx, kind=None):
        return _edit("r7", {"op": "add", "manual": {"name": nm, "lat": lat, "lng": lng}, "route": route, "index": idx, "insert": True,
                            **({"kind": kind} if kind else {})})
    names = lambda ln: [s["name"] for s in getattr(_sp("r7"), ln)]
    ins("출발A", 37.40, 127.11, "out", 0)                                            # 출발지 바로 다음
    ins("출발B", 37.30, 127.05, "out", 1, "식사")                                    # 들를 곳 뒤(끝)
    ins("출발C", 37.35, 127.08, "out", 1)                                            # 들를 곳 사이
    assert names("depart_stops") == ["출발A", "출발C", "출발B"]
    assert next(s for s in _sp("r7").depart_stops if s["name"] == "출발B")["kind"] == "식당"         # 식사로 끼워 넣음
    ins("현지X", 34.95, 127.48, "out", 3)                                            # 마지막 들를 곳 뒤 + 여행지 안 → 첫날 첫 현지 장소 앞
    day1 = [it["place_name"] for it in _state("r7")["itinerary"] if it["date"] == "2026-11-28" and it["type"] != "move"]
    assert day1[0] == "현지X" and day1.index("현지X") < day1.index(next(n for n in day1 if n.startswith("사성암")))
    ins("복귀A", 37.38, 127.12, "back", 0)
    ins("복귀B", 37.36, 127.11, "back", 1)
    ins("복귀C", 37.37, 127.115, "back", 1)
    assert names("return_stops") == ["복귀A", "복귀C", "복귀B"]
    ins("현지Y", 34.96, 127.49, "back", 3)                                           # 마지막 날 마지막 현지 장소 뒤
    last = [it["place_name"] for it in _state("r7")["itinerary"] if it["date"] == "2026-11-29" and it["type"] != "move"]
    assert last[-1] == "현지Y"
    mv = [it for it in _state("r7")["itinerary"] if it["date"] == "2026-11-28" and it["type"] == "move"]
    assert [it["place_name"] for it in mv if it["notes"][0] == "가는 길"][:3] == ["출발A", "출발C", "출발B"]
    ts = [HM(it["start_time"]) for it in mv]
    assert ts == sorted(ts)


def test_return_road_meal_placeholder_is_inside_single_return_journey(monkeypatch):
    """마지막 현지 장소 → 오는 길 저녁(길 위 식사 자리) → 집: 귀가 이동은 하나(떠나는 시각 = 마지막 장소가 끝난 시각),
    길 위 식사는 그 안의 한 지점. 식사 시각 앞 구간 = 마지막 장소에서, 뒤 구간 = 식사가 끝난 시각에서 시작."""
    monkeypatch.setattr(planner, "_return_base", lambda spec: 220)
    monkeypatch.setattr(planner, "one_way_minutes", lambda spec: 120)
    spec = _spec(depart_time=6 * 60)
    spec.arrive_time = 20 * 60
    rows = [[PickItem(place_id=_id("사성암"), stay_minutes=60, reason="r"), PickItem(place_id=_lodging(), role="stay", stay_minutes=0, reason="숙소")],
            [PickItem(place_id=_id(n), stay_minutes=90, reason="r") for n in ("죽도봉공원", "순천 드라마촬영장", "순천 낙안읍성")]]
    items = planner.schedule(spec, PlanPick(days=[DayPick(day=i + 1, items=r) for i, r in enumerate(rows)]), {})
    last = [it for it in items if it["date"] == "2026-11-29"]
    local = [it for it in last if it["type"] != "move"]
    back = [it for it in last if it["type"] == "move" and it["notes"][0] in ("오는 길", "귀가")]
    ret = [it for it in back if it["notes"][0] == "귀가"]
    assert len(ret) == 1                                                       # 귀가 이동은 하나
    meal = [it for it in back if it["notes"][0] == "오는 길"]
    assert meal                                                                # 길 위 저녁 식사 자리가 생기는 모양
    if meal:
        assert HM(ret[0]["start_time"]) == HM(local[-1]["end_time"])
        assert HM(ret[0]["start_time"]) <= HM(meal[0]["start_time"]) and HM(meal[0]["end_time"]) <= HM(ret[0]["end_time"])
        assert HM(ret[0]["end_time"]) - HM(ret[0]["start_time"]) == 220 + (HM(meal[0]["end_time"]) - HM(meal[0]["start_time"]))
        assert meal[0]["travel_minutes_from_prev"] == HM(meal[0]["start_time"]) - HM(local[-1]["end_time"])      # 앞 구간은 마지막 장소에서
