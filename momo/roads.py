"""자차 장거리 경로·소요시간(도로 기준) + 국가교통정보센터(ITS) 실시간 소통·CCTV (SERVICE.md 3-6 (22)).

- 경로: OSRM 공개 서버의 실제 도로 경로(거리·막힘 없는 소요시간·경로 좌표), 결과는 data/route_cache.json에 캐시
- 평균 혼잡 보정: 출발 요일·시간대별 계수(주말 오전 하행·일요일 오후 상행 등, 경험치) + 2시간마다 휴게소 15분
- ITS(키 ITS_API_KEY가 있을 때만): 여행일이 오늘이면 경로 주변 실시간 평균 속도로 보정, 경로 주변 CCTV 목록
모두 추정치이며 출처를 함께 표기한다.
"""
from __future__ import annotations

import json
import math
import os
from datetime import date
from functools import lru_cache

import requests

from .config import DATA, today

UA = {"User-Agent": "momo-travel-counselor/0.1 (education project)"}
CACHE = DATA / "route_cache.json"
ITS_BASE = "https://openapi.its.go.kr:9443"


def _load() -> dict:
    try:
        return json.loads(CACHE.read_text(encoding="utf-8"))
    except Exception:
        return {}


@lru_cache(maxsize=256)
def osrm(a: tuple, b: tuple) -> dict | None:
    """(lat,lng) → (lat,lng) 자동차 경로: {distance_km, duration_min, path:[[lat,lng],...]}."""
    key = f"{a[0]:.2f},{a[1]:.2f}>{b[0]:.2f},{b[1]:.2f}"
    cache = _load()
    if key in cache:
        return cache[key]
    try:
        r = requests.get(f"https://router.project-osrm.org/route/v1/driving/{a[1]},{a[0]};{b[1]},{b[0]}",
                         params={"overview": "simplified", "geometries": "geojson"}, headers=UA, timeout=15).json()
        rt = r["routes"][0]
        out = {"distance_km": round(rt["distance"] / 1000, 1), "duration_min": round(rt["duration"] / 60),
               "path": [[round(y, 4), round(x, 4)] for x, y in rt["geometry"]["coordinates"]]}
    except Exception:
        return None
    cache[key] = out
    try:
        CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
    return out


def congestion(d: date, minute: int, outbound: bool) -> tuple[float, str]:
    """요일·시간대 평균 혼잡 계수(경험치) — (계수, 설명)."""
    wd, h = d.weekday(), minute // 60
    if outbound:
        if wd in (4, 5) and 7 <= h < 12:
            return 1.25, "주말 오전 하행 정체"
        if wd == 4 and 15 <= h < 20:
            return 1.3, "금요일 오후 하행 정체"
        if h < 7:
            return 1.0, "이른 출발이라 한산"
        return (1.15, "주말 평균") if wd >= 5 else (1.1, "평일 평균")
    if wd == 6 and 13 <= h < 21:
        return 1.35, "일요일 오후 상행 정체"
    if wd == 5 and 14 <= h < 20:
        return 1.2, "토요일 오후 상행"
    return (1.15, "주말 평균") if wd >= 5 else (1.1, "평일 평균")


def drive(a: tuple, b: tuple, d: date, depart_min: int, outbound: bool) -> dict | None:
    """도로 경로 + 혼잡 보정 + 휴게 → 예상 소요(분)."""
    r = osrm(a, b)
    if not r:
        return None
    f, why = congestion(d, depart_min, outbound)
    base = r["duration_min"] * f
    rests = int(base // 120)
    live = None
    if d == today():
        live = its_speed_factor(r["path"])
        if live:
            base = r["duration_min"] * live["factor"]
            why = f"실시간 소통 반영(경로 주변 평균 {live['avg_kmh']}km/h)"
    total = int(math.ceil((base + rests * 15) / 5) * 5)
    return {"distance_km": r["distance_km"], "free_min": r["duration_min"], "factor": round(f, 2), "reason": why,
            "rests": rests, "total_min": total, "path": r["path"], "live": bool(live),
            "source": "OSRM 도로 경로" + (" + 국가교통정보센터 실시간" if live else " + 요일·시간대 평균 혼잡 보정")}


# ---------------- 국가교통정보센터(ITS) ----------------

def its_key() -> str | None:
    return os.environ.get("ITS_API_KEY") or None


def _bbox(path: list, pad: float = 0.03) -> dict:
    ys, xs = [p[0] for p in path], [p[1] for p in path]
    return {"minX": min(xs) - pad, "maxX": max(xs) + pad, "minY": min(ys) - pad, "maxY": max(ys) + pad}


def _near(path: list, y: float, x: float, km: float = 2.0) -> bool:
    for py, px in path:
        if abs(py - y) * 111 < km and abs(px - x) * 88 < km:
            return True
    return False


def its_speed_factor(path: list) -> dict | None:
    """오늘 여행이면 경로 주변 고속·국도 실시간 평균 속도로 소요 계수(기준 85km/h)."""
    key = its_key()
    if not key:
        return None
    try:
        r = requests.get(f"{ITS_BASE}/trafficInfo", timeout=10, headers=UA, params={
            "apiKey": key, "type": "all", "drcType": "all", "getType": "json", **_bbox(path)}).json()
        rows = (r.get("body") or {}).get("items") or []
        sp = [float(x.get("speed") or 0) for x in rows if float(x.get("speed") or 0) > 0]
        if len(sp) < 5:
            return None
        avg = sum(sp) / len(sp)
        return {"avg_kmh": round(avg), "factor": max(0.9, min(2.5, 85 / avg))}
    except Exception:
        return None


def cctv_near(path: list, limit: int = 8) -> dict:
    """경로 주변 CCTV(고속도로·국도). 키가 없으면 안내만."""
    key = its_key()
    if not key:
        return {"available": False, "items": [],
                "message": "국가교통정보센터 API 키가 없어 CCTV를 불러오지 못했어요. .env에 ITS_API_KEY를 넣어 주세요."}
    items = []
    for typ in ("ex", "its"):
        try:
            r = requests.get(f"{ITS_BASE}/cctvInfo", timeout=10, headers=UA, params={
                "apiKey": key, "type": typ, "cctvType": "1", "getType": "json", **_bbox(path)}).json()
            for x in ((r.get("response") or {}).get("data") or []):
                y, xx = float(x.get("coordy") or 0), float(x.get("coordx") or 0)
                if y and _near(path, y, xx):
                    items.append({"name": x.get("cctvname"), "url": x.get("cctvurl"), "lat": y, "lng": xx,
                                  "format": x.get("cctvformat"), "road": "고속도로" if typ == "ex" else "국도"})
        except Exception:
            continue
    # 경로 순서대로 고르게
    def order(c):
        return min(range(len(path)), key=lambda i: abs(path[i][0] - c["lat"]) + abs(path[i][1] - c["lng"]))
    items.sort(key=order)
    step = max(1, len(items) // limit) if items else 1
    return {"available": True, "items": items[::step][:limit], "source": "국가교통정보센터(ITS)"}
