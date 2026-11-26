"""화면 개발·데모용 예시 plan(app/static/sample_plan.json) — LLM 없이 코드로 배치.

순천/구례 1박 2일 · 서울 강남구 출발 · KTX + 렌터카 · 부모님 동반 4명 · 예산 100만 원 · 사성암(사용자 지정, 관광공사 실시간 편입) 포함.
사용법: python scripts/make_sample_plan.py
"""
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from momo import brochure, data, planner  # noqa: E402
from momo.schemas import DayPick, PickItem, PlanPick  # noqa: E402

pl = data.places("suncheon_gurye")


def pid(t: str) -> str:
    return next(k for k, p in pl.items() if t in p["title"] and p["kind"] in ("attraction", "restaurant"))


spec = planner.TripSpec(region="suncheon_gurye", start=date(2026, 12, 12), nights=2, departure="경기",
                        origin_label="경기 성남시 분당구 판교동", long_transport="KTX·SRT", local_transport="렌터카", intensity="보통",
                        companion_type="노약자", companion_count=4, companions="부모님 동반",
                        assumptions=["intensity", "local_transport"], budget=1200000, budget_label="120만 원",
                        origin_coords=[37.39, 127.1], vehicle=None, given=["depart_time"], depart_time=7 * 60 + 30,
                        end_label="경기 성남시 분당구 판교동", end_coords=[37.39, 127.1], end_location="경기", arrive_time=21 * 60,
                        custom_costs=[{"id": "c1", "category": "기념품", "label": "순천 특산물", "amount": 40000}])
lodg = next(k for k, p in pl.items() if p["kind"] == "lodging" and "순천" in (p.get("sigungu") or ""))
from momo import hours  # noqa: E402
dinner2 = next(k for k, p in pl.items() if p["kind"] == "restaurant" and "구례" in (p.get("sigungu") or "")
               and hours.parse_open(p, date(2026, 12, 13)).status == "확인됨"
               and hours.parse_open(p, date(2026, 12, 13)).close >= 19 * 60 + 30 and "산채" not in p["title"])
plan = PlanPick(days=[
    DayPick(day=1, items=[
        PickItem(place_id=pid("남녘들밥상"), role="lunch", stay_minutes=60, reason="국가정원 근처 꼬막정식 한상"),
        PickItem(place_id=pid("순천만국가정원"), stay_minutes=120, reason="평지 산책로가 많아 부모님과 걷기 편해요"),
        PickItem(place_id=pid("순천만습지"), stay_minutes=60, reason="갈대밭 데크길에서 겨울 철새를 볼 수 있어요"),
        PickItem(place_id=pid("최대감"), role="dinner", stay_minutes=60, reason="벌교 꼬막 전문"),
        PickItem(place_id=lodg, role="stay", stay_minutes=0, reason="순천 시내 숙소")]),
    DayPick(day=2, items=[
        PickItem(place_id=pid("화엄사"), stay_minutes=90, reason="국내 최대 목조건물 각황전이 있는 천년 고찰"),
        PickItem(place_id=pid("그옛날산채"), role="lunch", stay_minutes=60, reason="화엄사 아래 산채 정식"),
        PickItem(place_id=pid("사성암"), stay_minutes=60, reason="요청하신 곳 — 오산 절벽 위 암자, 섬진강 전망"),
        PickItem(place_id=dinner2, role="dinner", stay_minutes=60, reason="구례에서 저녁까지 여는 식당"),
        PickItem(place_id=lodg, role="stay", stay_minutes=0, reason="이틀째 숙소")]),
    DayPick(day=3, items=[
        PickItem(place_id=pid("낙안읍성"), stay_minutes=90, reason="조선 읍성 마을, 평지 산책"),
        PickItem(place_id=pid("최대감"), role="lunch", stay_minutes=60, reason="벌교 꼬막 정식")])])
items = planner.schedule(spec, plan, {})
viol = planner.validate(spec, items, set(pl))
ranked = [(pid(t), r) for t, r in [
    ("순천만국가정원", "평지 위주라 노약자 동반에 가장 무난한 대표 명소"), ("화엄사", "구례 대표 사찰, 경사는 완만한 편"),
    ("순천만습지", "데크길이라 걷기 편하고 겨울 철새 시기"), ("사성암", "요청하신 장소, 전망 명소지만 오르막이 있어요"),
    ("낙안읍성", "조선 읍성 마을, 평지 산책"), ("선암사", "천년 고찰, 숲길 산책"), ("송광사", "삼보사찰 중 하나"),
    ("드라마촬영장", "순천 시내 실내외 체험")]]
out = brochure.build(spec, items, ranked, viol, ["여행 강도는 '보통'으로 가정했어요", "현지 이동은 렌터카로 가정했어요"])
(ROOT / "app/static/sample_plan.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
print("위반", viol)
for d in out["itinerary"]:
    print(d["day"], d["theme"], [(s["arrive"], s["name"]) for s in d["stops"]], d["day_travel_min"], "분")
print("경비", out["costs"]["total"], out["costs"]["per_person"], [(i["category"], i["amount"]) for i in out["costs"]["items"]])
print("경고", out["warnings"])

from momo import richtext, placesearch  # noqa: E402
ans = ("※ 걷기 편한 평지 위주로 골랐어요\n※ 현지 이동은 렌터카로 가정했어요\n"
       "☁ 날씨: 12월은 아침 영하권, 낮 10도 안팎(평년)\n추천 8곳 중 5곳을 일정에 넣었어요")
(ROOT / "app/static/sample_answer.html").write_text(richtext.plan_html(out, ans, [], "승인 대기"), encoding="utf-8")
(ROOT / "app/static/sample_search.json").write_text(json.dumps(placesearch.search("사성", "suncheon_gurye"), ensure_ascii=False, indent=1), encoding="utf-8")
print("samples ok")
