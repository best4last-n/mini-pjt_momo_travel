"""사용자 실사용 테스트(2026-11-26, 순천/구례 세션 3개)에서 나온 오류의 회귀 테스트 — LLM·네트워크 없이.

U1 점심 역할 식당이 관광지 뒤로 밀려 15시 이후 배치 → '점심 식사 항목 없음'이 끝까지 남아 확정 불가
U2 직접 고른 곳(브이월드·직접 추가) 다음에 넣기 → "1일차  바로 다음에…" 이름 빈칸, 빼기 → "user:fcaaa9b3를 뺐어요"
U3 '일정에 넣기'(날짜 지정 없음)가 그 장소의 휴무일(순천만국가정원 매월 마지막 월요일)에 배치
U4 '내 요청 기록' 개별·전체 삭제(사용자 요청) — 지운 기록은 이력에서 되살리지 않고, 일정은 그대로
U10 전체 동선 최적화 뒤 1일차 저녁 식사 3번(남녘들밥상 18:00 → 주변에서 저녁 19:20 → 명지원 20:40)
    → 규칙 '식사 중복'(카페 제외, 둘 다 사용자가 넣은 곳이면 허용) + 자동 정리(사용자가 넣지 않은 쪽을 뺌)
"""
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.main import app
from momo import agent, data, planner, store
from momo.schemas import DayPick, PickItem, PlanPick

c = TestClient(app)
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
    f = tmp_path / "u.sqlite"
    monkeypatch.setattr(store, "STATE_DB", f)
    monkeypatch.setattr(agent, "STATE_DB", f)
    monkeypatch.setattr(agent, "_GRAPH", None)
    yield f
    agent._GRAPH = None


def _lunch_missing(spec, items):
    return [v for v in planner.validate(spec, items, {it.get("place_id") for it in items} | {planner.MEAL_ID})
            if v["type"] == "식사 누락" and "점심" in v["detail"]]


def test_u1_late_lunch_is_moved_back_to_noon():
    spec = planner.TripSpec(region=REGION, start=date(2026, 11, 29), nights=0)
    row = [PickItem(place_id=_id("사성암"), stay_minutes=150, reason="r"),
           PickItem(place_id=_id("화엄사"), stay_minutes=150, reason="r"),
           PickItem(place_id=_id("남촌회관"), stay_minutes=60, role="lunch", reason="r")]
    before = planner.schedule(spec, PlanPick(days=[DayPick(day=1, items=row)]), {})
    assert _lunch_missing(spec, before)                       # 재현: 점심이 14시 넘어 배치
    fixed = agent._fill_meals(spec, [list(row)])
    items = planner.schedule(spec, PlanPick(days=[DayPick(day=1, items=fixed[0])]), {})
    assert not _lunch_missing(spec, items)
    names = [it["place_name"] for it in items if it["type"] != "move"]
    assert names.count("남촌회관") == 1 and not any("주변에서 점심" in n for n in names)   # 있던 식당을 옮김(새로 만들지 않음)
    assert names.index("사성암(구례)") < names.index("화엄사")                            # 다른 장소 순서는 그대로


def test_u1_edit_path_clears_lunch_violation(db):
    spec = planner.TripSpec(region=REGION, start=date(2026, 11, 29), nights=0)
    _seed("u1", spec, [[PickItem(place_id=_id("사성암"), stay_minutes=150, reason="r"),
                        PickItem(place_id=_id("화엄사"), stay_minutes=150, reason="r"),
                        PickItem(place_id=_id("남촌회관"), stay_minutes=60, role="lunch", reason="r")]])
    r = agent.handle_query("제목", "u1", "edit", edit={"op": "title", "title": "점심 확인"})
    assert not [v for v in r["violations"] if v["type"] == "식사 누락"]


def test_u2_user_place_names_in_messages(db):
    spec = planner.TripSpec(region=REGION, start=date(2026, 11, 29), nights=0)
    _seed("u2", spec, [[PickItem(place_id=_id("순천만국가정원"), stay_minutes=90, reason="r"),
                        PickItem(place_id=_id("남촌회관"), stay_minutes=60, role="lunch", reason="r")]])
    r = agent.handle_query("직접 추가", "u2", "edit", edit={"op": "add", "manual": {"name": "바빈스커피 테스트점", "lat": 34.95, "lng": 127.52}})
    uid = next(it["place_id"] for it in r["itinerary"] if it["place_name"] == "바빈스커피 테스트점")
    assert uid.startswith("user:")
    r = agent.handle_query("다음에 추가", "u2", "edit", edit={"op": "add", "place_id": _id("순천만습지"), "after": uid})
    assert any("바빈스커피 테스트점 바로 다음에" in ch for ch in r["changes"]), r["changes"]
    wet = next(it for it in r["itinerary"] if it["place_name"].startswith("순천만습지"))
    assert wet["reason"].startswith("바빈스커피 테스트점 근처")
    r = agent.handle_query("빼기", "u2", "edit", edit={"op": "remove", "place_id": uid})
    assert any("바빈스커피 테스트점" in ch for ch in r["changes"]) and not any("user:" in ch for ch in r["changes"])


def test_u3_add_without_day_skips_closed_day(db):
    spec = planner.TripSpec(region=REGION, start=date(2026, 11, 29), nights=1)     # 11-30(월) = 순천만국가정원 휴무
    _seed("u3", spec, [[PickItem(place_id=_id("사성암"), stay_minutes=90, reason="r"),
                        PickItem(place_id=_id("화엄사"), stay_minutes=90, reason="r")],
                       [PickItem(place_id=_id("남촌회관"), stay_minutes=60, role="lunch", reason="r")]])
    r = agent.handle_query("일정에 넣기", "u3", "edit", edit={"op": "add", "place_id": _id("순천만국가정원")})
    garden = next(it for it in r["itinerary"] if it["place_name"].startswith("순천만국가정원"))
    assert garden["date"] == "2026-11-29"
    assert not [v for v in r["violations"] if v["type"] == "휴무일"]


def test_u4_delete_request_history(db, monkeypatch):
    sid = "u4"
    for i in range(3):
        store.log_message(sid, "user", "chat", f"요청 {i}")
        store.log_message(sid, "momo", "chat", f"답변 {i}", status="승인 대기")
    rows = store.list_messages(sid)
    first_user = rows[0]["id"]
    d = c.delete(f"/trips/{sid}/messages/{first_user}").json()
    assert len(d["deleted"]) == 2                                   # 요청 + 그 답변
    left = [m["text"] for m in store.list_messages(sid)]
    assert left == ["요청 1", "답변 1", "요청 2", "답변 2"]
    assert c.delete(f"/trips/{sid}/messages/999999").status_code == 404
    d = c.delete(f"/trips/{sid}/messages").json()
    assert len(d["deleted"]) == 4
    called = []
    monkeypatch.setattr(agent, "graph", lambda: called.append(1) or (_ for _ in ()).throw(AssertionError("복원 시도")))
    assert agent.backfill_messages(sid) == 0 and not called         # 지운 기록은 체크포인트에서 되살리지 않음
    assert store.list_messages(sid) == []


def _dinner_row(*extra):
    return [PickItem(place_id=_id("순천 드라마촬영장"), stay_minutes=90, reason="r"),
            PickItem(place_id=_id("남녘들밥상"), stay_minutes=60, role="dinner", reason="r"), *extra,
            PickItem(place_id=_id("호텔라움"), stay_minutes=0, role="stay", reason="r")]


def _sched(spec, rows):
    return planner.schedule(spec, PlanPick(days=[DayPick(day=i + 1, items=r) for i, r in enumerate(rows)]), {})


def _clash(spec, items):
    return [v for v in planner.validate(spec, items, {it.get("place_id") for it in items} | {planner.MEAL_ID})
            if v["type"] == "식사 중복"]


def test_u10_triple_dinner_is_flagged_and_cleaned():
    spec = planner.TripSpec(region=REGION, start=date(2026, 11, 28), nights=1, waypoint_ids=[_id("남녘들밥상")])
    rows = [_dinner_row(PickItem(place_id=planner.MEAL_ID, role="dinner", stay_minutes=60, reason=""),
                        PickItem(place_id=_id("명지원"), stay_minutes=60, role="dinner", reason="r")), []]
    assert len(_clash(spec, _sched(spec, rows))) == 2                       # 재현: 18:00·19:20·20:40
    log = []
    fixed = agent._fill_meals(spec, [list(r) for r in rows], log)
    items = _sched(spec, fixed)
    assert not _clash(spec, items)
    d1 = [it["place_name"] for it in items if it["date"] == "2026-11-28" and it["type"] == "meal"]
    assert "남녘들밥상" in d1 and "명지원" not in d1 and not any("저녁 식사" in n for n in d1)   # 사용자가 넣은 식당만 남음
    assert any("명지원" in x for x in log)


def test_u10_cafe_after_meal_and_two_user_meals_are_allowed():
    spec = planner.TripSpec(region=REGION, start=date(2026, 11, 28), nights=1, waypoint_ids=[_id("남녘들밥상")])
    cafe = _sched(spec, [_dinner_row(PickItem(place_id=_id("카페 어그원"), stay_minutes=45, reason="r")), []])
    assert not _clash(spec, cafe)                                           # 식당 + 카페는 괜찮음
    spec2 = planner.TripSpec(region=REGION, start=date(2026, 11, 28), nights=1,
                             waypoint_ids=[_id("남녘들밥상"), _id("명지원")])
    rows = [_dinner_row(PickItem(place_id=_id("명지원"), stay_minutes=60, role="dinner", reason="r")), []]
    assert not _clash(spec2, _sched(spec2, rows))                            # 둘 다 사용자가 일부러 넣음 → 허용
    kept = agent._fill_meals(spec2, [list(r) for r in rows])
    assert [x.place_id for x in kept[0]].count(_id("명지원")) == 1


def test_u10_edit_adding_meal_next_to_auto_meal_keeps_users_choice(db):
    spec = planner.TripSpec(region=REGION, start=date(2026, 11, 29), nights=0)
    _seed("u10", spec, [[PickItem(place_id=_id("순천만국가정원"), stay_minutes=90, reason="r"),
                         PickItem(place_id=_id("남촌회관"), stay_minutes=60, role="lunch", reason="r"),
                         PickItem(place_id=_id("화엄사"), stay_minutes=90, reason="r")]])
    r = agent.handle_query("추가", "u10", "edit", edit={"op": "add", "place_id": _id("부부식당"), "after": _id("남촌회관")})
    meals = [it["place_name"] for it in r["itinerary"] if it["type"] == "meal"]
    assert "부부식당" in meals and "남촌회관" not in meals                  # 사용자가 고른 식당이 그 식사 자리를 맡음
    assert not [v for v in r["violations"] if v["type"] == "식사 중복"]
    assert any("식사가 겹쳐" in ch for ch in r["changes"])


# U11 다음 날로 이어지는 밤은 숙박이 기본(빠지면 알림·확정 불가), 차박·밤샘·야간 이동을 고르면 예외(사용자 요청 2026-11-26)
def _two_days():
    return [[PickItem(place_id=_id("순천만국가정원"), stay_minutes=90, reason="r"),
             PickItem(place_id=_id("남촌회관"), stay_minutes=60, role="lunch", reason="r"),
             PickItem(place_id=_id("사성암"), stay_minutes=90, reason="r")],
            [PickItem(place_id=_id("화엄사"), stay_minutes=90, reason="r"),
             PickItem(place_id=_id("부부식당"), stay_minutes=60, role="lunch", reason="r")]]


def test_u11_missing_night_stay_alerts_and_can_be_waived_or_filled(db):
    spec = planner.TripSpec(region=REGION, start=date(2026, 11, 28), nights=1)
    _seed("u11", spec, _two_days())
    r = agent.handle_query("제목", "u11", "edit", edit={"op": "title", "title": "숙박 확인"})
    miss = [v for v in r["violations"] if v["type"] == "숙박 누락"]
    assert miss and "숙박 필요 없음" in miss[0]["detail"]
    night = r["plan"]["itinerary"][0]["night"]
    assert night["status"] == "missing" and "차박" in night["options"]
    assert agent.handle_query("[확정]", "u11", "approve")["status"] != "확정"        # 알림이 풀리기 전엔 확정 안 됨
    # 예외: 차박
    r = agent.handle_query("차박", "u11", "edit", edit={"op": "night", "day": 1, "mode": "차박"})
    assert not [v for v in r["violations"] if v["type"] == "숙박 누락"]
    assert r["plan"]["itinerary"][0]["night"] == {"status": "none", "mode": "차박", "options": ["차박", "밤샘", "야간 이동"]}
    assert not any("조식" in (it.get("notes") or []) or "아침" in it["place_name"] for it in r["itinerary"])   # 숙소 아침 없음
    # 다시 숙소 넣기 → 가까운 숙소가 1일차 끝에
    r = agent.handle_query("숙소", "u11", "edit", edit={"op": "night", "day": 1, "mode": "stay"})
    d1 = [it for it in r["itinerary"] if it["date"] == "2026-11-28" and it["type"] != "move"]
    assert d1[-1]["type"] == "stay" and not [v for v in r["violations"] if v["type"] == "숙박 누락"]
    assert r["plan"]["itinerary"][0]["night"]["status"] == "stay"
    # 마지막 날 밤은 대상 아님
    assert "마지막 날" in agent.handle_query("x", "u11", "edit", edit={"op": "night", "day": 2, "mode": "차박"})["answer"]


def test_u11_removing_lodging_shows_alert_and_waiver_removes_existing_stay(db):
    spec = planner.TripSpec(region=REGION, start=date(2026, 11, 28), nights=1)
    rows = _two_days()
    rows[0].append(PickItem(place_id=_id("호텔라움"), stay_minutes=0, role="stay", reason="r"))
    _seed("u11b", spec, rows)
    r = agent.handle_query("밤샘", "u11b", "edit", edit={"op": "night", "day": 1, "mode": "야간 이동"})
    assert not any(it["type"] == "stay" for it in r["itinerary"]) and "호텔라움" in r["changes"][0]
    spec2 = agent.spec_from_dict(agent.graph().get_state({"configurable": {"thread_id": "u11b"}}).values["spec"])
    assert spec2.no_stay == {"1": "야간 이동"} and not planner.night_needs_stay(spec2, 0)


# U12 동선 편집에서 삭제(remove) · 순서 저장 때 식사 자리표시자가 숙소 뒤에 쌓이던 문제 · 같은 끼니 두 번
def test_u12_order_edit_can_remove_and_no_meal_after_stay(db):
    spec = planner.TripSpec(region=REGION, start=date(2026, 11, 28), nights=1, waypoint_ids=[_id("화엄사")])
    rows = _two_days()
    rows[0].append(PickItem(place_id=_id("호텔라움"), stay_minutes=0, role="stay", reason="r"))
    rows[1].insert(0, PickItem(place_id=_id("화엄사"), stay_minutes=60, reason="r"))   # 중복 없이 2일차 구성
    rows[1] = [rows[1][0], *rows[1][2:]]
    _seed("u12", spec, rows)
    ids1 = [_id("순천만국가정원"), _id("남촌회관"), _id("사성암"), _id("호텔라움")]
    r = agent.handle_query("[동선 편집]", "u12", "edit",
                           edit={"op": "order", "days": [{"day": 1, "place_ids": ids1}, {"day": 2, "place_ids": [_id("화엄사"), _id("부부식당")]}],
                                 "remove": [_id("사성암"), _id("화엄사")]})
    names = [it["place_name"] for it in r["itinerary"]]
    assert not any(n.startswith(("사성암", "화엄사")) for n in names) and "을(를) 일정에서 뺐어요" in r["changes"][0]
    st = agent.spec_from_dict(agent.graph().get_state({"configurable": {"thread_id": "u12"}}).values["spec"])
    assert _id("화엄사") not in st.waypoint_ids                      # 다시 짜도 되살아나지 않음
    for _ in range(3):                                                # 순서 저장을 반복해도 숙소 뒤 식사가 쌓이지 않음
        r = agent.handle_query("[동선 편집]", "u12", "edit", edit={"op": "order", "days": [{"day": 1, "place_ids": [_id("순천만국가정원"), _id("남촌회관"), _id("호텔라움")]}]})
    d1 = [it for it in r["itinerary"] if it["date"] == "2026-11-28" and it["type"] != "move"]
    assert d1[-1]["type"] == "stay"
    for d in ("2026-11-28", "2026-11-29"):
        meals = [it for it in r["itinerary"] if it["date"] == d and it["type"] == "meal" and "조식" not in (it.get("notes") or [])]
        assert sum(1 for m in meals if planner._slot(m) == "lunch") <= 1 and sum(1 for m in meals if planner._slot(m) == "dinner") <= 1
    assert not [v for v in r["violations"] if v["type"] == "식사 중복"]


def test_u12_same_slot_twice_is_clash_even_with_gap():
    spec = planner.TripSpec(region=REGION, start=date(2026, 11, 28), nights=0)
    mk = lambda pid, nm, s, e, t="meal": {"place_id": pid, "place_name": nm, "start_time": s, "end_time": e, "type": t, "notes": []}
    day = [mk(_id("남녘들밥상"), "남녘들밥상", "18:00", "19:00"), mk(_id("순천만습지"), "순천만습지", "19:10", "20:40", "place"),
           mk(None, "주변에서 저녁 식사", "23:35", "24:00")]
    assert len(planner.meal_clashes(spec, day)) == 1                 # 4시간 넘게 떨어져도 같은 저녁이면 중복
    cafe = [day[0], mk(_id("카페 어그원"), "카페 어그원", "19:10", "19:50")]
    assert not planner.meal_clashes(spec, cafe)                      # 식당 + 카페는 허용
    stay = [day[0], mk(_id("호텔라움"), "호텔라움", "21:00", "21:00", "stay"), mk(None, "주변에서 저녁 식사", "21:10", "22:10")]
    assert planner.meal_clashes(spec, stay)                          # 숙소 뒤 자리표시자


# U13 여행 강도 강행군: 하루 20곳 이상(가까운 곳 연달아), 06:30~23:00 / 숙소는 22:00~다음 날 02:00 도착도 문제 없음
def test_u13_intensity_gangheng():
    assert planner.INTENSITY["강행군"][2] >= 20
    spec = planner.TripSpec(region=REGION, start=date(2026, 11, 28), nights=1, intensity="강행군")
    w = planner.windows(spec)[1]
    assert w == (6 * 60 + 30, 23 * 60)
    assert planner.max_places(w, 1, False, "강행군") >= 20
    from momo import brochure, options
    assert "강행군" in brochure.CHECK_OPTIONS["intensity"] and options.INTENSITIES[0]["value"] == "강행군"


def test_u13_late_checkin_until_2am_is_ok_and_ics_rolls_over():
    from momo import hours, store
    spec = planner.TripSpec(region=REGION, start=date(2026, 11, 28), nights=1)
    mk = lambda s: [{"seq": 1, "date": "2026-11-28", "type": "stay", "place_id": _id("호텔라움"), "place_name": "호텔라움",
                     "start_time": s, "end_time": s, "travel_minutes_from_prev": 0, "hours_status": "확인됨", "source": "x", "notes": []}]
    late = lambda s: [v for v in planner.validate(spec, mk(s), {_id("호텔라움")}) if "숙소 도착" in v["detail"]]
    assert not late("23:30") and not late("25:50") and late("26:30")
    assert hours.fmt(25 * 60 + 10) == "25:10" and hours.label("25:10") == "익일 01:10" and hours.label("21:00") == "21:00"
    ics = store.to_ics("p", {"title": "t", "itinerary": mk("25:10")}).decode()
    assert "DTSTART;TZID=Asia/Seoul:20261129T011000" in ics or "20261129T011000" in ics


# U14 지점이 여러 곳인 상호(정지영·스타벅스·파스쿠찌 등 프랜차이즈)는 이름으로 넣을 때 지점을 물어본다
def _fake_branches(q, region, limit=8):
    from momo import placesearch
    if placesearch.brand_of(q) not in ("정지영", "스타벅스"):
        return []
    mk = lambda nm, ad, km: {"name": nm, "address": ad, "distance_km": km, "source": "브이월드",
                             "candidate": {"name": nm, "address": ad, "lat": 37.28 + km / 100, "lng": 127.01, "source": "브이월드",
                                           "key": f"vw:{nm}{ad}", "distance_km": km, "similarity": 0.9}}
    return [mk("정지영커피로스터즈 행궁본점", "수원시 팔달구 신풍로 42", 0.4), mk("정지영커피로스터즈", "수원시 장안구 경수대로 893", 1.8),
            mk("정지영커피로스터즈", "수원시 장안구 대평로 27", 2.0)]


def test_u14_brand_aliases():
    from momo import placesearch as P
    assert P.brand_of("파스쿠치") == "파스쿠찌" and P.brand_of("스벅") == "스타벅스" and P.brand_of("정지영 카페") == "정지영"
    assert P.brand_of("불국사") == "불국사"


def test_u14_name_add_asks_branch_then_adds_chosen(db, monkeypatch):
    from momo import placesearch
    monkeypatch.setattr(placesearch, "branches", _fake_branches)
    spec = planner.TripSpec(region="suwon", start=date(2026, 12, 19), nights=0)
    hw = next(pid for pid, p in data.places("suwon").items() if p["title"].startswith("화성행궁"))
    _seed("u14", spec, [[PickItem(place_id=hw, stay_minutes=90, reason="r")]])
    r = agent.handle_query("정지영 추가", "u14", "edit", edit={"op": "add", "name": "정지영카페"})
    assert r["status"] == "되묻기" and "지점이 3곳" in r["answer"]
    opts = r["place_choices"][0]["options"]
    assert len(opts) == 3 and all(o["edit"]["op"] == "add" and o["edit"]["candidate"] for o in opts)
    pick = opts[2]
    r = agent.handle_query("[지점 선택] 대평로", "u14", "edit", edit=pick["edit"])
    assert any(it["place_name"] == "정지영커피로스터즈" for it in r["itinerary"]), r["answer"]
    # 관광지(수집 데이터에 있는 명소)는 묻지 않음
    ids, users, notes = agent.resolve_waypoints(["화성행궁"], "suwon", (ch := []))
    assert ids == [hw] and not ch


# U15 출발 지점·최종 복귀 지점을 일정표(텍스트·HTML·Word)에 항상 표기 — 모르면 '미정'
def test_u15_origin_and_return_always_in_itinerary():
    import io
    import re as _re
    from docx import Document
    from momo import brochure, export, render, richtext
    spec = planner.TripSpec(region=REGION, start=date(2026, 11, 28), nights=0, departure="경기", origin_label="판교동")
    items = _sched(spec, [[PickItem(place_id=_id("순천만국가정원"), stay_minutes=90, reason="r")]])
    t = render.itinerary_text(items)
    assert "🏠" in t and "출발 지점: 판교동" in t and "최종 복귀 지점: 판교동 도착" in t
    plan = brochure.build(spec, items, [], [], [])
    h = richtext.plan_html(plan, "", [], "")
    homes = _re.findall(r'<tr class="mm-home">.*?</tr>', h)
    assert len(homes) == 2 and "판교동" in homes[0] and "최종 복귀" in homes[1]
    paras = [p.text for p in Document(io.BytesIO(export.to_docx("x", {"plan": plan, "title": "t"}))).paragraphs]
    assert any(p.startswith("🏠 출발 지점") for p in paras) and any(p.startswith("🏁 최종 복귀 지점") for p in paras)
    # 출발지를 모를 때도 자리는 표시
    s2 = planner.TripSpec(region=REGION, start=date(2026, 11, 28), nights=0)
    t2 = render.itinerary_text(_sched(s2, [[PickItem(place_id=_id("순천만국가정원"), stay_minutes=90, reason="r")]]))
    assert "출발 지점: 미정" in t2 and "최종 복귀 지점: 미정" in t2


# U16 꼭 가고 싶은 곳 잘림(80자) → 여러 줄·200곳까지, 일정 용량을 넘으면 넣을 만큼만 '반드시' + 나머지 후보 안내
def test_u16_clean_waypoints_long_list():
    names = [f"장소{i}" for i in range(1, 121)]
    raw = ["꼭 갈 곳 " + ", ".join(names[:60]), "\n".join(names[60:]), "장소3", "010-1234-5678"]
    got, drop = agent.clean_waypoints(raw)
    assert got[:3] == ["장소1", "장소2", "장소3"] and len([g for g in got if g.startswith("장소")]) == 120 and not drop
    assert not any("1234-5678" in g for g in got)                       # 개인정보는 가림
    many, drop = agent.clean_waypoints([f"곳{i}" for i in range(250)])
    assert len(many) == agent.MAX_WAYPOINTS and len(drop) == 50          # 넘친 곳도 버리지 않고 안내용으로 돌려줌


def test_u16_must_ids_caps_to_trip_capacity():
    ids = [pid for pid, p in data.places(REGION).items() if p["kind"] == "attraction"][:40]
    spec = planner.TripSpec(region=REGION, start=date(2026, 11, 28), nights=0, waypoint_ids=ids)
    must, over = planner.must_ids(spec), planner.waypoint_overflow(spec)
    assert 0 < len(must) < 40 and must == ids[:len(must)] and over == ids[len(must):]     # 적은 순서대로
    rows = planner.ensure_waypoints(spec, PlanPick(days=[DayPick(day=1, items=[])])).days[0].items
    assert {x.place_id for x in rows} == set(must)                       # 넘친 곳까지 밀어 넣지 않음
    g = planner.TripSpec(region=REGION, start=date(2026, 11, 28), nights=0, waypoint_ids=ids, intensity="강행군")
    assert len(planner.must_ids(g)) > len(must)                          # 강행군이면 더 들어감


# U17 요청 중지·취소: 멈추면 결과를 쓰지 않고 요청 전 상태(일정·기록·되돌리기)로
def test_u17_cancel_rolls_back_and_check_raises(db, monkeypatch):
    import threading
    import time
    from app import main
    from momo import cancel
    ev = threading.Event()
    tok = cancel.CURRENT.set(ev)
    cancel.check()
    ev.set()
    with pytest.raises(cancel.JobCancelled):
        cancel.check()
    cancel.CURRENT.reset(tok)

    spec = planner.TripSpec(region=REGION, start=date(2026, 11, 29), nights=0)
    _seed("u17", spec, [[PickItem(place_id=_id("사성암"), stay_minutes=90, reason="r")]])
    before = agent.graph().get_state({"configurable": {"thread_id": "u17"}}).values
    real_run = main._run
    started = threading.Event()

    def slow_run(prep):                       # 실제 편집을 끝낸 뒤 '중지'를 기다림 → 끝난 뒤 눌러도 되돌려야 함
        out = real_run(prep)
        started.set()
        for _ in range(100):
            if main._JOBS[[k for k, v in main._JOBS.items() if v["session_id"] == "u17"][-1]]["cancel"].is_set():
                break
            time.sleep(0.05)
        return out
    monkeypatch.setattr(main, "_run", slow_run)
    j = c.post("/query/async", json={"question": "제목", "session_id": "u17", "action": "edit",
                                       "edit": {"op": "title", "title": "취소될 제목"}}).json()
    assert started.wait(10)
    assert c.post(f"/jobs/{j['job_id']}/cancel").json()["state"] == "cancelling"
    for _ in range(100):
        s = c.get(f"/jobs/{j['job_id']}").json()
        if s["state"] != "running":
            break
        time.sleep(0.05)
    assert s["state"] == "cancelled" and s["response"]["status"] == "취소"
    after = agent.graph().get_state({"configurable": {"thread_id": "u17"}}).values
    assert agent.spec_from_dict(after["spec"]).title is None and after["draft_version"] == before["draft_version"]
    msgs = store.list_messages("u17")
    assert [m["status"] for m in msgs] == ["취소", "취소"] and store.undo_count("u17") == 0
    assert c.post(f"/jobs/{j['job_id']}/cancel").json()["state"] == "cancelled"     # 끝난 작업은 다시 못 멈춤


# U18 출발일 이동 중 끼니 누락(06:00 출발 → 아침 없음, 10:00 출발 → 점심 없음) → 가는 길/오는 길 식사 자동 배치
def _fixed_drive(monkeypatch, out_min=280, back_min=280):
    monkeypatch.setattr(planner, "one_way_minutes", lambda spec: out_min)
    monkeypatch.setattr(planner, "_return_base", lambda spec: back_min)


def test_u18_road_meals_on_departure_and_return(monkeypatch):
    _fixed_drive(monkeypatch)
    spec = planner.TripSpec(region=REGION, start=date(2026, 11, 28), nights=1, departure="경기", origin_label="판교동",
                            depart_time=6 * 60)
    assert [m["meal"] for m in planner.road_meals(spec)["out"]] == ["아침"]
    spec.depart_time = 10 * 60                       # 도착 14:50 → 여행지 점심 불가 → 가는 길 점심
    assert [m["meal"] for m in planner.road_meals(spec)["out"]] == ["점심"]
    base, win = planner._base_windows(spec), planner.windows(spec)
    assert win[0][0] >= base[0][0] + 50              # 식사 시간만큼 여행지 시작이 늦어짐
    rows = [[PickItem(place_id=_id("순천만국가정원"), stay_minutes=90, reason="r")],
            [PickItem(place_id=_id("화엄사"), stay_minutes=90, reason="r")]]
    items = planner.schedule(spec, PlanPick(days=[DayPick(day=i + 1, items=r) for i, r in enumerate(rows)]), {})
    road = [it for it in items if (it.get("notes") or [""])[0] == "가는 길"]
    assert len(road) == 1 and "점심" in road[0]["place_name"]
    assert 11 * 60 + 30 <= planner.hours.parse_hm(road[0]["start_time"]) <= 14 * 60
    # 마지막 날 늦게(18시 넘어) 집에 닿으면 오는 길 저녁
    spec.arrive_time = 23 * 60
    back = [it for it in planner.schedule(spec, PlanPick(days=[DayPick(day=1, items=rows[0]), DayPick(day=2, items=[
        PickItem(place_id=_id("화엄사"), stay_minutes=300, reason="r")])]), {}) if (it.get("notes") or [""])[0] == "오는 길"]
    assert back and "저녁" in back[0]["place_name"]
    # '아침 안 먹음'이면 가는 길 아침도 없음
    spec.depart_time, spec.breakfast = 6 * 60, "안 먹음"
    assert not planner.road_meals(spec)["out"]


# U19 출발 지점 다음·복귀 지점 앞에 들를 곳(전국 검색 결과) 넣기·빼기, 식당이면 그 끼니를 대신
def test_u19_route_stop_after_origin_and_before_return(db, monkeypatch):
    _fixed_drive(monkeypatch)
    spec = planner.TripSpec(region=REGION, start=date(2026, 11, 28), nights=0, departure="경기", origin_label="판교동",
                            origin_coords=[37.39, 127.11], depart_time=10 * 60)
    _seed("u19", spec, [[PickItem(place_id=_id("순천만국가정원"), stay_minutes=90, reason="r")]])
    cand = {"name": "대전 맛집", "lat": 36.35, "lng": 127.38, "category": "restaurant", "key": "osm:N1", "source": "OpenStreetMap"}
    r = c.post("/query", json={"question": "가는 길에 대전 맛집", "session_id": "u19", "action": "edit",
                               "edit": {"op": "route_stop", "leg": "out", "action": "add", "candidate": cand}}).json()
    assert "가는 길에 대전 맛집" in " ".join(r.get("changes") or [r["answer"]])
    st = agent.graph().get_state({"configurable": {"thread_id": "u19"}}).values
    s2 = agent.spec_from_dict(st["spec"])
    assert s2.depart_stops[0]["kind"] == "식당" and not planner.road_meals(s2)["out"]    # 식당이 가는 길 점심을 대신
    road = [it for it in st["itinerary"] if (it.get("notes") or [""])[0] == "가는 길"]
    assert [it["place_name"] for it in road] == ["대전 맛집"]
    # 일정표(브로셔)에서 출발 지점 다음 카드로 보이고, 현지 동선에는 끼지 않음
    from momo import brochure
    plan = brochure.build(s2, st["itinerary"], [], [], [])
    first = plan["itinerary"][0]["stops"][0]
    assert first["route"] == "out" and first["route_key"] == "osm:N1" and first["place_id"] is None
    r = c.post("/query", json={"question": "빼기", "session_id": "u19", "action": "edit",
                               "edit": {"op": "route_stop", "leg": "out", "action": "remove", "stop": {"key": "osm:N1"}}}).json()
    st = agent.graph().get_state({"configurable": {"thread_id": "u19"}}).values
    assert not st["spec"]["depart_stops"]
    assert [it["place_name"] for it in st["itinerary"] if (it.get("notes") or [""])[0] == "가는 길"] == ["가는 길 점심 · 휴게소·경유지 식당"]
    # 복귀 지점 앞
    c.post("/query", json={"question": "오는 길", "session_id": "u19", "action": "edit",
                           "edit": {"op": "route_stop", "leg": "back", "action": "add",
                                    "candidate": {"name": "전주 한옥마을", "lat": 35.81, "lng": 127.15, "key": "tour:9"}}})
    st = agent.graph().get_state({"configurable": {"thread_id": "u19"}}).values
    assert st["spec"]["return_stops"][0]["name"] == "전주 한옥마을"
    assert any(it["place_name"] == "전주 한옥마을" and (it.get("notes") or [""])[0] == "오는 길" for it in st["itinerary"])


def test_u19_route_stop_needs_origin(db):
    spec = planner.TripSpec(region=REGION, start=date(2026, 11, 28), nights=0)
    _seed("u19b", spec, [[PickItem(place_id=_id("순천만국가정원"), stay_minutes=90, reason="r")]])
    r = c.post("/query", json={"question": "가는 길", "session_id": "u19b", "action": "edit",
                               "edit": {"op": "route_stop", "leg": "out", "action": "add",
                                        "candidate": {"name": "x", "lat": 36.3, "lng": 127.4}}}).json()
    assert "출발지를 먼저" in r["answer"]


def test_u20_route_km_marks_places_on_the_way():
    spec = planner.TripSpec(region=REGION, start=date(2026, 11, 28), nights=0, departure="경기", origin_label="판교동",
                            origin_coords=[37.39, 127.11])
    rk = planner.route_km(spec, (36.35, 127.38))         # 대전: 판교 → 순천 경로 위
    assert rk["out"] < 25
    assert planner.route_km(spec, (37.75, 128.9))["out"] > 100      # 강릉: 경로 밖


# U21 일정 고치기에서 중간에 끼워 넣기(장소 사이·그날 첫 장소 앞)
def test_u21_insert_between_and_before_first(db):
    spec = planner.TripSpec(region=REGION, start=date(2026, 11, 29), nights=0)
    a, b, x, y = _id("사성암"), _id("화엄사"), _id("순천만국가정원"), _id("낙안읍성")
    _seed("u21", spec, [[PickItem(place_id=a, stay_minutes=60, reason="r"), PickItem(place_id=b, stay_minutes=60, reason="r")]])
    r = c.post("/query", json={"question": "끼워 넣기", "session_id": "u21", "action": "edit",
                               "edit": {"op": "add", "place_id": x, "after": a, "insert": True}}).json()
    assert "바로 다음에" in " ".join(r.get("changes") or [r["answer"]])
    c.post("/query", json={"question": "맨 앞", "session_id": "u21", "action": "edit",
                           "edit": {"op": "add", "place_id": y, "before": a, "insert": True}})
    st = agent.graph().get_state({"configurable": {"thread_id": "u21"}}).values
    order = [it.get("place_id") for it in st["itinerary"] if it["type"] == "place"]
    assert order.index(y) < order.index(a) < order.index(x) < order.index(b)


# U22 순서를 직접 정한 날 식사가 저녁 시간대(18:00)까지 밀려 4시간 빈 시간(섬진강 대나무숲길 14:05 → 라플라타 18:00)
#     → 연달아 붙이기(식사 시간대까지 30분 넘게 기다리지 않음) + 시작 시각 직접 정하기·자동으로 되돌리기
def test_u22_user_ordered_day_is_continuous_and_start_time_editable(db):
    spec = planner.TripSpec(region=REGION, start=date(2026, 11, 29), nights=0)
    a, m, b = _id("사성암"), _id("남촌회관"), _id("화엄사")
    row = [PickItem(place_id=a, stay_minutes=60, reason="r"), PickItem(place_id=m, stay_minutes=60, role="dinner", reason="r"),
           PickItem(place_id=b, stay_minutes=60, reason="r")]
    hm = planner.hours.parse_hm

    def gap(items):
        d = [it for it in items if it["type"] != "move"]
        return hm(d[1]["start_time"]) - hm(d[0]["end_time"])
    plan = PlanPick(days=[DayPick(day=1, items=row)])
    assert gap(planner.schedule(spec, plan, {})) > 120            # 재현: 식사 시간대까지 기다림
    spec.user_ordered_days = [1]
    assert gap(planner.schedule(spec, plan, {})) <= 60            # 직접 정한 날은 이어서
    _seed("u22", spec, [row])
    r = c.post("/query", json={"question": "시간", "session_id": "u22", "action": "edit",
                               "edit": {"op": "time", "place_id": b, "time": "16:30", "minutes": 45}}).json()
    assert "16:30" in " ".join(r.get("changes") or [r["answer"]])
    st = agent.graph().get_state({"configurable": {"thread_id": "u22"}}).values
    it = next(x for x in st["itinerary"] if x.get("place_id") == b)
    assert it["start_time"] == "16:30" and hm(it["end_time"]) - hm(it["start_time"]) == 45
    c.post("/query", json={"question": "자동", "session_id": "u22", "action": "edit", "edit": {"op": "time", "place_id": b, "time": None}})
    st = agent.graph().get_state({"configurable": {"thread_id": "u22"}}).values
    assert not st["spec"]["fixed_starts"]
    assert next(x for x in st["itinerary"] if x.get("place_id") == b)["start_time"] < "16:30"


# U24 첫날 출발 → 도착이 실제보다 너무 길게 잡히고 고칠 수 없음(판교→구례 240분 + 들르는 곳마다 휴게 + 중심 갔다 되돌아오기)
def test_u24_outbound_time_calibrated_and_editable(db, monkeypatch):
    from momo import roads
    monkeypatch.setattr(roads, "its_key", lambda: None)
    monkeypatch.setattr(roads, "road_alerts", lambda path, d: {"events": [], "cautions": [], "delay_min": 0})
    monkeypatch.setattr(roads, "osrm", lambda a, b: {"distance_km": 290.0, "duration_min": 220, "path": [list(a), list(b)],
                                                     "roads": [{"ref": "1", "name": "경부고속도로", "km": 280, "min": 200, "bbox": [0, 0, 0, 0]},
                                                               {"ref": "", "name": "산업로", "km": 10, "min": 20, "bbox": [0, 0, 0, 0]}]})
    dr = roads.drive((37.39, 127.11), (35.07, 127.47), date(2026, 11, 28), 6 * 60, True)
    assert dr["free_min"] == round(200 * roads.EXPRESS_FACTOR + 20)          # 고속도로 구간 보정
    assert planner._less_rests({"total_min": 200, "rests": 1}, [{"name": "x"}]) == 185   # 들르는 곳이 쉬기를 대신
    spec = planner.TripSpec(region=REGION, start=date(2026, 11, 29), nights=0, departure="경기", origin_label="판교동",
                            origin_coords=[37.39, 127.11], depart_time=7 * 60)
    _seed("u24", spec, [[PickItem(place_id=_id("사성암"), stay_minutes=60, reason="r")]])
    r = c.post("/query", json={"question": "운전", "session_id": "u24", "action": "edit",
                               "edit": {"op": "long_time", "leg": "out", "minutes": 150}}).json()
    assert "2시간 30분" in " ".join(r.get("changes") or [r["answer"]])
    st = agent.graph().get_state({"configurable": {"thread_id": "u24"}}).values
    s2 = agent.spec_from_dict(st["spec"])
    assert planner.one_way_minutes(s2) == 150
    mv = next(it for it in st["itinerary"] if it["type"] == "move" and it["notes"][0] == "출발")
    assert mv["start_time"] == "07:00" and mv["end_time"] == "10:00"      # 운전 150분 + 가는 길 아침 30분
    c.post("/query", json={"question": "자동", "session_id": "u24", "action": "edit", "edit": {"op": "long_time", "leg": "out", "minutes": None}})
    assert not agent.graph().get_state({"configurable": {"thread_id": "u24"}}).values["spec"]["long_override"]


# U25 출발지에서 들르는 곳도 동선에 포함(이동 줄·번호) + 머무는 시간·순서 바꾸기
def test_u25_route_stops_are_part_of_route_and_editable(db, monkeypatch):
    _fixed_drive(monkeypatch)
    from momo import brochure
    spec = planner.TripSpec(region=REGION, start=date(2026, 11, 28), nights=0, departure="경기", origin_label="판교동",
                            origin_coords=[37.39, 127.11], depart_time=8 * 60, breakfast="안 먹음")
    _seed("u25", spec, [[PickItem(place_id=_id("사성암"), stay_minutes=60, reason="r")]])
    for nm, lat, lng in (("청주 상당산성", 36.66, 127.54), ("전주 한옥마을", 35.81, 127.15)):
        c.post("/query", json={"question": nm, "session_id": "u25", "action": "edit",
                               "edit": {"op": "route_stop", "leg": "out", "action": "add", "candidate": {"name": nm, "lat": lat, "lng": lng, "key": nm}}})
    st = agent.graph().get_state({"configurable": {"thread_id": "u25"}}).values
    s2 = agent.spec_from_dict(st["spec"])
    d0 = brochure.build(s2, st["itinerary"], [], [], [])["itinerary"][0]
    names = [x["name"] for x in d0["stops"]]
    assert names[:2] == ["청주 상당산성", "전주 한옥마을"] and [x["order"] for x in d0["stops"][:3]] == [1, 2, 3]
    legs = {(l["from_name"], l["to_name"]) for l in d0["legs"]}
    assert ("판교동", "청주 상당산성") in legs and ("청주 상당산성", "전주 한옥마을") in legs
    assert any(l["from_name"] == "전주 한옥마을" for l in d0["legs"])          # 들른 곳 → 여행지 첫 장소
    c.post("/query", json={"question": "머무름", "session_id": "u25", "action": "edit",
                           "edit": {"op": "route_stop", "leg": "out", "action": "stay", "key": "청주 상당산성", "minutes": 90}})
    c.post("/query", json={"question": "순서", "session_id": "u25", "action": "edit",
                           "edit": {"op": "route_stop", "leg": "out", "action": "move", "key": "청주 상당산성", "dir": 1}})
    st = agent.graph().get_state({"configurable": {"thread_id": "u25"}}).values
    assert [x["name"] for x in st["spec"]["depart_stops"]] == ["전주 한옥마을", "청주 상당산성"]
    assert st["spec"]["depart_stops"][1]["stay_min"] == 90
    road = [it for it in st["itinerary"] if (it.get("notes") or [""])[0] == "가는 길"]
    assert [it["place_name"] for it in road] == ["전주 한옥마을", "청주 상당산성"]        # 정한 순서 그대로
    hm = planner.hours.parse_hm
    assert hm(road[1]["end_time"]) - hm(road[1]["start_time"]) == 90


def test_u25_route_legs_not_counted_as_local_travel(monkeypatch):
    _fixed_drive(monkeypatch)
    from momo import brochure
    spec = planner.TripSpec(region=REGION, start=date(2026, 11, 28), nights=0, departure="경기", origin_label="판교동",
                            origin_coords=[37.39, 127.11], depart_time=8 * 60, breakfast="안 먹음",
                            depart_stops=[{"key": "k", "name": "청주 상당산성", "lat": 36.66, "lng": 127.54, "detour_min": 15, "stay_min": 45, "kind": "관광지"}])
    items = _sched(spec, [[PickItem(place_id=_id("사성암"), stay_minutes=60, reason="r"),
                           PickItem(place_id=_id("화엄사"), stay_minutes=60, reason="r")]])
    plan = brochure.build(spec, items, [], [], [])
    d0 = plan["itinerary"][0]
    assert any(l.get("long") for l in d0["legs"])                                   # 동선에는 보임
    assert d0["day_distance_km"] < 60 and plan["totals"]["local"]["distance_km"] < 60    # 현지 이동 합계엔 안 셈
