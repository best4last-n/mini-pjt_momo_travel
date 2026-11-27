"""강도 권장 초과 선택지(2026-11-27): 질문만 하고 고를 곳이 없던 문제 — 계획서 suggest와 '그대로 둘게요'(over_ok_days)."""
from datetime import date

from src.planning import brochure, planner


def _items(n_day1: int):
    d = "2026-11-28"
    out = [{"date": d, "type": "place", "place_id": f"p{i}", "place_name": f"장소{i}", "notes": []} for i in range(n_day1)]
    out.append({"date": d, "type": "meal", "place_id": "m1", "place_name": "식당", "notes": []})
    out.append({"date": d, "type": "place", "place_id": "FREE:1", "place_name": "개인 시간", "notes": []})
    out.append({"date": d, "type": "place", "place_id": "n1", "place_name": "야경", "notes": ["야간"]})
    return out


def test_over_cap_suggest_lists_places_and_respects_keep():
    spec = planner.TripSpec(region="gyeongju", start=date(2026, 11, 28), nights=0, intensity="보통", waypoint_ids=["p0"])
    cap = planner.INTENSITY["보통"][2]
    sg = brochure.over_cap_suggest(spec, _items(cap + 1))
    assert len(sg) == 1 and sg[0]["day"] == 1 and sg[0]["count"] == cap + 1
    names = [p["name"] for p in sg[0]["places"]]
    assert "개인 시간" not in names and "야경" not in names and "식당" not in names
    assert sg[0]["places"][-1]["place_id"] == "p0" and sg[0]["places"][-1]["must"]      # 꼭 갈 곳은 뒤로
    spec.over_ok_days = [1]
    assert brochure.over_cap_suggest(spec, _items(cap + 1)) == []                     # 그대로 둘게요 → 다시 묻지 않음
    spec.over_ok_days = []
    assert brochure.over_cap_suggest(spec, _items(cap)) == []                         # 권장 이내면 없음
