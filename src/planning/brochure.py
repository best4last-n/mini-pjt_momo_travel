"""브로셔 계획서 데이터(plan) — guide/travel_planner_system_prompt.md 10절 스키마 (SERVICE.md 3-6 (4)(7)).

코드가 itinerary(시각 계산·규칙 검사 완료)와 수집 데이터로 조립한다. LLM은 순위·이유만 제공.
화면·내보내기(HTML·DOCX·인쇄)·공유 페이지가 모두 이 구조를 쓴다.
"""
from __future__ import annotations

import re
from collections import Counter
from datetime import date

from src.planning import alternatives, hours
from src.shared import data, travel

KIND_KO = {"attraction": "관광", "restaurant": "음식점", "lodging": "숙박", "festival": "축제"}
CHECK_OPTIONS = {
    "intensity": ["강행군", "빡빡", "보통", "여유"],
    "companions": ["보통", "노약자", "어린이·유아"],
    "long_transport": ["자차", "KTX·SRT", "고속·시외버스", "비행기"],
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


_URL_CH = r"[A-Za-z0-9\-._~:/?#@!$&*+,;=%]"
_URL_RE = re.compile(r"(https?://" + _URL_CH + r"+|(?:www\.)?[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:kr|com|net|org)(?:/" + _URL_CH + r"*)?)", re.I)


def links_of(p: dict) -> list[dict]:
    """관광공사 homepage 필드(형식 제각각: <a href>, 'URL', '이름 URL', 'www.도메인') → [{label, url}] (중복 제거).
    링크 앞에 이름이 있으면 그 이름으로(예: '수원관광 홈페이지'), 없으면 '공식 홈페이지'. 사용자 지정 장소는 place_url."""
    raw = p.get("homepage") or ""
    text = re.sub(r"<a [^>]*href=[\"']([^\"']+)[\"'][^>]*>.*?</a>", r" \1 ", raw, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    out, seen, pos = [], set(), 0
    for m in _URL_RE.finditer(text):
        u = m.group(0).strip().rstrip(".,;")
        before = text[pos:m.start()]
        pos = m.end()
        if "@" in u or len(u) < 6:
            continue
        if not u.lower().startswith(("http://", "https://")):
            u = "https://" + u
        key = re.sub(r"^https?://(www\.)?", "", u.lower()).rstrip("/")
        if key in seen:
            continue
        seen.add(key)
        low = u.lower()
        prefix = re.sub(r"(공식|홈페이지|:|\s)+", " ", before).strip().split(" ")[-1][:12] if before.strip() else ""
        label = ("인스타그램" if "instagram." in low else "블로그" if "blog." in low else "페이스북" if "facebook." in low
                 else "유튜브" if "youtube." in low else f"{prefix} 홈페이지" if prefix
                 else "지자체 관광 안내" if re.match(r"https?://(www|main|culture)\.[a-z]+\.go\.kr(/tour|/)?/?$", low) else "공식 홈페이지")
        out.append({"label": label, "url": u})
    if p.get("place_url") and str(p["place_url"]).startswith("https://"):
        out.append({"label": "지도에서 보기", "url": p["place_url"]})
    return out[:4]


def fee_check(p: dict) -> bool:
    """입장료를 확인해야 하는 곳: 관광지인데 요금 정보가 없거나 '문의·변동·별도' 같은 말이 있는 곳."""
    if p.get("kind") not in ("attraction", "festival"):
        return False
    fee = hours.clean((p.get("intro") or {}).get("usefee")) or ""
    if not fee:
        return True
    return bool(re.search(r"(문의|변동|별도|상이|홈페이지|확인|유료)", fee)) and not re.fullmatch(r"\s*무료\s*", fee)


def lodging_times(p: dict) -> dict:
    """체크인·체크아웃(관광공사 숙박 소개, 없으면 일반적인 15:00/11:00)·조식 단서."""
    intro = p.get("intro") or {}
    cin, cout = hours.clean(intro.get("checkintime")) or "", hours.clean(intro.get("checkouttime")) or ""
    m_in, m_out = re.search(r"\d{1,2}:\d{2}", cin), re.search(r"\d{1,2}:\d{2}", cout)
    facil = " ".join(str(intro.get(k) or "") for k in ("subfacility", "foodplace"))
    return {"checkin": m_in.group(0) if m_in else "15:00", "checkout": m_out.group(0) if m_out else "11:00",
            "checkin_known": bool(m_in), "checkout_known": bool(m_out),
            "breakfast_hint": bool(re.search(r"(조식|레스토랑|뷔페|식당)", facil))}


def _lodging_notices(spec, days: list, places_by_id: dict) -> None:
    """숙소 체크인·체크아웃·연박 안내를 날짜별로 붙인다."""
    stays = []
    for d in days:
        st = next((s for s in d["stops"] if s["type"] == "stay"), None)
        stays.append(st)
    for i, d in enumerate(days):
        st = stays[i]
        if i < spec.nights:      # 다음 날로 이어지는 밤: 숙소 / 사용자가 고른 예외(차박·밤샘·야간 이동) / 빠짐(알림)
            mode = (getattr(spec, "no_stay", None) or {}).get(str(i + 1))
            d["night"] = ({"status": "stay"} if st else {"status": "none", "mode": mode} if mode
                          else {"status": "missing", "message": "다음 날까지 여행이 이어져요. 이 밤 숙소를 넣어 주세요 — 차박·밤샘·야간 이동이면 '숙박 필요 없음'을 골라 주세요."})
            d["night"]["options"] = ["차박", "밤샘", "야간 이동"]
        if st:
            p = places_by_id.get(st.get("place_id")) or {}
            t = lodging_times(p)
            prev_same = i > 0 and stays[i - 1] and stays[i - 1].get("place_id") == st.get("place_id")
            nights = 1
            j = i + 1
            while j < len(stays) and stays[j] and stays[j].get("place_id") == st.get("place_id"):
                nights, j = nights + 1, j + 1
            st.update({"checkin": t["checkin"], "checkout": t["checkout"], "checkin_known": t["checkin_known"],
                       "checkout_known": t["checkout_known"], "breakfast_hint": t["breakfast_hint"],
                       "consecutive": bool(prev_same), "nights_here": nights if not prev_same else None})
            if not prev_same:
                st["stay_note"] = (f"체크인 {t['checkin']}부터" + (f" · {nights}박 연속" if nights > 1 else "")
                                   + (" · 조식 정보 있음(숙소 확인)" if t["breakfast_hint"] else ""))
            else:
                st["stay_note"] = "어제와 같은 숙소(연박) — 체크인 없이 바로 쉬어요"
        if i > 0 and stays[i - 1]:
            prev = stays[i - 1]
            p = places_by_id.get(prev.get("place_id")) or {}
            t = lodging_times(p)
            tonight = stays[i] if i < len(stays) else None
            if tonight and tonight.get("place_id") == prev.get("place_id"):
                d["lodging_notice"] = {"kind": "연박", "title": "오늘도 같은 숙소예요",
                                       "message": f"오늘 밤도 {prev['name']}에서 묵어요. 큰 짐은 숙소에 두고 가볍게 나서세요(귀중품만 챙기기)."}
            elif tonight:
                d["lodging_notice"] = {"kind": "이동", "title": "오늘은 숙소를 옮겨요", "checkout": t["checkout"],
                                       "message": f"짐을 모두 챙겨 {t['checkout']}까지 {prev['name']}에서 체크아웃하세요. "
                                                  f"새 숙소 {tonight['name']} 체크인은 {lodging_times(places_by_id.get(tonight.get('place_id')) or {})['checkin']}부터예요."}
            else:
                d["lodging_notice"] = {"kind": "마지막", "title": "마지막 날 · 체크아웃", "checkout": t["checkout"],
                                       "message": f"짐을 모두 챙겨 {t['checkout']}까지 {prev['name']}에서 체크아웃하세요. "
                                                  "충전기·세면도구처럼 두고 오기 쉬운 물건을 한 번 더 확인해요!"}


def _planner():
    from src.planning import planner
    return planner


def _stopovers(spec) -> dict:
    from src.planning import stopovers
    try:
        sugg = stopovers.suggest(spec)
    except Exception:
        sugg = []
    added = {s.get("key") for s in spec.return_stops}
    for s in sugg:
        s["added"] = s["key"] in added
    return {"items": sugg, "added": list(spec.return_stops),
            "note": "여행 끝이 아쉬우면 돌아가는 길에 잠깐 들러 보세요. 우회 시간은 직선거리 기준 추정이에요."}


def _with_home(spec, days: list, long: dict) -> list:
    """1일차 맨 앞에 출발지(예: 판교동), 마지막 날 맨 뒤에 여행 후 도착지를 붙인다(지도·타임라인 표시용)."""
    from src.planning import planner
    if not days:
        return days
    _lodging_notices(spec, days, {**data.places(spec.region), **{w["place_id"]: w for w in spec.user_waypoints}})
    o, e = planner.origin_point(spec), planner.end_point(spec)
    ob, ib = long.get("outbound"), long.get("inbound")
    if o:
        days[0]["start_point"] = {**o, "kind": "home", "role": "출발", "time": (ob or {}).get("depart") or hours.fmt(spec.depart_time),
                                  "leg": ob}
    if e:
        days[-1]["end_point"] = {**e, "kind": "home", "role": "도착", "time": (ib or {}).get("arrive"), "leg": ib}
    return days


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
        "links": links_of(p), "homepage": (links_of(p) or [{}])[0].get("url"), "fee_check": fee_check(p),
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
    "gangneung": "KTX 강릉역에서 렌터카를 빌리면 편해요. 강릉·동해·삼척은 해안을 따라 길게 이어져 있어 하루에 한 도시씩 도는 걸 추천해요.",
    "jeonju": "전주 시내(한옥마을 일대)는 걸어서·버스·택시로 충분해요. 군산·완주까지 가려면 KTX 전주역에서 렌터카를 빌리는 게 편해요.",
    "tongyeong_geoje": "통영·거제는 KTX가 닿지 않아(진주역에서 1시간 안팎) 자차나 렌터카가 가장 편해요. 통영 시내는 케이블카·동피랑 일대를 걸어서 돌 수 있어요.",
    "jeju": "제주는 비행기로 가야 해요(제주공항 도착 후 렌터카가 가장 편해요). 동쪽·서쪽·남쪽이 멀어 하루에 한 방향만 도는 게 좋아요.",
    "suwon": "수도권에서는 전철(수원역)로 충분해요. 화성 성곽 일대는 걸어서, 광교 쪽은 버스·택시로 이동해요.",
}


def _vehicle_label(v: dict | None) -> str:
    if not v:
        return "미입력(중형 휘발유 기준)"
    from src.counsel.options import efficiency
    eff = v.get("efficiency_kmpl") or efficiency(v.get("type"), v.get("fuel"))
    parts = [v.get("type") or "중형", v.get("fuel") or "휘발유", f"{eff:g}km/{'kWh' if v.get('fuel') == '전기' else 'L'}"]
    for k in ("toll_discount", "parking_discount"):
        if v.get(k) and v[k] != "없음":
            parts.append(("통행료 " if k == "toll_discount" else "주차 ") + v[k])
    return " · ".join(parts)


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
         "options": ["당일치기", "1박 2일", "2박 3일", "3박 4일", "4박 5일", "6박 7일"], "hint": "최대 8박 9일까지 휴무일·운영시간을 검증해요. 길수록 한 지역 안에서 일정이 여유로워져요."},
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
         "hint": "강행군 하루 최대 25곳(06:30~23:00, 새벽형·야행성 · 숙소는 다음 날 02시까지) · 빡빡 하루 4~5곳(08~20시) · 보통 3~4곳(09~18:30) · 여유 2~3곳(10~17:30)"},
        {"key": "companions", "label": "동반자", "value": spec.companion_type
         + (f" {spec.companion_count}명" if spec.companion_count else "")
         + (f" · {spec.companions}" if spec.companions else ""), "status": _status("companions", a),
         "input": "select", "options": CHECK_OPTIONS["companions"],
         "hint": "노약자는 계단·경사 적은 곳, 어린이는 체험·넓은 야외를 우선해요."},
        {"key": "depart_time", "label": "출발 시각", "value": hours.fmt(spec.depart_time),
         "status": "confirmed" if "depart_time" in spec.given else "optional", "input": "time", "options": ["06:30", "07:00", "08:00", "09:00"],
         "hint": "출발 시각으로 첫날 현지 도착과 일정 시작을 맞춰요."},
        {"key": "arrival", "label": "여행 후 도착", "value": (spec.end_label or spec.end_location or spec.origin_label or spec.departure or "출발지")
         + (f" · {hours.fmt(spec.arrive_time)}까지" if spec.arrive_time else " · 22:00까지"),
         "status": "confirmed" if (spec.end_location or spec.arrive_time) else "optional", "input": "text",
         "options": ["출발지로", "21:00까지", "20:00까지"], "hint": "도착지·희망 시각에 맞춰 마지막 날 일정을 끝내요."},
        *([{"key": "vehicle", "label": "차량", "value": _vehicle_label(spec.vehicle),
            "status": "confirmed" if spec.vehicle else "optional", "input": "vehicle", "options": [],
            "hint": "차종·연료·연비·할인으로 유류비·통행료·주차비를 계산해요."}]
          if "자차" in (spec.long_transport, spec.local_transport) or spec.local_transport == "렌터카" else []),
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
        route_prev = None             # 가는 길/오는 길에 들른 곳(동선에 포함: 집 → 들를 곳 → … → 첫 장소)
        if di == 0:
            hp = _planner().origin_point(spec)
            if hp and hp.get("lat") is not None:
                route_prev = {"lat": hp["lat"], "lng": hp["lng"], "place_name": hp.get("name") or "출발 지점", "end_time": None, "home": True}
        for it in day_items:
            leg0 = (it.get("notes") or [""])[0]
            if it["type"] == "move" and leg0 in ("가는 길", "오는 길"):     # 출발 지점 다음·복귀 지점 앞(장거리 이동 중)
                rp = route_prev if leg0 == "가는 길" else (prev if prev is not None else None)
                if it.get("lat") is not None and rp is not None and rp.get("lat") is not None:
                    a, b = _coord(rp), _coord(it)
                    legs.append({"from": rp.get("place_id") or rp.get("place_name"), "from_name": rp.get("place_name"), "to": None,
                                 "to_name": it["place_name"], "mode": spec.long_transport if leg0 == "가는 길" else mode_ko,
                                 "distance_km": travel.distance_km(a, b) or 0.0,
                                 "duration_min": it.get("travel_minutes_from_prev") or 0, "estimated": True, "long": True,
                                 "home_leg": bool(rp.get("home")),       # 출발지 → 첫 들를 곳은 출발 카드가 보여 주는 구간
                                 "path": [list(a), list(b)] if a and b else []})
                if it.get("lat") is not None:
                    route_prev = it
                    if leg0 == "가는 길":
                        prev, prev_name = it, it["place_name"]
                stops.append({"order": len(stops) + 1, "place_id": None, "name": it["place_name"], "type": it.get("route_kind") or "place",
                              "arrive": it["start_time"], "depart": it["end_time"],
                              "arrive_label": hours.label(it["start_time"]), "depart_label": hours.label(it["end_time"]),
                              "stay_min": hours.parse_hm(it["end_time"]) - hours.parse_hm(it["start_time"]),
                              "hours_status": "확인 필요" if it.get("route_key") else "확인됨", "reason": it.get("reason") or "",
                              "lat": it.get("lat"), "lng": it.get("lng"), "source": it.get("source"),
                              "route": "out" if leg0 == "가는 길" else "back", "route_key": it.get("route_key"),
                              "route_meal": it.get("route_meal"), "route_stay": it.get("route_stay"),
                              "swap_hint": False, "swap_kind": None, "homepage": None, "fee_check": False})
                continue
            if it["type"] == "move" and leg0 == _planner().NIGHT_RETURN:     # 체크인 뒤 야간 일정 → 숙소로 돌아가기
                stops.append({"order": len(stops) + 1, "place_id": None, "name": it["place_name"], "type": "place",
                              "arrive": it["start_time"], "depart": it["end_time"],
                              "arrive_label": hours.label(it["start_time"]), "depart_label": hours.label(it["end_time"]),
                              "stay_min": 0, "hours_status": "확인됨", "reason": it.get("reason") or "", "lat": it.get("lat"),
                              "lng": it.get("lng"), "source": it.get("source"), "lodge_return": True, "night": True,
                              "swap_hint": False, "swap_kind": None, "homepage": None, "fee_check": False})
                if prev is not None and it["travel_minutes_from_prev"]:
                    a, b = _coord(prev), _coord(it)
                    legs.append({"from": prev.get("place_id") or prev_name, "from_name": prev_name, "to": None, "to_name": it["place_name"],
                                 "mode": mode_ko, "distance_km": travel.distance_km(a, b) or 0.0,
                                 "duration_min": it["travel_minutes_from_prev"], "estimated": True,
                                 "path": [list(a), list(b)] if a and b else []})
                continue
            if it["type"] == "move":
                key = "outbound" if (it["notes"] and it["notes"][0] == "출발") else "inbound"
                pl_ = _planner()
                seg = bool(it.get("route_segmented"))      # 들를 곳이 있으면 이 카드는 한 구간(출발지→첫 들를 곳 / 마지막 들른 곳→복귀 지점)
                drv = (hours.parse_hm(it["end_time"]) - hours.parse_hm(it["start_time"])) if seg else (
                    pl_.one_way_minutes(spec) if key == "outbound" else pl_._return_base(spec))
                long[key] = {"mode": spec.long_transport, "label": it["place_name"], "depart": it["start_time"], "segmented": seg,
                             "drive_min": drv, "drive_custom": bool((spec.long_override or {}).get("out" if key == "outbound" else "back"))
                             and not (seg and key == "outbound"),
                             "arrive": it["end_time"],
                             "duration_min": hours.parse_hm(it["end_time"]) - hours.parse_hm(it["start_time"]),
                             "estimated": True}
                if key == "outbound" and not seg:
                    prev, prev_name = {"lat": center[0], "lng": center[1]}, "도착 지점"
                continue
            from_route = prev is not None and (prev.get("notes") or [""])[0] == "가는 길"
            night = "야간" in (it.get("notes") or [])      # 체크인 뒤 저녁·야간 일정
            stop = {"order": len(stops) + 1, "place_id": it.get("place_id"), "name": it["place_name"],
                    "type": it["type"], "arrive": it["start_time"], "depart": it["end_time"],
                    "arrive_label": hours.label(it["start_time"]), "depart_label": hours.label(it["end_time"]),
                    "stay_min": hours.parse_hm(it["end_time"]) - hours.parse_hm(it["start_time"]),
                    "hours_status": it["hours_status"], "reason": it.get("reason") or "",
                    "lat": it.get("lat"), "lng": it.get("lng"), "source": it.get("source")}
            if night:
                stop["night"] = True
            _pl = data.places(spec.region).get(it.get("place_id")) or next(
                (w for w in spec.user_waypoints if w["place_id"] == it.get("place_id")), {"title": it["place_name"]})
            if (it.get("place_id") or "").startswith("FREE:"):
                stop.update(free=True, swap_hint=False, swap_kind=None, homepage=None, fee_check=False)
                stops.append(stop)
                prev, prev_name = (prev or it), (prev_name or it["place_name"])
                continue
            ko = (getattr(spec, "kind_override", None) or {}).get(it.get("place_id"))
            if ko:
                stop["kind_override"] = ko
            if (it.get("place_id") or "").startswith(_planner().VISIT_PREFIX):     # 숙소 다시 들르기
                stop.update(visit=True, swap_hint=False, swap_kind=None, homepage=None, fee_check=False)
                stops.append(stop)
                if prev is not None and it["travel_minutes_from_prev"]:
                    a, b = _coord(prev), _coord(it)
                    legs.append({"from": prev.get("place_id") or prev_name, "from_name": prev_name, "to": it.get("place_id"),
                                 "to_name": it["place_name"], "mode": mode_ko, "distance_km": travel.distance_km(a, b) or 0.0,
                                 "duration_min": it["travel_minutes_from_prev"], "estimated": True, "path": [list(a), list(b)] if a and b else []})
                prev, prev_name = it, it["place_name"]
                continue
            stop["swap_kind"] = alternatives.kind_label(_pl, it["type"])
            stop["homepage"] = (links_of(_pl) or [{}])[0].get("url")
            stop["fee_check"] = fee_check(_pl)
            stop["swap_hint"] = alternatives.swap_hint(_pl, it["type"])
            stops.append(stop)
            if prev is not None and (it["travel_minutes_from_prev"] or from_route):
                a, b = _coord(prev), _coord(it)
                dist = travel.distance_km(a, b) or 0.0
                # 가는 길에 들른 곳 → 여행지 첫 장소: 남은 운전 시간(들른 곳을 떠난 시각 → 도착 시각)
                dur = (hours.parse_hm(it["start_time"]) - hours.parse_hm(prev["end_time"])) if from_route else it["travel_minutes_from_prev"]
                legs.append({"from": prev.get("place_id") or prev_name, "from_name": prev_name, "to": it.get("place_id"),
                             "to_name": it["place_name"], "mode": spec.long_transport if from_route else mode_ko, "distance_km": dist,
                             "duration_min": max(0, dur), "estimated": True, "long": from_route,
                             **({"leg_key": "out", "drive_custom": bool((spec.long_override or {}).get("out"))} if from_route else {}),
                             "path": [list(a), list(b)] if a and b else []})
            prev, prev_name = it, it["place_name"]
            if it["type"] == "stay":
                stay_prev = it
        # 장거리(가는 길·오는 길) 구간은 동선에는 보이되 현지 이동 합계·유류비에서는 뺀다(장거리 비용은 따로 계산)
        dkm = round(sum(x["distance_km"] for x in legs if not x.get("long")), 1)
        dmin = sum(x["duration_min"] for x in legs if not x.get("long"))
        tot_km += dkm
        tot_min += dmin
        sg = Counter((pl.get(s["place_id"] or "") or {}).get("sigungu") for s in stops if s["type"] == "place")
        sg.pop(None, None)
        theme = f"{sg.most_common(1)[0][0]} 권역" if sg else data.region_label(spec.region)
        days.append({"day": di + 1, "date": ds, "weekday": "월화수목금토일"[d.weekday()], "theme": theme,
                     "stops": stops, "legs": legs, "day_distance_km": dkm, "day_travel_min": dmin})
    warnings = [f"{v['place_name']} {v['detail']}".strip() for v in violations]
    warnings += _planner().meal_advice(spec, items)        # 직접 정한 순서의 식사 시간 안내(위반 아님)
    cap_min = {"보통": 60, "여유": 40}.get(spec.intensity)
    for day in days:
        for lg in day["legs"]:
            if cap_min and lg["duration_min"] > cap_min and not lg.get("long"):
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
            "title": spec.title or f"{data.region_label(spec.region)} {length}", "title_custom": bool(spec.title),
            "default_title": f"{data.region_label(spec.region)} {length}", "regions": [data.region_label(spec.region)],
            "region_id": spec.region, "start_date": spec.start.isoformat(), "nights": spec.nights,
            "days": spec.nights + 1, "origin": spec.origin_label or spec.departure,
            "origin_point": _planner().origin_point(spec), "end_point": _planner().end_point(spec),
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
        "itinerary": _with_home(spec, days, long),
        "return_stopovers": _stopovers(spec),
        "booked_stay": getattr(spec, "booked_stay", None),
        "meals": {"breakfast": spec.breakfast, "breakfast_skip_days": list(spec.breakfast_skip_days),
                  "options": ["근처 식당", "숙소 조식", "안 먹음"],
                  "note": "숙박 다음 날 아침은 기본으로 넣어요. 숙소 조식이면 이동이 없어 30분 늦게 시작해도 돼요. 필요 없으면 빼 주세요."},
        "totals": {"local": {"distance_km": round(tot_km, 1), "travel_min": tot_min}, "long_distance": long},
        "warnings": warnings,
    }
    out["trip"]["progress"] = progress(out["trip"]["checklist"])
    from src.mobility import hubs
    from src.packing import packing
    from src.planning import planner as _pl
    out["weather"] = packing.weather_days(spec.region, spec.days)
    out["transport_guide"] = hubs.guide(spec, _pl.one_way_minutes(spec))
    if out["transport_guide"] and out["transport_guide"].get("fuel"):
        for f in out["transport_guide"]["fuel"][:2]:
            warnings.append(f"⛽ {f['at']}: {f['text']}")
    out["packing"] = packing.build(spec, out, out["weather"])
    from src.budget import costs
    out["costs"] = costs.estimate(spec, out)
    if out["costs"]["over_budget"]:
        warnings.append(f"예상 경비 {costs.fmt_won(out['costs']['total'])}가 예산 {costs.fmt_won(out['costs']['budget'])}보다 "
                        f"{costs.fmt_won(out['costs']['over_by'])} 많아요. 숙소 등급이나 이동수단, 일정 수를 조정해 볼까요?")
    out["suggest"] = over_cap_suggest(spec, items)
    return out


def over_cap_suggest(spec, items: list[dict]) -> list[dict]:
    """강도 권장보다 관광지가 많은 날 — 화면에서 '후보로 돌리기/그대로 둘게요'를 고르게 하는 선택지(2026-11-27).
    체크인 뒤 야간 일정·개인 시간·숙소 들르기·'그대로 둘게요'를 고른 날은 빼고, 꼭 가겠다고 한 곳은 뒤로."""
    from src.planning import planner as _pl
    cap = _pl.INTENSITY.get(spec.intensity, _pl.INTENSITY["보통"])[2]
    must = set(spec.waypoint_ids or [])
    out = []
    for di, d in enumerate(spec.days, 1):
        ds = d.isoformat()
        pls = [it for it in items if it.get("date") == ds and it.get("type") == "place" and it.get("place_id")
               and not str(it["place_id"]).startswith(("FREE:", "AGAIN:")) and "야간" not in (it.get("notes") or [])]
        if len(pls) > cap and di not in (spec.over_ok_days or []):
            pls.sort(key=lambda it: it["place_id"] in must)
            out.append({"type": "over_cap", "day": di, "count": len(pls), "cap": cap, "intensity": spec.intensity,
                        "places": [{"place_id": it["place_id"], "name": it.get("place_name") or "", "must": it["place_id"] in must}
                                   for it in pls]})
    return out
