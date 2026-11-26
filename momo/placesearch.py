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
    good = [c for c in out if (c["distance_km"] or 0) <= MAX_KM and c["similarity"] >= 0.8]
    if expand or not good:
        collect(nationwide=True)

    if want_food:   # '카페·식당'을 말했으면 음식점·카페가 아닌 곳은 점수 감점(안경점 '일공공일' 등)
        for c in out:
            if c.get("category") not in ("restaurant",):
                c["similarity"] = round(c["similarity"] - 0.25, 2)

    def ok(c):
        return c["source"].startswith("관광공사") and c["similarity"] >= 0.45 or c["similarity"] >= 0.6

    for c in out:
        d = c["distance_km"] if c["distance_km"] is not None else MAX_KM
        c["_score"] = min(d, 300) + (1 - c["similarity"]) * 60 + (0 if c["source"].startswith("관광공사") else 3)
    out.sort(key=lambda c: c["_score"])
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
    if not local and not far:
        msg = (f"근처와 전국에서 '{q}'을(를) 찾지 못했어요. 철자를 조금 바꾸거나(예: 영문·한글, 숫자 읽기) "
               "동·도로 이름과 함께 검색해 보세요.")
    return {"query": q, "variants": vs, "near": {"lat": near[0], "lng": near[1]}, "radius_km": MAX_KM,
            "results": local[:limit], "far": far[:5], "far_message": msg,
            "note": "가까운 순으로 보여 드려요. 거리는 직선거리예요. 사진: 한국관광공사 또는 위키미디어 공용(있을 때만)."}


def user_place_from_candidate(c: dict) -> dict:
    """OSM 후보 → 일정에 넣을 '사용자 지정' 장소."""
    ref = c.get("osm_ref") or c.get("key", "").replace("osm:", "")
    imgs = [{"url": c["image"], "source": "wikimedia", "license": "Wikimedia Commons", "credit": "위키미디어 공용"}] if c.get("image") else []
    return {"contentid": f"osm:{ref}", "place_id": f"osm:{ref}", "title": c.get("name") or "사용자 지정 장소",
            "lat": c.get("lat"), "lng": c.get("lng"), "kind": "attraction", "source": "사용자 지정",
            "addr": c.get("address") or "", "summary": None, "image_url": c.get("image"), "images": imgs}
