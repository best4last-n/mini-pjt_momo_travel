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

from . import data, guardrails, hours, retrieve, travel
from .config import (BUFFER_MIN, DAY_END, DAY_START, DEFAULT_DEPART, DINNER, HOME_LIMIT, LUNCH, MAX_REGEN)
from .llm import MOMO_PERSONA, structured
from .schemas import ItineraryItem, PlanPick

MEAL_ID = "MEAL"
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
    intensity: str = "보통"                 # 빡빡 / 보통 / 여유 (SERVICE.md 3-6 (2))
    long_transport: str = "자차"            # 자차 / KTX·SRT / 고속·시외버스
    local_transport: str = "자차"           # 자차 / 렌터카 / 대중교통
    companion_type: str = "보통"            # 보통 / 노약자 / 어린이·유아
    companion_count: int | None = None
    origin_label: str | None = None         # 시·군·구 표기(계산은 departure 시·도)
    assumptions: list[str] = field(default_factory=list)
    user_ordered_days: list[int] = field(default_factory=list)   # 사용자가 순서를 정한 날(1부터) — 최적화하지 않음
    route_saved_min: int = 0
    vehicle: dict | None = None             # {type, fuel, efficiency_kmpl, toll_discount, parking_discount}
    origin_coords: list | None = None       # 출발지 좌표(약 1km 반올림) — 있으면 시·도 대표 좌표 대신 사용
    end_coords: list | None = None
    end_location: str | None = None         # 여행 후 도착지(시·도), 없으면 출발지
    end_label: str | None = None
    arrive_time: int | None = None          # 도착 희망 시각(분), 없으면 22:00 상한
    custom_costs: list[dict] = field(default_factory=list)
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

    @property
    def days(self) -> list[date]:
        return hours.dates_of(self.start, self.nights + 1)


# ---------- 시간 창 ----------

INTENSITY = {   # 관광 시간대(분), 하루 관광지 상한, 구간 권장 상한(분)
    "빡빡": (8 * 60, 20 * 60, 5, None),
    "보통": (9 * 60, 18 * 60 + 30, 4, 60),
    "여유": (10 * 60, 17 * 60 + 30, 3, 40),
}


def local_mode(spec: TripSpec) -> str:
    return "대중교통" if spec.local_transport == "대중교통" else "자차"


def one_way_minutes(spec: TripSpec) -> int | None:
    if not spec.departure or spec.departure not in data.SIDO_COORDS:
        return None
    a = tuple(spec.origin_coords) if spec.origin_coords else data.SIDO_COORDS[spec.departure]
    if spec.long_transport in ("KTX·SRT", "비행기"):
        from . import hubs
        opts = hubs.rail_options(a, spec.region) if spec.long_transport == "KTX·SRT" else hubs.air_options(a, spec.region)
        if not opts and spec.long_transport == "비행기":
            opts = hubs.rail_options(a, spec.region)
        if opts:
            return opts[0]["total_min"]
    if spec.long_transport == "자차":      # 실제 도로 경로 + 요일·시간대 혼잡 + 휴게
        from . import roads
        dr = roads.drive(a, data.region_center(spec.region), spec.start, spec.depart_time, True)
        if dr:
            return dr["total_min"]
    return travel.intercity_minutes(a, data.region_center(spec.region), spec.long_transport)


def return_minutes(spec: TripSpec) -> int | None:
    """여행 지역 → 여행 후 도착지(없으면 출발지)."""
    coords = spec.end_coords or (None if spec.end_location else spec.origin_coords)
    dest = spec.end_location or spec.departure
    if not coords and (not dest or dest not in data.SIDO_COORDS):
        return None
    b = tuple(coords) if coords else data.SIDO_COORDS[dest]
    if spec.long_transport == "자차":
        from . import roads
        last = spec.days[-1]
        dr = roads.drive(data.region_center(spec.region), b, last, 16 * 60, False)
        if dr:
            return dr["total_min"]
    return travel.intercity_minutes(data.region_center(spec.region), b, spec.long_transport)


def home_limit(spec: TripSpec) -> int:
    return spec.arrive_time if spec.arrive_time else HOME_LIMIT


def windows(spec: TripSpec) -> list[tuple[int, int]]:
    ow = one_way_minutes(spec)
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
    cap = 7 if many else INTENSITY.get(intensity, INTENSITY["보통"])[2]
    free = win[1] - win[0] - n_meals * 80
    return max(1, min(cap, free // (100 if not many else 80)))


def meal_required(win: tuple[int, int], meal: tuple[int, int]) -> bool:
    """점심은 시간 창과 30분 이상 겹치면, 저녁은 60분 식사가 온전히 들어갈 때만 필수(귀가 길 저녁은 안내로)."""
    need = 60 if meal == DINNER else 30
    return min(win[1], meal[1]) - max(win[0], meal[0]) >= need


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

PICK_SYSTEM = MOMO_PERSONA + """
지금은 일정의 뼈대만 고른다. 반드시 후보 목록의 id만 쓴다(목록에 없는 장소 금지).
규칙:
- 하루 관광지 3~5곳(많이 원해도 최대 7곳), 가까운 곳끼리 묶어 이동을 줄인다(좌표 참고).
- 하루 시간 창 안에서 점심(role=lunch, 12~14시)·저녁(role=dinner, 18~20시)이 필요하다고 주어지면 음식점을 넣는다.
- 점심·저녁은 식사가 되는 음식점으로 고른다(카페·베이커리·디저트 전문점은 식사 자리에 넣지 않는다).
- N박이면 1~N일차 마지막에 숙박(role=stay)을 하루 1곳씩(같은 숙소 연박 가능, 다음 날 동선 쪽 숙소면 더 좋음). 마지막 날은 숙박 없음.
- 해당 일차에 '휴무'인 곳은 그날 넣지 않는다. '운영시간 확인 필요'는 하루 최대 1곳, 식사·마지막 자리에 두지 않는다.
- 운영시간이 짧은 곳(예: 17시 마감)은 그날 앞쪽에 둔다. 체류시간은 관광지 60~120분, 식사 60분.
- reason은 후보 정보(요약·종류)에 근거한 한 줄. 지어내지 않는다.
- ranked: 후보 중 관광지·축제 추천 1~10위를 우선순위대로(동반자 적합성 → 지역 대표성 → 시기·휴무 → 동선 → 관심사).
  일정에 넣은 곳을 포함하고, 나머지는 사용자가 바꿔 넣을 후보다. 이유는 사용자 조건과 연결한 한 문장.
- 동반자가 노약자면 계단·급경사·장거리 도보가 필수인 곳은 순위를 낮추고, 어린이·유아면 체험·넓은 야외를 우선한다.
"""


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
    if spec.many_places:
        msg.append("사용자가 아주 많은 곳을 원함: 하루 최대 7곳까지, 이동이 짧은 곳 위주.")
    if spec.waypoint_ids:
        msg.append(f"사용자가 꼭 넣어 달라고 한 장소 id: {', '.join(spec.waypoint_ids)} (반드시 포함)")
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

def _pcoord(spec: TripSpec, pid: str):
    p = data.places(spec.region).get(pid) or next((w for w in spec.user_waypoints if w["place_id"] == pid), None)
    return (p.get("lat"), p.get("lng")) if p and p.get("lat") else None


def ensure_waypoints(spec: TripSpec, picked: PlanPick) -> PlanPick:
    """사용자가 꼭 가겠다고 한 장소가 빠졌으면 코드가 넣는다: 관광지가 가장 적은 날의 저녁·숙박 앞(그다음 동선 최적화)."""
    from .schemas import DayPick, PickItem
    want = [*spec.waypoint_ids, *[w["place_id"] for w in spec.user_waypoints]]
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
    from .schemas import DayPick
    base_items = schedule(spec, picked, {})
    base_v = len(validate(spec, base_items, allowed))
    center = data.region_center(spec.region)
    new_days, last_stay = [], None
    for d in sorted(picked.days, key=lambda x: x.day):
        if d.day in fixed_days:
            new_days.append(d)
        else:
            start = (_pcoord(spec, last_stay) if last_stay else (center if spec.departure else None))
            new_days.append(DayPick(day=d.day, items=optimize_day(spec, list(d.items), start)))
        stay = next((x.place_id for x in d.items if x.role == "stay"), None)
        last_stay = stay or last_stay
    cand = PlanPick(days=new_days, ranked=picked.ranked, note=picked.note)
    items = schedule(spec, cand, {})
    v = len(validate(spec, items, allowed))
    saved = _travel_total(base_items) - _travel_total(items)
    if v <= base_v and saved > 0:
        return cand, saved
    return picked, 0


# ---------- 코드 시각 계산 ----------

def _coords(p: dict | None) -> tuple | None:
    return (p.get("lat"), p.get("lng")) if p and p.get("lat") else None


def schedule(spec: TripSpec, plan: PlanPick, cands: dict[str, dict]) -> list[dict]:
    wins = windows(spec)
    pl = data.places(spec.region)
    user_wp = {w["place_id"]: w for w in spec.user_waypoints}
    ow = one_way_minutes(spec)
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
        if ow is not None and di == 0:
            items.append(_move(d, f"{spec.departure} → {data.region_label(spec.region)}", spec.depart_time,
                               spec.depart_time + ow, "출발"))
        cursor, prev = w[0], None
        origin = last_stay if di > 0 and last_stay else (hub if di == 0 and ow is not None else None)
        for pk in picks:
            placeholder = pk.place_id == MEAL_ID
            if placeholder:   # '주변에서 식사' (운영시간이 맞는 식당이 없을 때)
                label = "점심" if pk.role == "lunch" else "저녁"
                p = {"contentid": None, "title": f"주변에서 {label} 식사", "kind": "restaurant", "source": "식사 안내",
                     "lat": prev.get("lat") if prev else None, "lng": prev.get("lng") if prev else None}
                info = hours.OpenInfo("확인됨")
            else:
                p = pl.get(pk.place_id) or user_wp.get(pk.place_id)
                if p is None:   # 후보 밖 id → 그대로 두고 검사에서 '없는 장소'로 잡힘
                    p = {"contentid": pk.place_id, "title": f"(알 수 없는 장소 {pk.place_id})", "kind": "attraction"}
                info = hours.parse_open(p, d)
            if prev is None and origin is not None and not placeholder:     # 숙소·도착 지점에서 첫 장소까지
                tmin, tsrc = travel.local_minutes(origin.get("contentid"), p.get("contentid"), _coords(origin), _coords(p), lm)
            elif prev is None:
                tmin, tsrc = 0, "estimate"
            elif placeholder or prev.get("source") == "식사 안내":
                tmin, tsrc = 10, "estimate"
            else:
                tmin, tsrc = travel.local_minutes(prev.get("contentid"), p.get("contentid"), _coords(prev), _coords(p), lm)
            start = cursor + (tmin + BUFFER_MIN if prev is not None else tmin)
            if pk.role == "lunch":
                start = max(start, LUNCH[0])
            elif pk.role == "dinner":
                start = max(start, DINNER[0])
            if info.status == "확인됨" and p.get("kind") != "lodging":
                start = max(start, info.open)
            if p.get("kind") == "lodging":
                start = max(start, info.open)
            start = _ceil5(start)
            stay = 0 if pk.role == "stay" else max(30, min(pk.stay_minutes, 180))
            for a, b in info.breaks:          # 휴게·준비시간과 겹치면 끝난 뒤로
                if start < b and start + stay > a:
                    start = _ceil5(b)
            end = start + stay
            typ = {"lunch": "meal", "dinner": "meal", "stay": "stay"}.get(pk.role, "place")
            if p.get("kind") == "restaurant" and typ == "place":
                typ = "meal"
            items.append(_item(p, d, typ, start, end, tmin, tsrc, mode, pk.reason, info))
            cursor, prev = end, p
            if typ == "stay":
                last_stay = p
        rw = return_minutes(spec)
        if rw is not None and di == len(spec.days) - 1:
            back = max(cursor, w[0])
            dest = spec.end_label or spec.end_location or spec.departure
            items.append(_move(d, f"{data.region_label(spec.region)} → {dest}", back, back + rw, "귀가"))
    for i, it in enumerate(items, 1):
        it["seq"] = i
    return items


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
        prev = None
        check_cnt = 0
        for idx, it in enumerate(day):
            s, e = hours.parse_hm(it["start_time"]), hours.parse_hm(it["end_time"])
            name, pid = it["place_name"], it.get("place_id")
            if pid and pid not in allowed_ids and pid not in user_ids:
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
            if it["type"] != "stay" and (s < win[0] or e > win[1]):
                v.append(_v("시간 창 밖", it, f"현지 가능 시간 {hours.fmt(win[0])}~{hours.fmt(win[1])}"))
            if it["type"] == "stay" and s > HOME_LIMIT:
                v.append(_v("시간 창 밖", it, "숙소 도착이 22:00 이후"))
            prev = it
        if check_cnt > 1:
            v.append({"type": "확인 필요 초과", "seq": None, "place_name": "", "date": ds,
                      "detail": f"{ds} 운영시간 확인 필요 장소 {check_cnt}곳(하루 최대 1곳)"})
        meals = [hours.parse_hm(it["start_time"]) for it in day if it["type"] == "meal"]
        for label, (a, b) in (("점심", LUNCH), ("저녁", DINNER)):
            if meal_required(mw, (a, b)) and not any(a - 30 <= m < b for m in meals):
                v.append({"type": "식사 누락", "seq": None, "place_name": "", "date": ds,
                          "detail": f"{ds} {label}({hours.fmt(a)}~{hours.fmt(b)}) 식사 항목 없음"})
        if di < spec.nights and (not day or day[-1]["type"] != "stay"):
            v.append({"type": "숙박 누락", "seq": None, "place_name": "", "date": ds, "detail": f"{di + 1}일차 마지막에 숙박 없음"})
    return v


def _v(t: str, it: dict, detail: str) -> dict:
    return {"type": t, "seq": it["seq"], "place_name": it["place_name"], "date": it["date"], "detail": detail}


def feedback_text(viol: list[dict]) -> str:
    return "\n".join(f"- [{x['type']}] {x['date']} {x['place_name']}: {x['detail']}" for x in viol)


# ---------- 마지막 보정(재생성 2회 후에도 위반 시) ----------

def repair(spec: TripSpec, items: list[dict], viol: list[dict], allowed: set[str],
           cands: dict[str, dict] | None = None) -> list[dict]:
    """재생성 2회 후에도 위반이 남을 때의 코드 보정.
    위반 항목 제거(숙박 유지) → 빠진 식사는 '주변에서 식사' 자리표시자 → 시간 창을 넘으면 뒤쪽 관광지부터 덜어 냄."""
    from .schemas import DayPick, PickItem
    bad = {x["seq"] for x in viol if x["seq"] is not None}
    keep = [it for it in items if it["type"] != "move" and (it["seq"] not in bad or it["type"] == "stay")
            and it.get("place_id")]
    wins = windows(spec)
    days: list[list[PickItem]] = []
    for di, d in enumerate(spec.days):
        ds = d.isoformat()
        day = [it for it in keep if it["date"] == ds]
        picks = [PickItem(place_id=it["place_id"], role=_role_of(it), reason=it["reason"],
                          stay_minutes=max(30, hours.parse_hm(it["end_time"]) - hours.parse_hm(it["start_time"])))
                 for it in day if it["type"] != "stay"]
        stay = [PickItem(place_id=it["place_id"], role="stay", stay_minutes=0, reason=it["reason"])
                for it in day if it["type"] == "stay"][:1]
        if di < spec.nights and not stay:
            lodg = next((p for p in (cands or {}).values() if p.get("kind") == "lodging"), None)
            if lodg:
                stay = [PickItem(place_id=lodg["contentid"], role="stay", stay_minutes=0, reason="동선이 가까운 숙소예요")]
        if di >= spec.nights:
            stay = []
        # 빠진 식사 자리표시자: 점심은 시간 순서상 정오 무렵, 저녁은 맨 뒤
        for role, win in (("lunch", LUNCH), ("dinner", DINNER)):
            if meal_required(meal_windows(spec)[di], win) and not any(pk.role == role for pk in picks):
                if role == "lunch":
                    before = [it for it in day if it["type"] != "stay" and hours.parse_hm(it["start_time"]) < 12 * 60]
                    picks.insert(min(len(before), len(picks)), PickItem(place_id=MEAL_ID, role="lunch", stay_minutes=60, reason=""))
                else:
                    picks.append(PickItem(place_id=MEAL_ID, role="dinner", stay_minutes=60, reason=""))
        days.append(picks + stay)
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
        keep = set(spec.waypoint_ids) | {w["place_id"] for w in spec.user_waypoints}   # 사용자가 지정한 곳은 보호
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


def _role_of(it: dict) -> str:
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
    for picks in [*(locked or {}).values(), *(prefix or {}).values()]:
        for pk in picks:
            if pk.place_id in pl:
                cands.setdefault(pk.place_id, pl[pk.place_id])
    allowed = set(cands)
    trace.append({"step": "retrieve", "input": request[:200], "output": f"후보 {len(cands)}곳: " + ", ".join(p["title"] for p in list(cands.values())[:15])})
    feedback, items, viol, regen, last_ranked = None, [], [], 0, []
    for attempt in range(MAX_REGEN + 1):
        picked = pick(spec, cands, request, feedback, base, indoor=indoor)
        last_ranked = picked.ranked or last_ranked
        picked = ensure_waypoints(spec, picked)
        if prefix or indoor:
            from .schemas import DayPick
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
            from .schemas import DayPick
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
