"""출발지·도착지 위치 찾기 — '판교동', '분당 정자역', '부산 해운대구' 같은 동·역·구 이름도 인식 (SERVICE.md 3-6 (16)).

순서: 시·도/대표 도시 별칭(즉시) → Nominatim(한국, 주소 구성요소 포함) → Photon(철자 유사).
개인정보 최소화: 표기는 동(읍·면) 단위까지, 좌표는 소수 2자리(약 1km)로 반올림해 저장·계산. 상세 번지는 받지 않음.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache

import requests

from . import data

UA = {"User-Agent": "momo-travel-counselor/0.1 (education project)"}
STATE_TO_SIDO = {
    "서울특별시": "서울", "부산광역시": "부산", "대구광역시": "대구", "인천광역시": "인천", "광주광역시": "광주",
    "대전광역시": "대전", "울산광역시": "울산", "세종특별자치시": "세종", "경기도": "경기", "강원특별자치도": "강원",
    "강원도": "강원", "충청북도": "충북", "충청남도": "충남", "전북특별자치도": "전북", "전라북도": "전북",
    "전라남도": "전남", "전남광주통합특별시": "전남", "경상북도": "경북", "경상남도": "경남", "제주특별자치도": "제주",
}


def _sido_of(text: str) -> str | None:
    for k, v in STATE_TO_SIDO.items():
        if k in (text or ""):
            return v
    return data.norm_sido(text)


def _round(x: float) -> float:
    return round(float(x), 2)


@lru_cache(maxsize=512)
def _nominatim(q: str) -> str:
    try:
        r = requests.get("https://nominatim.openstreetmap.org/search", timeout=6, headers=UA, params={
            "q": q, "format": "json", "limit": 6, "countrycodes": "kr", "addressdetails": 1, "accept-language": "ko"})
        return json.dumps(r.json(), ensure_ascii=False)
    except Exception:
        return "[]"


@lru_cache(maxsize=512)
def _photon(q: str) -> str:
    try:
        r = requests.get("https://photon.komoot.io/api/", timeout=6, headers=UA,
                         params={"q": q, "limit": 6, "lat": 36.5, "lon": 127.8})
        return json.dumps(r.json().get("features", []), ensure_ascii=False)
    except Exception:
        return "[]"


def _label_from_addr(a: dict) -> tuple[str, str | None]:
    state = a.get("province") or a.get("state") or a.get("city") if a.get("state") else (a.get("province") or a.get("city") or "")
    parts = []
    st = a.get("province") or a.get("state") or ""
    city = a.get("city") or a.get("county") or a.get("town") or ""
    gu = a.get("borough") or a.get("city_district") or a.get("district") or ""
    dong = a.get("quarter") or a.get("suburb") or a.get("neighbourhood") or a.get("village") or ""
    sido = _sido_of(st) or _sido_of(city) or _sido_of(state or "")
    short = {"서울특별시": "서울", "부산광역시": "부산", "대구광역시": "대구", "인천광역시": "인천", "광주광역시": "광주",
             "대전광역시": "대전", "울산광역시": "울산"}
    for x in (short.get(st, st), short.get(city, city), gu, dong):
        x = (x or "").strip()
        if x and x not in parts and not re.search(r"\d", x):
            parts.append(x.replace("특별자치도", "").replace("광역시", "") if x == st else x)
    return " ".join(parts[:4]), sido


def search(q: str, limit: int = 6) -> list[dict]:
    q = re.sub(r"\[[^\]]*\]", "", q or "").strip()
    if not q:
        return []
    out, seen = [], set()
    quick = data.norm_sido(q)
    if quick and q in (quick, *[k for k, v in data.SIDO_ALIASES.items() if v == quick and k.endswith(("도", "시"))])             and quick in data.SIDO_COORDS:
        lat, lng = data.SIDO_COORDS[quick]
        out.append({"label": q, "sido": quick, "lat": _round(lat), "lng": _round(lng), "source": "대표 도시"})
        seen.add(q)
    # 브이월드: 주소(도로명·지번)면 주소 검색, 아니면 역·시설 이름 검색 — 표기는 시·군·구(주소)·시설명+시군구까지
    from .placesearch import vworld_search
    is_addr = bool(re.search(r"(로|길)\s*\d|\d+\s*(번지|-\d)|[동리]\s*\d", q))
    vw = []
    for kind, cat in ((("address", "road"), ("address", "parcel")) if is_addr else (("place", None),)):
        for raw in vworld_search(q, kind, addr_type=cat or "road"):
            i = json.loads(raw)
            pt = i.get("point") or {}
            addr = (i.get("address") or {}).get("road") or (i.get("address") or {}).get("parcel") or ""
            toks = addr.split()
            area = " ".join(toks[:3]) if toks else ""
            label = area if kind == "address" else f"{i.get('title')} ({' '.join(toks[:2])})"
            sido = _sido_of(toks[0] if toks else "") if toks else None
            if pt.get("y") and sido and label not in seen:
                seen.add(label)
                vw.append({"label": label, "sido": sido, "lat": _round(pt["y"]), "lng": _round(pt["x"]), "source": "브이월드"})
        if vw:
            break
    from .placesearch import similarity
    vw.sort(key=lambda r: -similarity(q, r["label"].split(" (")[0]))   # 이름이 같은 곳(수원역) 먼저
    out.extend(vw[:4])
    for x in json.loads(_nominatim(q)):
        label, sido = _label_from_addr(x.get("address") or {})
        if not sido or not label or label in seen:
            continue
        seen.add(label)
        out.append({"label": label, "sido": sido, "lat": _round(x["lat"]), "lng": _round(x["lon"]), "source": "OpenStreetMap"})
    if len(out) < 2:
        for f in json.loads(_photon(q)):
            pr = f.get("properties") or {}
            if pr.get("countrycode") != "KR":
                continue
            lng, lat = f["geometry"]["coordinates"]
            label = " ".join(x for x in (pr.get("state"), pr.get("city") or pr.get("county"), pr.get("district"),
                                         pr.get("name") if pr.get("type") in ("district", "locality", "city", "county") else None) if x)
            sido = _sido_of(pr.get("state") or "") or _sido_of(pr.get("city") or "")
            if not sido or not label or label in seen:
                continue
            seen.add(label)
            out.append({"label": label, "sido": sido, "lat": _round(lat), "lng": _round(lng), "source": "OpenStreetMap"})
    return out[:limit]


def locate(text: str | None, place: dict | None = None) -> dict | None:
    """화면에서 고른 후보(place)가 있으면 그대로, 없으면 텍스트로 찾아 첫 결과."""
    if place and place.get("lat") and place.get("sido"):
        return {"label": place.get("label") or text, "sido": place["sido"], "lat": _round(place["lat"]),
                "lng": _round(place["lng"])}
    if not text:
        return None
    res = search(text, 1)
    return res[0] if res else None
