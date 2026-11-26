"""개편 기능(LLM 없이): 경비·예산, 진행률, 동선 최적화, 브로셔 plan, 조사 처리."""
from datetime import date

from momo import brochure, costs, data, planner
from momo.agent import _josa
from momo.schemas import DayPick, PickItem, PlanPick


def test_budget_and_fee_parsing():
    assert costs.parse_budget("60만 원") == (600000, False)
    assert costs.parse_budget("1인 20만원") == (200000, True)
    assert costs.parse_budget("300,000원") == (300000, False)
    assert costs.parse_budget("넉넉하게") == (None, False)
    assert costs.parse_fee("어른 4,000원 / 청소년 2,000원") == 4000
    assert costs.parse_fee("무료") == 0
    assert costs.parse_fee("") is None


def _gj():
    pl = data.places("gyeongju")
    return pl, (lambda t: next(k for k, p in pl.items() if t in p["title"]))


def test_optimizer_keeps_meal_anchor_and_reduces_distance():
    pl, pid = _gj()
    spec = planner.TripSpec(region="gyeongju", start=date(2026, 12, 12), nights=0)
    row = [PickItem(place_id=pid("천마총"), stay_minutes=60, reason=""),
           PickItem(place_id=pid("교동집밥"), role="lunch", stay_minutes=60, reason=""),
           PickItem(place_id=pid("석굴암"), stay_minutes=60, reason=""), PickItem(place_id=pid("첨성대"), stay_minutes=60, reason=""),
           PickItem(place_id=pid("국립경주박물관"), stay_minutes=60, reason="")]
    plan = PlanPick(days=[DayPick(day=1, items=row)])
    new, saved = planner.optimize_plan(spec, plan, set(pl), set())
    roles = [x.role for x in new.days[0].items]
    assert roles.index("lunch") == 1 and saved > 0
    # 사용자 순서를 정한 날은 바꾸지 않음
    same, saved2 = planner.optimize_plan(spec, plan, set(pl), {1})
    assert [x.place_id for x in same.days[0].items] == [x.place_id for x in row] and saved2 == 0


def test_brochure_plan_has_checklist_progress_costs():
    pl, pid = _gj()
    spec = planner.TripSpec(region="gyeongju", start=date(2026, 12, 12), nights=0, departure="대전", origin_label="대전",
                            long_transport="자차", local_transport="자차", companion_count=3,
                            assumptions=["intensity"], budget=300000, budget_label="30만 원")
    plan = PlanPick(days=[DayPick(day=1, items=[
        PickItem(place_id=pid("교동집밥"), role="lunch", stay_minutes=60, reason="점심"),
        PickItem(place_id=pid("천마총"), stay_minutes=60, reason="대표 명소"),
        PickItem(place_id=pid("첨성대"), stay_minutes=60, reason="가까움")])])
    items = planner.schedule(spec, plan, {})
    p = brochure.build(spec, items, [(pid("천마총"), "대표"), (pid("불국사"), "후보")], [], [])
    st = {c["key"]: c["status"] for c in p["trip"]["checklist"]}
    assert st["intensity"] == "assumed" and st["budget"] == "confirmed" and st["interests"] == "optional"
    assert p["trip"]["progress"]["total"] == 9 and p["trip"]["progress"]["percent"] < 100
    cats = {i["category"] for i in p["costs"]["items"]}
    assert {"유류비", "통행료", "식비"} <= cats and p["costs"]["people"] == 3
    assert any(not x["in_itinerary"] for x in p["places"])          # 후보 장소
    assert p["itinerary"][0]["legs"] and all(lg["estimated"] for lg in p["itinerary"][0]["legs"])


def test_expense_summary():
    planned = {"items": [{"category": "식비", "amount": 30000}, {"category": "숙박", "amount": 100000}],
               "total": 130000, "people": 2, "budget": 200000}
    s = costs.by_category(planned, [{"category": "식비", "amount": 42000}])
    row = next(r for r in s["by_category"] if r["category"] == "식비")
    assert row["diff"] == 12000 and s["total_actual"] == 42000 and s["per_person_actual"] == 21000


def test_josa():
    assert _josa("여행 강도", "을를") == "여행 강도를"
    assert _josa("출발지", "을를") == "출발지를"
    assert _josa("'서울'", "으로로") == "'서울'로"
    assert _josa("'100만 원'", "으로로") == "'100만 원'으로"
