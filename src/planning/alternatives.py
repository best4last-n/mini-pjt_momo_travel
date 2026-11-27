"""일정 속 장소 바꾸기 — 대안 후보 top N (SERVICE.md 3-6 (23)).

숙소·식당·카페·사용자가 넣은 곳은 대안이 많아 바꿀 일이 잦다. 원래 장소 주변에서 같은 종류를 모아 점수로 정렬한다.
- 출처: 수집된 관광공사 장소 → 관광공사 위치기반 검색(locationBasedList2) → 브이월드 장소 검색(개인 카페·식당 포함)
  → (검색어가 있으면) 장소 찾기 결과
- 점수(100): 가까움 30 · 그날 운영 20 · 취향·동반자 15 · 인기·검증 20 · 정보 충실 15
  · 인기: 관광공사 '관광지별 연관 관광지'(T맵 검색량 기반 순위, 활용 신청 시)에 나오면 순위로, 없으면 관광공사 소개 여부
- 평점·리뷰 수: 카카오·네이버·구글 공개 API 키가 없어 받지 않는다(화면에 그대로 밝힘). Google Places는 규칙상 사용 안 함.
"""
from __future__ import annotations

import json
import re
from datetime import date, timedelta
from functools import lru_cache

from src.shared import data, travel
from src.planning import hours
from src.places import placesearch
from src.shared.config import DATA, today

CAFE_RE = re.compile(r"(카페|까페|커피|cafe|coffee|베이커리|bakery|제과|찻집|다원|디저트|티하우스|브런치)", re.I)
LODGE_RE = re.compile(r"(호텔|모텔|펜션|리조트|게스트하우스|한옥스테이|민박|스테이|콘도|글램핑|캠핑)")
NON_LODGE_RE = re.compile(r"(카페|커피|음식|식당|주거|주택|아파트|통신판매|의류|잡화|학교|공장|사무|판매|소매|도매|쇼핑|편의)")
FOOD_RE = re.compile(r"(음식|식당|한식|중식|일식|양식|분식|주점|고기|국밥|횟집|맛집|레스토랑)")
TYPE_ID = {"식당": "39", "카페": "39", "숙소": "32", "관광지": "12"}
VW_QUERY = {"카페": ["카페", "커피"], "식당": ["식당", "음식점"], "숙소": ["펜션", "호텔"], "관광지": ["관광지"]}
RADIUS_KM = {"숙소": 25, "관광지": 20, "식당": 12, "카페": 12}
WIDE_KM = {"숙소": 50}           # 후보가 적으면 넓혀 찾는 범위(숙소는 멀어도 묵을 만하다)
QUERY_KM = {"숙소": 250, "관광지": 250, "식당": 250, "카페": 250}   # 이름으로 찾을 때: 원래 장소에서 250km까지(2026-11-26, 기존 식당·카페 60km)
COMPANION_WORDS = {
    "어린이": "아이|어린이|키즈|체험|놀이|가족", "아이": "아이|어린이|키즈|체험|놀이|가족", "노약자": "주차|휴식|평지|한정식|엘리베이터|부모",
    "부모": "주차|휴식|평지|한정식|엘리베이터|부모", "배우자": "분위기|전망|뷰|야경|산책|감성",
    "연인": "분위기|전망|뷰|야경|산책|감성", "친구": "분위기|전망|뷰|활동|감성",
}
CRITERIA = [{"key": "distance", "label": "가까움", "weight": 30}, {"key": "open", "label": "그날 운영", "weight": 20},
            {"key": "fit", "label": "취향·동반자", "weight": 15}, {"key": "popular", "label": "인기·검증", "weight": 20},
            {"key": "info", "label": "정보 충실", "weight": 15}]
NOTE = ("평점·리뷰 수는 공개 API(카카오·네이버·구글) 키가 없어 받지 못해요. 점수는 원래 장소와의 거리, 그날 운영 여부, "
        "관심사·동반자와 맞는 정도, 인기·검증(관광공사 소개 여부·연관 관광지 순위), 사진·운영시간 등 정보 충실도로 계산했어요.")


def kind_label(place: dict | None, item_type: str | None = None, role: str | None = None) -> str:
    p = place or {}
    text = f"{p.get('title') or p.get('name') or ''} {p.get('vworld_category') or ''}"
    if item_type == "stay" or role == "stay" or p.get("kind") == "lodging" or LODGE_RE.search(text):
        return "숙소"
    if CAFE_RE.search(text):
        return "카페"
    if p.get("kind") == "restaurant" or item_type == "meal" or role in ("lunch", "dinner") or FOOD_RE.search(text):
        return "식당"
    return "관광지"


def swap_hint(place: dict | None, item_type: str | None) -> bool:
    p = place or {}
    return kind_label(p, item_type) != "관광지" or p.get("source") in ("사용자 지정", "직접 추가") or bool(p.get("added_by_user"))


# ---------------- 인기(관광공사 연관 관광지, 활용 신청이 있어야 동작) ----------------

@lru_cache(maxsize=8)
def popular_ranks(region: str) -> dict:
    """이름 키 → 순위(작을수록 인기). 실패하면 {} (서비스 미신청 등)."""
    cache = DATA / "popular_cache.json"
    try:
        allc = json.loads(cache.read_text(encoding="utf-8"))
    except Exception:
        allc = {}
    if region in allc:
        return allc[region]
    import requests
    from src.places.tourlive import _client
    try:
        key = _client().service_key()
    except Exception:
        return {}
    cfg = data.regions_config().get(region) or {"sigungu": []}
    counts: dict[str, list] = {}
    d0 = today().replace(day=1)
    for back in (2, 3, 4):
        ym = (d0 - timedelta(days=28 * back)).strftime("%Y%m")
        ok = False
        for sg in cfg["sigungu"]:
            try:
                r = requests.get("https://apis.data.go.kr/B551011/TarRlteTarService1/areaBasedList1", timeout=15, params={
                    "serviceKey": key, "MobileOS": "ETC", "MobileApp": "momo", "_type": "json", "numOfRows": 1000,
                    "pageNo": 1, "baseYm": ym, "areaCd": str(sg["regn"])[:2], "signguCd": f"{sg['regn']}{sg['signgu']}"[-5:]})
                if r.status_code != 200:
                    return {}
                items = ((((r.json().get("response") or {}).get("body") or {}).get("items") or {}).get("item")) or []
            except Exception:
                return {}
            for it in items if isinstance(items, list) else [items]:
                nm, rk = it.get("rlteTatsNm"), int(it.get("rlteRank") or 99)
                if nm:
                    counts.setdefault(placesearch.name_key(nm), []).append(rk)
                    ok = True
        if ok:
            break
    if not counts:
        return {}
    order = sorted(counts, key=lambda k: (-len(counts[k]), min(counts[k])))
    ranks = {k: i + 1 for i, k in enumerate(order)}
    allc[region] = ranks
    try:
        cache.write_text(json.dumps(allc, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
    return ranks


# ---------------- 후보 모으기 ----------------

@lru_cache(maxsize=128)
def _tour_nearby(lat: float, lng: float, type_id: str, radius_m: int) -> tuple:
    from src.places.tourlive import _client
    try:
        rows = _client().call("locationBasedList2", mapX=lng, mapY=lat, radius=radius_m, contentTypeId=type_id,
                              arrange="E", numOfRows=60, pageNo=1)
        return tuple(json.dumps(r, ensure_ascii=False) for r in rows)
    except Exception:
        return ()


def _matches(kl: str, place_like: dict) -> bool:
    return kind_label(place_like) == kl


def _vw(raw: str, kl: str | None) -> dict | None:
    """브이월드 장소 한 건 → 후보(건물 이름·출입구 제외, kl이 있으면 종류 맞는 것만)."""
    i = json.loads(raw)
    pt = i.get("point") or {}
    try:
        lat, lng = float(pt["y"]), float(pt["x"])
    except (KeyError, ValueError):
        return None
    cat = i.get("category") or ""
    pl = {"title": i.get("title") or "", "lat": lat, "lng": lng, "vworld_category": cat.split(">")[-1].strip(),
          "addr": (i.get("address") or {}).get("road") or (i.get("address") or {}).get("parcel") or ""}
    both = cat + pl["title"]
    if re.search(r"(진출입|건물|주차|사무실|화장실|정류장|입구)", both):
        return None
    if (kl == "숙소" and (not LODGE_RE.search(both) or NON_LODGE_RE.search(cat)) or kl == "카페" and not CAFE_RE.search(both)
            or kl == "식당" and (not FOOD_RE.search(cat) or CAFE_RE.search(cat)) or kl == "관광지" and FOOD_RE.search(cat)):
        return None
    return {"src": "브이월드", "p": pl, "key": f"vworld:{i.get('id') or (pl['title'], round(lat, 5))}"}


@lru_cache(maxsize=16)
def _tour_area(region: str, type_id: str) -> tuple:
    """지역 시군구 전체의 같은 종류(숙소 등) — 위치기반 20km 한계를 넘어 넓게."""
    from src.places.tourlive import _client
    cfg = data.regions_config().get(region) or {"sigungu": []}
    rows = []
    for sg in cfg["sigungu"]:
        try:
            rows += _client().call("areaBasedList2", contentTypeId=type_id, lDongRegnCd=sg["regn"], lDongSignguCd=sg["signgu"],
                                   arrange="Q", numOfRows=100, pageNo=1)
        except Exception:
            continue
    return tuple(json.dumps(r, ensure_ascii=False) for r in rows)


def _gather_wide(spec, target: dict, kl: str) -> list[dict]:
    near = (target["lat"], target["lng"])
    out = []
    for raw in _tour_area(spec.region, TYPE_ID[kl]):
        h = json.loads(raw)
        if h.get("mapy"):
            out.append({"src": "관광공사", "place_id": h["contentid"], "wide": True, "p": {
                "title": h["title"], "lat": float(h["mapy"]), "lng": float(h["mapx"]), "addr": h.get("addr1"),
                "image_url": h.get("firstimage2") or h.get("firstimage") or None, "kind": "lodging"}})
    for word in VW_QUERY[kl] + ["리조트", "한옥", "게스트하우스"]:
        for raw in placesearch.vworld_search(word, "place", round(near[0], 2), round(near[1], 2), 0.4, size=40):
            c = _vw(raw, kl)
            if c:
                out.append({**c, "wide": True})
    return out


def _gather(spec, target: dict, kl: str, q: str) -> list[dict]:
    near = (target["lat"], target["lng"])
    rkm = RADIUS_KM[kl]
    out: list[dict] = []
    for pid, p in data.places(spec.region).items():               # ① 수집 데이터
        if p.get("lat") and _matches(kl, p):
            out.append({"src": "관광공사", "place_id": pid, "p": p})
    tid = TYPE_ID[kl]                                               # ② 관광공사 위치기반
    for raw in _tour_nearby(round(near[0], 4), round(near[1], 4), tid, rkm * 1000):
        h = json.loads(raw)
        if not h.get("mapy"):
            continue
        is_cafe = h.get("cat3") == "A05020900" or bool(CAFE_RE.search(h.get("title") or ""))
        if kl == "카페" and not is_cafe or kl == "식당" and is_cafe:
            continue
        out.append({"src": "관광공사", "place_id": h["contentid"], "p": {
            "title": h["title"], "lat": float(h["mapy"]), "lng": float(h["mapx"]), "addr": h.get("addr1"),
            "image_url": h.get("firstimage2") or h.get("firstimage") or None, "kind": {"39": "restaurant", "32": "lodging"}.get(tid, "attraction")}})
    if q:          # 검색어가 있으면 그 결과만(종류 제한 없이, 원래 장소 근처 우선)
        out = []
        # 원래 장소 근처(±22km)만 보던 제약 때문에 라플라타(구례)·한씨네대가추어탕(순천)이 안 나옴(2026-11-26)
        # → 근처 + 전국(브이월드는 bbox 없이) 둘 다 찾고, 거리는 QUERY_KM(250km)으로 자른다
        seen_vw = set()
        for raw in (placesearch.vworld_search(q, "place", round(near[0], 3), round(near[1], 3), 0.2, size=40)
                    + placesearch.vworld_search(q, "place", size=40)):
            c = _vw(raw, "숙소" if kl == "숙소" else None)
            if c and (c["p"]["title"], round(c["p"]["lat"], 4)) not in seen_vw:
                seen_vw.add((c["p"]["title"], round(c["p"]["lat"], 4)))
                out.append({**c, "query_hit": True})
        res = placesearch.search(q, spec.region, near=near, limit=20, expand=True)
        for c in (res.get("results") or []) + (res.get("far") or []):
            nm_cat = f"{c['name']} {c.get('vworld_category') or ''}"
            if re.search(r"(진출입|입구|주차|정류장|사무실|화장실)", nm_cat):
                continue
            vcat = c.get("vworld_category") or ""
            if kl == "숙소" and (NON_LODGE_RE.search(vcat) or not (c.get("category") == "lodging" or LODGE_RE.search(nm_cat)
                                                              or (c.get("similarity") or 0) >= 0.85)):
                continue          # 숙소를 찾는데 식당·가게가 섞이지 않게(이름이 거의 같으면 예외: 예) 쌍산재)
            pl = {"title": c["name"], "lat": c["lat"], "lng": c["lng"], "addr": c.get("address"), "image_url": c.get("image"),
                  "vworld_category": (c.get("vworld_category") or "").split(">")[-1].strip() or None}
            out.append({"src": c["source"], "place_id": c.get("place_id"), "p": pl, "cand": c, "query_hit": True})
        return out
    for word in VW_QUERY[kl]:                                        # ③ 브이월드(개인 카페·식당 포함)
        for raw in placesearch.vworld_search(word, "place", round(near[0], 3), round(near[1], 3), 0.12, size=40):
            c = _vw(raw, kl)
            if c:
                out.append(c)
    return out


def _score(spec, d: date, target: dict, kl: str, c: dict, ranks: dict) -> dict | None:
    p = c["p"]
    dist = travel.km((target["lat"], target["lng"]), (p["lat"], p["lng"]))
    lim = (QUERY_KM.get(kl, placesearch.MAX_KM) if c.get("query_hit")        # 직접 찾은 이름은 여행 지역 전체(숙소는 더 멀리)
           else WIDE_KM.get(kl, RADIUS_KM[kl]) if c.get("wide") else RADIUS_KM[kl])
    if dist > lim:
        return None
    s_dist = round(30 * max(0.0, 1 - dist / lim))
    st, htxt = "확인 필요", None
    if c["src"] == "관광공사" and c.get("place_id") in data.places(spec.region):
        info = hours.parse_open(data.places(spec.region)[c["place_id"]], d)
        st = info.status
        if info.open is not None and st == "확인됨":
            htxt = f"{hours.fmt(info.open)}~{hours.fmt(info.close)}"
    s_open = {"확인됨": 20, "확인 필요": 10, "휴무": 0}.get(st, 10)
    text = " ".join(str(x) for x in (p.get("title"), p.get("summary"), p.get("highlights"), p.get("companion_notes"),
                                     p.get("vworld_category")) if x)
    hits = [w for w in (spec.preferences or []) if w and w in text]
    comp = next((v for k, v in COMPANION_WORDS.items() if k in (spec.companion_type or spec.companions or "")), None)
    chit = re.search(comp, text) if comp else None
    s_fit = min(15, 5 + 4 * len(hits) + (4 if chit else 0))
    rk = ranks.get(placesearch.name_key(p["title"]))
    if rk:
        s_pop = round(20 * max(0.3, 1 - (rk - 1) / 60))
    else:
        s_pop = 12 if c["src"] == "관광공사" and c.get("place_id") in data.places(spec.region) else 8 if c["src"] == "관광공사" else 4
    img = p.get("image_url") or ((p.get("images") or [{}])[0].get("url"))
    s_info = (6 if img else 0) + (5 if htxt else 0) + (4 if p.get("summary") else 0)
    parts = {"distance": s_dist, "open": s_open, "fit": s_fit, "popular": s_pop, "info": s_info}
    why = [f"원래 곳에서 {dist:.1f}km", ({"확인됨": "체크인 정보 있음", "휴무": "그날 휴무", "확인 필요": "체크인 확인 필요"} if kl == "숙소"
                                       else {"확인됨": "그날 영업", "휴무": "그날 휴무", "확인 필요": "운영시간 확인 필요"})[st]]
    if hits:
        why.append("관심사 " + "·".join(hits[:2]))
    if chit:
        why.append(f"동반자에 맞는 '{chit.group(0)}'")
    if rk:
        why.append(f"연관 관광지 인기 {rk}위")
    choice = ({"place_id": c["place_id"]} if c["src"].startswith("관광공사") and c.get("place_id") else
              {"candidate": {"name": p["title"], "lat": p["lat"], "lng": p["lng"], "address": (p.get("addr") or "")[:60],
                             "source": c["src"], "key": c.get("key") or (c.get("cand") or {}).get("key"),
                             "image": img, "category": "lodging" if kl == "숙소" else "restaurant" if kl in ("식당", "카페") else "attraction",
                             "osm_ref": (c.get("cand") or {}).get("osm_ref")}})
    if dist > RADIUS_KM[kl]:
        why.insert(0, f"조금 멀어요(약 {dist:.0f}km)")
    return {"score": sum(parts.values()), "parts": parts, "name": p["title"], "far": dist > placesearch.MAX_KM,
            "category": p.get("vworld_category") or kl, "address": (p.get("addr") or "")[:60], "distance_km": round(dist, 1),
            "lat": p["lat"], "lng": p["lng"], "image": img, "image_source": "한국관광공사" if img and c["src"].startswith("관광공사") else None,
            "hours": htxt, "hours_status": st, "source": c["src"], "popular_rank": rk, "rating": None, "review_count": None,
            "why": " · ".join(why), "choice": choice}


def alternatives(spec, itinerary: list[dict], place_id: str, q: str = "", limit: int = 20) -> dict:
    item = next((it for it in itinerary if it.get("place_id") == place_id), None)
    if not item:
        return {"error": "그 장소는 일정에 없어요.", "items": []}
    pl = data.places(spec.region).get(place_id) or next((w for w in spec.user_waypoints if w["place_id"] == place_id), {})
    lat, lng = item.get("lat") or pl.get("lat"), item.get("lng") or pl.get("lng")
    if not lat:
        lat, lng = data.region_center(spec.region)
    kl = kind_label(pl or {"title": item.get("place_name")}, item.get("type"))
    d = date.fromisoformat(item["date"]) if item.get("date") else spec.start
    target = {"place_id": place_id, "name": item.get("place_name"), "kind_label": kl, "lat": lat, "lng": lng,
              "day": (d - spec.start).days + 1, "arrive": item.get("start_time")}
    ranks = popular_ranks(spec.region)
    taken = {it.get("place_id") for it in itinerary if it.get("place_id")} | {place_id}
    seen, rows = [], []
    for c in _gather(spec, target, kl, (q or "").strip()[:40]):
        if (c.get("place_id") and c["place_id"] in taken) or c["p"].get("title") == target["name"]:
            continue
        k = placesearch.name_key(c["p"]["title"])
        if any(k == k2 and travel.km((c["p"]["lat"], c["p"]["lng"]), xy) < 1 for k2, xy in seen):
            continue
        r = _score(spec, d, target, kl, c, ranks)
        if r:
            seen.append((k, (c["p"]["lat"], c["p"]["lng"])))
            rows.append(r)
    if kl in WIDE_KM and not q and len(rows) < 10:          # 근처에 숙소가 적으면 지역 전체·더 넓게
        for c in _gather_wide(spec, target, kl):
            if (c.get("place_id") and c["place_id"] in taken) or c["p"].get("title") == target["name"]:
                continue
            k = placesearch.name_key(c["p"]["title"])
            if any(k == k2 and travel.km((c["p"]["lat"], c["p"]["lng"]), xy) < 1 for k2, xy in seen):
                continue
            r = _score(spec, d, target, kl, c, ranks)
            if r:
                seen.append((k, (c["p"]["lat"], c["p"]["lng"])))
                rows.append(r)
        widened = True
    else:
        widened = False
    if q:      # 이름으로 찾았으면 이름이 비슷한 순서가 먼저
        for r in rows:
            r["similarity"] = round(placesearch.similarity(q, r["name"]), 2)
        rows.sort(key=lambda r: (-round(r["similarity"], 1), -r["score"], r["distance_km"]))
    else:
        rows.sort(key=lambda r: (-r["score"], r["distance_km"]))
    top = rows[:limit]
    missing = [r for r in top if not r.get("address")]
    if missing:          # 주소 없는 후보(관광공사·브이월드 일부)는 좌표로 주소를 채워 어느 지역인지 보이게(2026-11-26 사용자 요청)
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=8) as ex:
            for r, addr in zip(missing, ex.map(lambda r: placesearch.vworld_address(round(r["lat"], 5), round(r["lng"], 5)), missing)):
                r["address"] = (addr or "")[:60]
    for i, r in enumerate(top, 1):
        r["rank"] = i
        r["area"] = placesearch.area_of(r.get("address"))
    return {"target": target, "criteria": CRITERIA, "items": rows[:limit], "total_found": len(rows),
            "sources": sorted({r["source"] for r in rows}), "has_reviews": False, "widened": widened,
            "note": NOTE + (f" 근처에 숙소가 적어 {WIDE_KM[kl]}km 안까지 넓혀 찾았어요." if widened else "")
            + (" 숙소는 이름으로 찾으면 전국까지 찾고, 먼 곳은 거리를 함께 보여 드려요." if kl == "숙소" else ""),
            "popular_source": "관광공사 연관 관광지(T맵 검색량)" if ranks else None}
