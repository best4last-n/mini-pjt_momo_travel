"""귀가길에 잠깐 들러 볼 만한 곳 (SERVICE.md 3-6 (28)).

여행 끝이 아쉬울 때: 여행 지역 → 집(여행 후 도착지) 자차 도로 경로를 따라 일정 간격마다 관광공사 위치기반 검색으로
관광지·카페를 모아, 경로에서 가까운(우회가 짧은) 곳을 고른다. 우회 시간 = 경로에서 떨어진 직선거리 × 2 ÷ 40km/h(추정).
"""
from __future__ import annotations

import json
from functools import lru_cache

from src.shared import data, travel


@lru_cache(maxsize=256)
def _nearby(lat: float, lng: float, type_id: str) -> tuple:
    from src.places.tourlive import _client
    try:
        rows = _client().call("locationBasedList2", mapX=lng, mapY=lat, radius=6000, contentTypeId=type_id,
                              arrange="Q", numOfRows=20, pageNo=1)        # Q: 대표 이미지 있는 것 우선
        return tuple(json.dumps(r, ensure_ascii=False) for r in rows)
    except Exception:
        return ()


def _cum(path: list) -> list[float]:
    out, acc = [0.0], 0.0
    for a, b in zip(path, path[1:]):
        acc += travel.km(tuple(a), tuple(b))
        out.append(acc)
    return out


def suggest(spec, limit: int = 6) -> list[dict]:
    """자차(또는 렌터카 귀가) + 집이 여행 지역에서 60km 넘게 떨어진 경우에만."""
    from src.planning import planner
    from src.mobility import roads
    if spec.long_transport != "자차":
        return []
    home = planner.end_point(spec)
    if not home or not home.get("lat"):
        return []
    center = data.region_center(spec.region)
    if travel.km(center, (home["lat"], home["lng"])) < 60:
        return []
    r = roads.osrm(center, (home["lat"], home["lng"]))
    if not r or not r.get("path"):
        return []
    path, cum = r["path"], None
    cum = _cum(path)
    total = cum[-1]
    marks = [k for k in (30, 70, 120, 170, 220, 270) if 20 < k < total - 20]
    pts = []
    for k in marks:
        i = min(range(len(cum)), key=lambda j: abs(cum[j] - k))
        pts.append((k, path[i]))
    region_ids = set(data.places(spec.region))
    seen, out = set(), []
    for k, (lat, lng) in pts:
        for tid in ("12", "39"):
            for raw in _nearby(round(lat, 3), round(lng, 3), tid):
                h = json.loads(raw)
                if not h.get("mapy") or h["contentid"] in region_ids or h["contentid"] in seen:
                    continue
                if tid == "39" and h.get("cat3") != "A05020900":      # 먹거리는 카페·찻집만(잠깐 쉬어 가기)
                    continue
                y, x = float(h["mapy"]), float(h["mapx"])
                off = min(travel.km((y, x), tuple(p)) for p in path[max(0, path.index([lat, lng]) - 12): path.index([lat, lng]) + 12] or path)
                if off > 6:
                    continue
                seen.add(h["contentid"])
                detour = int(round(off * 2 / 40 * 60 / 5) * 5) + 5
                out.append({"key": f"tour:{h['contentid']}", "place_id": h["contentid"], "name": h["title"],
                            "kind": "카페" if tid == "39" else "관광지", "lat": y, "lng": x, "address": (h.get("addr1") or "")[:40],
                            "image": h.get("firstimage2") or h.get("firstimage") or None, "km_from_start": k,
                            "off_route_km": round(off, 1), "detour_min": detour, "stay_min": 30 if tid == "39" else 45,
                            "source": "한국관광공사",
                            "why": f"돌아가는 길 약 {k}km 지점 · 경로에서 {off:.1f}km · 우회 약 {detour}분"})
    out.sort(key=lambda s: (s["detour_min"] + (0 if s["image"] else 10) + (5 if s["kind"] == "카페" else 0)))
    picked, used_marks = [], {}
    for s in out:              # 구간마다 고르게(한 지점에 2곳까지)
        if used_marks.get(s["km_from_start"], 0) >= 2:
            continue
        used_marks[s["km_from_start"]] = used_marks.get(s["km_from_start"], 0) + 1
        picked.append(s)
        if len(picked) >= limit:
            break
    return sorted(picked, key=lambda s: s["km_from_start"])
