"""브로셔 계획서 데이터(plan) — guide/travel_planner_system_prompt.md 10절 스키마 (SERVICE.md 3-6 (4)(7)).

코드가 itinerary(시각 계산·규칙 검사 완료)와 수집 데이터로 조립한다. LLM은 순위·이유만 제공.
화면·내보내기(HTML·DOCX·인쇄)·공유 페이지가 모두 이 구조를 쓴다.
"""
from __future__ import annotations

from collections import Counter
from datetime import date

from . import data, hours, travel

KIND_KO = {"attraction": "관광", "restaurant": "음식점", "lodging": "숙박", "festival": "축제"}
CHECK_OPTIONS = {
    "intensity": ["빡빡", "보통", "여유"],
    "companions": ["보통", "노약자", "어린이·유아"],
    "long_transport": ["자차", "KTX·SRT", "고속·시외버스"],
    "local_transport": ["자차", "렌터카", "대중교통"],
}
RURAL = {"suncheon_gurye"}   # 대중교통 배차가 드문 군 지역 포함 권역


def category(p: dict) -> str:
    if p.get("contenttypeid") == "14":
        return "문화시설"
    if p.get("kind") == "attraction" and p.get("contenttypeid") == "28":
        return "레포츠"
    return KIND_KO.get(p.get("kind"), "관광")


def _coord(p: dict | None):
    return (p.get("lat"), p.get("lng")) if p and p.get("lat") else None


def nearby(p: dict, region: str, kind: str, k: int = 3, max_km: float = 3.0) -> list[dict]:
    c = _coord(p)
    if not c:
        return []
    rows = []
    for q in data.places(region).values():
        if q["contentid"] == p.get("contentid") or q["kind"] != kind or not _coord(q):
            continue
        d = travel.km(c, _coord(q))
        if d <= max_km:
            rows.append((d, q))
    rows.sort(key=lambda x: x[0])
    out = []
    for d, q in rows[:k]:
        item = {"name": q["title"], "distance_km": round(d, 1), "place_id": q["contentid"],
                "note": (q.get("summary") or "")[:60]}
        if kind == "restaurant":
            intro = q.get("intro") or {}
            item["kind"] = "식당"
            item["note"] = hours.clean(intro.get("firstmenu") or intro.get("treatmenu") or "")[:40] or item["note"]
        out.append(item)
    return out


def place_entry(p: dict, spec, rank: int | None, reason: str, in_itin: bool, on_date) -> dict:
    info = hours.parse_open(p, on_date) if on_date else None
    hours_text = None
    if info and info.status == "확인됨":
        hours_text = ("상시 개방" if (info.open, info.close) == (0, 1440)
                      else (f"체크인 {hours.fmt(info.open)}" if p.get("kind") == "lodging"
                            else f"{hours.fmt(info.open)}~{hours.fmt(info.close)}"))
    imgs = p.get("images") or ([{"url": p["image_url"], "source": "tourapi", "license": p.get("image_license"),
                                 "credit": "한국관광공사"}] if p.get("image_url") else [])
    cn = p.get("companion_notes") or {}
    cnote = {"노약자": cn.get("elder"), "어린이·유아": cn.get("child")}.get(spec.companion_type) or ""
    intro = p.get("intro") or {}
    return {
        "id": p["contentid"], "rank": rank, "name": p["title"], "category": category(p),
        "address": p.get("addr") or "", "lat": p.get("lat"), "lng": p.get("lng"),
        "tour_content_id": p["contentid"], "in_itinerary": in_itin, "reason": reason or "",
        "summary": p.get("summary") or "", "highlights": p.get("highlights") or [],
        "guide_tips": p.get("guide_tips") or "", "stay_min": None,
        "hours": hours_text or (hours.clean(intro.get("usetime") or intro.get("opentimefood")) or None),
        "hours_status": info.status if info else None,
        "closed": hours.clean(intro.get("restdate") or intro.get("restdatefood")) or None,
        "fee": hours.clean(intro.get("usefee")) or None,
        "parking": hours.clean(intro.get("parking") or intro.get("parkingfood")) or None,
        "contact": hours.clean(intro.get("infocenter") or intro.get("infocenterfood") or p.get("tel")) or None,
        "companion_notes": cnote, "images": imgs[:4],
        "nearby_sights": nearby(p, spec.region, "attraction") if p.get("kind") != "lodging" else [],
        "foods": nearby(p, spec.region, "restaurant") if p.get("kind") in ("attraction", "festival", "lodging") else [],
        "source": "사용자 지정" if p.get("source") == "사용자 지정" else "한국관광공사 TourAPI",
        "added_by_user": bool(p.get("added_by_user") or p.get("source") == "사용자 지정"),
    }


TRANSPORT_HINT = {
    "suncheon_gurye": "구례는 관광지 사이 버스가 드물어요. KTX로 순천역까지 온 뒤 역에서 렌터카를 빌리는 조합을 가장 추천해요.",
    "gyeongju": "KTX 신경주역에서 렌터카를 빌리거나 자차가 편해요. 대릉원·첨성대·동궁과 월지 일대는 걸어서 돌 수 있어요.",
    "suwon": "수도권에서는 전철(수원역)로 충분해요. 화성 성곽 일대는 걸어서, 광교 쪽은 버스·택시로 이동해요.",
}


def _status(key: str, assumed: set, missing: bool = False) -> str:
    """신호등: confirmed(초록, 사용자가 말함) / assumed(노랑, 기본값) / missing(빨강, 결과에 영향이 큰데 비어 있음)."""
    if missing:
        return "missing"
    return "assumed" if key in assumed else "confirmed"


def checklist(spec) -> list[dict]:
    """여행 계획 확정 요소(가이드 3절 필수 7개 + 선택 관심사). 화면 오른쪽 정보창에서 신호등으로 보이고 눌러서 바꾼다."""
    a = set(spec.assumptions)
    length = "당일치기" if spec.nights == 0 else f"{spec.nights}박 {spec.nights + 1}일"
    no_origin = not spec.departure
    date_txt = f"{spec.start.month}월 {spec.start.day}일({'월화수목금토일'[spec.start.weekday()]})"
    return [
        {"key": "region", "label": "여행 지역", "value": data.region_label(spec.region), "status": "confirmed",
         "input": "select", "options": [data.region_label(r) for r in data.available_regions()],
         "hint": "지역을 바꾸면 일정을 새로 짜요."},
        {"key": "start_date", "label": "출발일", "value": date_txt, "iso": spec.start.isoformat(),
         "status": _status("date", a), "input": "date", "options": [],
         "hint": "날짜에 따라 휴무일·운영시간·축제가 달라져요."},
        {"key": "length", "label": "여행 길이", "value": length, "status": _status("length", a), "input": "select",
         "options": ["당일치기", "1박 2일"], "hint": "지금은 당일~1박 2일까지 꼼꼼히 검증해요."},
        {"key": "origin", "label": "출발지", "value": spec.origin_label or spec.departure or "미정",
         "status": _status("origin", a, missing=no_origin), "input": "text",
         "options": ["서울", "경기 수원", "대전", "광주", "부산", "대구"],
         "hint": "오가는 시간을 계산해 첫날·마지막 날 일정을 맞춰요. 비어 있으면 현지 도착 기준으로만 짜요."},
        {"key": "long_transport", "label": "장거리 이동", "value": spec.long_transport,
         "status": _status("long_transport", a, missing=no_origin and "long_transport" in a), "input": "select",
         "options": CHECK_OPTIONS["long_transport"], "hint": TRANSPORT_HINT.get(spec.region, "")},
        {"key": "local_transport", "label": "현지 이동", "value": spec.local_transport,
         "status": _status("local_transport", a), "input": "select", "options": CHECK_OPTIONS["local_transport"],
         "hint": TRANSPORT_HINT.get(spec.region, "")},
        {"key": "intensity", "label": "여행 강도", "value": spec.intensity, "status": _status("intensity", a),
         "input": "select", "options": CHECK_OPTIONS["intensity"],
         "hint": "빡빡 하루 4~5곳(08~20시) · 보통 3~4곳(09~18:30) · 여유 2~3곳(10~17:30)"},
        {"key": "companions", "label": "동반자", "value": spec.companion_type
         + (f" {spec.companion_count}명" if spec.companion_count else "")
         + (f" · {spec.companions}" if spec.companions else ""), "status": _status("companions", a),
         "input": "select", "options": CHECK_OPTIONS["companions"],
         "hint": "노약자는 계단·경사 적은 곳, 어린이는 체험·넓은 야외를 우선해요."},
        {"key": "budget", "label": "최대 예산", "value": spec.budget_label or "미정",
         "status": "confirmed" if spec.budget else "missing", "input": "text",
         "options": ["30만 원", "50만 원", "100만 원", "1인 20만 원"],
         "hint": "교통·유류비·통행료·식비·입장료·숙박을 더한 예상 경비와 비교해 넘치면 알려 드려요."},
        {"key": "interests", "label": "관심사 (선택)", "value": ", ".join(spec.preferences) or "없음",
         "status": "optional", "input": "text", "options": ["자연", "사찰", "역사", "체험", "카페", "사진", "맛집"],
         "hint": "말씀하시면 추천 순위에 반영해요."},
    ]


def progress(conds: list[dict]) -> dict:
    """확정 진행률: 필수 확정 요소(선택 항목 제외) 중 사용자가 확정한 비율. 가정·미정이 남아도 그대로 확정 가능."""
    req = [c for c in conds if c["status"] != "optional"]
    done = [c for c in req if c["status"] == "confirmed"]
    pct = round(100 * len(done) / len(req)) if req else 0
    return {"percent": pct, "confirmed": len(done), "total": len(req),
            "assumed": [c["label"] for c in req if c["status"] == "assumed"],
            "missing": [c["label"] for c in req if c["status"] == "missing"],
            "label": "100% 확정 완료" if pct == 100 else f"{pct}% 진행 중"}


def build(spec, items: list[dict], ranked: list[tuple[str, str]], violations: list[dict], notes: list[str]) -> dict:
    pl = data.places(spec.region)
    user = {w["place_id"]: w for w in spec.user_waypoints}
    itin_ids = [it["place_id"] for it in items if it.get("place_id") and it["type"] != "move"]
    first_date = {}
    for it in items:
        if it.get("place_id"):
            first_date.setdefault(it["place_id"], date.fromisoformat(it["date"]))
    reasons = {it["place_id"]: it.get("reason") for it in items if it.get("place_id")}
    rank_of = {pid: i + 1 for i, (pid, _) in enumerate(ranked)}
    reasons.update({pid: r for pid, r in ranked if r and pid not in reasons})
    places, seen = [], set()
    for pid in [*[p for p, _ in ranked], *itin_ids]:
        if pid in seen:
            continue
        p = pl.get(pid) or user.get(pid)
        if not p:
            continue
        seen.add(pid)
        places.append(place_entry(p, spec, rank_of.get(pid), reasons.get(pid, ""), pid in itin_ids,
                                  first_date.get(pid, spec.start)))
    # 일정·구간
    mode_ko = "대중교통" if spec.local_transport == "대중교통" else "자동차"
    days, tot_km, tot_min = [], 0.0, 0
    long = {"outbound": None, "inbound": None}
    stay_prev = None
    center = data.region_center(spec.region)
    for di, d in enumerate(spec.days):
        ds = d.isoformat()
        day_items = [it for it in items if it["date"] == ds]
        stops, legs = [], []
        prev = stay_prev if di > 0 else None
        prev_name = stay_prev["place_name"] if (di > 0 and stay_prev) else None
        for it in day_items:
            if it["type"] == "move":
                key = "outbound" if (it["notes"] and it["notes"][0] == "출발") else "inbound"
                long[key] = {"mode": spec.long_transport, "label": it["place_name"], "depart": it["start_time"],
                             "arrive": it["end_time"],
                             "duration_min": hours.parse_hm(it["end_time"]) - hours.parse_hm(it["start_time"]),
                             "estimated": True}
                if key == "outbound":
                    prev, prev_name = {"lat": center[0], "lng": center[1]}, "도착 지점"
                continue
            stop = {"order": len(stops) + 1, "place_id": it.get("place_id"), "name": it["place_name"],
                    "type": it["type"], "arrive": it["start_time"], "depart": it["end_time"],
                    "stay_min": hours.parse_hm(it["end_time"]) - hours.parse_hm(it["start_time"]),
                    "hours_status": it["hours_status"], "reason": it.get("reason") or "",
                    "lat": it.get("lat"), "lng": it.get("lng"), "source": it.get("source")}
            stops.append(stop)
            if prev is not None and it["travel_minutes_from_prev"]:
                a, b = _coord(prev), _coord(it)
                dist = travel.distance_km(a, b) or 0.0
                legs.append({"from": prev.get("place_id") or prev_name, "from_name": prev_name, "to": it.get("place_id"),
                             "to_name": it["place_name"], "mode": mode_ko, "distance_km": dist,
                             "duration_min": it["travel_minutes_from_prev"], "estimated": True,
                             "path": [list(a), list(b)] if a and b else []})
            prev, prev_name = it, it["place_name"]
            if it["type"] == "stay":
                stay_prev = it
        dkm = round(sum(x["distance_km"] for x in legs), 1)
        dmin = sum(x["duration_min"] for x in legs)
        tot_km += dkm
        tot_min += dmin
        sg = Counter((pl.get(s["place_id"] or "") or {}).get("sigungu") for s in stops if s["type"] == "place")
        sg.pop(None, None)
        theme = f"{sg.most_common(1)[0][0]} 권역" if sg else data.region_label(spec.region)
        days.append({"day": di + 1, "date": ds, "weekday": "월화수목금토일"[d.weekday()], "theme": theme,
                     "stops": stops, "legs": legs, "day_distance_km": dkm, "day_travel_min": dmin})
    warnings = [f"{v['place_name']} {v['detail']}".strip() for v in violations]
    cap_min = {"보통": 60, "여유": 40}.get(spec.intensity)
    for day in days:
        for lg in day["legs"]:
            if cap_min and lg["duration_min"] > cap_min:
                warnings.append(f"{day['day']}일차 {lg['from_name']} → {lg['to_name']} 이동이 약 {lg['duration_min']}분으로 "
                                f"'{spec.intensity}' 강도 권장({cap_min}분)보다 길어요")
    if spec.local_transport == "대중교통" and spec.region in RURAL:
        warnings.append("구례 등 군 지역은 버스 배차가 드물어요. 막차·배차를 확인하시고 필요하면 택시를 함께 쓰세요")
    for p in places:
        if p["in_itinerary"] and spec.companion_type == "노약자" and any(
                w in (p["companion_notes"] or "") for w in ("계단", "경사", "오르막", "등산", "가파")):
            warnings.append(f"{p['name']}: {p['companion_notes']}")
    length = "당일치기" if spec.nights == 0 else f"{spec.nights}박 {spec.nights + 1}일"
    out = {
        "trip": {
            "title": f"{data.region_label(spec.region)} {length}", "regions": [data.region_label(spec.region)],
            "region_id": spec.region, "start_date": spec.start.isoformat(), "nights": spec.nights,
            "days": spec.nights + 1, "origin": spec.origin_label or spec.departure,
            "transport": {"long_distance": spec.long_transport, "local": spec.local_transport,
                          "note": "모든 이동시간은 직선거리 기반 추정"},
            "intensity": spec.intensity,
            "companions": {"type": spec.companion_type, "count": spec.companion_count, "note": spec.companions or ""},
            "assumptions": notes, "checklist": checklist(spec),
            "progress": None,
            "route": {"optimized": bool(getattr(spec, "route_saved_min", 0)), "saved_min": getattr(spec, "route_saved_min", 0),
                      "user_ordered_days": list(getattr(spec, "user_ordered_days", [])),
                      "note": (f"동선을 정리해 이동을 약 {spec.route_saved_min}분 줄였어요" if getattr(spec, "route_saved_min", 0) else "")
                      + (" · 직접 조정하신 날은 그 순서를 그대로 지켜요" if getattr(spec, "user_ordered_days", []) else "")},
        },
        "places": places,
        "itinerary": days,
        "totals": {"local": {"distance_km": round(tot_km, 1), "travel_min": tot_min}, "long_distance": long},
        "warnings": warnings,
    }
    out["trip"]["progress"] = progress(out["trip"]["checklist"])
    from . import costs
    out["costs"] = costs.estimate(spec, out)
    if out["costs"]["over_budget"]:
        warnings.append(f"예상 경비 {costs.fmt_won(out['costs']['total'])}가 예산 {costs.fmt_won(out['costs']['budget'])}보다 "
                        f"{costs.fmt_won(out['costs']['over_by'])} 많아요. 숙소 등급이나 이동수단, 일정 수를 조정해 볼까요?")
    return out
