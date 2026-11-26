"""여행 경비 추정과 예산 비교 (SERVICE.md 3-6 (11)).

모든 금액은 추정이며 근거(basis)를 함께 남긴다. 단가는 2026년 기준 대략값(아래 상수, 한 곳에서 관리).
- 장거리: KTX·SRT 1인 편도 ≈ 직선거리×1.2km × 120원/km, 고속·시외버스 ≈ 도로거리 × 90원/km,
          자차 = 유류비(도로거리 ÷ 연비 12km/L × 1,700원/L) + 고속도로 통행료(도로거리 × 50원/km)
- 현지: 자차·렌터카 유류비(현지 이동거리 기준), 렌터카 하루 70,000원, 대중교통 구간당 1,500원/인
- 식비: 점심 12,000원 · 저녁 15,000원 / 인 (식사 항목 수 기준)
- 입장료: 관광공사 요금 정보(usefee)에 금액이 있으면 성인 요금 × 인원, '무료'면 0, 없으면 '확인 필요'(합계 제외)
- 숙박: 1박 객실 100,000원 기준가 × 객실 수(4인당 1실)
"""
from __future__ import annotations

import math
import re

from . import data, hours, travel

FUEL_PRICE = 1700        # 원/L
FUEL_EFF = 12            # km/L
TOLL_PER_KM = 50         # 원/km (고속도로 1종 대략)
KTX_PER_KM = 120         # 원/km (직선×1.2 기준)
BUS_PER_KM = 90          # 원/km (도로거리 기준)
RENT_PER_DAY = 70000
TRANSIT_PER_LEG = 1500
MEAL = {"lunch": 12000, "dinner": 15000}
CAFE_PER_PERSON_DAY = 6000   # 카페·디저트·음료 1인 1일 추정
ROOM_PER_NIGHT = 100000
DEFAULT_PEOPLE = 2


def _r100(x: float) -> int:
    return int(round(x / 100.0) * 100)


def parse_budget(text: str | None) -> tuple[int | None, bool]:
    """'60만 원', '100만원 정도', '300000원', '1인 20만원' → (원, 1인 기준 여부)."""
    if not text:
        return None, False
    t = text.replace(",", "").replace(" ", "")
    per = bool(re.search(r"(1인|인당|한사람)", t))
    m = re.search(r"(\d+(?:\.\d+)?)만", t)
    if m:
        return int(float(m.group(1)) * 10000), per
    m = re.search(r"(\d{4,})", t)
    if m:
        return int(m.group(1)), per
    return None, per


def fmt_won(n: int | None) -> str:
    if n is None:
        return "확인 필요"
    return f"{n:,}원"     # 표·합계에서 단위가 섞이지 않게 항상 원 단위


def parse_fee(text: str | None) -> int | None:
    """관광공사 usefee 텍스트에서 성인 1인 요금. '무료' → 0, 금액 없으면 None."""
    t = hours.clean(text)
    if not t:
        return None
    if re.search(r"무료", t) and not re.search(r"\d[\d,]*\s*원", t):
        return 0
    m = re.search(r"(어른|성인|대인|일반)[^\d]{0,8}([\d,]+)\s*원", t) or re.search(r"([\d,]+)\s*원", t)
    if not m:
        return None
    try:
        return int(m.group(m.lastindex).replace(",", ""))
    except ValueError:
        return None


def _car(spec) -> dict:
    """차량 정보 → 연비·단가·통행료/주차 할인 배수와 설명."""
    from . import options as O
    v = spec.vehicle or {}
    vtype, fuel = v.get("type") or "중형", v.get("fuel") or "휘발유"
    eff = float(v.get("efficiency_kmpl") or O.efficiency(vtype, fuel))
    price = O.FUEL_PRICES.get(fuel, FUEL_PRICE)
    unit = O.FUEL_UNIT.get(fuel, "L")
    return {"type": vtype, "fuel": fuel, "eff": eff, "price": price, "unit": unit,
            "toll_km": O.TOLL_PER_KM.get(vtype, TOLL_PER_KM),
            "toll_mul": O.TOLL_DISCOUNTS.get(v.get("toll_discount") or "없음", 1.0),
            "park_mul": O.PARKING_DISCOUNTS.get(v.get("parking_discount") or "없음", 1.0),
            "toll_label": v.get("toll_discount") or "없음", "park_label": v.get("parking_discount") or "없음",
            "given": bool(spec.vehicle)}


def _fuel_cost(km: float, car: dict) -> tuple[int, str]:
    amount = _r100(km / car["eff"] * car["price"])
    basis = (f"{km:,.0f}km ÷ 연비 {car['eff']:g}km/{car['unit']} × {car['price']:,}원/{car['unit']} "
             f"({car['type']} · {car['fuel']}{'' if car['given'] else ', 차량 정보 없어 중형 휘발유 기준'})")
    return amount, basis


def estimate(spec, plan: dict) -> dict:
    people = spec.companion_count or DEFAULT_PEOPLE
    items = []
    car = _car(spec)

    def add(cat, label, amount, basis, unknown=False):
        items.append({"category": cat, "label": label, "amount": None if unknown else int(amount),
                      "basis": basis, "estimated": True, "unknown": unknown})

    # 장거리
    if spec.departure and spec.departure in data.SIDO_COORDS:
        a = tuple(spec.origin_coords) if getattr(spec, "origin_coords", None) else data.SIDO_COORDS[spec.departure]
        b = data.region_center(spec.region)
        straight = travel.km(a, b)
        road = straight * 1.3
        trip = f"{spec.origin_label or spec.departure}↔{data.region_label(spec.region)}"
        if spec.long_transport == "KTX·SRT":
            one = _r100(max(8400, straight * 1.2 * KTX_PER_KM))
            add("장거리 교통", f"KTX·SRT {trip} 왕복 {people}명", one * 2 * people,
                f"1인 편도 약 {one:,}원 × 2회 × {people}명 (거리 기준 추정)")
        elif spec.long_transport == "고속·시외버스":
            one = _r100(road * BUS_PER_KM)
            add("장거리 교통", f"고속·시외버스 {trip} 왕복 {people}명", one * 2 * people,
                f"1인 편도 약 {one:,}원 × 2회 × {people}명 (거리 기준 추정)")
        else:
            fuel, basis = _fuel_cost(road * 2, car)
            add("유류비", f"자차 {trip} 왕복 약 {road * 2:,.0f}km", fuel, basis)
            toll = _r100(road * 2 * car["toll_km"] * car["toll_mul"])
            add("통행료", f"고속도로 통행료(하이패스) {trip} 왕복", toll,
                f"도로거리 {road * 2:,.0f}km × 약 {car['toll_km']}원/km"
                + (f" × 할인({car['toll_label']})" if car["toll_mul"] != 1.0 else "") + " (추정)")
    # 현지
    local_km = plan.get("totals", {}).get("local", {}).get("distance_km") or 0
    n_days = spec.nights + 1
    if spec.local_transport == "렌터카":
        add("렌터카", f"렌터카 {n_days}일", RENT_PER_DAY * n_days, f"하루 {RENT_PER_DAY:,}원 × {n_days}일 (보험 포함 기준가 추정)")
    if spec.local_transport in ("자차", "렌터카") and local_km:
        amount, basis = _fuel_cost(local_km, car if spec.local_transport == "자차" else {**car, "type": "중형", "fuel": "휘발유",
                                                                                          "eff": 12, "price": FUEL_PRICE, "unit": "L", "given": True})
        add("유류비", f"현지 이동 약 {local_km:,.0f}km" + (" (렌터카)" if spec.local_transport == "렌터카" else ""), amount, basis)
    if spec.local_transport == "대중교통":
        legs = sum(len(d.get("legs") or []) for d in plan.get("itinerary", []))
        add("현지 교통", f"버스 등 {legs}구간 × {people}명", legs * TRANSIT_PER_LEG * people,
            f"구간당 1인 {TRANSIT_PER_LEG:,}원 (택시 이용 시 추가)")
    # 식비·입장료·숙박
    places = {p["id"]: p for p in plan.get("places", [])}
    pl = data.places(spec.region)
    parking_items: list = []
    for d in plan.get("itinerary", []):
        for s in d.get("stops", []):
            if s["type"] == "meal":
                kind = "lunch" if hours.parse_hm(s["arrive"]) < 16 * 60 else "dinner"
                add("식비", f"{d['day']}일차 {'점심' if kind == 'lunch' else '저녁'} · {s['name']}", MEAL[kind] * people,
                    f"1인 {MEAL[kind]:,}원 × {people}명 (평균 단가 추정)")
            elif s["type"] == "place" and s.get("place_id"):
                p = pl.get(s["place_id"]) or {}
                if spec.local_transport in ("자차", "렌터카"):
                    ptxt = hours.clean((p.get("intro") or {}).get("parking") or "")
                    if "무료" not in ptxt and "불가" not in ptxt and "없음" not in ptxt:
                        from .options import PARKING_PER_PLACE
                        fee_p = _r100(PARKING_PER_PLACE * car["park_mul"])
                        if fee_p:
                            parking_items.append((s["name"], fee_p))
                fee = parse_fee((p.get("intro") or {}).get("usefee") or (p.get("intro") or {}).get("usefeeleports"))
                if fee is None:
                    if p.get("kind") == "attraction" and "상시" not in (places.get(s["place_id"], {}).get("hours") or ""):
                        add("입장료", f"{s['name']}", 0, "관광공사 요금 정보 없음 — 현장 확인 필요", unknown=True)
                elif fee > 0:
                    add("입장료", f"{s['name']} {people}명", fee * people, f"성인 1인 {fee:,}원(관광공사 요금 정보) × {people}명")
            elif s["type"] == "stay":
                rooms = math.ceil(people / 4)
                add("숙박", f"{s['name']} 1박 {rooms}실", ROOM_PER_NIGHT * rooms,
                    f"1박 객실 {ROOM_PER_NIGHT:,}원 기준가 × {rooms}실 (실제 요금은 예약 시 확인)")
    if parking_items:
        add("주차비", f"관광지 주차 {len(parking_items)}곳", sum(x[1] for x in parking_items),
            f"1곳 약 {PARKING_ESTIMATE_TXT(car)} (공영주차장 기준 추정, 관광공사 정보에 '무료'면 제외)"
            + ": " + ", ".join(x[0] for x in parking_items[:4]) + ("…" if len(parking_items) > 4 else ""))
    n_days = spec.nights + 1
    add("카페·디저트", f"카페·디저트·음료 {n_days}일 × {people}명", CAFE_PER_PERSON_DAY * n_days * people,
        f"1인 1일 {CAFE_PER_PERSON_DAY:,}원 (추정)")
    for c in getattr(spec, "custom_costs", []) or []:
        items.append({"category": c["category"], "label": c["label"], "amount": int(c["amount"]),
                      "basis": "직접 추가한 비용", "estimated": False, "unknown": False, "custom": True, "id": c["id"]})
    total = sum(i["amount"] for i in items if not i["unknown"])
    budget = spec.budget
    if budget and spec.budget_per_person:
        budget = budget * people
    return {
        "currency": "KRW", "people": people, "people_assumed": not spec.companion_count, "items": items,
        "total": total, "per_person": _r100(total / people), "budget": budget,
        "over_budget": bool(budget and total > budget), "over_by": (total - budget) if budget and total > budget else 0,
        "note": "모든 금액은 추정이에요. 입장료는 관광공사 요금 정보에 금액이 있을 때만 넣었고, 없으면 '확인 필요'로 두었어요."
                + ("" if spec.companion_count else f" 인원을 말씀하지 않아 {DEFAULT_PEOPLE}명 기준으로 계산했어요."),
    }


def PARKING_ESTIMATE_TXT(car: dict) -> str:
    from .options import PARKING_PER_PLACE
    base = f"{PARKING_PER_PLACE:,}원"
    return base + (f" × 할인({car['park_label']})" if car["park_mul"] != 1.0 else "")


def by_category(planned: dict | None, actual: list[dict]) -> dict:
    cats: dict[str, dict] = {}
    for i in (planned or {}).get("items", []):
        c = cats.setdefault(i["category"], {"category": i["category"], "planned": 0, "actual": 0})
        c["planned"] += i["amount"] or 0
    for a in actual:
        c = cats.setdefault(a["category"], {"category": a["category"], "planned": 0, "actual": 0})
        c["actual"] += a["amount"]
    rows = [{**c, "diff": c["actual"] - c["planned"]} for c in cats.values()]
    people = (planned or {}).get("people") or DEFAULT_PEOPLE
    ta = sum(a["amount"] for a in actual)
    return {"by_category": rows, "total_planned": (planned or {}).get("total", 0), "total_actual": ta,
            "per_person_actual": _r100(ta / people), "budget": (planned or {}).get("budget"), "people": people}
