"""일정 생성 = LLM 선택(장소·순서·체류·이유) + 코드 시각 계산 + 코드 규칙 검사 + 재생성(최대 2회).

SERVICE.md 3-4b·3-4c·3-4e·5절 규칙 검사(하드 게이트):
  휴무일 배치 · 운영시간 밖 · 시간 겹침(앞 종료+이동+10분 > 다음 시작) · 검색 결과에 없는 장소
  + 현지 시간 창 · 식사(점심/저녁) · 1박 숙박 · '확인 필요' 하루 1곳/식사·마지막 금지
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import date

from src.shared import data, travel
from src.guardrails import guardrails
from src.planning import hours
from src.places import retrieve
from src.shared.config import (BUFFER_MIN, DAY_END, DAY_START, DEFAULT_DEPART, DINNER, HOME_LIMIT, LUNCH, MAX_REGEN, MEAL_FLEX,
                     STAY_LIMIT)
from src.shared.llm import structured
from src.planning.prompts import PICK_SYSTEM
from src.planning.schemas import ItineraryItem, PlanPick

MEAL_ID = "MEAL"
FREE_PREFIX = "FREE:"         # 개인 시간(휴식·자유 시간) 블록: place_id = "FREE:<id>", 이름은 reason
VISIT_PREFIX = "AGAIN:"       # 숙소 다시 들르기(낮에 들어가 쉬었다 다시 나옴): "AGAIN:<숙소 id>:<4자리>" — 같은 숙소를 여러 번 넣어도 id가 겹치지 않게
NIGHT_RETURN = "숙소 복귀"       # 체크인 뒤 야간 일정을 마치고 숙소로 돌아가는 줄(type=move, notes[0])
FAR_REGION_KM = 60           # 여행 지역 중심에서 이만큼 넘게 떨어진 곳은 '현지 장소'가 아니다(출발지 근처 등)
ROUTE_NEAR_KM = 30           # 가는 길/오는 길 직선 경로에서 이 안이면 '길 위의 들를 곳'으로 본다
KIND_OVERRIDES = ("식사", "카페", "관광지")   # 사용자가 직접 바꾸는 분류(카페가 식사로 잡히는 경우 등)


def visit_base(pid: str | None) -> str | None:
    """숙소 들르기 id → 원래 숙소 id(아니면 None)."""
    if pid and str(pid).startswith(VISIT_PREFIX):
        return str(pid)[len(VISIT_PREFIX):].rsplit(":", 1)[0]
    return None
KIND_KO = {"attraction": "관광지", "restaurant": "음식점", "lodging": "숙박", "festival": "축제"}


@dataclass
class TripSpec:
    region: str
    start: date
    nights: int
    departure: str | None = None          # 시·도
    depart_time: int = DEFAULT_DEPART
    transport: str = "자차"                 # (하위 호환) 현지 이동
    companions: str | None = None
    intensity: str = "보통"                 # 강행군 / 빡빡 / 보통 / 여유 (SERVICE.md 3-6 (2))
    long_transport: str = "자차"            # 자차 / KTX·SRT / 고속·시외버스
    local_transport: str = "자차"           # 자차 / 렌터카 / 대중교통
    companion_type: str = "보통"            # 보통 / 노약자 / 어린이·유아
    companion_count: int | None = None
    origin_label: str | None = None         # 시·군·구 표기(계산은 departure 시·도)
    assumptions: list[str] = field(default_factory=list)
    user_ordered_days: list[int] = field(default_factory=list)   # 사용자가 순서를 정한 날(1부터) — 최적화하지 않음
    over_ok_days: list[int] = field(default_factory=list)       # 강도 권장보다 많아도 '그대로 둘게요'를 고른 날(다시 묻지 않음)
    route_saved_min: int = 0
    vehicle: dict | None = None             # {type, fuel, efficiency_kmpl, toll_discount, parking_discount}
    origin_coords: list | None = None       # 출발지 좌표(약 1km 반올림) — 있으면 시·도 대표 좌표 대신 사용
    end_coords: list | None = None
    end_location: str | None = None         # 여행 후 도착지(시·도), 없으면 출발지
    end_label: str | None = None
    arrive_time: int | None = None          # 도착 희망 시각(분), 없으면 22:00 상한
    custom_costs: list[dict] = field(default_factory=list)
    title: str | None = None                # 사용자가 정한 여행 제목(예: 2026 가을 순천·남해·구례 여행)
    breakfast: str = "근처 식당"            # 근처 식당 / 숙소 조식 / 안 먹음 (숙박 다음 날 아침)
    breakfast_skip_days: list[int] = field(default_factory=list)   # 그날만 아침 빼기(1부터)
    return_stops: list[dict] = field(default_factory=list)         # 귀가길에 들를 곳 {name, lat, lng, detour_min, stay_min, …}
    depart_stops: list[dict] = field(default_factory=list)         # 가는 길에 들를 곳(출발 지점 다음) — return_stops와 같은 모양
    long_override: dict = field(default_factory=dict)   # {"out"|"back": 분} 사용자가 정한 장거리 운전 시간(들르는 곳·식사 빼고)
    fixed_starts: dict = field(default_factory=dict)    # {"일차:place_id": 분} 사용자가 정한 시작 시각(앞 일정이 늦게 끝나면 그 뒤로)
    no_stay: dict = field(default_factory=dict)    # {"N": 차박|밤샘|야간 이동|숙박 필요 없음} 그 밤은 숙소 없이(사용자가 고른 예외)
    preset: str | None = None
    given: list[str] = field(default_factory=list)      # 사용자가 직접 준 선택 항목(depart_time 등)
    pack_checked: list[str] = field(default_factory=list)   # 준비물 체크한 id
    pack_custom: list[dict] = field(default_factory=list)   # 사용자가 추가한 준비물
    budget: int | None = None               # 최대 예산(원)
    budget_per_person: bool = False
    budget_label: str | None = None
    preferences: list[str] = field(default_factory=list)
    many_places: bool = False
    waypoint_ids: list[str] = field(default_factory=list)       # 수집 데이터에 있는 경유지
    user_waypoints: list[dict] = field(default_factory=list)    # OSM으로 찾은 사용자 지정 장소
    exclude_ids: list[str] = field(default_factory=list)
    kind_override: dict = field(default_factory=dict)   # {place_id: 식사|카페|관광지} 사용자가 바꾼 분류
    booked_stay: str | None = None          # 예약한 숙소(같은 지역 여행이면 모든 밤을 이 숙소로)

    @property
    def days(self) -> list[date]:
        return hours.dates_of(self.start, self.nights + 1)


# ---------- 시간 창 ----------

INTENSITY = {   # 관광 시간대(분), 하루 관광지 상한, 구간 권장 상한(분)
    "강행군": (6 * 60 + 30, 23 * 60, 25, None),   # 새벽형·야행성: 가까운 곳 연달아 하루 20곳 이상도, 숙소는 다음 날 02:00까지
    "빡빡": (8 * 60, 20 * 60, 5, None),
    "보통": (9 * 60, 18 * 60 + 30, 4, 60),
    "여유": (10 * 60, 17 * 60 + 30, 3, 40),
}


def local_mode(spec: TripSpec) -> str:
    return "대중교통" if spec.local_transport == "대중교통" else "자차"


def _less_rests(dr: dict, stops: list) -> int:
    """들르는 곳이 있으면 그곳이 휴게소 쉬기를 대신(쉬기 15분 × min(쉬기 횟수, 들르는 곳 수) 빼기)."""
    return int(dr["total_min"]) - 15 * min(int(dr.get("rests") or 0), len(stops or []))


def one_way_minutes(spec: TripSpec) -> int | None:
    if not spec.departure or spec.departure not in data.SIDO_COORDS:
        return None
    if (spec.long_override or {}).get("out"):            # 사용자가 정한 운전 시간
        return int(spec.long_override["out"])
    a = tuple(spec.origin_coords) if spec.origin_coords else data.SIDO_COORDS[spec.departure]
    if spec.long_transport in ("KTX·SRT", "비행기"):
        from src.mobility import hubs
        opts = hubs.rail_options(a, spec.region) if spec.long_transport == "KTX·SRT" else hubs.air_options(a, spec.region)
        if not opts and spec.long_transport == "비행기":
            opts = hubs.rail_options(a, spec.region)
        if opts:
            return opts[0]["total_min"]
    if spec.long_transport == "자차":      # 실제 도로 경로 + 요일·시간대 혼잡 + 휴게
        from src.mobility import roads
        dr = roads.drive(a, data.region_center(spec.region), spec.start, spec.depart_time, True)
        if dr:
            return _less_rests(dr, spec.depart_stops)
    return travel.intercity_minutes(a, data.region_center(spec.region), spec.long_transport)


def return_minutes(spec: TripSpec) -> int | None:
    """여행 지역 → 여행 후 도착지(없으면 출발지) 전체 시간. 귀가길에 들르는 곳이 있으면 구간별(들른 곳 사이) 운전 + 머무는 시간 합."""
    lp = leg_plan(spec, "back", 16 * 60)
    return lp["total"] if lp else None


def _return_ok(spec: TripSpec) -> bool:
    coords = spec.end_coords or (None if spec.end_location else spec.origin_coords)
    dest = spec.end_location or spec.departure
    return bool(coords) or bool(dest and dest in data.SIDO_COORDS)


def _return_base(spec: TripSpec) -> int | None:
    coords = spec.end_coords or (None if spec.end_location else spec.origin_coords)
    dest = spec.end_location or spec.departure
    if not coords and (not dest or dest not in data.SIDO_COORDS):
        return None
    b = tuple(coords) if coords else data.SIDO_COORDS[dest]
    if (spec.long_override or {}).get("back"):
        return int(spec.long_override["back"])
    if spec.long_transport == "자차":
        from src.mobility import roads
        last = spec.days[-1]
        dr = roads.drive(data.region_center(spec.region), b, last, 16 * 60, False)
        if dr:
            return _less_rests(dr, spec.return_stops)
    return travel.intercity_minutes(data.region_center(spec.region), b, spec.long_transport)


def _seg_min(spec: TripSpec, a: tuple, b: tuple, d: date, t: int, outbound: bool) -> int:
    """바로 앞 실제 지점(a) → 다음 지점(b) 운전 시간(분). 자차는 도로 경로, 그 밖은 직선 추정(들르는 곳 구간은 늘 차로)."""
    if spec.long_transport == "자차":
        from src.mobility import roads
        dr = roads.drive(tuple(a), tuple(b), d, int(t) % 1440, outbound)
        if dr:
            return int(dr["total_min"])
    return travel.intercity_minutes(a, b, "자차")


def leg_plan(spec: TripSpec, leg: str, t0: int, near_xy: tuple | None = None, meals: list | None = None) -> dict | None:
    """가는 길(out)·오는 길(back)의 구간별 시간표 (2026-11-27 '출발지 → 버거킹 → 구례' 사례).
    출발지(오는 길은 마지막 현지 장소 near_xy, 없으면 여행지 중심) → 들를 곳 → … → 도착(가는 길은 첫 현지 장소 near_xy, 없으면 여행지 중심 /
    오는 길은 복귀 지점). 모든 구간은 '바로 앞 실제 지점 → 다음 지점'으로 따로 잰다. 직접 정한 운전 시간(long_override)은 마지막(긴) 구간에만 적용.
    들를 곳이 없으면 예전 그대로 한 구간(출발지 → 여행지). events: drive / stop / meal."""
    out = leg == "out"
    stops = list((spec.depart_stops if out else spec.return_stops) or [])
    day_no = 1 if out else len(spec.days)
    ov = (spec.long_override or {}).get("out" if out else "back")
    center = data.region_center(spec.region)
    if out:
        o = origin_point(spec)
        a0 = (o["lat"], o["lng"]) if o and o.get("lat") is not None else None
        a0_name = (o or {}).get("name") or spec.departure or "출발지"
        z = tuple(near_xy) if near_xy and None not in near_xy else tuple(center)
        z_name = "첫 장소" if near_xy else data.region_label(spec.region)
        has = _has_origin(spec)
    else:
        e = end_point(spec)
        z = (e["lat"], e["lng"]) if e and e.get("lat") is not None else None
        z_name = (e or {}).get("name") or spec.end_location or spec.departure or "집"
        a0 = tuple(near_xy) if near_xy and None not in near_xy else tuple(center)
        a0_name = "마지막 장소" if near_xy else data.region_label(spec.region)
        has = _return_ok(spec)
    car = spec.long_transport == "자차"
    if not stops or not car or a0 is None or z is None:
        whole = one_way_minutes(spec) if out else _return_base(spec)
        if whole is None:
            return None
    else:
        whole = None
        if not has:
            return None
    n = len(stops)
    d = spec.start if out else spec.days[-1]
    t, a, ev, drove, a_name = t0, a0, [], 0, a0_name
    pending = sorted(meals or [], key=lambda m: m["target"])

    def drive_to(b, to_name, m, last):
        nonlocal t, a, drove, a_name
        ev.append({"kind": "drive", "from_name": a_name, "to_name": to_name, "a": a, "b": b, "minutes": m, "start": t})
        while pending and (last or pending[0]["target"] < t + m):
            mt = pending.pop(0)
            s = _ceil5(max(t, mt["target"]))
            if last:
                s = min(s, t + max(0, m - 20))
            s = max(s, t)
            part = s - t
            ev.append({"kind": "meal", "meal": mt["meal"], "start": s, "end": s + mt["minutes"], "part": part})
            m -= part
            drove += part
            t = s + mt["minutes"]
        t += m
        drove += m
        a, a_name = b, to_name

    for i, s_ in enumerate(stops):
        b = (s_["lat"], s_["lng"])
        if a0 is None:       # 좌표를 모르면 전체 시간을 구간 수로 나눠 어림
            m = max(10, int(whole or 0) // (n + 1))
        else:
            m = _seg_min(spec, a, b, d, t, out)
        drive_to(b, s_["name"], m, False)
        arr = t
        fx = (spec.fixed_starts or {}).get(f"{day_no}:{s_.get('key')}")
        if fx is not None and int(fx) > arr:
            arr = int(fx)
        stay = int(s_.get("stay_min") or 40)
        ev.append({"kind": "stop", "stop": s_, "arrive": arr, "depart": arr + stay, "name": s_["name"], "drive": drove})
        drove, t = 0, arr + stay
    if not stops:
        m = int(whole)
    elif ov:
        m = int(ov)
    elif whole is not None:
        m = max(20, int(whole) - sum(x["minutes"] for x in ev if x["kind"] == "drive"))
    else:
        m = _seg_min(spec, a, z, d, t, out)
    last_start = t
    drive_to(z, z_name, m, True)
    return {"leg": leg, "t0": t0, "events": ev, "end": t, "total": t - t0, "segmented": bool(stops),
            "last_start": last_start, "last_min": m}


def _has_origin(spec: TripSpec) -> bool:
    return bool(spec.departure and spec.departure in data.SIDO_COORDS)


def route_meal_starts(spec: TripSpec, di: int) -> list[int]:
    """그날 가는 길(첫날)·오는 길(마지막 날)에서 끼니를 먹는 들를 곳(식당)의 도착 시각들 — 현지 식사 자리 판단용(어림)."""
    out: list[int] = []
    if di == 0 and spec.depart_stops and _has_origin(spec):
        lp = leg_plan(spec, "out", spec.depart_time)
        out += [e["arrive"] for e in (lp or {}).get("events", []) if e["kind"] == "stop" and _is_food_stop(e["stop"])]
    if di == len(spec.days) - 1 and spec.return_stops and _return_ok(spec):
        base = _base_windows(spec)
        if base:
            lp = leg_plan(spec, "back", base[-1][1])
            out += [e["arrive"] for e in (lp or {}).get("events", []) if e["kind"] == "stop" and _is_food_stop(e["stop"])]
    return out


def route_meal_covers(spec: TripSpec, di: int, meal: tuple[int, int]) -> bool:
    """가는 길/오는 길 식당이 그 끼니(점심·저녁 시간대)를 대신하는가."""
    return any(meal[0] - 30 <= m < meal[1] for m in route_meal_starts(spec, di))


def home_limit(spec: TripSpec) -> int:
    return spec.arrive_time if spec.arrive_time else HOME_LIMIT


def windows(spec: TripSpec) -> list[tuple[int, int]]:
    """현지 관광 시간 창. 가는 길 들를 곳·가는 길 식사만큼 첫날 시작이 늦고, 오는 길 식사만큼 마지막 날 끝이 당겨진다."""
    base = _base_windows(spec)
    if not base:
        return base
    rm = road_meals(spec)
    extra_out = sum(m["minutes"] for m in rm["out"])        # 들를 곳 시간은 _base_windows(구간별 시간표)에 이미 들어 있음
    extra_back = sum(m["minutes"] for m in rm["back"])
    if extra_out and one_way_minutes(spec) is not None:
        base[0] = (_ceil5(base[0][0] + extra_out), base[0][1])
    if extra_back:
        base[-1] = (base[-1][0], base[-1][1] - extra_back)
    return base


ROAD_MEAL_MIN = {"아침": 30, "점심": 50, "저녁": 50}      # 휴게소·경유지 식당(이동 중이라 짧게)
ROAD_BREAKFAST = (7 * 60, 9 * 60)


def route_extra(stops: list[dict]) -> int:
    return sum(int(s.get("detour_min") or 0) + int(s.get("stay_min") or 40) for s in stops or [])


def _is_food_stop(s: dict) -> bool:
    return s.get("kind") in ("식당", "restaurant")


def road_meals(spec: TripSpec) -> dict:
    """이동 중 끼니(2026-11-26 '출발일 아침·점심을 못 먹게 되어 있음'): 장거리 이동이 끼니 시간에 걸리고
    여행지에서는 그 끼니를 먹을 수 없으면 '가는 길/오는 길 식사'(휴게소·경유지 식당)를 넣는다.
      가는 길 아침: 07:30 전에 출발하고 여행지 도착이 09:00 이후(아침 안 먹음 설정이면 빼기)
      가는 길 점심: 12:30 전에 출발하고 여행지에서 점심을 못 먹는 시각(13:30 넘어 도착)에 도착
      오는 길 점심·저녁: 마지막 날 여행지를 떠나는 시각 이후에 끼니 시간이 걸리면
    사용자가 가는 길/오는 길에 넣은 식당이 있으면 그 식당이 그 끼니를 대신한다(빈 식사 자리를 하나씩 뺀다)."""
    out, back = [], []
    base = _base_windows(spec)
    if not base:
        return {"out": out, "back": back}
    lo = leg_plan(spec, "out", spec.depart_time)
    if lo is not None:
        t0 = spec.depart_time
        t1 = lo["end"]
        if (spec.breakfast != "안 먹음" and 1 not in spec.breakfast_skip_days
                and t0 < ROAD_BREAKFAST[0] + 30 and t1 >= ROAD_BREAKFAST[1]):
            out.append({"meal": "아침", "target": max(t0 + 60, ROAD_BREAKFAST[0])})
        mw0 = (base[0][0], max(base[0][1], DINNER[1]))
        if not meal_required(mw0, LUNCH) and t0 <= LUNCH[0] + 30 and t1 > LUNCH[0] + 30:
            out.append({"meal": "점심", "target": max(t0 + 60, LUNCH[0])})
    rw = return_minutes(spec)
    if rw is not None:
        e = base[-1][1]
        last = base[-1]
        if not meal_required(last, LUNCH) and e <= LUNCH[0] + 30 and e + rw > LUNCH[0] + 30:
            back.append({"meal": "점심", "target": max(e + 60, LUNCH[0])})
        if not meal_required(last, DINNER) and e <= DINNER[0] + 60 and e + rw > DINNER[0] + 30:
            back.append({"meal": "저녁", "target": max(e + 60, DINNER[0])})
    for lst, stops in ((out, spec.depart_stops), (back, spec.return_stops)):
        for _ in [s for s in stops or [] if _is_food_stop(s)][:len(lst)]:
            lst.pop()          # 사용자가 고른 식당이 끼니를 대신(늦은 끼니부터)
        for m in lst:
            m["minutes"] = ROAD_MEAL_MIN[m["meal"]]
    return {"out": out, "back": back}


def route_ends(spec: TripSpec) -> dict:
    """가는 길(출발 지점 → 여행 지역)·오는 길(여행 지역 → 최종 복귀 지점) 양 끝 좌표. 모르면 None."""
    center = data.region_center(spec.region)
    o, e = origin_point(spec), end_point(spec)
    return {"out": ((o["lat"], o["lng"]), center) if o and o.get("lat") is not None else None,
            "back": (center, (e["lat"], e["lng"])) if e and e.get("lat") is not None else None}


def route_stop_from(spec: TripSpec, leg: str, c: dict) -> dict | None:
    """검색 후보 → 가는 길/오는 길 들를 곳. 우회 = (A→P + P→B − A→B) 직선 × 1.25 ÷ 70km/h(추정) + 5분."""
    ends = route_ends(spec).get(leg)
    try:
        p = (float(c["lat"]), float(c["lng"]))
    except (KeyError, TypeError, ValueError):
        return None
    if not ends:
        return None
    a, b = ends
    extra_km = max(0.0, travel.km(a, p) + travel.km(p, b) - travel.km(a, b))
    detour = int(math.ceil(extra_km * 1.25 / 70 * 60 / 5) * 5) + 5
    nm = str(c.get("name") or c.get("title") or "들를 곳")[:40]
    cat = c.get("category") or c.get("kind")
    kind = "식당" if cat in ("restaurant", "식당") and not re.search(r"(카페|커피|coffee|cafe|베이커리|디저트)", nm, re.I) else \
        "카페" if re.search(r"(카페|커피|coffee|cafe|베이커리|디저트)", nm, re.I) else "관광지"
    return {"key": str(c.get("key") or c.get("place_id") or f"user:{nm}")[:80], "name": nm, "lat": p[0], "lng": p[1],
            "detour_min": min(180, detour), "stay_min": {"식당": 50, "카페": 30}.get(kind, 45), "kind": kind,
            "km_from_start": round(travel.km(a, p)), "address": (c.get("address") or "")[:40],
            "place_id": c.get("place_id"), "source": c.get("source") or "장소 찾기"}


def route_leg_for(spec: TripSpec, lat: float, lng: float, day_idx: int = 0) -> str | None:
    """장소가 여행 지역에서 멀(FAR_REGION_KM 초과)면: 가는 길/오는 길 경로 가까이(ROUTE_NEAR_KM 이내)면 'out'/'back'
    (일정이 아니라 길 위의 들를 곳으로 — 출발지 근처 식당을 현지 일정에 넣어 300km 이동이 구간으로 잡히던 문제),
    경로에서도 멀면 'far'. 여행 지역 안이면 None. 첫날은 가는 길, 마지막 날은 오는 길을 먼저 본다."""
    p = (float(lat), float(lng))
    if travel.km(data.region_center(spec.region), p) <= FAR_REGION_KM:
        return None
    near = {k: v for k, v in route_km(spec, p).items() if v <= ROUTE_NEAR_KM}
    if not near:
        return "far"
    if len(near) == 1:
        return next(iter(near))
    return "back" if day_idx >= spec.nights and spec.nights > 0 else "out" if day_idx == 0 else min(near, key=near.get)


def route_km(spec: TripSpec, p: tuple) -> dict:
    """후보가 가는 길·오는 길 경로(직선)에서 얼마나 떨어졌는지(km)."""
    out = {}
    for leg, ends in route_ends(spec).items():
        if ends:
            out[leg] = round(_seg_km(p, *ends), 1)
    return out


def _seg_km(p: tuple, a: tuple, b: tuple) -> float:
    k = math.cos(math.radians((a[0] + b[0]) / 2))
    ax, ay, bx, by, px, py = a[1] * k, a[0], b[1] * k, b[0], p[1] * k, p[0]
    dx, dy = bx - ax, by - ay
    t = 0.0 if dx == dy == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return travel.km(p, (ay + t * dy, (ax + t * dx) / k))


def _base_windows(spec: TripSpec) -> list[tuple[int, int]]:
    lo = leg_plan(spec, "out", spec.depart_time)
    ow = lo["total"] if lo else None
    rw = return_minutes(spec)
    out = []
    n = spec.nights + 1
    s0, e0, _, _ = INTENSITY.get(spec.intensity, INTENSITY["보통"])
    for i in range(n):
        s, e = s0, e0
        if ow is not None and i == 0:
            s = max(s0, spec.depart_time + ow + BUFFER_MIN)
        if rw is not None and i == n - 1:
            e = min(e0, home_limit(spec) - rw)
        out.append((_ceil5(s), e))
    return out


def meal_windows(spec: TripSpec) -> list[tuple[int, int]]:
    """식사용 시간 창: 관광 시간대가 끝난 뒤에도 저녁(~20:00)은 둘 수 있다. 귀가 상한이 걸린 마지막 날은 예외."""
    rw = return_minutes(spec)
    out = []
    for i, (s, e) in enumerate(windows(spec)):
        last_capped = rw is not None and i == len(spec.days) - 1
        out.append((s, e if last_capped else max(e, DINNER[1])))
    return out


def _ceil5(m: int) -> int:
    return int(math.ceil(m / 5) * 5)


def max_places(win: tuple[int, int], n_meals: int, many: bool = False, intensity: str = "보통") -> int:
    """시간 창에 들어갈 관광지 수 어림(체류 75분 + 이동·여유 25분), 강도별 상한."""
    cap = INTENSITY.get(intensity, INTENSITY["보통"])[2]
    cap = max(cap, 7) if many else cap
    free = win[1] - win[0] - n_meals * 80
    per = 40 if intensity == "강행군" else 80 if many else 100    # 강행군: 짧게 보고(25분) 가까운 곳으로 바로(15분)
    return max(1, min(cap, free // per))


def meal_required(win: tuple[int, int], meal: tuple[int, int]) -> bool:
    """점심은 시간 창과 30분 이상 겹치면, 저녁은 60분 식사가 온전히 들어갈 때만 필수(귀가 길 저녁은 안내로)."""
    need = 60 if meal == DINNER else 30
    return min(win[1], meal[1]) - max(win[0], meal[0]) >= need


NO_STAY_MODES = ("차박", "밤샘", "야간 이동", "숙박 필요 없음")


def night_needs_stay(spec: TripSpec, di: int) -> bool:
    """di(0부터)일차 밤: 다음 날까지 여행이 이어지면 숙박이 기본. 사용자가 차박·밤샘·야간 이동을 고른 밤은 예외."""
    return di < spec.nights and str(di + 1) not in (spec.no_stay or {})


# ---------- 후보 ----------

INDOOR_WORDS = re.compile(r"(박물관|미술관|전시관|전시실|기념관|과학관|체험관|공방|실내|식물원|온실|아쿠아|도서관|내부\s*관람|내부를\s*공개|키즈카페)")


def is_indoor(p: dict) -> bool:
    """실내 장소 여부: 문화시설(14)·음식점·숙박이거나 제목·요약에 실내 시설 단어."""
    if p.get("kind") in ("restaurant", "lodging") or p.get("contenttypeid") == "14":
        return True
    return bool(INDOOR_WORDS.search(f"{p.get('title', '')} {p.get('summary') or ''}"))


def candidates(spec: TripSpec, query: str) -> dict[str, dict]:
    """LLM에 보여 줄 후보(= '검색 결과'). 모든 날 휴무인 곳은 뺀다."""
    region = spec.region
    q = " ".join([query, spec.companions or "", *spec.preferences]).strip()
    picked: dict[str, dict] = {}
    n_att = 18 + 7 * max(0, spec.nights - 1)
    if spec.intensity == "강행군":      # 하루 20곳 이상 고를 수 있게 후보를 넉넉히
        n_att = max(n_att, 30 * (spec.nights + 1))
    n_food = 12 + 4 * max(0, spec.nights - 1)
    for kinds, k in ((["attraction", "festival"], n_att), (["restaurant"], n_food), (["lodging"], 5 if spec.nights else 0)):
        if k:
            for d in retrieve.search(q, region, kinds, k=k):
                picked[d["doc_id"]] = d
    pl = data.places(region)
    for pid, p in pl.items():           # 대표 명소는 항상 후보에
        if p.get("must_include") and p["kind"] == "attraction" and pid not in picked:
            picked[pid] = {"doc_id": pid, "text": "", "metadata": {"title": p["title"], "kind": p["kind"]}}
    if any("실내" in x for x in spec.preferences):   # 실내 요청이면 실내 관광지를 모두 후보에
        for pid, p in pl.items():
            if p["kind"] == "attraction" and is_indoor(p):
                picked.setdefault(pid, {"doc_id": pid})
    for pid in spec.waypoint_ids:
        if pid in pl:
            picked[pid] = {"doc_id": pid, "text": "", "metadata": {"title": pl[pid]["title"], "kind": pl[pid]["kind"]}}
    out = {}
    for pid in picked:
        p = pl.get(pid)
        if not p or pid in spec.exclude_ids:
            continue
        infos = [hours.parse_open(p, d) for d in spec.days]
        if all(i.status == "휴무" for i in infos):
            continue
        out[pid] = p
    return out


def candidate_lines(spec: TripSpec, cands: dict[str, dict]) -> str:
    lines = []
    for pid, p in cands.items():
        per_day = []
        for i, d in enumerate(spec.days, 1):
            info = hours.parse_open(p, d)
            if info.status == "휴무":
                per_day.append(f"{i}일차 휴무")
            elif info.status == "확인 필요":
                per_day.append(f"{i}일차 운영시간 확인 필요")
            elif p["kind"] == "lodging":
                per_day.append(f"체크인 {hours.fmt(info.open)}")
            else:
                per_day.append(f"{i}일차 {hours.fmt(info.open)}~{hours.fmt(info.close)}"
                               + (f"(휴게 {','.join(hours.fmt(a) + '~' + hours.fmt(b) for a, b in info.breaks)})" if info.breaks else ""))
        summ = guardrails.safe_external(p.get("summary") or "")
        tag = " [실내]" if p["kind"] == "attraction" and is_indoor(p) else ""
        lines.append(f"- id={pid} | {guardrails.safe_external(p['title'], 60)}{tag} | {KIND_KO[p['kind']]} | 좌표 {p.get('lat', 0):.3f},{p.get('lng', 0):.3f} "
                     f"| {' / '.join(per_day)} | {summ}")
    return "\n".join(lines)


# ---------- LLM 선택 ----------



def pick(spec: TripSpec, cands: dict[str, dict], request: str, feedback: str | None = None,
         base: list[dict] | None = None, indoor: bool = False) -> PlanPick:
    wins, mwins = windows(spec), meal_windows(spec)
    day_lines = []
    for i, (d, w, mw) in enumerate(zip(spec.days, wins, mwins), 1):
        meals = [n for n, m in (("점심", LUNCH), ("저녁", DINNER)) if meal_required(mw, m)]
        cap = max_places(w, sum(1 for n in meals if n == "점심"), spec.many_places, spec.intensity)
        day_lines.append(f"{i}일차 {d.isoformat()}({'월화수목금토일'[d.weekday()]}) 시간 창 {hours.fmt(w[0])}~{hours.fmt(w[1])}"
                         f" · 필요한 식사: {', '.join(meals) or '없음'} · 관광지는 최대 {cap}곳(시간 창을 넘기면 규칙 위반)")
    msg = [f"요청: <data>{request}</data>",
           f"동행: {spec.companions or '정보 없음'}({spec.companion_type}) · 여행 강도: {spec.intensity} · "
           f"현지 이동: {spec.local_transport} · 취향: {', '.join(spec.preferences) or '없음'}",
           f"여행: {data.region_label(spec.region)} {spec.nights}박 {spec.nights + 1}일",
           *day_lines]
    if spec.intensity == "강행군":
        msg.append("여행 강도 강행군(새벽형·야행성): 가까운 곳끼리 연달아 묶어 하루 최대한 많이(20곳 이상도 가능) 넣는다. "
                   "관광지 머무는 시간은 20~40분으로 짧게, 이동 10~15분 거리끼리 잇는다. 숙소 도착은 다음 날 02:00까지 괜찮다.")
    elif spec.many_places:
        msg.append("사용자가 아주 많은 곳을 원함: 하루 최대 7곳까지, 이동이 짧은 곳 위주.")
    if spec.waypoint_ids:
        must = [w for w in must_ids(spec) if w in spec.waypoint_ids]
        msg.append(f"사용자가 꼭 넣어 달라고 한 장소 id: {', '.join(must)} (반드시 포함)")
        extra = [w for w in waypoint_overflow(spec) if w in spec.waypoint_ids]
        if extra:
            msg.append(f"사용자가 더 적은 곳(일정에 다 못 들어감, 시간이 남을 때만): {', '.join(extra[:60])}")
    if base:
        msg.append("현재 일정(수정 요청과 무관한 항목은 그대로 같은 순서로 유지):\n" + "\n".join(
            f"- {it['date']} {it['start_time']} {it['place_name']} (id={it.get('place_id')}, role={it['type']})"
            for it in base if it["type"] != "move"))
    if indoor:
        msg.append("수정하는 구간의 관광지는 반드시 [실내] 표시가 있는 후보만 쓴다(야외 유적·공원 금지). 수정 대상이 아닌 항목은 그대로 둔다.")
    if feedback:
        msg.append(f"직전 초안의 규칙 위반(반드시 고칠 것):\n{feedback}")
    msg.append(f"후보 목록 <data>\n{candidate_lines(spec, cands)}\n</data>")
    prompt = [("system", PICK_SYSTEM), ("human", "\n".join(msg))]
    out = None
    for attempt in range(2):     # 구조화 출력이 비거나 파싱 실패하면 한 번 더
        try:
            out = structured(PlanPick, temperature=0.2, max_tokens=min(12000, 4000 + 1000 * spec.nights)).invoke(prompt)
            if out.days:
                return out
        except Exception:
            if attempt == 1:
                raise
    return out


# ---------- 동선 최적화 (SERVICE.md 3-6 (12)) ----------

def short_label(label: str | None) -> str | None:
    """'판교동 (경기도 성남시)' → '판교동'."""
    return label.split(" (")[0].strip() if label else label


def origin_point(spec: TripSpec) -> dict | None:
    """출발지(집 등) 점: 이름·좌표(약 1km 반올림, 없으면 시·도 대표 좌표)."""
    if not (spec.origin_label or spec.departure):
        return None
    xy = spec.origin_coords or (data.SIDO_COORDS.get(spec.departure) if spec.departure else None)
    return {"name": short_label(spec.origin_label) or spec.departure, "label": spec.origin_label or spec.departure,
            "lat": xy[0] if xy else None, "lng": xy[1] if xy else None, "approx": not spec.origin_coords}


def end_point(spec: TripSpec) -> dict | None:
    """여행 후 도착지(따로 없으면 출발지)."""
    if spec.end_coords or spec.end_location or spec.end_label:
        xy = spec.end_coords or data.SIDO_COORDS.get(spec.end_location or "")
        lab = spec.end_label or spec.end_location
        return {"name": short_label(lab), "label": lab, "lat": xy[0] if xy else None, "lng": xy[1] if xy else None,
                "approx": not spec.end_coords}
    return origin_point(spec)


def _pcoord(spec: TripSpec, pid: str):
    pid = visit_base(pid) or pid
    p = data.places(spec.region).get(pid) or next((w for w in spec.user_waypoints if w["place_id"] == pid), None)
    return (p.get("lat"), p.get("lng")) if p and p.get("lat") else None


def must_ids(spec: TripSpec) -> list[str]:
    """꼭 갈 곳 중 이번 일정에 '반드시' 넣을 곳(사용자가 적은 순서대로, 일정 용량까지). 나머지는 후보로 남긴다.
    용량 = 날마다 들어갈 관광지 수 어림(max_places)의 합 — 100곳 넘게 적어도 시간 창을 넘겨 확정 불가가 되지 않게."""
    want = [x for x in dict.fromkeys([*spec.waypoint_ids, *[w["place_id"] for w in spec.user_waypoints]])
            if x != spec.booked_stay]      # 예약한 숙소는 낮 일정에 넣지 않음
    wins, mwins = windows(spec), meal_windows(spec)
    cap = sum(max_places(w, 1 if meal_required(mw, LUNCH) else 0, spec.many_places, spec.intensity)
              for w, mw in zip(wins, mwins))
    return want[:max(1, cap)]


def waypoint_overflow(spec: TripSpec) -> list[str]:
    want = [x for x in dict.fromkeys([*spec.waypoint_ids, *[w["place_id"] for w in spec.user_waypoints]])
            if x != spec.booked_stay]      # 예약한 숙소는 낮 일정에 넣지 않음
    must = set(must_ids(spec))
    return [w for w in want if w not in must]


def ensure_waypoints(spec: TripSpec, picked: PlanPick) -> PlanPick:
    """사용자가 꼭 가겠다고 한 장소가 빠졌으면 코드가 넣는다: 관광지가 가장 적은 날의 저녁·숙박 앞(그다음 동선 최적화)."""
    from src.planning.schemas import DayPick, PickItem
    want = must_ids(spec)
    have = {x.place_id for d in picked.days for x in d.items}
    missing = [w for w in want if w not in have]
    if not missing:
        return picked
    days = {d.day: list(d.items) for d in picked.days}
    for d in range(1, spec.nights + 2):
        days.setdefault(d, [])
    wins = windows(spec)

    def spare(k):   # 그날 남는 관광 시간(분) ≈ 시간 창 − 관광지×100분 − 점심 80분
        w = wins[k - 1]
        return (w[1] - w[0]) - 100 * sum(1 for x in days[k] if x.role == "place") - (80 if meal_required(w, LUNCH) else 0)
    for wid in missing:
        to = max(days, key=spare)
        row = days[to]
        idx = next((i for i, x in enumerate(row) if x.role in ("dinner", "stay")), len(row))
        row.insert(idx, PickItem(place_id=wid, role="place", stay_minutes=75, reason="꼭 가고 싶다고 하신 곳"))
    return PlanPick(days=[DayPick(day=k, items=v) for k, v in sorted(days.items())], ranked=picked.ranked, note=picked.note)


def restore_user_blocks(spec: TripSpec, picked: PlanPick, base: list[dict] | None) -> PlanPick:
    """채팅으로 일정을 다시 짤 때 LLM이 빠뜨린, 사용자가 화면에서 넣은 블록(개인 시간·숙소 다시 들르기)을
    원래 날짜의 숙소·저녁 앞에 되돌려 놓는다(꼭 갈 장소는 ensure_waypoints가 맡음)."""
    from src.planning.schemas import DayPick, PickItem
    if not base:
        return picked
    have = {x.place_id for d in picked.days for x in d.items}
    ds = [d.isoformat() for d in spec.days]
    days = {d.day: list(d.items) for d in picked.days}
    add = False
    for it in base:
        pid = it.get("place_id") or ""
        if it["type"] == "move" or not pid.startswith((FREE_PREFIX, VISIT_PREFIX)) or pid in have or it["date"] not in ds:
            continue
        k = ds.index(it["date"]) + 1
        row = days.setdefault(k, [])
        idx = next((i for i, x in enumerate(row) if x.role in ("dinner", "stay")), len(row))
        row.insert(idx, PickItem(place_id=pid, role="place", reason=it.get("reason") or "",
                                 stay_minutes=max(15, hours.parse_hm(it["end_time"]) - hours.parse_hm(it["start_time"]))))
        add = True
    if not add:
        return picked
    return PlanPick(days=[DayPick(day=k, items=v) for k, v in sorted(days.items())], ranked=picked.ranked, note=picked.note)


def _route_km(spec: TripSpec, order: list, start, end) -> float:
    """직선거리 합(주변 식사 자리표시처럼 좌표 없는 항목은 그 자리에 머무는 것으로)."""
    pts = [p for p in [start] + [_pcoord(spec, x.place_id) if x.place_id != MEAL_ID else None for x in order] + [end] if p]
    return sum(travel.km(a, b) for a, b in zip(pts, pts[1:]))


def _split_free(row: list) -> tuple[list, dict]:
    """개인 시간 블록을 떼어 두고 {앞 장소 id: [블록…]}로 기억(맨 앞이면 None)."""
    rest, glue, prev = [], {}, None
    for x in row:
        if x.place_id.startswith(FREE_PREFIX):
            glue.setdefault(prev, []).append(x)
        else:
            rest.append(x)
            prev = x.place_id
    return rest, glue


def _join_free(row: list, glue: dict) -> list:
    out = list(glue.get(None, []))
    for x in row:
        out.append(x)
        out += glue.get(x.place_id, [])
    return out


def tsp_quick(spec: TripSpec, row: list, start, end) -> tuple[list, float]:
    rest, glue = _split_free(row)
    if glue:
        o, k = _tsp_quick(spec, rest, start, end)
        return _join_free(o, glue), k
    return _tsp_quick(spec, row, start, end)


def _tsp_quick(spec: TripSpec, row: list, start, end) -> tuple[list, float]:
    """숙소는 맨 끝 고정, 나머지(관광지·식사)는 칸막이 없이 순서를 바꿔 직선거리 최소화.
    8곳 이하는 완전 탐색, 그보다 많으면 2-opt. → (순서, km)"""
    import itertools
    stay = [x for x in row if x.role == "stay"]
    mov = [x for x in row if x.role != "stay"]
    tail = _pcoord(spec, stay[0].place_id) if stay else end
    if len(mov) <= 1:
        return mov + stay, _route_km(spec, mov, start, tail)
    if len(mov) <= 8:
        best = min(itertools.permutations(mov), key=lambda o: _route_km(spec, o, start, tail))
        return list(best) + stay, _route_km(spec, best, start, tail)
    cur = list(mov)
    improved = True
    while improved:
        improved = False
        for i in range(len(cur) - 1):
            for j in range(i + 1, len(cur)):
                cand = cur[:i] + cur[i:j + 1][::-1] + cur[j + 1:]
                if _route_km(spec, cand, start, tail) < _route_km(spec, cur, start, tail) - 0.05:
                    cur, improved = cand, True
    return cur + stay, _route_km(spec, cur, start, tail)


def best_day_order(spec: TripSpec, rows: list[list], di: int, start, end, allowed: set) -> list:
    """그날 순서 최적화(칸막이 없음): 거리 기준 후보 순서들을 실제 시각 계산으로 검증해 고른다.
    조건: 점심 14:00·저녁 20:30 전에 시작, 그날 규칙 위반이 늘지 않음. 점수 = 이동 분 + 0.5 × 기다리는 분."""
    import itertools
    from src.planning.schemas import DayPick
    row, glue = _split_free(rows[di])
    stay = [x for x in row if x.role == "stay"]
    mov = [x for x in row if x.role != "stay"]
    if len(mov) <= 1:
        return rows[di]
    tail = _pcoord(spec, stay[0].place_id) if stay else end
    if len(mov) <= 8:
        cands = sorted(itertools.permutations(mov), key=lambda o: _route_km(spec, o, start, tail))[:40]
        cands = [_join_free(list(c) + stay, glue) for c in cands]
    else:
        cands = [_join_free(tsp_quick(spec, row, start, end)[0], glue)]
    row = _join_free(row, glue)
    cands.append(list(row))
    ds = spec.days[di].isoformat()
    day_open = windows(spec)[di][0]

    def evaluate(order):
        rr = [list(r) for r in rows]
        rr[di] = order
        items = schedule(spec, PlanPick(days=[DayPick(day=i + 1, items=r) for i, r in enumerate(rr)]), {})
        day = [it for it in items if it["date"] == ds and it["type"] != "move" and "조식" not in (it.get("notes") or [])
               and "개인 시간" not in (it.get("notes") or [])]
        viol = [v for v in validate(spec, items, allowed) if v.get("date") in (ds, None)]
        trav = sum(it["travel_minutes_from_prev"] or 0 for it in day)
        wait, prev_end = 0, day_open          # 그날 첫 장소 전 기다림도 센다(예: 아침에 점심부터)
        meal_ok = True
        for it, pk in zip(day, [x for x in order if not x.place_id.startswith(FREE_PREFIX)]):
            s = hours.parse_hm(it["start_time"])
            if prev_end is not None:
                wait += max(0, s - prev_end - (it["travel_minutes_from_prev"] or 0) - BUFFER_MIN)
            prev_end = hours.parse_hm(it["end_time"])
            if pk.role == "lunch" and s > LUNCH[1]:
                meal_ok = False
            if pk.role == "dinner" and s > DINNER[1] + 30:
                meal_ok = False
        return meal_ok, len(viol), trav + 0.5 * wait

    base_ok, base_v, base_score = evaluate(list(row))
    best, best_key = list(row), (0 if base_ok else 1, base_v, base_score)
    for c in cands:
        ok, nv, sc = evaluate(c)
        key = (0 if ok else 1, nv, sc)
        if key[:2] < best_key[:2] or (key[:2] == best_key[:2] and sc < best_key[2] - 0.5):
            best, best_key = c, key
    return best


def optimize_day(spec: TripSpec, row: list, start=None) -> list:
    """식사·숙소는 제자리(시간 고정점), 그 사이 관광지 묶음만 순서를 바꿔 직선거리 합을 최소화(7곳 이하 완전 탐색)."""
    import itertools
    out, seg, prev_pt = [], [], start

    def flush(next_pt):
        nonlocal seg, prev_pt
        if len(seg) > 1 and all(_pcoord(spec, x.place_id) for x in seg) and len(seg) <= 7:
            def cost(order):
                pts = [prev_pt] + [_pcoord(spec, x.place_id) for x in order] + [next_pt]
                pts = [p for p in pts if p]
                return sum(travel.km(a, b) for a, b in zip(pts, pts[1:]))
            seg = list(min(itertools.permutations(seg), key=cost))
        out.extend(seg)
        if seg:
            prev_pt = _pcoord(spec, seg[-1].place_id) or prev_pt
        seg = []

    for x in row:
        if x.role == "place" and x.place_id != MEAL_ID:
            seg.append(x)
            continue
        nxt = _pcoord(spec, x.place_id) if x.place_id != MEAL_ID else None
        flush(nxt)
        out.append(x)
        if nxt:
            prev_pt = nxt
    flush(None)
    return out


def _travel_total(items: list[dict]) -> int:
    return sum(it["travel_minutes_from_prev"] or 0 for it in items if it["type"] != "move")


def optimize_plan(spec: TripSpec, picked: PlanPick, allowed: set, fixed_days: set) -> tuple[PlanPick, int]:
    """날짜별로 최적화한 순서를 써 보고, 규칙 위반이 늘지 않고 이동이 줄 때만 채택. (채택 PlanPick, 줄어든 분)"""
    from src.planning.schemas import DayPick
    base_items = schedule(spec, picked, {})
    base_v = len(validate(spec, base_items, allowed))
    center = data.region_center(spec.region)
    ordered = sorted(picked.days, key=lambda x: x.day)
    rows = [list(d.items) for d in ordered]
    last_stay = None
    for i, d in enumerate(ordered):
        start = (_pcoord(spec, last_stay) if last_stay else center)
        if d.day not in fixed_days:
            rows[i] = best_day_order(spec, rows, i, start, center if i == len(rows) - 1 else None, allowed | {MEAL_ID})
        stay = next((x.place_id for x in d.items if x.role == "stay"), None)
        last_stay = stay or last_stay
    cand = PlanPick(days=[DayPick(day=d.day, items=r) for d, r in zip(ordered, rows)], ranked=picked.ranked, note=picked.note)
    items = schedule(spec, cand, {})
    v = len(validate(spec, items, allowed))
    saved = _travel_total(base_items) - _travel_total(items)
    if v < base_v or (v <= base_v and saved > 0):        # 규칙 위반이 줄거나, 같으면서 이동이 줄 때
        return cand, max(0, saved)
    return picked, 0


# ---------- 코드 시각 계산 ----------

def _coords(p: dict | None) -> tuple | None:
    return (p.get("lat"), p.get("lng")) if p and p.get("lat") else None


def schedule(spec: TripSpec, plan: PlanPick, cands: dict[str, dict]) -> list[dict]:
    wins = windows(spec)
    pl = data.places(spec.region)
    user_wp = {w["place_id"]: w for w in spec.user_waypoints}
    # 들를 곳이 있는 자차 가는 길은 구간별로 재므로 '출발지 → 여행지 중심' 전체 시간은 필요 없다(있는지 여부만)
    ow = 0 if (spec.depart_stops and spec.long_transport == "자차" and _has_origin(spec)) else one_way_minutes(spec)
    items: list[dict] = []
    lm = local_mode(spec)
    mode = "transit" if lm == "대중교통" else "car"
    center = data.region_center(spec.region)
    hub = {"contentid": None, "lat": center[0], "lng": center[1], "title": "도착 지점"}
    last_stay = None
    for di, d in enumerate(spec.days):
        w = wins[di]
        day = next((x for x in plan.days if x.day == di + 1), None)
        picks = day.items if day else []
        direct_out = False
        if ow is not None and di == 0:
            rm = road_meals(spec)
            lp = leg_plan(spec, "out", spec.depart_time, _first_local_xy(spec, picks, pl, user_wp), rm["out"])
            home_nm = short_label(spec.origin_label) or spec.departure
            if lp["segmented"]:       # 출발지 → 첫 들를 곳 → … → 첫 현지 장소: 구간마다 바로 앞 지점에서 잰다
                first = lp["events"][0]
                mv = _move(d, f"{home_nm} → {first['to_name']}", spec.depart_time, spec.depart_time + first["minutes"], "출발")
                mv["route_segmented"] = True
                items.append(mv)
                direct_out = True
            else:
                items.append(_move(d, f"{home_nm} → " + data.region_label(spec.region), spec.depart_time, lp["end"], "출발"))
            items += _route_items(spec, d, lp)
        cursor, prev = w[0], None
        if direct_out:             # 마지막 들를 곳에서 첫 장소로 바로 — 여행지 중심(도착 지점)을 거치지 않음
            cursor = max(INTENSITY.get(spec.intensity, INTENSITY["보통"])[0], _ceil5(lp["end"]))
        night_stay = None         # 이 날 숙소 체크인 뒤(야간 일정)에 들어간 장소가 있으면 숙소로 돌아가는 줄을 붙인다
        night_cnt = 0
        origin = last_stay if di > 0 and last_stay else (hub if di == 0 and ow is not None and not direct_out else None)
        picked_bf = any(pk.role == "breakfast" for pk in picks)       # 사용자가 아침 식당을 직접 고른 날
        if (di == 0 and not picked_bf and spec.breakfast != "안 먹음" and 1 not in spec.breakfast_skip_days
                and w[0] < ROAD_BREAKFAST[1] and not any(m["meal"] == "아침" for m in road_meals(spec)["out"])):
            # 첫날도 아침 시간(~09:00)에 현지 일정이 시작되면 아침 식사 자리(2026-11-26 사용자 요청) — '식당 고르기'로 바꿀 수 있음
            bs = max(ROAD_BREAKFAST[0], w[0])
            be = bs + 45
            ref = hub if ow is not None else None
            bp = {"contentid": None, "title": "아침 식사 · 주변 식당", "kind": "restaurant", "source": "식사 안내",
                  "lat": ref.get("lat") if ref else None, "lng": ref.get("lng") if ref else None}
            bi = _item(bp, d, "meal", bs, be, 0, "estimate", mode, "여행 첫날 아침이에요. '식당 고르기'로 원하는 식당을 넣을 수 있어요",
                       hours.OpenInfo("확인됨"))
            bi["notes"] = ["조식"]
            items.append(bi)
            cursor = max(cursor, be + 10)
        if di > 0 and last_stay and not picked_bf and spec.breakfast != "안 먹음" and (di + 1) not in spec.breakfast_skip_days:
            hotel = spec.breakfast == "숙소 조식"
            bs = max((6 if spec.intensity == "강행군" else 7) * 60, w[0] - (45 if hotel else 75))       # 숙소 조식은 이동이 없어 30분 늦게 시작해도 됨
            be = bs + (35 if hotel else 45)
            bp = {"contentid": None, "title": f"숙소 조식 · {last_stay.get('title', '')}" if hotel else "숙소 근처 아침 식사",
                  "kind": "restaurant", "source": "식사 안내", "lat": last_stay.get("lat"), "lng": last_stay.get("lng")}
            bi = _item(bp, d, "meal", bs, be, 0 if hotel else 10, "estimate", mode,
                       "숙소에서 바로 먹어 이동 시간을 아껴요" if hotel else "숙소 가까운 곳에서 든든하게 시작해요", hours.OpenInfo("확인됨"))
            bi["notes"] = ["조식"] + (["숙소 조식"] if hotel else [])
            items.append(bi)
            cursor = max(cursor - 10, be + (0 if hotel else 10))
        for pk in picks:
            placeholder = pk.place_id == MEAL_ID
            if pk.place_id.startswith(FREE_PREFIX):      # 개인 시간: 바로 앞 장소에서 그대로(이동 없음)
                fs = cursor + (BUFFER_MIN if prev is not None else 0)
                fe = fs + max(15, min(pk.stay_minutes, 360))
                fp = {"contentid": pk.place_id, "title": pk.reason or "개인 시간", "kind": "free",
                      "lat": prev.get("lat") if prev else None, "lng": prev.get("lng") if prev else None}
                fi = _item(fp, d, "place", fs, fe, 0, "estimate", mode, pk.reason or "개인 시간", hours.OpenInfo("확인됨"))
                fi.update(source="개인 시간", notes=["개인 시간"], hours_text=None)
                items.append(fi)
                cursor = fe
                if prev is None and origin is not None:
                    prev = {**origin}
                continue
            if placeholder:   # '주변에서 식사' (운영시간이 맞는 식당이 없을 때)
                label = "점심" if pk.role == "lunch" else "저녁"
                p = {"contentid": None, "title": f"주변에서 {label} 식사", "kind": "restaurant", "source": "식사 안내",
                     "lat": prev.get("lat") if prev else None, "lng": prev.get("lng") if prev else None}
                info = hours.OpenInfo("확인됨")
            elif visit_base(pk.place_id):     # 숙소 다시 들르기: 위치는 그 숙소, 체크인 시각 제약 없음
                base_p = pl.get(visit_base(pk.place_id)) or user_wp.get(visit_base(pk.place_id)) or {}
                p = {**base_p, "title": f"숙소 들르기 · {base_p.get('title') or '숙소'}", "kind": "lodging_visit"}
                info = hours.OpenInfo("확인됨")
            else:
                p = pl.get(pk.place_id) or user_wp.get(pk.place_id)
                if p is None:   # 후보 밖 id → 그대로 두고 검사에서 '없는 장소'로 잡힘
                    p = {"contentid": pk.place_id, "title": f"(알 수 없는 장소 {pk.place_id})", "kind": "attraction"}
                info = hours.parse_open(p, d)
            if pk.role == "breakfast" and prev is None and di > 0 and last_stay:     # 직접 고른 아침 식당: 숙소 아침 시각에 맞춰
                cursor = min(cursor, max((6 if spec.intensity == "강행군" else 7) * 60, w[0] - 75))
            if prev is None and origin is not None and not placeholder:     # 숙소·도착 지점에서 첫 장소까지
                tmin, tsrc = travel.local_minutes(origin.get("contentid"), p.get("contentid"), _coords(origin), _coords(p), lm)
                home = origin_point(spec) if origin is hub else None
                if home and home.get("lat") is not None and _coords(p) and None not in _coords(p) and \
                        travel.km((home["lat"], home["lng"]), _coords(p)) <= travel.km((home["lat"], home["lng"]), center):
                    tmin = max(10, tmin // 3)        # 오는 길 쪽 장소: 중심까지 갔다 되돌아오지 않음(2026-11-26 도착 시각이 너무 늦음)
            elif prev is None:
                tmin, tsrc = 0, "estimate"
            elif placeholder or prev.get("source") == "식사 안내":
                tmin, tsrc = 10, "estimate"
            else:
                tmin, tsrc = travel.local_minutes(prev.get("contentid"), p.get("contentid"), _coords(prev), _coords(p), lm)
            start = cursor + (tmin + BUFFER_MIN if prev is not None else tmin)
            meal0 = LUNCH[0] if pk.role == "lunch" else DINNER[0] if pk.role == "dinner" else None
            if (spec.kind_override or {}).get(pk.place_id) == "카페":
                meal0 = None             # 카페로 바꾼 곳은 식사 시간대까지 기다리지 않음
            if meal0 is not None and start < meal0:
                # 사용자가 순서를 정한 날은 시간을 연달아 붙인다: 식사 시간대까지 30분 넘게 기다리지 않음
                # (2026-11-26 섬진강 대나무숲길 14:05 → 라플라타 18:00, 4시간 빈 시간이 생기고 뒤 일정이 밀림)
                if placeholder or (di + 1) not in spec.user_ordered_days:     # 빈 식사 자리는 늘 식사 시간대에. 직접 정한 날의 식당은 기다리지 않음(식사 시간은 권장)
                    start = meal0
            fixed = (spec.fixed_starts or {}).get(f"{di + 1}:{pk.place_id}")
            if fixed is not None and int(fixed) > start:      # 사용자가 정한 시작 시각(그보다 일찍은 도착 못 하면 도착 시각)
                start = int(fixed)
            if info.status == "확인됨" and p.get("kind") != "lodging":
                start = max(start, info.open)
            if p.get("kind") == "lodging":
                start = max(start, info.open)
            start = _ceil5(start)
            stay = 0 if pk.role == "stay" else max(15, min(pk.stay_minutes, 360))     # 사용자가 늘리거나 줄일 수 있게
            for a, b in info.breaks:          # 휴게·준비시간과 겹치면 끝난 뒤로
                if start < b and start + stay > a:
                    start = _ceil5(b)
            end = start + stay
            typ = {"breakfast": "meal", "lunch": "meal", "dinner": "meal", "stay": "stay"}.get(pk.role, "place")
            if p.get("kind") == "restaurant" and typ == "place":
                typ = "meal"
            ko = (spec.kind_override or {}).get(pk.place_id)
            if ko and typ != "stay":
                typ = "place" if ko == "관광지" else "meal"
            it_ = _item(p, d, typ, start, end, tmin, tsrc, mode, pk.reason, info)
            if visit_base(pk.place_id):
                it_["place_id"] = pk.place_id
                it_["notes"] = ["숙소 들르기"]
            if direct_out and prev is None and not items_local(items, d):      # 마지막 들를 곳 → 첫 장소 긴 이동 구간(표시용)
                it_["long_leg_min"] = lp["last_min"]      # 현지 이동(travel_minutes_from_prev)에는 세지 않는다
            if night_stay is not None and typ != "stay":      # 체크인 뒤 저녁·야간 일정
                it_["notes"] = [*(it_.get("notes") or []), "야간"]
                night_cnt += 1
            items.append(it_)
            cursor, prev = end, p
            if typ == "stay":
                last_stay = p
                night_stay = p
        if night_stay is not None and night_cnt and prev is not None and prev is not night_stay:
            tmin_b, tsrc_b = travel.local_minutes(prev.get("contentid"), night_stay.get("contentid"), _coords(prev), _coords(night_stay), lm)
            ret = _ceil5(cursor + tmin_b)
            mv = _move(d, f"숙소로 돌아가기 · {night_stay.get('title') or '숙소'}", ret, ret, NIGHT_RETURN)
            mv.update(travel_minutes_from_prev=tmin_b, travel_source=tsrc_b, lat=night_stay.get("lat"), lng=night_stay.get("lng"),
                      reason="밤 일정을 마치고 숙소로 돌아가요")
            items.append(mv)
        if di < spec.nights and not any(pk.role == "stay" for pk in picks):
            last_stay = None          # 숙소 없이 보낸 밤(차박·밤샘·야간 이동) 다음 날은 숙소 아침·숙소 출발 없음
        rw = return_minutes(spec)
        if rw is not None and di == len(spec.days) - 1:
            back = max(cursor, w[0])
            dest = short_label(spec.end_label or spec.end_location or spec.origin_label) or spec.departure
            rm = road_meals(spec)
            meals_b = []        # 실제로 여행지를 떠나는 시각(back) 기준으로 다시 판단: 일찍 떠나 저녁 전에 집에 닿으면 빼기
            for m in rm["back"]:
                meal = LUNCH if m["meal"] == "점심" else DINNER
                if back <= meal[0] + (30 if meal == LUNCH else 60) and back + rw > meal[0] + 30:
                    meals_b.append({**m, "target": max(back + 60, meal[0])})
            last_xy = _coords(prev) if prev is not None and _coords(prev) and None not in _coords(prev) else None
            lp = leg_plan(spec, "back", back, last_xy, meals_b)
            items += _route_items(spec, d, lp)
            # 마지막 운전 구간에 오는 길 식사가 있으면 귀가 줄은 식사 뒤부터(식사 전 운전은 식사 줄의 '앞 지점에서 운전 N분')
            # — 귀가 줄이 식사와 시간이 겹쳐 보이던 문제(2차 평가 #1·#5·#20, 2026-11-27)
            road_meal = [e for e in lp["events"] if e["kind"] == "meal" and e["start"] >= lp["last_start"]]
            # 계획서 귀가 카드는 예전 그대로(떠나는 시각·이름·운전 시간) — route_leave·route_label·route_drive_min으로 넘김(U48)
            if lp["segmented"]:       # 마지막 들른 곳 → 복귀 지점
                lastn = [e for e in lp["events"] if e["kind"] == "stop"][-1]["name"]
                if road_meal:
                    mv = _move(d, f"오는 길 {road_meal[-1]['meal']} 식당 → {dest}", road_meal[-1]["end"], lp["end"], "귀가")
                    mv.update(route_leave=hours.fmt(lp["last_start"]), route_label=f"{lastn} → {dest}", route_drive_min=lp["last_min"])
                else:
                    mv = _move(d, f"{lastn} → {dest}", lp["last_start"], lp["end"], "귀가")
                mv["route_segmented"] = True
                items.append(mv)
            elif road_meal:
                mv = _move(d, f"오는 길 {road_meal[-1]['meal']} 식당 → {dest}", road_meal[-1]["end"], lp["end"], "귀가")
                mv.update(route_leave=hours.fmt(back), route_label=f"{data.region_label(spec.region)} → {dest}")
                items.append(mv)
            else:
                items.append(_move(d, f"{data.region_label(spec.region)} → {dest}", back, lp["end"], "귀가"))
    for i, it in enumerate(items, 1):
        it["seq"] = i
    return items


ROUTE_LEG = {"out": "가는 길", "back": "오는 길"}


def items_local(items: list[dict], d: date) -> bool:
    """그날 이미 현지 장소(이동·조식 안내 제외) 항목이 있는가."""
    ds = d.isoformat()
    return any(it["date"] == ds and it["type"] != "move" and "조식" not in (it.get("notes") or []) for it in items)


def _first_local_xy(spec: TripSpec, picks: list, pl: dict, user_wp: dict) -> tuple | None:
    """그날 첫 현지 장소의 좌표(가는 길 마지막 구간의 도착점). 자리표시자·개인 시간이면 None → 여행지 중심."""
    for pk in picks:
        if pk.place_id == MEAL_ID or pk.place_id.startswith(FREE_PREFIX):
            return None
        bid = visit_base(pk.place_id) or pk.place_id
        p = pl.get(bid) or next((w for w in spec.user_waypoints if w["place_id"] == bid), None)
        if p and p.get("lat") is not None:
            return (p["lat"], p["lng"])
        return None
    return None


def _route_items(spec: TripSpec, d: date, lp: dict) -> list[dict]:
    """장거리 이동 중에 들르는 곳·먹는 끼니를 시각이 있는 항목으로(type=move, notes[0]='가는 길'/'오는 길').
    시각은 구간별 시간표(leg_plan)에서 — 각 줄의 travel_minutes_from_prev는 '바로 앞 실제 지점에서 여기까지 운전 시간'.
    move라서 규칙 검사·현지 동선·다시 짜기에서는 빠지고, 브로셔·일정표에는 출발 지점 다음/복귀 지점 앞 카드로 보인다."""
    leg = lp["leg"]
    out = []
    for e in lp["events"]:
        if e["kind"] == "drive":
            continue
        if e["kind"] == "meal":
            name = f"{ROUTE_LEG[leg]} {e['meal']} · 휴게소·경유지 식당"
            it = _move(d, name, e["start"], e["end"], ROUTE_LEG[leg])
            it.update(place_name=name, route_kind="meal", route_key=None, lat=None, lng=None, source="식사 안내",
                      reason=(f"{'출발' if leg == 'out' else '귀가'} 이동이 {e['meal']} 시간에 걸려요. 휴게소나 경유지 식당에서 먹어요"
                              " — '+ 들를 곳'으로 식당을 고르면 그곳으로 바뀌어요"),
                      route_meal=e["meal"], travel_minutes_from_prev=e["part"], route_stay=None)
            out.append(it)
            continue
        x = e["stop"]
        it = _move(d, x["name"], e["arrive"], e["depart"], ROUTE_LEG[leg])
        it.update(place_name=x["name"], route_kind="meal" if _is_food_stop(x) else "place", route_key=x.get("key"),
                  lat=x.get("lat"), lng=x.get("lng"), source=x.get("source") or "장소 찾기", route_meal=None,
                  reason=f"{ROUTE_LEG[leg]}에 들러요 · 앞 지점에서 운전 약 {e['drive']}분(추정)",
                  travel_minutes_from_prev=e["drive"], route_stay=int(x.get("stay_min") or 40))
        out.append(it)
    return out


def _move(d: date, name: str, s: int, e: int, leg: str) -> dict:
    return ItineraryItem(seq=0, date=d.isoformat(), type="move", place_name=name, start_time=hours.fmt(s),
                         end_time=hours.fmt(e), source="추정(직선거리)", travel_source="estimate",
                         hours_status="확인됨", notes=[leg]).model_dump()


def _order_key(it: dict):
    leg = it["notes"][0] if it["type"] == "move" and it["notes"] else ""
    return (it["date"], {"출발": 0, "귀가": 2}.get(leg, 1), hours.parse_hm(it["start_time"]))


def _item(p: dict, d: date, typ: str, s: int, e: int, tmin: int, tsrc: str, mode: str, reason: str,
          info: hours.OpenInfo) -> dict:
    user = p.get("source") == "사용자 지정"
    if p.get("source") == "식사 안내":
        return ItineraryItem(seq=0, date=d.isoformat(), type="meal", place_name=p["title"], start_time=hours.fmt(s),
                             end_time=hours.fmt(e), travel_minutes_from_prev=tmin, transport_mode=mode, travel_source=tsrc,
                             reason=reason or "운영시간이 확인된 식당이 시간대에 맞지 않아 주변 식당을 안내해요",
                             source="식사 안내", hours_status="확인됨").model_dump()
    hours_text = None
    if info.status == "확인됨" and p.get("kind") == "lodging":
        hours_text = f"체크인 {hours.fmt(info.open)}"
    elif info.status == "확인됨":
        hours_text = "상시 개방" if (info.open, info.close) == (0, 1440) else f"{hours.fmt(info.open)}~{hours.fmt(info.close)}"
    return ItineraryItem(
        seq=0, date=d.isoformat(), type=typ, place_id=p.get("contentid"), place_name=p.get("title", ""),
        start_time=hours.fmt(s), end_time=hours.fmt(e), travel_minutes_from_prev=tmin, transport_mode=mode,
        travel_source=tsrc, reason=reason, source="사용자 지정" if user else "한국관광공사 TourAPI",
        hours_status=info.status, hours_text=hours_text, lat=p.get("lat"), lng=p.get("lng"),
        image_url=p.get("image_url"), image_license=p.get("image_license"), summary=p.get("summary"),
        notes=info.notes,
    ).model_dump()


# ---------- 규칙 검사 (validate_plan) ----------

def validate(spec: TripSpec, items: list[dict], allowed_ids: set[str]) -> list[dict]:
    v: list[dict] = []
    pl = data.places(spec.region)
    user_ids = {w["place_id"] for w in spec.user_waypoints}
    wins, mwins = windows(spec), meal_windows(spec)
    for di, d in enumerate(spec.days):
        ds = d.isoformat()
        day = [it for it in items if it["date"] == ds and it["type"] != "move"]
        w, mw = wins[di], mwins[di]
        if di == 0 and spec.depart_stops:      # 가는 길 들를 곳 → 첫 장소는 실제 지점 기준: 마지막 들른 곳을 떠나 그 운전 시간 뒤면 충분
            rt = [it for it in items if it["date"] == ds and it["type"] == "move" and (it.get("notes") or [""])[0] == "가는 길"]
            if rt and day:
                fl = max(hours.parse_hm(x["end_time"]) for x in rt) + (day[0].get("long_leg_min") or 0)
                w = (min(w[0], fl), w[1])
                mw = (min(mw[0], fl), mw[1])
        route_meals = [hours.parse_hm(it["start_time"]) for it in items if it["date"] == ds and it["type"] == "move"
                       and (it.get("notes") or [""])[0] in ("가는 길", "오는 길") and it.get("route_kind") == "meal"]
        prev = None
        check_cnt = 0
        for idx, it in enumerate(day):
            s, e = hours.parse_hm(it["start_time"]), hours.parse_hm(it["end_time"])
            name, pid = it["place_name"], it.get("place_id")
            if pid and pid.startswith(FREE_PREFIX):
                continue
            if pid and pid not in allowed_ids and pid not in user_ids and not visit_base(pid):
                v.append(_v("없는 장소", it, "검색 결과(후보)에 없는 장소"))
            p = pl.get(pid) if pid else None
            if p:
                info = hours.parse_open(p, d)
                if info.status == "휴무":
                    v.append(_v("휴무일", it, f"{ds} 휴무 ({info.rest_raw})"))
                elif info.status == "확인됨" and it["type"] != "stay":
                    if s < info.open or e > info.close:
                        v.append(_v("운영시간 밖", it, f"운영 {hours.fmt(info.open)}~{hours.fmt(info.close)}, 배치 {it['start_time']}~{it['end_time']}"))
                    for a, b in info.breaks:
                        if s < b and e > a:
                            v.append(_v("운영시간 밖", it, f"휴게시간 {hours.fmt(a)}~{hours.fmt(b)}와 겹침"))
                elif it["type"] == "stay" and info.status == "확인됨" and s < info.open:
                    v.append(_v("운영시간 밖", it, f"체크인 {hours.fmt(info.open)} 전 도착"))
            if it["hours_status"] == "확인 필요" and it["source"] != "사용자 지정":
                check_cnt += 1
                if it["type"] in ("meal",) or (idx == len(day) - 1 and it["type"] != "stay"):
                    v.append(_v("확인 필요 배치", it, "운영시간 확인 필요 장소를 식사·마지막 자리에 둠"))
            if prev is not None:
                pe = hours.parse_hm(prev["end_time"])
                if pe + it["travel_minutes_from_prev"] + BUFFER_MIN > s:
                    v.append(_v("시간 겹침", it, f"앞 일정 종료 {prev['end_time']} + 이동 {it['travel_minutes_from_prev']}분 + 여유 10분 > 시작 {it['start_time']}"))
            win = mw if it["type"] == "meal" else w
            if "조식" in (it.get("notes") or []):
                continue
            early_bf = it["type"] == "meal" and s < BREAKFAST_END and e <= win[1]    # 직접 고른 아침 식당(숙소 아침 시각)
            if "야간" in (it.get("notes") or []):       # 체크인 뒤 저녁·야간 일정은 관광 시간 창 대신 자정 규칙으로 본다
                prev = it
                continue
            if it["type"] != "stay" and not early_bf and (s < win[0] or e > win[1]):
                v.append(_v("시간 창 밖", it, f"현지 가능 시간 {hours.fmt(win[0])}~{hours.fmt(win[1])}"))
            if it["type"] == "stay" and s > STAY_LIMIT:       # 22:00~다음 날 02:00 늦은 체크인은 문제 없음
                v.append(_v("시간 창 밖", it, "숙소 도착이 다음 날 02:00 이후"))
            prev = it
        if check_cnt > 1:
            v.append({"type": "확인 필요 초과", "seq": None, "place_name": "", "date": ds,
                      "detail": f"{ds} 운영시간 확인 필요 장소 {check_cnt}곳(하루 최대 1곳)"})
        for a, b in ([] if (di + 1) in (spec.user_ordered_days or []) else meal_runs(spec, day)):       # 식사 연달아 4번 이상(직접 정한 순서의 날은 안내만)(3번까지 허용, 카페 제외 — 분류는 화면에서 바꿀 수 있음)
            v.append(_v("식사 중복", b, f"숙소 체크인({a['start_time']}) 뒤에 식사" if a["type"] == "stay" else
                        f"{a['place_name']}({a['start_time']})부터 식사가 {MEAL_RUN_MAX}번 넘게 연달아 있어요({b['start_time']}) "
                        "— 카페라면 분류를 '카페'로 바꿔 주세요"))
        meals = [hours.parse_hm(it["start_time"]) for it in day if it["type"] == "meal"] + route_meals       # 가는 길·오는 길 식당도 그 끼니
        # 식사 시간대는 '권장'(2026-11-27 사용자: 꼭 그 시간에 먹어야 하는 건 아님) — 점심 11:00~15:30,
        # 저녁 16:30~21:30 사이에 시작하는 식사가 있으면 그 끼니로 인정. 순서를 직접 정한 날 습지 뒤 14시대 점심 등 허용.
        for label, (a, b), (fa, fb) in (("점심", LUNCH, MEAL_FLEX["점심"]), ("저녁", DINNER, MEAL_FLEX["저녁"])):
            if (di + 1) not in (spec.user_ordered_days or []) and meal_required(mw, (a, b)) and not any(fa <= m < fb for m in meals):
                v.append({"type": "식사 누락", "seq": None, "place_name": "", "date": ds,
                          "detail": f"{ds} {label}({hours.fmt(a)}~{hours.fmt(b)}) 식사 항목 없음"})
        v += midnight_check(spec, di, ds, items)
        if night_needs_stay(spec, di) and not any(it["type"] == "stay" for it in day):     # 다음 날까지 이어지는 밤
            v.append({"type": "숙박 누락", "seq": None, "place_name": "", "date": ds,
                      "detail": f"{di + 1}일차 밤 숙소 없음 — 숙소를 넣거나 '숙박 필요 없음(차박·밤샘·야간 이동)'을 골라 주세요"})
    return v


def midnight_check(spec: TripSpec, di: int, ds: str, items: list[dict]) -> list[dict]:
    """하루 일정이 자정(24:00)을 넘으면 조용히 두지 않고 알린다(2026-11-27 판교 버거킹 → 25:00 숙소 사례).
    관광·식사가 24:00 뒤에 끝나거나(강행군이 아니면) 숙소 도착이 24:00 뒤면 위반. 몇 곳을 줄이면 되는지 어림해 안내."""
    day = [it for it in items if it["date"] == ds and (it["type"] != "move" or (it.get("notes") or [""])[0] == NIGHT_RETURN)]
    late = [it for it in day if hours.parse_hm(it["end_time"]) > 1440 and (it["type"] != "stay" or spec.intensity != "강행군")]
    if not late:
        return []
    over = max(hours.parse_hm(it["end_time"]) for it in late) - 1440
    acts = [it for it in day if it["type"] in ("place", "meal") and not (it.get("place_id") or "").startswith(FREE_PREFIX)]
    n, acc = 0, 0
    for it in reversed(acts):
        acc += hours.parse_hm(it["end_time"]) - hours.parse_hm(it["start_time"]) + (it.get("travel_minutes_from_prev") or 0)
        n += 1
        if acc >= over:
            break
    n = max(1, n)
    return [{"type": "자정 초과", "seq": None, "place_name": "", "date": ds,
             "detail": f"{di + 1}일차 일정이 자정을 넘어요 — {n}곳 줄이기 권장(마지막 {hours.label(max(it['end_time'] for it in late))})"}]


def _v(t: str, it: dict, detail: str) -> dict:
    return {"type": t, "seq": it["seq"], "place_name": it["place_name"], "date": it["date"], "detail": detail}


def feedback_text(viol: list[dict]) -> str:
    return "\n".join(f"- [{x['type']}] {x['date']} {x['place_name']}: {x['detail']}" for x in viol)


# ---------- 마지막 보정(재생성 2회 후에도 위반 시) ----------

def repair(spec: TripSpec, items: list[dict], viol: list[dict], allowed: set[str],
           cands: dict[str, dict] | None = None) -> list[dict]:
    """재생성 2회 후에도 위반이 남을 때의 코드 보정.
    위반 항목 제거(숙박 유지) → 빠진 식사는 '주변에서 식사' 자리표시자 → 시간 창을 넘으면 뒤쪽 관광지부터 덜어 냄."""
    from src.planning.schemas import DayPick, PickItem
    bad = {x["seq"] for x in viol if x["seq"] is not None}
    # 사용자가 직접 넣은 곳(추가·끼워 넣기·꼭 갈 곳·개인 시간·숙소 들르기)은 규칙 위반이어도 조용히 지우지 않는다 —
    # 위반은 그대로 안내(확정 전 조정)하고 지울지는 사용자가 정한다(2026-11-26: 조건 변경 때 방금 추가한 곳이 사라진 문제)
    protect = lambda it: user_meant(spec, it.get("place_id")) or str(it.get("place_id") or "").startswith((FREE_PREFIX, VISIT_PREFIX))
    keep = [it for it in items if it["type"] != "move" and (it["seq"] not in bad or it["type"] == "stay" or protect(it))
            and it.get("place_id")]
    wins = windows(spec)
    days: list[list[PickItem]] = []
    for di, d in enumerate(spec.days):
        ds = d.isoformat()
        day = [it for it in keep if it["date"] == ds]
        mk = lambda it: PickItem(place_id=it["place_id"], role=_role_of(it), reason=it["reason"],
                                 stay_minutes=max(30, hours.parse_hm(it["end_time"]) - hours.parse_hm(it["start_time"])))
        si = next((k for k, it in enumerate(day) if it["type"] == "stay"), None)
        picks = [mk(it) for it in day[:si] if it["type"] != "stay"] if si is not None else [mk(it) for it in day]
        night = [mk(it) for it in day[si + 1:] if it["type"] != "stay"] if si is not None else []     # 체크인 뒤 야간 일정은 숙소 뒤에 그대로
        stay = [PickItem(place_id=day[si]["place_id"], role="stay", stay_minutes=0, reason=day[si]["reason"])] if si is not None else []
        if night_needs_stay(spec, di) and not stay:
            lodg = next((p for p in (cands or {}).values() if p.get("kind") == "lodging"), None)
            if lodg:
                stay = [PickItem(place_id=lodg["contentid"], role="stay", stay_minutes=0, reason="동선이 가까운 숙소예요")]
        if di >= spec.nights:
            stay, picks, night = [], picks + night, []
        # 빠진 식사 자리표시자: 점심은 시간 순서상 정오 무렵, 저녁은 맨 뒤
        for role, win in (("lunch", LUNCH), ("dinner", DINNER)):
            if (meal_required(meal_windows(spec)[di], win) and not any(pk.role == role for pk in picks)
                    and not route_meal_covers(spec, di, win)):
                if role == "lunch":
                    before = [it for it in day if it["type"] != "stay" and hours.parse_hm(it["start_time"]) < 12 * 60]
                    picks.insert(min(len(before), len(picks)), PickItem(place_id=MEAL_ID, role="lunch", stay_minutes=60, reason=""))
                else:
                    picks.append(PickItem(place_id=MEAL_ID, role="dinner", stay_minutes=60, reason=""))
        days.append(picks + stay + night)
    days = dedupe_meals(spec, place_lunch(spec, days))
    out: list[dict] = []
    for _ in range(8):     # 항목에 걸린 시간 위반만 풀어 냄: 위반한 관광지를 빼고, 식사·숙소가 밀렸으면 그 앞 관광지를 하나 뺌
        out = schedule(spec, PlanPick(days=[DayPick(day=i + 1, items=x) for i, x in enumerate(days)]), {})
        v = [x for x in validate(spec, out, allowed | {MEAL_ID})
             if x["type"] in ("시간 창 밖", "시간 겹침", "운영시간 밖") and x["seq"] is not None]
        if not v:
            break
        bad = next(it for it in out if it["seq"] == v[0]["seq"])
        di = [d.isoformat() for d in spec.days].index(bad["date"])
        row = days[di]
        keep = set(must_ids(spec)) | {pk.place_id for r in days for pk in r
                                      if user_meant(spec, pk.place_id) or pk.place_id.startswith((FREE_PREFIX, VISIT_PREFIX))}   # 사용자가 넣은 곳은 보호
        hit = next((i for i, pk in enumerate(row) if pk.role == "place" and pk.place_id == bad.get("place_id")
                    and pk.place_id not in keep), None)
        if hit is None and bad.get("place_id") in keep:   # 지정 장소가 밀렸으면 그날 다른 관광지를 뺌
            hit = max((i for i, pk in enumerate(row) if pk.role == "place" and pk.place_id not in keep), default=None)
        if hit is None:      # 식사·숙소가 밀림 → 그 앞의 마지막 관광지
            key = bad.get("place_id") or MEAL_ID
            pos = next((i for i, pk in enumerate(row) if pk.place_id == key), len(row))
            hit = max((i for i, pk in enumerate(row[:pos]) if pk.role == "place" and pk.place_id not in keep), default=None)
        if hit is None:
            break
        row.pop(hit)
    return out


CAFE_RE = re.compile(r"(카페|까페|커피|cafe|coffee|베이커리|bakery|제과|찻집|다원|디저트|티하우스|브런치)", re.I)
MEAL_GAP_MIN = 120      # 앞 식사가 끝나고 이 시간 안에 또 식사(카페 제외)면 '식사 중복'
MEAL_RUN_MAX = 3        # 규칙 검사: 연달아 오는 식사는 3번까지 허용(2026-11-26 사용자 요청 — 카페가 식사로 잡혀 추가가 막힘)
BREAKFAST_END = 10 * 60 + 30     # 이 시각 전에 시작하는 식사는 아침


def user_meant(spec: TripSpec, pid: str | None) -> bool:
    """사용자가 일부러 넣은 곳: 꼭 갈 곳·대화/화면에서 추가·바꾸기로 고른 곳(waypoint_ids), 직접 고른 곳(user_waypoints)."""
    return bool(pid) and (pid in spec.waypoint_ids or any(w.get("place_id") == pid for w in spec.user_waypoints))


def _real_meal(spec: TripSpec, it: dict) -> bool:
    """아침 식사·카페·디저트를 뺀 '식사' 항목."""
    if it["type"] != "meal" or "조식" in (it.get("notes") or []):
        return False
    pid = it.get("place_id")
    ko = (spec.kind_override or {}).get(pid) if pid else None
    if ko:
        return ko == "식사"
    p = (data.places(spec.region).get(pid) if pid else None) or next(
        (w for w in spec.user_waypoints if w.get("place_id") == pid), {}) if pid else {}
    return not CAFE_RE.search(f"{it.get('place_name') or ''} {(p or {}).get('vworld_category') or ''}")


def _slot(it: dict) -> str:
    m = hours.parse_hm(it["start_time"])
    return "breakfast" if m < BREAKFAST_END else "lunch" if m < 16 * 60 else "dinner"


def meal_runs(spec: TripSpec, day_items: list[dict], limit: int = MEAL_RUN_MAX) -> list[tuple[dict, dict]]:
    """규칙 검사용: 사이에 관광지 없이 연달아(앞 식사 끝 → MEAL_GAP_MIN 안) 오는 식사가 limit번을 넘으면 (첫 식사, 넘친 식사).
    카페·아침은 세지도 끊지도 않는다. 숙소 체크인 뒤 '주변 식사' 자리표시자는 그대로 중복으로 본다."""
    out, run = [], []
    for it in sorted(day_items, key=lambda x: hours.parse_hm(x["start_time"])):
        if _real_meal(spec, it):
            if run and hours.parse_hm(it["start_time"]) - hours.parse_hm(run[-1]["end_time"]) < MEAL_GAP_MIN:
                run.append(it)
            else:
                run = [it]
            if len(run) > limit:
                out.append((run[0], it))
        elif it["type"] in ("place", "stay") and not (it.get("place_id") or "").startswith(FREE_PREFIX):
            run = []
    stay = next((it for it in day_items if it["type"] == "stay"), None)
    if stay:
        out += [(stay, it) for it in day_items if it.get("place_id") is None and it["type"] == "meal"
                and "조식" not in (it.get("notes") or []) and day_items.index(it) > day_items.index(stay)]
    return out


def meal_clashes(spec: TripSpec, day_items: list[dict]) -> list[tuple[dict, dict]]:
    """같은 날 식사(아침·카페 제외) 중복 쌍: ① 앞 식사 종료 후 MEAL_GAP_MIN 안에 또 식사 ② 같은 끼니(점심=16시 전, 저녁=16시부터)
    두 번 ③ 숙소 체크인 뒤 식사. 둘 다 사용자가 넣은 곳이면 허용(③은 자리표시자만)."""
    meals = sorted((it for it in day_items if _real_meal(spec, it) and "야간" not in (it.get("notes") or [])),
                   key=lambda x: hours.parse_hm(x["start_time"]))
    out = []
    ordered = bool(day_items) and any(d.isoformat() == day_items[0].get("date") and (i + 1) in (spec.user_ordered_days or [])
                                      for i, d in enumerate(spec.days))
    for i, b in enumerate(meals):
        for a in meals[:i]:
            if user_meant(spec, a.get("place_id")) and user_meant(spec, b.get("place_id")):
                continue
            if ordered and a.get("place_id") and b.get("place_id") and user_meant(spec, a.get("place_id")) == user_meant(spec, b.get("place_id")):
                continue      # 직접 정한 순서의 식당 둘은 둘 다 둔다(안내만: meal_advice) — 모모가 고른 식당 vs 사용자가 고른 식당이면 예전처럼 사용자 쪽이 맡음
            if hours.parse_hm(b["start_time"]) - hours.parse_hm(a["end_time"]) < MEAL_GAP_MIN or _slot(a) == _slot(b):
                out.append((a, b))
                break
    stay = next((it for it in day_items if it["type"] == "stay"), None)
    if stay:
        late = [it for it in day_items if it.get("place_id") is None and it["type"] == "meal"
                and day_items.index(it) > day_items.index(stay)]
        out += [(stay, it) for it in late if not any(it is y for _, y in out)]
    return out


def dedupe_meals(spec: TripSpec, days: list[list], log: list | None = None) -> list[list]:
    """식사 중복 정리(사용자 테스트 2026-11-26: 전체 동선 최적화 뒤 1일차 저녁 18:00·19:20·20:40 세 번).
    연달아 잡힌 식사 중 사용자가 넣지 않은 쪽을 뺀다 — '주변에서 식사' 자리표시자 → 모모가 고른 식당 순. 카페는 그대로."""
    from src.planning.schemas import DayPick
    days = [list(r) for r in days]
    for _ in range(6):
        items = schedule(spec, PlanPick(days=[DayPick(day=i + 1, items=r) for i, r in enumerate(days)]), {})
        changed = False
        for di, d in enumerate(spec.days):
            ds = d.isoformat()
            day = [it for it in items if it["date"] == ds and it["type"] != "move" and "조식" not in (it.get("notes") or [])]
            row = days[di]
            if len(day) != len(row):
                continue
            for a, b in meal_clashes(spec, day):
                drop = b if a["type"] == "stay" else min(
                    (a, b), key=lambda it: (user_meant(spec, it.get("place_id")), it.get("place_id") is not None))
                k = day.index(drop)
                if user_meant(spec, row[k].place_id):
                    continue
                if ((di + 1) in (spec.user_ordered_days or []) and row[k].place_id != MEAL_ID
                        and not any(user_meant(spec, x.get("place_id")) for x in (a, b))):     # 순서를 직접 정한 날은 고른 식당을 빼지 않음
                    continue
                row.pop(k)
                if log is not None and a["type"] != "stay":     # 숙소 뒤 자리표시자는 조용히 정리
                    keep = b if drop is a else a
                    log.append(f"{di + 1}일차 {keep['place_name']}와(과) 식사가 겹쳐 "
                               f"{'주변 식사 자리' if drop.get('place_id') is None else drop['place_name']}을(를) 뺐어요(카페는 그대로)")
                changed = True
                break
        if not changed:
            break
    return days


def place_lunch(spec: TripSpec, days: list[list]) -> list[list]:
    """점심 자리 바로잡기(사용자 테스트 2026-11-26): 점심 역할 식당이 있어도 관광지 뒤로 밀려 15시 넘어 배치되면
    '점심 식사 항목 없음'이 풀리지 않아 확정할 수 없었다. 실제 배치 시각을 보고, 점심이 빠진 날은
    정오를 차지하는 장소 앞으로 점심(없으면 '주변에서 점심 식사')을 옮긴다. 다른 장소 순서는 그대로."""
    from src.planning.schemas import DayPick, PickItem
    days = [list(r) for r in days]
    mw = meal_windows(spec)
    for _ in range(2):
        items = schedule(spec, PlanPick(days=[DayPick(day=i + 1, items=r) for i, r in enumerate(days)]), {})
        changed = False
        for di, d in enumerate(spec.days):
            if not meal_required(mw[di], LUNCH):
                continue
            if di + 1 in (spec.user_ordered_days or []):     # 순서를 직접 정한 날은 식당도 정한 자리 그대로(식사 시간은 권장)
                continue
            ds = d.isoformat()
            day = [it for it in items if it["date"] == ds and it["type"] != "move" and "조식" not in (it.get("notes") or [])]
            if any(it["type"] == "meal" and MEAL_FLEX["점심"][0] <= hours.parse_hm(it["start_time"]) < MEAL_FLEX["점심"][1]
                   for it in day):
                continue
            row = days[di]
            if len(day) != len(row):        # 배치와 고른 목록이 어긋나면 손대지 않음
                continue
            target = next((i for i, it in enumerate(day) if it["type"] != "stay"
                           and hours.parse_hm(it["end_time"]) > LUNCH[0] + 30), None)
            if target is None:
                target = next((i for i, pk in enumerate(row) if pk.role in ("dinner", "stay")), len(row))
            li = next((i for i, pk in enumerate(row) if pk.role == "lunch"), None)
            if li is not None and li < target:   # 점심이 이미 앞에 있는데도 빠졌으면(시간 창 문제) 그대로
                continue
            pk = row.pop(li) if li is not None else PickItem(place_id=MEAL_ID, role="lunch", stay_minutes=60, reason="")
            row.insert(target, pk)
            changed = True
        if not changed:
            break
    return days


def apply_booked_stay(spec: TripSpec, rows: list[list]) -> list[list]:
    """예약한 숙소(spec.booked_stay)를 숙소가 필요한 모든 밤에 일괄로(2026-11-26 사용자 요청).
    '숙박 필요 없음'을 고른 밤은 건드리지 않는다. rows: 일차별 PickItem 목록(제자리 수정)."""
    from src.planning.schemas import PickItem
    sid = spec.booked_stay
    if not sid:
        return rows
    for di, row in enumerate(rows):
        if di >= spec.nights or str(di + 1) in (spec.no_stay or {}):
            continue
        k = next((i for i, x in enumerate(row) if x.role == "stay"), None)
        pk = PickItem(place_id=sid, role="stay", stay_minutes=0, reason="예약하신 숙소예요")
        if k is None:
            row.append(pk)
        else:
            row[k] = pk
    return rows


def _role_of(it: dict) -> str:
    if "야간" in (it.get("notes") or []):
        return "place"
    if it["type"] == "meal" and hours.parse_hm(it["start_time"]) < BREAKFAST_END and it.get("place_id"):
        return "breakfast"
    if it["type"] == "meal":
        return "lunch" if hours.parse_hm(it["start_time"]) < 16 * 60 else "dinner"
    return "stay" if it["type"] == "stay" else "place"


# ---------- 전체 루프 ----------

def build_plan(spec: TripSpec, request: str, base: list[dict] | None = None, trace: list | None = None,
               locked: dict | None = None, prefix: dict | None = None, indoor: bool = False) -> dict:
    """locked: {일차: [PickItem]} — 수정 대상이 아닌 날은 기존 선택 그대로 고정.
    prefix: {일차: [PickItem]} — 수정 대상 날의 앞부분(예: '오후만' 수정 시 오전·점심) 고정.
    indoor: 수정 구간의 관광지는 실내만 허용(코드로 강제)."""
    trace = trace if trace is not None else []
    cands = candidates(spec, request)
    for w in spec.user_waypoints:
        cands.setdefault(w["place_id"], w)
    pl = data.places(spec.region)
    if spec.booked_stay and spec.booked_stay in pl:
        cands.setdefault(spec.booked_stay, pl[spec.booked_stay])
    for picks in [*(locked or {}).values(), *(prefix or {}).values()]:
        for pk in picks:
            if pk.place_id in pl:
                cands.setdefault(pk.place_id, pl[pk.place_id])
    allowed = set(cands)
    trace.append({"step": "retrieve", "input": request[:200], "output": f"후보 {len(cands)}곳: " + ", ".join(p["title"] for p in list(cands.values())[:15])})
    feedback, items, viol, regen, last_ranked = None, [], [], 0, []
    from src.shared.cancel import check as _cancel_check
    for attempt in range(MAX_REGEN + 1):
        _cancel_check()
        picked = pick(spec, cands, request, feedback, base, indoor=indoor)
        last_ranked = picked.ranked or last_ranked
        picked = ensure_waypoints(spec, picked)
        picked = restore_user_blocks(spec, picked, base)
        if spec.booked_stay:      # 예약한 숙소: 모든 밤을 그 숙소로(LLM이 고른 숙소 대신)
            from src.planning.schemas import DayPick
            have = {x.day for x in picked.days}
            picked.days += [DayPick(day=d, items=[]) for d in range(1, spec.nights + 2) if d not in have]
            picked.days.sort(key=lambda x: x.day)
            apply_booked_stay(spec, [dp.items for dp in picked.days])
        if spec.no_stay:          # 사용자가 숙박 필요 없음(차박·밤샘·야간 이동)을 고른 밤에는 숙소를 다시 넣지 않음
            for dp in picked.days:
                if str(dp.day) in spec.no_stay:
                    dp.items = [x for x in dp.items if x.role != "stay"]
        if prefix or indoor:
            from src.planning.schemas import DayPick
            new_days = []
            for d in range(1, spec.nights + 2):
                cur = next((x for x in picked.days if x.day == d), DayPick(day=d, items=[]))
                if locked and d in locked:
                    new_days.append(cur)
                    continue
                pre = (prefix or {}).get(d, [])
                pre_ids = {x.place_id for x in pre}
                has_lunch = any(x.role == "lunch" for x in pre)
                rest = [x for x in cur.items if x.place_id not in pre_ids and not (has_lunch and x.role == "lunch")]
                if indoor:
                    rest = [x for x in rest if x.role != "place" or is_indoor(cands.get(x.place_id) or pl.get(x.place_id) or {})]
                new_days.append(DayPick(day=d, items=pre + rest))
            picked.days = new_days
        if locked:
            from src.planning.schemas import DayPick
            picked.days = [DayPick(day=d, items=locked[d]) if d in locked else
                           next((x for x in picked.days if x.day == d), DayPick(day=d, items=[]))
                           for d in range(1, spec.nights + 2)]
        fixed = set(spec.user_ordered_days) | set((locked or {}).keys()) | set((prefix or {}).keys())
        picked, saved = optimize_plan(spec, picked, allowed, fixed)
        spec.route_saved_min = saved
        if saved:
            trace.append({"step": "tool:optimize_route", "input": f"고정 날 {sorted(fixed) or '없음'}",
                          "output": f"동선 재정렬로 이동 {saved}분 단축"})
        items = schedule(spec, picked, cands)
        viol = validate(spec, items, allowed)
        trace.append({"step": "generate_plan" if attempt == 0 else "regenerate",
                      "input": f"시도 {attempt + 1}" + (f" · 위반 {len(feedback.splitlines())}건 반영" if feedback else ""),
                      "output": " / ".join(f"{it['date'][5:]} {it['start_time']} {it['place_name']}" for it in items)[:600]})
        trace.append({"step": "validate", "input": f"항목 {len(items)}개", "output": feedback_text(viol) or "위반 0건"})
        if not viol:
            break
        regen = attempt + 1 if attempt < MAX_REGEN else regen
        feedback = feedback_text(viol)
    repaired = False
    if viol:
        items = repair(spec, items, viol, allowed, cands)
        viol = validate(spec, items, allowed)
        repaired = True
        trace.append({"step": "validate", "input": "재생성 2회 후 위반 항목 제거·식사 안내로 보정", "output": feedback_text(viol) or "위반 0건"})
    for it in items:
        if it["type"] != "move":
            trace.append({"step": "tool:estimate_travel", "input": it["place_name"],
                          "output": f"{it['travel_minutes_from_prev']}분 ({it['travel_source']})"})
            break
    return {"itinerary": items, "violations": viol, "regenerations": regen, "repaired": repaired,
            "candidates": cands, "note": None, "ranked": rank_list(spec, items, cands, last_ranked)}


def rank_list(spec: TripSpec, items: list[dict], cands: dict, llm_ranked: list) -> list[tuple[str, str]]:
    """추천 1~10위: LLM 순위(후보 id만) → 일정에 든 관광지 → 대표 명소 → 나머지 후보 순으로 채움."""
    out, seen = [], set()

    def add(pid, reason):
        p = cands.get(pid) or data.places(spec.region).get(pid)
        if p and pid not in seen and p.get("kind") in ("attraction", "festival") and len(out) < 10:
            seen.add(pid)
            out.append((pid, reason or (p.get("summary") or "")[:60]))
    for r in llm_ranked or []:
        add(r.place_id, r.reason)
    for it in items:
        if it["type"] == "place" and it.get("place_id"):
            add(it["place_id"], it.get("reason"))
    for pid, p in cands.items():
        if p.get("must_include"):
            add(pid, "처음 가면 빠뜨리면 아쉬운 대표 명소예요")
    for pid in cands:
        add(pid, "")
    return out


def meal_advice(spec: TripSpec, items: list[dict]) -> list[str]:
    """직접 순서를 정한 날의 식사 안내(위반이 아님 — 식사 시간은 권장): 끼니가 시간대에 없거나 식당이 가깝게 붙은 경우."""
    out: list[str] = []
    for di, d in enumerate(spec.days):
        if (di + 1) not in (spec.user_ordered_days or []):
            continue
        ds = d.isoformat()
        day = [it for it in items if it["date"] == ds and it["type"] != "move" and "조식" not in (it.get("notes") or [])]
        mw = meal_windows(spec)[di]
        meals = [hours.parse_hm(it["start_time"]) for it in day if it["type"] == "meal"]
        for label, win in (("점심", LUNCH), ("저녁", DINNER)):
            fa, fb = MEAL_FLEX[label]
            if meal_required(mw, win) and not any(fa <= m < fb for m in meals):
                out.append(f"{di + 1}일차 {label}({hours.fmt(fa)}~{hours.fmt(fb)}) 시간대에 식사가 없어요 — 권장일 뿐이라 정하신 순서 그대로 뒀어요. 원하면 '+ 식사'로 넣어 보세요")
        real = sorted((it for it in day if _real_meal(spec, it) and "야간" not in (it.get("notes") or [])), key=lambda x: hours.parse_hm(x["start_time"]))
        for a, b in zip(real, real[1:]):
            if hours.parse_hm(b["start_time"]) - hours.parse_hm(a["end_time"]) < MEAL_GAP_MIN or _slot(a) == _slot(b):
                out.append(f"{di + 1}일차 {a['place_name']}와(과) {b['place_name']} 식사가 가까워요 — 정하신 순서 그대로 두었어요")
    return out
