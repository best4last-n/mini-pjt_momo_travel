"""자차 장거리 경로·소요시간(도로 기준) + 국가교통정보센터(ITS) 소통·예측·돌발·주의운전 (SERVICE.md 3-6 (22)·(25)).

- 경로: OSRM 공개 서버의 실제 도로 경로(거리·막힘 없는 소요시간·경로 좌표·도로별 구간), data/route_cache.json 캐시
- 구간별 속도(도로마다 따로, 그 도로에 들어서는 시각 기준):
  ① 오늘 곧(±90분) 출발 → ITS 교통소통정보(trafficInfo) 실시간 속도
  ② 오늘·내일 → ITS 교통예측정보(bypassFCastInfo, 본선 구간 M 링크) 시간대별 예측 속도
  ③ 그 밖 → 요일·시간대 평균 혼잡 계수(경험치)
- 돌발상황정보(eventInfo): 경로 주변 사고(차로 차단)·공사·기상 → 지연 가산·목록
- 주의운전구간 정보(posIncidentInfo): 졸음쉼터·갓길차로·휴게소 혼잡·고장차·작업 등 경로 주변 안내
- 2시간마다 휴게 15분. 모두 추정치이며 출처를 함께 표기한다. ITS_API_KEY가 없으면 ③만 쓴다.
"""
from __future__ import annotations

import json
import math
import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache

import requests

from .config import DATA, today

UA = {"User-Agent": "momo-travel-counselor/0.1 (education project)"}
CACHE = DATA / "route_cache.json"
ITS_BASE = "https://openapi.its.go.kr:9443"
KST = timezone(timedelta(hours=9))
TTL = 600            # ITS 응답 메모리 캐시(초)
_MEM: dict = {}


def _load() -> dict:
    try:
        return json.loads(CACHE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _roads_from_steps(steps: list) -> list[dict]:
    """OSRM 단계 → 같은 도로가 이어지는 구간 묶음 [{ref, name, km, min, bbox}] (짧은 연결로는 앞 구간에 합침)."""
    out: list[dict] = []
    for s in steps:
        ref, name = (s.get("ref") or "").split(";")[0].strip(), (s.get("name") or "").strip()
        coords = (s.get("geometry") or {}).get("coordinates") or []
        ys, xs = [c[1] for c in coords], [c[0] for c in coords]
        km, mn = s["distance"] / 1000, s["duration"] / 60
        if out and (out[-1]["ref"], out[-1]["name"]) == (ref, name) or (out and km < 2 and not (ref or name)):
            o = out[-1]
            o["km"] += km
            o["min"] += mn
            if ys:
                o["bbox"] = [min(o["bbox"][0], min(ys)), min(o["bbox"][1], min(xs)), max(o["bbox"][2], max(ys)), max(o["bbox"][3], max(xs))]
            continue
        out.append({"ref": ref, "name": name, "km": km, "min": mn,
                    "bbox": [min(ys), min(xs), max(ys), max(xs)] if ys else [0, 0, 0, 0]})
    for o in out:
        o["km"], o["min"] = round(o["km"], 1), round(o["min"], 1)
    return out


@lru_cache(maxsize=256)
def osrm(a: tuple, b: tuple) -> dict | None:
    """(lat,lng) → (lat,lng) 자동차 경로: {distance_km, duration_min, path, roads}."""
    key = f"{a[0]:.2f},{a[1]:.2f}>{b[0]:.2f},{b[1]:.2f}"
    cache = _load()
    if key in cache and cache[key].get("roads") is not None:
        return cache[key]
    try:
        r = requests.get(f"https://router.project-osrm.org/route/v1/driving/{a[1]},{a[0]};{b[1]},{b[0]}",
                         params={"overview": "simplified", "geometries": "geojson", "steps": "true"},
                         headers=UA, timeout=20).json()
        rt = r["routes"][0]
        out = {"distance_km": round(rt["distance"] / 1000, 1), "duration_min": round(rt["duration"] / 60),
               "path": [[round(y, 4), round(x, 4)] for x, y in rt["geometry"]["coordinates"]],
               "roads": _roads_from_steps(rt["legs"][0]["steps"])}
    except Exception:
        return cache.get(key)          # 옛 캐시(도로 구간 없음)라도
    cache[key] = out
    try:
        CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
    return out


def congestion(d: date, minute: int, outbound: bool) -> tuple[float, str]:
    """요일·시간대 평균 혼잡 계수(경험치) — (계수, 설명)."""
    wd, h = d.weekday(), (minute // 60) % 24
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


# ---------------- 국가교통정보센터(ITS) ----------------

def its_key() -> str | None:
    return os.environ.get("ITS_API_KEY") or None


def _its(endpoint: str, **params) -> list[dict]:
    """ITS 호출(메모리 캐시 TTL). 실패하면 []."""
    key = its_key()
    if not key:
        return []
    ck = (endpoint, tuple(sorted(params.items())))
    hit = _MEM.get(ck)
    if hit and time.time() - hit[0] < TTL:
        return hit[1]
    try:
        r = requests.get(f"{ITS_BASE}/{endpoint}", timeout=12, headers=UA,
                         params={"apiKey": key, "getType": "json", **params}).json()
        body = r.get("body") or (r.get("response") or {})
        items = (body.get("items") if isinstance(body, dict) else None) or (body.get("data") if isinstance(body, dict) else None) or []
    except Exception:
        items = []
    _MEM[ck] = (time.time(), items)
    return items


def _core(name: str) -> str:
    for w in ("고속도로", "고속국도", "선"):
        name = name.replace(w, "")
    return name.strip()


def _pad(bb: list, deg: float = 0.02) -> dict:
    return {"minX": round(bb[1] - deg, 4), "maxX": round(bb[3] + deg, 4), "minY": round(bb[0] - deg, 4), "maxY": round(bb[2] + deg, 4)}


def _harmonic(rows: list[tuple[float, float]]) -> float | None:
    """[(길이 m, 속도 km/h)] → 길이 가중 조화평균 속도."""
    rows = [(l, s) for l, s in rows if l > 0 and s > 0]
    if not rows:
        return None
    return sum(l for l, _ in rows) / sum(l / s for l, s in rows)


def live_speed(seg: dict) -> float | None:
    """실시간 소통: 구간 bbox 안에서 같은 도로 이름의 링크 속도."""
    core = _core(seg["name"])
    if not core:
        return None
    typ = "ex" if "고속" in seg["name"] else "its"
    rows = []
    for it in _its("trafficInfo", type="all", drcType="all", **_pad(seg["bbox"])):
        rn = it.get("roadName") or ""
        if core in rn or (typ == "its" and seg["ref"] and f"{seg['ref']}호선" in rn):
            try:
                sp, tt = float(it.get("speed") or 0), float(it.get("travelTime") or 0)
            except ValueError:
                continue
            rows.append((sp / 3.6 * tt, sp))
    return _harmonic(rows) if len(rows) >= 3 else None


@lru_cache(maxsize=1)
def fc_sections() -> list[dict]:
    try:
        return json.loads((DATA / "its_fc_sections.json").read_text(encoding="utf-8"))
    except Exception:
        return []


def forecast_speed(seg: dict, d: date, hour: int) -> float | None:
    """교통예측정보: 이 도로(노선 번호)의 본선 구간(M 링크) 예측 속도. 오늘·내일만 제공된다."""
    if not seg["ref"].isdigit():
        return None
    core = _core(seg["name"])
    secs = [s for s in fc_sections() if s.get("ROAD_NO") == seg["ref"] and (not core or core in (s.get("ROAD_NM") or ""))][:10]
    if not secs:
        return None

    def one(s):
        return _its("bypassFCastInfo", sectionId=s["MLN_SCTN_ID"], routeNo=s["ROAD_NO"],
                    fCastDate=d.strftime("%Y%m%d"), fCastHour=f"{hour:02d}")
    rows = []
    with ThreadPoolExecutor(max_workers=5) as ex:
        for items in ex.map(one, secs):
            for it in items:
                if it.get("sectionType") != "M":
                    continue
                try:
                    rows.append((float(it.get("length") or 0), float(it.get("speed") or 0)))
                except ValueError:
                    continue
    return _harmonic(rows) if len(rows) >= 3 else None


def _near(path: list, y: float, x: float, km: float = 2.0) -> bool:
    for py, px in path:
        if abs(py - y) * 111 < km and abs(px - x) * 88 < km:
            return True
    return False


def _path_bbox(path: list) -> list:
    ys, xs = [p[0] for p in path], [p[1] for p in path]
    return [min(ys), min(xs), max(ys), max(xs)]


def road_alerts(path: list, d: date) -> dict:
    """경로 주변 돌발(사고·공사·기상)과 주의운전 구간. 지연 가산(분)은 그날 계속될 돌발만."""
    if not its_key() or not path:
        return {"events": [], "cautions": [], "delay_min": 0}
    bb = _pad(_path_bbox(path), 0.03)
    events, delay = [], 0
    for it in _its("eventInfo", type="all", eventType="all", **bb):
        try:
            y, x = float(it.get("coordY") or 0), float(it.get("coordX") or 0)
        except ValueError:
            continue
        if not (y and _near(path, y, x, 1.5)):
            continue
        end = (it.get("endDate") or "")[:8]
        ongoing = not end or end >= d.strftime("%Y%m%d")
        if not ongoing:
            continue
        et, blocked = it.get("eventType") or "", (it.get("lanesBlocked") or "").strip()
        add = 10 if et == "교통사고" and d == today() else 3 if et == "공사" and blocked else 0
        delay += add
        events.append({"type": et, "detail": it.get("eventDetailType"), "road": it.get("roadName"),
                       "message": (it.get("message") or "")[:80], "lanes_blocked": blocked or None,
                       "start": it.get("startDate"), "end": it.get("endDate"), "lat": y, "lng": x, "delay_min": add})
    cautions, seen = [], set()
    if (d - today()).days <= 1:          # 실시간 안내라 오늘·내일 여행에만
        for it in _its("posIncidentInfo", **bb):
            try:
                y, x = float(it.get("startY") or 0), float(it.get("startX") or 0)
            except ValueError:
                continue
            msg = (it.get("message") or "").strip()
            if not (y and msg and _near(path, y, x, 1.5)) or msg in seen:
                continue
            seen.add(msg)
            cautions.append({"type": it.get("outbrkType"), "message": msg[:60], "road": it.get("revRouteName") or None,
                             "lat": y, "lng": x})
    order = {"교통사고": 0, "공사": 1, "기상": 2}
    events.sort(key=lambda e: order.get(e["type"], 3))
    return {"events": events[:12], "cautions": cautions[:12], "delay_min": min(30, delay)}


# OSRM 기본 차량 속도는 국내 고속도로(제한 100~110km/h)보다 낮게 잡혀, 고속도로 구간이 실제 길찾기보다 10~15% 길게 나옴
# (판교→구례 OSRM 220분 · 실제 약 3시간 10~20분, 2026-11-26 사용자 지적) → 고속도로 구간만 보정
EXPRESS_FACTOR = 0.88


@lru_cache(maxsize=128)
def _drive_cached(a: tuple, b: tuple, d: date, depart_min: int, outbound: bool, bucket: int) -> dict | None:
    r = osrm(a, b)
    if not r:
        return None
    f, why = congestion(d, depart_min, outbound)
    now = datetime.now(KST)
    live_ok = d == today() and abs(depart_min - (now.hour * 60 + now.minute)) <= 90
    fc_ok = 0 <= (d - today()).days <= 1
    segs, t, used = [], float(depart_min), set()
    roads = r.get("roads") or [{"ref": "", "name": "", "km": r["distance_km"], "min": r["duration_min"], "bbox": [0, 0, 0, 0]}]
    jobs = []
    roads = [{**s, "min": s["min"] * (EXPRESS_FACTOR if "고속도로" in (s.get("name") or "") else 1.0)} for s in roads]
    for s in roads:                       # 구간마다 들어서는 시각(혼잡 계수로 근사)
        jobs.append((s, d + timedelta(days=int(t // 1440)), int(t % 1440) // 60))
        t += s["min"] * f

    def speed_of(job):
        s, dd, hh = job
        if its_key() and s["km"] >= 5:
            if live_ok:
                sp = live_speed(s)
                if sp:
                    return sp, "실시간 소통"
            if fc_ok and 0 <= (dd - today()).days <= 1:
                sp = forecast_speed(s, dd, hh)
                if sp:
                    return sp, "ITS 예측"
        return None, None
    with ThreadPoolExecutor(max_workers=4) as ex:
        speeds = list(ex.map(speed_of, jobs))
    base = 0.0
    for (s, dd, hh), (sp, src) in zip(jobs, speeds):
        if sp:
            m = max(s["km"] / max(sp, 5) * 60, s["min"] * 0.95)      # 막힘 없을 때보다 빠를 수는 없게
            used.add(src)
        else:
            m, src = s["min"] * f, "평균 혼잡"
        base += m
        segs.append({"road": s["name"] or (f"{s['ref']}번 도로" if s["ref"] else "일반 도로"), "ref": s["ref"],
                     "km": s["km"], "free_min": round(s["min"]), "min": round(m), "speed_kmh": round(sp) if sp else None,
                     "source": src, "hour": hh})
    alerts = road_alerts(r["path"], d)
    rests = int(base // 120)
    total = int(math.ceil((base + rests * 15 + alerts["delay_min"]) / 5) * 5)
    live = "실시간 소통" in used
    if used:
        why = (" + ".join(sorted(used)) + " 반영") + (f", 나머지 구간은 {why}" if any(s["source"] == "평균 혼잡" and s["km"] >= 5 for s in segs) else "")
    free = round(sum(s["min"] for s in roads))
    return {"distance_km": r["distance_km"], "free_min": free, "factor": round(base / max(1, free), 2),
            "reason": why, "rests": rests, "total_min": total, "path": r["path"], "live": live,
            "segments": [s for s in segs if s["km"] >= 3], "events": alerts["events"], "cautions": alerts["cautions"],
            "event_delay_min": alerts["delay_min"], "its_used": sorted(used),
            "source": "OSRM 도로 경로 + " + (" · ".join(f"국가교통정보센터 {u}" for u in sorted(used)) if used else "요일·시간대 평균 혼잡 보정")
            + (" + 돌발상황" if alerts["events"] else "")}


def drive(a: tuple, b: tuple, d: date, depart_min: int, outbound: bool) -> dict | None:
    """도로 경로 + 구간별 ITS 실시간/예측 속도(없으면 평균 혼잡) + 돌발 지연 + 휴게 → 예상 소요(분). 10분 단위 캐시."""
    return _drive_cached(tuple(a), tuple(b), d, int(depart_min), bool(outbound), int(time.time() // TTL))


def cctv_near(path: list, limit: int = 8) -> dict:
    """경로 주변 CCTV(고속도로·국도). 키가 없으면 안내만."""
    if not its_key():
        return {"available": False, "items": [],
                "message": "국가교통정보센터 API 키가 없어 CCTV를 불러오지 못했어요. .env에 ITS_API_KEY를 넣어 주세요."}
    items = []
    bb = _path_bbox(path)
    for typ in ("ex", "its"):
        for x in _its("cctvInfo", type=typ, cctvType="1", **_pad(bb, 0.03)):
            try:
                y, xx = float(x.get("coordy") or 0), float(x.get("coordx") or 0)
            except ValueError:
                continue
            if y and _near(path, y, xx):
                items.append({"name": x.get("cctvname"), "url": x.get("cctvurl"), "lat": y, "lng": xx,
                              "format": x.get("cctvformat"), "road": "고속도로" if typ == "ex" else "국도"})

    def order(c):
        return min(range(len(path)), key=lambda i: abs(path[i][0] - c["lat"]) + abs(path[i][1] - c["lng"]))
    items.sort(key=order)
    step = max(1, len(items) // limit) if items else 1
    return {"available": True, "items": items[::step][:limit], "source": "국가교통정보센터(ITS)"}
