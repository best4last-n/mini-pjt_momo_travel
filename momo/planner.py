"""일정 생성 = LLM 선택(장소·순서·체류·이유) + 코드 시각 계산 + 코드 규칙 검사 + 재생성(최대 2회).

SERVICE.md 3-4b·3-4c·3-4e·5절 규칙 검사(하드 게이트):
  휴무일 배치 · 운영시간 밖 · 시간 겹침(앞 종료+이동+10분 > 다음 시작) · 검색 결과에 없는 장소
  + 현지 시간 창 · 식사(점심/저녁) · 1박 숙박 · '확인 필요' 하루 1곳/식사·마지막 금지
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date

from . import data, hours, retrieve, travel
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
    transport: str = "자차"
    companions: str | None = None
    preferences: list[str] = field(default_factory=list)
    many_places: bool = False
    waypoint_ids: list[str] = field(default_factory=list)       # 수집 데이터에 있는 경유지
    user_waypoints: list[dict] = field(default_factory=list)    # OSM으로 찾은 사용자 지정 장소
    exclude_ids: list[str] = field(default_factory=list)

    @property
    def days(self) -> list[date]:
        return hours.dates_of(self.start, self.nights + 1)


# ---------- 시간 창 ----------

def one_way_minutes(spec: TripSpec) -> int | None:
    if not spec.departure or spec.departure not in data.SIDO_COORDS:
        return None
    return travel.intercity_minutes(data.SIDO_COORDS[spec.departure], data.region_center(spec.region))


def windows(spec: TripSpec) -> list[tuple[int, int]]:
    ow = one_way_minutes(spec)
    out = []
    n = spec.nights + 1
    for i in range(n):
        s, e = DAY_START, DAY_END
        if ow is not None:
            if i == 0:
                s = max(DAY_START, spec.depart_time + ow + BUFFER_MIN)
            if i == n - 1:
                e = min(DAY_END, HOME_LIMIT - ow)
        out.append((_ceil5(s), e))
    return out


def _ceil5(m: int) -> int:
    return int(math.ceil(m / 5) * 5)


def max_places(win: tuple[int, int], n_meals: int, many: bool = False) -> int:
    """시간 창에 들어갈 관광지 수 어림(체류 75분 + 이동·여유 25분)."""
    free = win[1] - win[0] - n_meals * 80
    return max(1, min(7 if many else 5, free // (100 if not many else 80)))


def meal_required(win: tuple[int, int], meal: tuple[int, int]) -> bool:
    return min(win[1], meal[1]) - max(win[0], meal[0]) >= 30


# ---------- 후보 ----------

def candidates(spec: TripSpec, query: str) -> dict[str, dict]:
    """LLM에 보여 줄 후보(= '검색 결과'). 모든 날 휴무인 곳은 뺀다."""
    region = spec.region
    q = " ".join([query, spec.companions or "", *spec.preferences]).strip()
    picked: dict[str, dict] = {}
    for kinds, k in ((["attraction", "festival"], 18), (["restaurant"], 12), (["lodging"], 5 if spec.nights else 0)):
        if k:
            for d in retrieve.search(q, region, kinds, k=k):
                picked[d["doc_id"]] = d
    pl = data.places(region)
    for pid, p in pl.items():           # 대표 명소는 항상 후보에
        if p.get("must_include") and p["kind"] == "attraction" and pid not in picked:
            picked[pid] = {"doc_id": pid, "text": "", "metadata": {"title": p["title"], "kind": p["kind"]}}
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
        summ = (p.get("summary") or "")[:90]
        lines.append(f"- id={pid} | {p['title']} | {KIND_KO[p['kind']]} | 좌표 {p.get('lat', 0):.3f},{p.get('lng', 0):.3f} "
                     f"| {' / '.join(per_day)} | {summ}")
    return "\n".join(lines)


# ---------- LLM 선택 ----------

PICK_SYSTEM = MOMO_PERSONA + """
지금은 일정의 뼈대만 고른다. 반드시 후보 목록의 id만 쓴다(목록에 없는 장소 금지).
규칙:
- 하루 관광지 3~5곳(많이 원해도 최대 7곳), 가까운 곳끼리 묶어 이동을 줄인다(좌표 참고).
- 하루 시간 창 안에서 점심(role=lunch, 12~14시)·저녁(role=dinner, 18~20시)이 필요하다고 주어지면 음식점을 넣는다.
- 점심·저녁은 식사가 되는 음식점으로 고른다(카페·베이커리·디저트 전문점은 식사 자리에 넣지 않는다).
- 1박이면 1일차 마지막에 숙박(role=stay) 1곳.
- 해당 일차에 '휴무'인 곳은 그날 넣지 않는다. '운영시간 확인 필요'는 하루 최대 1곳, 식사·마지막 자리에 두지 않는다.
- 운영시간이 짧은 곳(예: 17시 마감)은 그날 앞쪽에 둔다. 체류시간은 관광지 60~120분, 식사 60분.
- reason은 후보 정보(요약·종류)에 근거한 한 줄. 지어내지 않는다.
"""


def pick(spec: TripSpec, cands: dict[str, dict], request: str, feedback: str | None = None,
         base: list[dict] | None = None) -> PlanPick:
    wins = windows(spec)
    day_lines = []
    for i, (d, w) in enumerate(zip(spec.days, wins), 1):
        meals = [n for n, m in (("점심", LUNCH), ("저녁", DINNER)) if meal_required(w, m)]
        cap = max_places(w, len(meals), spec.many_places)
        day_lines.append(f"{i}일차 {d.isoformat()}({'월화수목금토일'[d.weekday()]}) 시간 창 {hours.fmt(w[0])}~{hours.fmt(w[1])}"
                         f" · 필요한 식사: {', '.join(meals) or '없음'} · 관광지는 최대 {cap}곳(시간 창을 넘기면 규칙 위반)")
    msg = [f"요청: <data>{request}</data>",
           f"동행: {spec.companions or '정보 없음'} · 취향: {', '.join(spec.preferences) or '없음'}",
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
    if feedback:
        msg.append(f"직전 초안의 규칙 위반(반드시 고칠 것):\n{feedback}")
    msg.append(f"후보 목록 <data>\n{candidate_lines(spec, cands)}\n</data>")
    return structured(PlanPick, temperature=0.2, max_tokens=3000).invoke(
        [("system", PICK_SYSTEM), ("human", "\n".join(msg))])


# ---------- 코드 시각 계산 ----------

def _coords(p: dict | None) -> tuple | None:
    return (p.get("lat"), p.get("lng")) if p and p.get("lat") else None


def schedule(spec: TripSpec, plan: PlanPick, cands: dict[str, dict]) -> list[dict]:
    wins = windows(spec)
    pl = data.places(spec.region)
    user_wp = {w["place_id"]: w for w in spec.user_waypoints}
    ow = one_way_minutes(spec)
    items: list[dict] = []
    mode = "car"
    for di, d in enumerate(spec.days):
        w = wins[di]
        day = next((x for x in plan.days if x.day == di + 1), None)
        picks = day.items if day else []
        if ow is not None and di == 0:
            items.append(_move(d, f"{spec.departure} → {data.region_label(spec.region)}", spec.depart_time,
                               spec.depart_time + ow, "출발"))
        cursor, prev = w[0], None
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
            if prev is None:
                tmin, tsrc = 0, "estimate"
            elif placeholder or prev.get("source") == "식사 안내":
                tmin, tsrc = 10, "estimate"
            else:
                tmin, tsrc = travel.local_minutes(prev.get("contentid"), p.get("contentid"), _coords(prev), _coords(p))
            start = cursor + (tmin + BUFFER_MIN if prev is not None else 0)
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
        if ow is not None and di == len(spec.days) - 1:
            back = max(cursor, w[0])
            items.append(_move(d, f"{data.region_label(spec.region)} → {spec.departure}", back, back + ow, "귀가"))
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
    wins = windows(spec)
    for di, d in enumerate(spec.days):
        ds = d.isoformat()
        day = [it for it in items if it["date"] == ds and it["type"] != "move"]
        w = wins[di]
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
            if it["type"] != "stay" and (s < w[0] or e > w[1]):
                v.append(_v("시간 창 밖", it, f"현지 가능 시간 {hours.fmt(w[0])}~{hours.fmt(w[1])}"))
            if it["type"] == "stay" and s > HOME_LIMIT:
                v.append(_v("시간 창 밖", it, "숙소 도착이 22:00 이후"))
            prev = it
        if check_cnt > 1:
            v.append({"type": "확인 필요 초과", "seq": None, "place_name": "", "date": ds,
                      "detail": f"{ds} 운영시간 확인 필요 장소 {check_cnt}곳(하루 최대 1곳)"})
        meals = [hours.parse_hm(it["start_time"]) for it in day if it["type"] == "meal"]
        for label, (a, b) in (("점심", LUNCH), ("저녁", DINNER)):
            if meal_required(w, (a, b)) and not any(a - 30 <= m < b for m in meals):
                v.append({"type": "식사 누락", "seq": None, "place_name": "", "date": ds,
                          "detail": f"{ds} {label}({hours.fmt(a)}~{hours.fmt(b)}) 식사 항목 없음"})
        if spec.nights and di == 0 and (not day or day[-1]["type"] != "stay"):
            v.append({"type": "숙박 누락", "seq": None, "place_name": "", "date": ds, "detail": "1일차 마지막에 숙박 없음"})
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
        if spec.nights and di == 0 and not stay:
            lodg = next((p for p in (cands or {}).values() if p.get("kind") == "lodging"), None)
            if lodg:
                stay = [PickItem(place_id=lodg["contentid"], role="stay", stay_minutes=0, reason="동선이 가까운 숙소예요")]
        if not (spec.nights and di == 0):
            stay = []
        # 빠진 식사 자리표시자: 점심은 시간 순서상 정오 무렵, 저녁은 맨 뒤
        for role, win in (("lunch", LUNCH), ("dinner", DINNER)):
            if meal_required(wins[di], win) and not any(pk.role == role for pk in picks):
                if role == "lunch":
                    before = [it for it in day if it["type"] != "stay" and hours.parse_hm(it["start_time"]) < 12 * 60]
                    picks.insert(min(len(before), len(picks)), PickItem(place_id=MEAL_ID, role="lunch", stay_minutes=60, reason=""))
                else:
                    picks.append(PickItem(place_id=MEAL_ID, role="dinner", stay_minutes=60, reason=""))
        days.append(picks + stay)
    out: list[dict] = []
    for _ in range(8):     # 시간 창 밖·겹침이 남으면 식사 아닌 마지막 관광지를 하나씩 덜어 냄
        out = schedule(spec, PlanPick(days=[DayPick(day=i + 1, items=x) for i, x in enumerate(days)]), {})
        v = [x for x in validate(spec, out, allowed | {MEAL_ID}) if x["type"] in ("시간 창 밖", "시간 겹침", "운영시간 밖", "식사 누락")]
        if not v:
            break
        dates = [d.isoformat() for d in spec.days]
        di = dates.index(v[0]["date"])
        idx = max((i for i, pk in enumerate(days[di]) if pk.role == "place"), default=None)
        if idx is None:
            break
        days[di].pop(idx)
    return out


def _role_of(it: dict) -> str:
    if it["type"] == "meal":
        return "lunch" if hours.parse_hm(it["start_time"]) < 16 * 60 else "dinner"
    return "stay" if it["type"] == "stay" else "place"


# ---------- 전체 루프 ----------

def build_plan(spec: TripSpec, request: str, base: list[dict] | None = None, trace: list | None = None,
               locked: dict | None = None) -> dict:
    """locked: {일차: [PickItem]} — 수정 대상이 아닌 날은 기존 선택 그대로 고정."""
    trace = trace if trace is not None else []
    cands = candidates(spec, request)
    for w in spec.user_waypoints:
        cands.setdefault(w["place_id"], w)
    pl = data.places(spec.region)
    for picks in (locked or {}).values():
        for pk in picks:
            if pk.place_id in pl:
                cands.setdefault(pk.place_id, pl[pk.place_id])
    allowed = set(cands)
    trace.append({"step": "retrieve", "input": request[:200], "output": f"후보 {len(cands)}곳: " + ", ".join(p["title"] for p in list(cands.values())[:15])})
    feedback, items, viol, regen = None, [], [], 0
    for attempt in range(MAX_REGEN + 1):
        picked = pick(spec, cands, request, feedback, base)
        if locked:
            from .schemas import DayPick
            picked.days = [DayPick(day=d, items=locked[d]) if d in locked else
                           next((x for x in picked.days if x.day == d), DayPick(day=d, items=[]))
                           for d in range(1, spec.nights + 2)]
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
            "candidates": cands, "note": None}
