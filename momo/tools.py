"""도메인 도구 (SERVICE.md 3-1). 실행 중 외부 호출은 날씨(예보 범위 안)와 OSM 지명 검색(경유지)뿐.

각 도구는 사용한 문서를 CONTEXTS(요청 단위 목록)에 넣어 API contexts로 돌려준다.
"""
from __future__ import annotations

import contextvars
from datetime import date

import requests

from . import data, hours, retrieve
from .config import today

CONTEXTS: contextvars.ContextVar[list] = contextvars.ContextVar("contexts", default=None)
UA = {"User-Agent": "momo-travel-counselor/0.1 (education project)"}


def add_context(doc_id: str, text: str) -> None:
    lst = CONTEXTS.get()
    if lst is not None and not any(c["doc_id"] == doc_id for c in lst):
        lst.append({"doc_id": doc_id, "text": text})


def place_doc_text(p: dict) -> str:
    intro = p.get("intro") or {}
    keys = ("usetime", "opentimefood", "checkintime", "restdate", "restdatefood", "infocenter", "infocenterfood")
    info = " / ".join(f"{k}: {hours.clean(intro.get(k))}" for k in keys if hours.clean(intro.get(k)))
    return f"{p['title']} | {p.get('addr', '')} | 운영 정보: {info or '없음'} | 소개: {hours.clean(p.get('overview'))[:600]}"


# ---------- 운영 정보 ----------

def operating_info(name: str, region: str | None = None, on: date | None = None) -> dict | None:
    d = retrieve.find_by_name(name, region)
    if not d:
        hits = retrieve.search(name, region, k=1, use_vector=False)
        d = hits[0] if hits else None
    if not d:
        return None
    p = data.all_places()[d["doc_id"]]
    add_context(p["contentid"], place_doc_text(p))
    intro = p.get("intro") or {}
    res = {
        "place_id": p["contentid"], "name": p["title"],
        "usetime": hours.clean(intro.get("usetime") or intro.get("opentimefood") or intro.get("checkintime")),
        "restdate": hours.clean(intro.get("restdate") or intro.get("restdatefood")) or "정보 없음",
        "contact": hours.clean(intro.get("infocenter") or intro.get("infocenterfood") or p.get("tel")),
        "source": "한국관광공사 TourAPI",
    }
    if on:
        info = hours.parse_open(p, on)
        res["on_date"] = {"date": on.isoformat(), "status": info.status,
                          "open": hours.fmt(info.open), "close": hours.fmt(info.close)}
    return res


# ---------- 축제 (수집 스냅샷 조회) ----------

def festivals(region: str, start: date, end: date) -> list[dict]:
    out = []
    for p in data.places(region).values():
        if p["kind"] != "festival":
            continue
        s, e = p.get("event_start") or "", p.get("event_end") or ""
        if s <= end.strftime("%Y%m%d") and e >= start.strftime("%Y%m%d"):
            add_context(p["contentid"], place_doc_text(p))
            out.append({"name": p["title"], "start": s, "end": e, "source": "한국관광공사 TourAPI"})
    add_context(f"tool:search_festivals:{region}", f"축제 조회 결과({data.region_label(region)} {start.isoformat()}~{end.isoformat()}, "
                f"관광공사 수집 스냅샷 2026-12-01~2027-02-28): " + (", ".join(f"{x['name']}({x['start']}~{x['end']})" for x in out) or "해당 기간 축제·행사 없음"))
    return out


# ---------- 날씨 ----------

SEASONAL = {
    12: "12월은 아침 기온이 영하권으로 떨어지고 낮에도 10도 안팎으로 쌀쌀한 편",
    1: "1월은 한 해 중 가장 추운 때라 아침 영하 5도 안팎, 낮에도 5도 안팎",
    2: "2월은 아직 추위가 남아 아침 영하권, 낮 5~10도 안팎",
    3: "3월은 일교차가 크고 낮 10~15도 안팎", 11: "11월은 낮 10~15도 안팎, 아침은 쌀쌀한 편",
}


def weather(region: str, days: list[date]) -> dict:
    """예보 범위(오늘부터 15일) 안이면 Open-Meteo, 밖이면 계절 평년 안내."""
    lat, lng = data.region_center(region)
    if all(0 <= (d - today()).days <= 15 for d in days):
        try:
            r = requests.get("https://api.open-meteo.com/v1/forecast", timeout=6, headers=UA, params={
                "latitude": lat, "longitude": lng, "timezone": "Asia/Seoul",
                "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
                "start_date": days[0].isoformat(), "end_date": days[-1].isoformat()})
            dd = r.json()["daily"]
            rows = [f"{t[5:]} 최저 {lo:.0f}도/최고 {hi:.0f}도, 강수확률 {pp}%" for t, lo, hi, pp in
                    zip(dd["time"], dd["temperature_2m_min"], dd["temperature_2m_max"], dd["precipitation_probability_max"])]
            rain = any((pp or 0) >= 60 for pp in dd["precipitation_probability_max"])
            text = "예보(Open-Meteo): " + " · ".join(rows)
            add_context("tool:get_weather", f"날씨 도구 결과: {text}")
            return {"kind": "forecast", "text": text, "rain": rain}
        except Exception:
            pass
    m = days[0].month
    text = f"여행 날짜가 예보 범위(약 16일) 밖이라 계절 평년 안내예요: {SEASONAL.get(m, '계절에 맞는 옷차림을 준비하세요')}"
    add_context("tool:get_weather", f"날씨 도구 결과: {text}")
    return {"kind": "seasonal", "rain": False, "text": text}


def season_of(d: date) -> str:
    return {12: "winter", 1: "winter", 2: "winter", 3: "spring", 4: "spring", 5: "spring",
            6: "summer", 7: "summer", 8: "summer"}.get(d.month, "autumn")


# ---------- OSM 지명 검색 (사용자 지정 경유지) ----------

def geocode(name: str, region_label: str | None = None) -> dict | None:
    q = f"{name} {region_label or ''}".strip()
    try:
        r = requests.get("https://nominatim.openstreetmap.org/search", timeout=5, headers=UA,
                         params={"q": q, "format": "json", "limit": 1, "countrycodes": "kr"})
        rows = r.json()
    except Exception:
        return None
    if not rows:
        return None
    x = rows[0]
    return {"contentid": f"osm:{x.get('osm_id')}", "place_id": f"osm:{x.get('osm_id')}", "title": name,
            "lat": float(x["lat"]), "lng": float(x["lon"]), "kind": "attraction", "source": "사용자 지정",
            "addr": x.get("display_name", "")[:80], "summary": None, "image_url": None}
