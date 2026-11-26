"""수집 데이터 로딩: 장소, 지역 설정, 지역 대표 지점, 출발지 좌표표."""
from __future__ import annotations

import json
from functools import lru_cache

from .config import DATA

# 시·도 대표 도시 좌표표 (SERVICE.md 3-4e, 저장소 고정)
SIDO_COORDS = {
    "서울": (37.5665, 126.9780), "부산": (35.1796, 129.0756), "대구": (35.8714, 128.6014),
    "인천": (37.4563, 126.7052), "광주": (35.1595, 126.8526), "대전": (36.3504, 127.3845),
    "울산": (35.5384, 129.3114), "세종": (36.4800, 127.2890), "경기": (37.2636, 127.0286),
    "강원": (37.8813, 127.7298), "충북": (36.6424, 127.4890), "충남": (36.8151, 127.1139),
    "전북": (35.8242, 127.1480), "전남": (34.8118, 126.3922), "경북": (36.5684, 128.7294),
    "경남": (35.2280, 128.6811), "제주": (33.4996, 126.5312),
}
SIDO_ALIASES = {
    "서울특별시": "서울", "부산광역시": "부산", "대구광역시": "대구", "인천광역시": "인천", "광주광역시": "광주",
    "대전광역시": "대전", "울산광역시": "울산", "세종특별자치시": "세종", "경기도": "경기", "수원": "경기",
    "강원도": "강원", "춘천": "강원", "충청북도": "충북", "청주": "충북", "충청남도": "충남", "천안": "충남",
    "전라북도": "전북", "전주": "전북", "전라남도": "전남", "목포": "전남", "경상북도": "경북", "안동": "경북",
    "경상남도": "경남", "창원": "경남", "제주도": "제주", "성남": "경기", "용인": "경기", "고양": "경기",
    "분당": "경기", "일산": "경기", "포항": "경북", "김해": "경남", "강남": "서울", "강남구": "서울",
}

# 지원 지역 별칭 → region id
REGION_ALIASES = {
    "경주": "gyeongju", "순천": "suncheon_gurye", "구례": "suncheon_gurye", "순천/구례": "suncheon_gurye",
    "수원": "suwon",
}


def norm_sido(name: str | None) -> str | None:
    if not name:
        return None
    name = name.strip()
    if name in SIDO_COORDS:
        return name
    if name in SIDO_ALIASES:
        return SIDO_ALIASES[name]
    for k, v in SIDO_ALIASES.items():
        if k in name:
            return v
    for k in SIDO_COORDS:
        if name.startswith(k):
            return k
    return None


@lru_cache
def regions_config() -> dict:
    return json.loads((DATA / "config" / "regions.json").read_text(encoding="utf-8"))["regions"]


@lru_cache
def available_regions() -> tuple[str, ...]:
    return tuple(r for r in regions_config() if (DATA / "tourapi" / r / "places.json").exists())


def region_label(region: str) -> str:
    return regions_config().get(region, {}).get("label", region)


@lru_cache
def places(region: str) -> dict[str, dict]:
    path = DATA / "tourapi" / region / "places.json"
    rows = json.loads(path.read_text(encoding="utf-8"))
    return {p["contentid"]: p for p in rows}


@lru_cache
def all_places() -> dict[str, dict]:
    out: dict[str, dict] = {}
    for r in available_regions():
        out.update(places(r))
    return out


@lru_cache
def region_center(region: str) -> tuple[float, float]:
    pts = [(p["lat"], p["lng"]) for p in places(region).values()
           if p.get("lat") and p["kind"] in ("attraction", "restaurant")]
    return (sum(a for a, _ in pts) / len(pts), sum(b for _, b in pts) / len(pts))
