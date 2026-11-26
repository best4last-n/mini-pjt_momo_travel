"""장소 찾기(정확한 이름을 몰라도) — SERVICE.md 3-6 (15).

사용자가 '사성', '구례 절벽 암자'처럼 대충 말해도 비슷한 곳을 모아 **가까운 순**으로 보여 주고,
거리·대략 주소·사진 미리보기로 고르게 한다. 출처 우선순위:
  ① 수집된 관광공사 장소(이름 유사도 + 하이브리드 검색) ② 관광공사 실시간 키워드 검색(지역 → 전국)
  ③ OpenStreetMap(Photon: 철자 유사·위치 가중, Nominatim 보조) + 위키미디어 공용 사진(Wikidata P18)
Google Places는 프로젝트 규칙(AGENTS.md)상 사용하지 않는다. 외부 호출은 결과 캐시.
"""
from __future__ import annotations

import difflib
import json
import re
from functools import lru_cache

import requests

from . import data, retrieve, travel

UA = {"User-Agent": "momo-travel-counselor/0.1 (education project)"}
MAX_KM = 60


# 상호명 정규화: 업종어(카페·cafe·커피·베이커리·식당·본점·점 …)는 붙든 빠지든 같은 이름으로,
# 숫자 ↔ 한글 숫자 읽기(1001 ↔ 일공공일), 공백·기호·대소문자 무시. 예) 라플라타카페 → 라플라타, 일공공일 카페 → 1001
GENERIC = ["카페", "까페", "cafe", "café", "coffee", "커피", "커피숍", "베이커리", "bakery", "디저트", "dessert",
           "식당", "레스토랑", "restaurant", "본점", "지점", "점", "맛집", "brunch", "브런치", "라운지", "lounge", "하우스", "house"]
HANGUL_DIGIT = {"공": "0", "영": "0", "일": "1", "이": "2", "삼": "3", "사": "4", "오": "5", "육": "6", "칠": "7", "팔": "8", "구": "9"}
DIGIT_HANGUL = {"0": "공", "1": "일", "2": "이", "3": "삼", "4": "사", "5": "오", "6": "육", "7": "칠", "8": "팔", "9": "구"}
_GEN_RE = re.compile("(" + "|".join(sorted((re.escape(g) for g in GENERIC), key=len, reverse=True)) + ")", re.I)


def _strip_generic(t: str) -> str:
    out = _GEN_RE.sub(" ", t or "")
    return out if out.strip() else (t or "")


def _hangul_num_to_digits(t: str) -> str:
    """3글자 이상 연속된 한글 숫자 읽기(공·영 포함)를 숫자로: 일공공일 → 1001."""
    def rep(m):
        run = m.group(0)
        return "".join(HANGUL_DIGIT[c] for c in run) if ("공" in run or "영" in run or len(run) >= 4) else run
    return re.sub(r"[공영일이삼사오육칠팔구]{3,}", rep, t or "")


def _norm(t: str) -> str:
    t = re.sub(r"\(.*?\)|\[.*?\]", "", t or "").lower()
    return re.sub(r"[\s·,.\-_'’!&]+", "", t)


def name_key(t: str) -> str:
    """비교용 핵심 이름: 업종어 제거 → 한글 숫자 → 숫자 → 공백·기호 제거."""
    return _norm(_hangul_num_to_digits(_strip_generic(t)))


def variants(q: str) -> list[str]:
    """검색어 변형(최대 4개): 원문, 업종어 뺀 이름, 숫자→한글 읽기, 한글 숫자→숫자."""
    q = (q or "").strip()
    base = re.sub(r"\s+", " ", _strip_generic(q)).strip()
    cands = [q, base]
    if re.search(r"\d", base):
        cands.append(re.sub(r"\d", lambda m: DIGIT_HANGUL[m.group(0)], base))
    digit = _hangul_num_to_digits(base)
    if digit != base:
        cands.append(digit)
    out = []
    for c in cands:
        c = c.strip()
        if c and len(c) >= 2 and c.lower() not in [x.lower() for x in out]:
            out.append(c)
    return out[:4]


def similarity(q: str, name: str) -> float:
    a, b = name_key(q), name_key(name)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if a in b or b in a:   # 일부만 겹치면 겹친 비율만큼(사성→사성암 0.87, 1001→1001안경콘택트… 0.71)
        return 0.6 + 0.4 * min(len(a), len(b)) / max(len(a), len(b))
    return difflib.SequenceMatcher(None, a, b).ratio()


def _cand(src, name, lat, lng, addr, near, sim, **kw):
    dist = travel.km(near, (lat, lng)) if (lat and lng and near) else None
    return {"source": src, "name": name, "lat": lat, "lng": lng, "address": (addr or "")[:60],
            "distance_km": round(dist, 1) if dist is not None else None, "similarity": round(sim, 2), **kw}


@lru_cache(maxsize=256)
def _photon(q: str, lat: float, lng: float) -> tuple:
    try:
        r = requests.get("https://photon.komoot.io/api/", timeout=6, headers=UA,
                         params={"q": q, "lat": lat, "lon": lng, "limit": 10})
        return tuple(json.dumps(f, ensure_ascii=False) for f in r.json().get("features", []))
    except Exception:
        return ()


@lru_cache(maxsize=256)
def _nominatim(q: str, lat: float, lng: float) -> tuple:
    try:
        r = requests.get("https://nominatim.openstreetmap.org/search", timeout=6, headers=UA, params={
            "q": q, "format": "json", "limit": 8, "countrycodes": "kr", "addressdetails": 0,
            "viewbox": f"{lng - 0.7},{lat + 0.6},{lng + 0.7},{lat - 0.6}", "bounded": 0})
        return tuple(json.dumps(x, ensure_ascii=False) for x in r.json())
    except Exception:
        return ()


def _wikimedia_images(osm_refs: list[str]) -> dict:
    """osm 참조(N123/W456/R789) → 위키미디어 공용 사진 썸네일 URL(Wikidata P18). 없으면 비움."""
    if not osm_refs:
        return {}
    try:
        r = requests.get("https://nominatim.openstreetmap.org/lookup", timeout=6, headers=UA,
                         params={"osm_ids": ",".join(osm_refs[:20]), "format": "json", "extratags": 1})
        rows = r.json()
        qids = {f"{x['osm_type'][0].upper()}{x['osm_id']}": (x.get("extratags") or {}).get("wikidata") for x in rows}
        ids = [q for q in qids.values() if q]
        if not ids:
            return {}
        w = requests.get("https://www.wikidata.org/w/api.php", timeout=6, headers=UA, params={
            "action": "wbgetentities", "ids": "|".join(ids[:40]), "props": "claims", "format": "json"}).json()
        img = {}
        for qid, ent in (w.get("entities") or {}).items():
            p18 = ((ent.get("claims") or {}).get("P18") or [{}])[0]
            fn = (((p18.get("mainsnak") or {}).get("datavalue") or {}).get("value"))
            if fn:
                img[qid] = "https://commons.wikimedia.org/wiki/Special:FilePath/" + requests.utils.quote(fn) + "?width=360"
        return {ref: img.get(q) for ref, q in qids.items() if q and img.get(q)}
    except Exception:
        return {}


FOOD_VALUES = {"cafe", "restaurant", "bakery", "fast_food", "ice_cream", "pub", "bar", "coffee", "pastry", "confectionery", "food_court"}
FOOD_WORDS = re.compile(r"(카페|까페|cafe|café|coffee|커피|베이커리|bakery|디저트|식당|레스토랑|restaurant|맛집|브런치)", re.I)
POI_KEYS = {"amenity", "tourism", "leisure", "historic", "shop", "natural", "craft", "man_made"}


def _tour_hits(variant: str, region: str, nationwide: bool) -> list[dict]:
    from . import tourlive
    if not nationwide:
        return tourlive.search_candidates(variant, region)
    try:
        hits = tourlive._client().call("searchKeyword2", keyword=variant, numOfRows=10, pageNo=1)
        return [h for h in hits if h.get("contenttypeid") in tourlive.KIND]
    except Exception:
        return []


def search(q: str, region: str, near: tuple | None = None, limit: int = 8, expand: bool = False) -> dict:
    """여행 지역 근처(MAX_KM) 우선. 근처에 잘 맞는 곳이 없으면(또는 expand) 전국까지 찾아 '먼 후보'로 따로 돌려준다."""
    q = (q or "").strip()
    if not q:
        return {"query": q, "results": [], "far": []}
    near = near or data.region_center(region)
    vs = variants(q)
    out, seen = [], set()

    def push(c, key):
        if key in seen:
            return
        seen.add(key)
        c["similarity"] = round(max(similarity(v, c["name"]) for v in vs), 2)
        out.append(c)

    # ① 수집 데이터(이름 정규화 유사도 + 하이브리드 검색)
    pl = data.places(region)
    scored = sorted(((max(similarity(v, p["title"]) for v in vs), pid) for pid, p in pl.items()), reverse=True)[:6]
    for d in retrieve.search(vs[-1], region, k=4):
        scored.append((max(0.45, similarity(q, d["metadata"]["title"])), d["doc_id"]))
    for sim, pid in sorted(scored, reverse=True):
        p = pl.get(pid)
        if not p or sim < 0.45:
            continue
        imgs = p.get("images") or ([{"url": p["image_url"]}] if p.get("image_url") else [])
        push(_cand("관광공사(수집)", p["title"], p.get("lat"), p.get("lng"), p.get("addr"), near, sim,
                   place_id=pid, image=(imgs[0].get("thumb") or imgs[0]["url"]) if imgs else None,
                   category=p["kind"], key=f"tour:{pid}"), f"tour:{pid}")

    def collect(nationwide: bool):
        from . import tourlive
        for v in vs:
            for h in _tour_hits(v, region, nationwide):
                if h.get("mapy"):
                    pid = h["contentid"]
                    push(_cand("관광공사", h["title"], float(h["mapy"]), float(h["mapx"]), h.get("addr1"), near, 0,
                               place_id=pid, image=h.get("firstimage2") or h.get("firstimage") or None,
                               category=tourlive.KIND.get(h.get("contenttypeid"), "attraction"), key=f"tour:{pid}"), f"tour:{pid}")
        osm = []
        for v in vs:
            for raw in _photon(v, near[0], near[1]):
                f = json.loads(raw)
                pr, (lng, lat) = f.get("properties") or {}, (f.get("geometry") or {}).get("coordinates", [None, None])
                if pr.get("countrycode") not in (None, "KR") or lat is None or not pr.get("name"):
                    continue
                if pr.get("osm_key") not in POI_KEYS:     # 건물 번호·도로·버스 노선 등 장소가 아닌 것 제외
                    continue
                addr = " ".join(x for x in (pr.get("state"), pr.get("city") or pr.get("county"), pr.get("district"), pr.get("street")) if x)
                osm.append((pr["name"], lat, lng, addr, f"{(pr.get('osm_type') or 'N')[0]}{pr.get('osm_id')}",
                            "restaurant" if pr.get("osm_value") in FOOD_VALUES else "attraction"))
        if not osm:
            for v in vs[:2]:
                for raw in _nominatim(v, near[0], near[1]):
                    x = json.loads(raw)
                    osm.append((x.get("name") or x.get("display_name", "").split(",")[0], float(x["lat"]), float(x["lon"]),
                                ", ".join(reversed(x.get("display_name", "").split(",")[1:4])).strip(),
                                f"{x['osm_type'][0].upper()}{x['osm_id']}",
                                "restaurant" if x.get("type") in FOOD_VALUES else "attraction"))
        if not nationwide:
            osm = [o for o in osm if travel.km(near, (o[1], o[2])) <= MAX_KM]
        imgs = _wikimedia_images([o[4] for o in osm[:10]])
        for name, lat, lng, addr, ref, cat in osm:
            if any(name_key(c["name"]) == name_key(name) and c.get("distance_km") is not None
                   and abs(c["distance_km"] - travel.km(near, (lat, lng))) < 1 for c in out):
                continue   # 관광공사와 같은 곳
            push(_cand("OpenStreetMap", name, lat, lng, addr, near, 0, osm_ref=ref, image=imgs.get(ref),
                       category=cat, key=f"osm:{ref}"), f"osm:{ref}")

    want_food = bool(FOOD_WORDS.search(q))
    collect(nationwide=False)
    for c in vworld_sources(vs, near, False, data.region_label(region).split("/")[-1]) + extra_sources(vs, near, False):
        push(c, c["key"])
    good = [c for c in out if (c["distance_km"] or 0) <= MAX_KM and c["similarity"] >= 0.8]
    if expand or not good:
        collect(nationwide=True)
        for c in vworld_sources(vs, near, True) + extra_sources(vs, near, True):
            push(c, c["key"])

    if want_food:   # '카페·식당'을 말했으면 음식점·카페가 아닌 곳은 점수 감점(안경점 '일공공일' 등)
        for c in out:
            if c.get("category") not in ("restaurant",):
                c["similarity"] = round(c["similarity"] - 0.25, 2)

    def ok(c):
        return c["source"].startswith("관광공사") and c["similarity"] >= 0.45 or c["similarity"] >= 0.6

    for c in out:
        d = c["distance_km"] if c["distance_km"] is not None else MAX_KM
        c["_score"] = min(d, 300) + (1 - c["similarity"]) * 60 + (0 if c["source"].startswith(("관광공사", "브이월드")) else 3)
    out.sort(key=lambda c: c["_score"])
    dedup = []
    for c in out:
        if any(name_key(c["name"]) == name_key(x["name"]) and c.get("lat") and x.get("lat")
               and travel.km((c["lat"], c["lng"]), (x["lat"], x["lng"])) < 1 for x in dedup):
            continue
        dedup.append(c)
    out = dedup
    local = [c for c in out if (c["distance_km"] is None or c["distance_km"] <= MAX_KM) and ok(c)]
    far = [{**c, "far": True} for c in out if c["distance_km"] is not None and c["distance_km"] > MAX_KM and c["similarity"] >= 0.85]
    far.sort(key=lambda c: (-c["similarity"], c["distance_km"]))
    for c in out + far:
        c.pop("_score", None)
    msg = ""
    if not [c for c in local if c["similarity"] >= 0.8] and far:
        f = far[0]
        msg = (f"여행 지역 근처({MAX_KM}km 안)에서는 '{q}'와 같은 곳을 찾지 못했어요. "
               f"더 먼 곳에서 '{f['name']}'(약 {f['distance_km']:,.0f}km, {f['address'][:24]})을 찾았어요. 일정에 포함할까요?")
    # 사진이 없는 상위 후보는 네이버 이미지 검색으로 미리보기(키가 있을 때)
    if naver_keys():
        for c in (local[:4] + far[:2]):
            if not c.get("image"):
                c["image"] = naver_image(f"{c['name']} {(c.get('address') or '').split(' ')[1] if c.get('address') else ''}".strip())
                c["image_source"] = "네이버 이미지 검색" if c["image"] else None
    sources = ["관광공사"] + (["브이월드"] if vworld_key() else []) + ["OpenStreetMap"] + (["카카오맵"] if kakao_key() else []) + (["네이버"] if naver_keys() else [])
    if not local and not far:
        msg = (f"근처와 전국에서 '{q}'을(를) 찾지 못했어요(찾아본 곳: {', '.join(sources)}). "
               "이름과 주소를 직접 넣거나 지도에서 위치를 찍어 '직접 추가'할 수 있어요.")

    return {"query": q, "sources": sources, "manual_add": not local, "variants": vs, "near": {"lat": near[0], "lng": near[1]}, "radius_km": MAX_KM,
            "results": local[:limit], "far": far[:5], "far_message": msg,
            "note": "가까운 순으로 보여 드려요. 거리는 직선거리예요. 사진: 한국관광공사 또는 위키미디어 공용(있을 때만)."}


def user_place_from_candidate(c: dict) -> dict:
    """OSM 후보 → 일정에 넣을 '사용자 지정' 장소."""
    pid = f"osm:{c['osm_ref']}" if c.get("osm_ref") else (c.get("key") or f"user:{abs(hash((c.get('name'), c.get('lat'), c.get('lng')))) % 10**8}")
    src = {"카카오맵": ("kakao", "카카오맵"), "네이버": ("naver", c.get("image_source") or "네이버 이미지 검색")}.get(c.get("source"),
                                                                                         ("wikimedia", "위키미디어 공용"))
    imgs = [{"url": c["image"], "source": src[0], "license": "출처 사이트 이용 조건", "credit": src[1]}] if c.get("image") else []
    return {"contentid": pid, "place_id": pid, "title": c.get("name") or "사용자 지정 장소", "place_url": c.get("place_url"),
            "lat": c.get("lat"), "lng": c.get("lng"), "kind": "attraction", "source": "사용자 지정",
            "addr": c.get("address") or "", "summary": None, "image_url": c.get("image"), "images": imgs}


# ---------------- 선택 연동: 카카오 로컬 · 네이버 검색(키가 있을 때만, 화면 긁기 없음) ----------------

def kakao_key() -> str | None:
    import os
    return os.environ.get("KAKAO_REST_API_KEY") or None


def naver_keys() -> tuple[str, str] | None:
    import os
    i, s = os.environ.get("NAVER_CLIENT_ID"), os.environ.get("NAVER_CLIENT_SECRET")
    return (i, s) if i and s else None


@lru_cache(maxsize=256)
def kakao_search(q: str, lat: float, lng: float, radius_m: int = 20000) -> tuple:
    """카카오 로컬 키워드 검색(가까운 순). radius_m=0이면 전국."""
    key = kakao_key()
    if not key:
        return ()
    params = {"query": q, "x": lng, "y": lat, "sort": "distance" if radius_m else "accuracy", "size": 15}
    if radius_m:
        params["radius"] = min(radius_m, 20000)
    try:
        r = requests.get("https://dapi.kakao.com/v2/local/search/keyword.json", params=params, timeout=6,
                         headers={"Authorization": f"KakaoAK {key}"}).json()
        return tuple(json.dumps(d, ensure_ascii=False) for d in r.get("documents", []))
    except Exception:
        return ()


@lru_cache(maxsize=256)
def naver_local(q: str) -> tuple:
    keys = naver_keys()
    if not keys:
        return ()
    try:
        r = requests.get("https://openapi.naver.com/v1/search/local.json", params={"query": q, "display": 5}, timeout=6,
                         headers={"X-Naver-Client-Id": keys[0], "X-Naver-Client-Secret": keys[1]}).json()
        return tuple(json.dumps(d, ensure_ascii=False) for d in r.get("items", []))
    except Exception:
        return ()


@lru_cache(maxsize=256)
def naver_image(q: str) -> str | None:
    keys = naver_keys()
    if not keys:
        return None
    try:
        r = requests.get("https://openapi.naver.com/v1/search/image", params={"query": q, "display": 1, "filter": "medium"},
                         timeout=6, headers={"X-Naver-Client-Id": keys[0], "X-Naver-Client-Secret": keys[1]}).json()
        it = (r.get("items") or [{}])[0]
        u = it.get("thumbnail") or it.get("link")
        return u if u and u.startswith("https://") else None
    except Exception:
        return None


@lru_cache(maxsize=128)
def naver_blogs(q: str) -> tuple:
    """최근 블로그 후기(제목·링크·날짜) — 원문 링크로만 안내."""
    keys = naver_keys()
    if not keys:
        return ()
    try:
        r = requests.get("https://openapi.naver.com/v1/search/blog.json", params={"query": q, "display": 3, "sort": "date"},
                         timeout=6, headers={"X-Naver-Client-Id": keys[0], "X-Naver-Client-Secret": keys[1]}).json()
        return tuple(json.dumps({"title": re.sub(r"<[^>]+>", "", it.get("title", "")), "link": it.get("link"),
                                 "date": it.get("postdate"), "blogger": it.get("bloggername")}, ensure_ascii=False)
                     for it in r.get("items", []))
    except Exception:
        return ()


def extra_sources(q_variants: list[str], near: tuple, nationwide: bool) -> list[dict]:
    """카카오·네이버 결과를 공통 후보 형식으로."""
    out = []
    for v in q_variants[:2]:
        for raw in kakao_search(v, round(near[0], 3), round(near[1], 3), 0 if nationwide else 20000):
            d = json.loads(raw)
            lat, lng = float(d["y"]), float(d["x"])
            cat = "restaurant" if any(w in (d.get("category_group_code") or "") for w in ("CE7", "FD6")) else "attraction"
            out.append(_cand("카카오맵", d["place_name"], lat, lng, d.get("road_address_name") or d.get("address_name"), near, 0,
                             category=cat, key=f"kakao:{d['id']}", place_url=d.get("place_url"), phone=d.get("phone"),
                             kakao_category=d.get("category_name")))
        for raw in naver_local(v):
            d = json.loads(raw)
            try:
                lng, lat = int(d["mapx"]) / 1e7, int(d["mapy"]) / 1e7
            except (KeyError, ValueError):
                continue
            name = re.sub(r"<[^>]+>", "", d.get("title", ""))
            cat = "restaurant" if re.search(r"(카페|음식|커피|디저트|베이커리)", d.get("category", "")) else "attraction"
            out.append(_cand("네이버", name, lat, lng, d.get("roadAddress") or d.get("address"), near, 0,
                             category=cat, key=f"naver:{name}:{round(lat, 4)}", place_url=d.get("link") or None))
    return out


# ---------------- 브이월드 검색(국가 POI·주소, VWORLD_API_KEY) ----------------

def vworld_key() -> str | None:
    import os
    return os.environ.get("VWORLD_API_KEY") or None


@lru_cache(maxsize=512)
def vworld_search(q: str, kind: str = "place", lat: float | None = None, lng: float | None = None, deg: float = 0.6,
                  addr_type: str = "road") -> tuple:
    """브이월드 검색 2.0 — kind=place(상호·시설) / address(도로명·지번). lat·lng가 있으면 그 주변 bbox로 제한."""
    key = vworld_key()
    if not key or not q:
        return ()
    params = {"service": "search", "request": "search", "version": "2.0", "crs": "EPSG:4326", "size": 15, "page": 1,
              "query": q, "type": kind, "format": "json", "errorformat": "json", "key": key}
    if kind == "address":
        params["category"] = addr_type
    if lat is not None:
        params["bbox"] = f"{lng - deg},{lat - deg},{lng + deg},{lat + deg}"
    try:
        r = requests.get("https://api.vworld.kr/req/search", params=params, timeout=8).json()
        items = (((r.get("response") or {}).get("result") or {}).get("items")) or []
        return tuple(json.dumps(i, ensure_ascii=False) for i in items)
    except Exception:
        return ()


def vworld_sources(q_variants: list[str], near: tuple, nationwide: bool, region_label: str = "") -> list[dict]:
    out = []
    qs = list(q_variants[:2]) + ([f"{q_variants[-1]} {region_label}"] if region_label and not nationwide else [])
    for v in qs:
        rows = vworld_search(v, "place") if nationwide else vworld_search(v, "place", round(near[0], 3), round(near[1], 3))
        for raw in rows:
            i = json.loads(raw)
            pt = i.get("point") or {}
            try:
                lat, lng = float(pt["y"]), float(pt["x"])
            except (KeyError, ValueError):
                continue
            addr = (i.get("address") or {}).get("road") or (i.get("address") or {}).get("parcel") or ""
            catname = i.get("category") or ""
            cat = "restaurant" if re.search(r"(카페|음식|음료|제과|베이커리|식당|한식|중식|일식|양식|분식|주점)", catname) else "attraction"
            out.append(_cand("브이월드", i.get("title") or "", lat, lng, addr, near, 0, category=cat,
                             key=f"vworld:{i.get('id') or (i.get('title'), round(lat, 5))}", vworld_category=catname))
    return out
