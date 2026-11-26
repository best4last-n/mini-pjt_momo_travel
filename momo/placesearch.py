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


def _norm(t: str) -> str:
    return re.sub(r"\s+|\(.*?\)|\[.*?\]|[·,.\-]", "", t or "")


def similarity(q: str, name: str) -> float:
    a, b = _norm(q), _norm(name)
    if not a or not b:
        return 0.0
    if a in b or b in a:
        return 0.9 + 0.1 * min(len(a), len(b)) / max(len(a), len(b))
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


def search(q: str, region: str, near: tuple | None = None, limit: int = 8) -> dict:
    q = (q or "").strip()
    if not q:
        return {"query": q, "results": []}
    near = near or data.region_center(region)
    out, seen = [], set()

    def push(c, key):
        if key in seen:
            return
        seen.add(key)
        out.append(c)

    # ① 수집 데이터
    pl = data.places(region)
    scored = sorted(((similarity(q, p["title"]), pid) for pid, p in pl.items()), reverse=True)[:6]
    for d in retrieve.search(q, region, k=4):
        scored.append((max(0.45, similarity(q, d["metadata"]["title"])), d["doc_id"]))
    for sim, pid in sorted(scored, reverse=True):
        p = pl.get(pid)
        if not p or sim < 0.45:
            continue
        imgs = p.get("images") or ([{"url": p["image_url"]}] if p.get("image_url") else [])
        push(_cand("관광공사(수집)", p["title"], p.get("lat"), p.get("lng"), p.get("addr"), near, sim,
                   place_id=pid, image=(imgs[0].get("thumb") or imgs[0]["url"]) if imgs else None,
                   category=p["kind"], key=f"tour:{pid}"), f"tour:{pid}")
    # ② 관광공사 실시간
    try:
        from . import tourlive
        for h in tourlive.search_candidates(q, region):
            pid = h["contentid"]
            if h.get("mapy"):
                push(_cand("관광공사", h["title"], float(h["mapy"]), float(h["mapx"]), h.get("addr1"), near,
                           similarity(q, h["title"]), place_id=pid, image=h.get("firstimage2") or h.get("firstimage") or None,
                           category=tourlive.KIND.get(h.get("contenttypeid"), "attraction"), key=f"tour:{pid}"), f"tour:{pid}")
    except Exception:
        pass
    # ③ OpenStreetMap(Photon → Nominatim)
    osm = []
    for raw in _photon(q, near[0], near[1]):
        f = json.loads(raw)
        pr, (lng, lat) = f.get("properties") or {}, (f.get("geometry") or {}).get("coordinates", [None, None])
        if pr.get("countrycode") not in (None, "KR") or lat is None:
            continue
        name = pr.get("name") or ""
        addr = " ".join(x for x in (pr.get("state"), pr.get("city") or pr.get("county"), pr.get("district"), pr.get("street")) if x)
        osm.append(("Photon", name, lat, lng, addr, f"{(pr.get('osm_type') or 'N')[0]}{pr.get('osm_id')}"))
    if not osm:
        for raw in _nominatim(q, near[0], near[1]):
            x = json.loads(raw)
            osm.append(("Nominatim", x.get("name") or x.get("display_name", "").split(",")[0], float(x["lat"]), float(x["lon"]),
                        ", ".join(reversed(x.get("display_name", "").split(",")[1:4])).strip(), f"{x['osm_type'][0].upper()}{x['osm_id']}"))
    osm = [o for o in osm if o[1] and travel.km(near, (o[2], o[3])) <= MAX_KM]
    imgs = _wikimedia_images([o[5] for o in osm[:10]])
    for _, name, lat, lng, addr, ref in osm:
        if any(c["name"] == name and c.get("distance_km") is not None
               and abs(c["distance_km"] - travel.km(near, (lat, lng))) < 1 for c in out):
            continue   # 관광공사와 같은 곳
        push(_cand("OpenStreetMap", name, lat, lng, addr, near, similarity(q, name), osm_ref=ref, image=imgs.get(ref),
                   category="attraction", key=f"osm:{ref}"), f"osm:{ref}")
    # 정렬: 이름이 비슷하고(유사도) 가까운 곳 우선
    for c in out:
        d = c["distance_km"] if c["distance_km"] is not None else MAX_KM
        c["_score"] = d + (1 - c["similarity"]) * 40 + (0 if c["source"].startswith("관광공사") else 3)
    out = [c for c in out if (c["distance_km"] is None or c["distance_km"] <= MAX_KM)
           and (c["source"].startswith("관광공사") or c["similarity"] >= 0.5)]
    out.sort(key=lambda c: c["_score"])
    for c in out:
        c.pop("_score", None)
    return {"query": q, "near": {"lat": near[0], "lng": near[1]}, "results": out[:limit],
            "note": "가까운 순으로 보여 드려요. 거리는 직선거리예요. 사진: 한국관광공사 또는 위키미디어 공용(있을 때만)."}


def user_place_from_candidate(c: dict) -> dict:
    """OSM 후보 → 일정에 넣을 '사용자 지정' 장소."""
    ref = c.get("osm_ref") or c.get("key", "").replace("osm:", "")
    imgs = [{"url": c["image"], "source": "wikimedia", "license": "Wikimedia Commons", "credit": "위키미디어 공용"}] if c.get("image") else []
    return {"contentid": f"osm:{ref}", "place_id": f"osm:{ref}", "title": c.get("name") or "사용자 지정 장소",
            "lat": c.get("lat"), "lng": c.get("lng"), "kind": "attraction", "source": "사용자 지정",
            "addr": c.get("address") or "", "summary": None, "image_url": c.get("image"), "images": imgs}
