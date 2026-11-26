"""이동시간 추정 (SERVICE.md 3-1 estimate_travel, 3-4e). 실행 중 실시간 호출 없음.

1차 범위: 자차 기준 추정만. 대중교통을 말해도 같은 추정 + "대중교통 시간은 추정이에요" 안내(3-4g 연기 결정).
OSRM 캐시(data/travel_cache.json)가 있으면 우선 사용.
"""
from __future__ import annotations

import json
import math
from functools import lru_cache

from .config import DATA


def km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


@lru_cache
def _cache() -> dict:
    p = DATA / "travel_cache.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def local_minutes(a_id: str | None, b_id: str | None, a: tuple | None, b: tuple | None,
                  mode: str = "자차") -> tuple[int, str]:
    """현지 구간: (분, travel_source). mode: 자차·렌터카 / 대중교통 (SERVICE.md 3-6 (3))."""
    key = f"{a_id}>{b_id}"
    if mode != "대중교통" and a_id and b_id and key in _cache():
        return int(_cache()[key]), "cache"
    if not a or not b or None in a or None in b:
        return 20, "estimate"
    if a == b:
        return 5, "estimate"
    d = km(a, b)
    if mode == "대중교통":
        mins = d * 1.3 / 4.5 * 60 if d <= 1.2 else d * 1.3 / 15 * 60 + 10
    else:
        # 시내(10km까지) 30km/h, 그 너머 군·국도 구간 60km/h
        mins = (min(d, 10) * 1.3 / 30 + max(d - 10, 0) * 1.3 / 60) * 60
    return max(5, int(math.ceil(mins / 5) * 5)), "estimate"


def distance_km(a: tuple | None, b: tuple | None) -> float | None:
    if not a or not b or None in a or None in b:
        return None
    return round(km(a, b) * 1.3, 1)


LONG_MODES = {  # 장거리: (도로 계수, 속도 km/h, 접근·대기 분)
    "자차": (1.3, 80, 0), "KTX·SRT": (1.2, 150, 30), "고속·시외버스": (1.3, 70, 20), "비행기": (1.0, 400, 110),
}


def intercity_minutes(a: tuple, b: tuple, mode: str = "자차") -> int:
    """출발지 ↔ 여행 지역 대표 지점(추정)."""
    f, v, wait = LONG_MODES.get(mode, LONG_MODES["자차"])
    mins = km(a, b) * f / v * 60 + wait
    return int(math.ceil(mins / 10) * 10)
