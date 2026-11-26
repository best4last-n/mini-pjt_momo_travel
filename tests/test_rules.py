"""규칙 기반 부분(LLM 없이): 운영시간·휴무 해석, 일정 시각 계산, 규칙 검사."""
from datetime import date

from momo import data, hours, planner
from momo.schemas import DayPick, PickItem, PlanPick

SAT, MON = date(2026, 12, 12), date(2026, 12, 14)


def _by_title(region, title):
    return next(p for p in data.places(region).values() if p["title"].startswith(title))


def test_hours_variants():
    assert hours.parse_hours_text("09:00~18:00 (입장 마감 17:00)", SAT)[:2] == (540, 1020)
    # 계절별: 12월은 동절기
    o, c, _, _ = hours.parse_hours_text("- 하절기(3월~10월) 09:00~18:00- 동절기(11월~2월) 09:00~17:00", SAT)
    assert (o, c) == (540, 1020)
    # 평일/주말
    assert hours.parse_hours_text("- 평일 12:00~17:30- 주말 11:00~17:30", SAT)[0] == 660
    assert hours.parse_hours_text("- 평일 12:00~17:30- 주말 11:00~17:30", MON)[0] == 720
    # 준비시간은 휴게로
    _, _, br, _ = hours.parse_hours_text("- 11:00~21:00- 준비시간 15:30~17:00- 마지막 주문 20:30", SAT)
    assert br == [(930, 1020)]
    assert hours.parse_hours_text("상시 개방", SAT)[:2] == (0, 1440)
    assert hours.parse_hours_text("", SAT) is None


def test_rest_days():
    assert hours.rest_check("매주 월요일", MON)[0] is True
    assert hours.rest_check("매주 월요일", SAT)[0] is False
    assert hours.rest_check("매주 화요일~수요일", date(2026, 12, 16))[0] is True
    assert hours.rest_check("3월, 11월 2번째 월요일 / 1월 1일 /", date(2027, 1, 1))[0] is True
    assert hours.rest_check("설·추석 당일", date(2027, 2, 6))[0] is True
    assert hours.rest_check("연중무휴", MON)[0] is False
    assert hours.rest_check("점포 별로 상이함", MON)[0] is None


def test_museum_and_closed_restaurant():
    museum = _by_title("gyeongju", "국립경주박물관")
    info = hours.parse_open(museum, SAT)
    assert info.status == "확인됨" and (info.open, info.close) == (600, 1050)
    gyodong = _by_title("gyeongju", "교동집밥")       # 매주 월요일 휴무
    assert hours.parse_open(gyodong, MON).status == "휴무"


def _spec(**kw):
    return planner.TripSpec(region="gyeongju", start=SAT, nights=0, **kw)


def test_schedule_has_no_overlap_and_validate_catches_closed():
    spec = _spec()
    ids = {t: _by_title("gyeongju", t)["contentid"] for t in ("국립경주박물관", "경주 첨성대", "교동집밥", "천마총", "료코")}
    plan = PlanPick(days=[DayPick(day=1, items=[
        PickItem(place_id=ids["경주 첨성대"], stay_minutes=60, reason="r"),
        PickItem(place_id=ids["국립경주박물관"], stay_minutes=90, reason="r"),
        PickItem(place_id=ids["교동집밥"], stay_minutes=60, role="lunch", reason="r"),
        PickItem(place_id=ids["천마총"], stay_minutes=90, reason="r"),
        PickItem(place_id=ids["료코"], stay_minutes=60, role="dinner", reason="r"),
    ])])
    items = planner.schedule(spec, plan, {})
    assert planner.validate(spec, items, set(ids.values())) == []
    # 같은 일정을 월요일로 옮기면 교동집밥(월 휴무) 위반
    spec_mon = planner.TripSpec(region="gyeongju", start=MON, nights=0)
    v = planner.validate(spec_mon, planner.schedule(spec_mon, plan, {}), set(ids.values()))
    assert any(x["type"] == "휴무일" for x in v)


def test_validate_unknown_place_and_overlap():
    spec = _spec()
    cid = _by_title("gyeongju", "경주 첨성대")["contentid"]
    items = planner.schedule(spec, PlanPick(days=[DayPick(day=1, items=[
        PickItem(place_id=cid, stay_minutes=60, reason="r")])]), {})
    v = planner.validate(spec, items, set())          # 후보에 없는 장소
    assert any(x["type"] == "없는 장소" for x in v)
    items2 = [dict(items[0]), dict(items[0], seq=2, start_time="09:30", end_time="10:00", travel_minutes_from_prev=5)]
    v2 = planner.validate(spec, items2, {cid})
    assert any(x["type"] == "시간 겹침" for x in v2)


def test_departure_window_seoul_daytrip():
    spec = _spec(departure="서울")
    (s, e), = planner.windows(spec)
    ow = planner.one_way_minutes(spec)
    rw = planner.return_minutes(spec)       # 귀가는 따로 계산(도로 경로·요일 혼잡)
    (bs, be), = planner._base_windows(spec)
    assert bs == max(540, 480 + ow + 10) and be == min(18 * 60 + 30, 1320 - rw)
    # 이동 중 끼니(가는 길·오는 길 식사)만큼 여행지 시간이 줄어든다(2026-11-26)
    rm = planner.road_meals(spec)
    assert s == planner._ceil5(bs + sum(m["minutes"] for m in rm["out"])) and e == be - sum(m["minutes"] for m in rm["back"])
