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


def local_minutes(a_id: str | None, b_id: str | None, a: tuple | None, b: tuple | None) -> tuple[int, str]:
    """현지 구간: (분, travel_source)."""
    key = f"{a_id}>{b_id}"
    if a_id and b_id and key in _cache():
        return int(_cache()[key]), "cache"
    if not a or not b or None in a or None in b:
        return 20, "estimate"
    if a == b:
        return 0, "estimate"
    mins = km(a, b) * 1.3 / 30 * 60
    return max(5, int(math.ceil(mins / 5) * 5)), "estimate"


def intercity_minutes(a: tuple, b: tuple) -> int:
    """출발지 ↔ 여행 지역 대표 지점: 직선 × 1.3 ÷ 80km/h."""
    mins = km(a, b) * 1.3 / 80 * 60
    return int(math.ceil(mins / 10) * 10)
