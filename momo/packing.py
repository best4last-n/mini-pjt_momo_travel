"""날짜별 날씨와 여행 준비물 체크리스트 (SERVICE.md 3-6 (19)).

날씨: 예보 범위(오늘부터 15일) 안이면 Open-Meteo 일별 예보, 밖이면 지역 계절 평년값(표기 '평년').
준비물: 공통(필수) + 날짜별(그날 날씨·방문지) + 동반자·이동수단별 + 사용자가 추가한 항목. 체크 상태는 세션(spec)에 저장.
규칙 기반(LLM 미사용) — 항목마다 '왜 필요한지' 한 줄.
"""
from __future__ import annotations

import hashlib
import re
from datetime import date

import requests

from . import data
from .config import today

UA = {"User-Agent": "momo-travel-counselor/0.1 (education project)"}
# 월별 평년(대략, 남부·중부 내륙 평균) 최저/최고 °C, 강수확률 %
NORMALS = {1: (-5, 5, 20), 2: (-3, 8, 25), 3: (2, 14, 30), 4: (7, 20, 35), 5: (12, 25, 35), 6: (17, 28, 50),
           7: (22, 30, 60), 8: (22, 31, 55), 9: (17, 27, 45), 10: (9, 21, 25), 11: (3, 14, 30), 12: (-2, 8, 20)}
WMO = [((0,), "☀️", "맑음"), ((1, 2), "⛅", "구름 조금"), ((3,), "☁️", "흐림"), ((45, 48), "🌫", "안개"),
       ((51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82), "🌧", "비"), ((71, 73, 75, 77, 85, 86), "❄️", "눈"),
       ((95, 96, 99), "⛈", "뇌우")]


def _wmo(code: int) -> tuple[str, str]:
    for codes, icon, label in WMO:
        if code in codes:
            return icon, label
    return "⛅", "구름"


def weather_days(region: str, days: list[date]) -> list[dict]:
    lat, lng = data.region_center(region)
    out = {}
    in_range = [d for d in days if 0 <= (d - today()).days <= 15]
    if in_range:
        try:
            r = requests.get("https://api.open-meteo.com/v1/forecast", timeout=6, headers=UA, params={
                "latitude": lat, "longitude": lng, "timezone": "Asia/Seoul",
                "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
                "start_date": in_range[0].isoformat(), "end_date": in_range[-1].isoformat()})
            dd = r.json()["daily"]
            for t, code, hi, lo, pp in zip(dd["time"], dd["weather_code"], dd["temperature_2m_max"],
                                           dd["temperature_2m_min"], dd["precipitation_probability_max"]):
                icon, label = _wmo(int(code or 0))
                out[t] = {"date": t, "kind": "forecast", "icon": icon, "summary": label, "tmin": round(lo), "tmax": round(hi),
                          "pop": int(pp or 0), "note": "Open-Meteo 예보"}
        except Exception:
            pass
    res = []
    for d in days:
        k = d.isoformat()
        if k in out:
            res.append(out[k])
            continue
        lo, hi, pp = NORMALS[d.month]
        icon = "❄️" if hi <= 3 else ("☀️" if pp <= 25 else "⛅")
        res.append({"date": k, "kind": "seasonal", "icon": icon, "summary": "평년 기준", "tmin": lo, "tmax": hi, "pop": pp,
                    "note": "예보 범위(약 16일) 밖이라 평년 기준이에요"})
    return res


def _id(label: str, day: int | None) -> str:
    return "p" + hashlib.sha1(f"{day}:{label}".encode()).hexdigest()[:8]


def _item(label: str, category: str, reason: str, day: int | None = None) -> dict:
    return {"id": _id(label, day), "label": label, "category": category, "reason": reason}


OUTDOOR_HARD = re.compile(r"(산|암|봉|계곡|성곽|사지|등산|숲길|오름|전망대|사$)")


def build(spec, plan: dict, weather: list[dict]) -> dict:
    checked = set(getattr(spec, "pack_checked", []) or [])
    common = [
        _item("신분증", "필수", "숙박·렌터카·항공 탑승에 필요해요"),
        _item("휴대폰·충전기", "필수", "지도·예약 확인용"),
        _item("보조배터리", "필수", "이동이 길면 배터리가 빨리 닳아요"),
        _item("카드·약간의 현금", "필수", "시장·주차장은 현금만 받는 곳도 있어요"),
        _item("상비약(소화제·진통제·밴드)", "건강", "현지 약국 찾기 어려운 시간 대비"),
    ]
    if spec.nights:
        common += [_item("세면도구·잠옷", "숙박", f"{spec.nights}박 숙박"), _item(f"갈아입을 옷 {spec.nights}벌", "옷", "날짜 수만큼")]
    lt, loc = spec.long_transport, spec.local_transport
    if "자차" in (lt, loc):
        common += [_item("운전면허증", "차량", "운전자 필수"), _item("하이패스 카드(잔액 확인)", "차량", "통행료 자동 결제"),
                   _item("차량 점검(타이어 공기압·워셔액)", "차량", "장거리 운전 전 점검")]
        if any(w["tmin"] <= 0 for w in weather):
            common.append(_item("성에 제거기·스노 체인(필요 시)", "차량", "영하권 아침 운전 대비"))
    if loc == "렌터카":
        common += [_item("운전면허증(렌터카 필수)", "차량", "대여 시 면허 확인"), _item("렌터카 예약 확인 문자", "교통", "픽업 장소·시간 확인")]
    if lt == "KTX·SRT":
        common.append(_item("승차권(앱 QR)", "교통", "역에서 바로 탑승"))
    if lt == "비행기":
        common += [_item("탑승권(모바일)", "교통", "탑승 수속"), _item("액체류 100ml 이하 지퍼백", "교통", "기내 반입 규정")]
    if lt == "고속·시외버스":
        common.append(_item("버스 승차권(앱)", "교통", "터미널에서 바로 탑승"))
    if spec.companion_type == "어린이·유아":
        common += [_item("아이 간식·물", "아이", "이동 중 보채지 않게"), _item("물티슈·여벌옷", "아이", "체험 후 갈아입기"),
                   _item("유모차·아기띠(필요 시)", "아이", "긴 산책로 대비")]
    if spec.companion_type == "노약자":
        common += [_item("복용 중인 약", "어르신", "일정 중 시간 맞춰 복용"), _item("휴대용 방석·지팡이(필요 시)", "어르신", "오래 걷거나 앉을 때")]
    if spec.budget:
        common.append(_item("경비 기록 앱·영수증 챙기기", "경비", "여행 후 정산 리포트에 입력"))
    by_day = []
    places = {p["id"]: p for p in plan.get("places", [])}
    for i, d in enumerate(plan.get("itinerary", []), 1):
        w = next((x for x in weather if x["date"] == d["date"]), None)
        items = []
        if w:
            if w["tmin"] <= 0:
                items += [_item("두꺼운 외투·장갑·목도리", "날씨", f"아침 최저 {w['tmin']}°C", i), _item("핫팩", "날씨", "야외 관람 시 손 시림", i)]
            elif w["tmin"] <= 8:
                items.append(_item("가벼운 겉옷", "날씨", f"아침 최저 {w['tmin']}°C", i))
            if w["tmax"] >= 28:
                items += [_item("선크림·모자", "날씨", f"낮 최고 {w['tmax']}°C", i), _item("물", "날씨", "더위 탈수 예방", i)]
            if w["pop"] >= 50 or "비" in w["summary"]:
                items += [_item("우산·우비", "날씨", f"강수확률 {w['pop']}%", i), _item("방수 신발·여분 양말", "날씨", "젖은 길 대비", i)]
            if "눈" in w["summary"]:
                items.append(_item("미끄럼 방지 신발", "날씨", "눈길", i))
        names = [s["name"] for s in d.get("stops", []) if s["type"] == "place"]
        hard = [n for n in names if OUTDOOR_HARD.search(re.sub(r"\(.*?\)|\[.*?\]", "", n).strip())]
        if hard:
            items.append(_item("편한 운동화", "사찰/등산", f"{', '.join(hard[:2])} — 걷는 길·계단이 있어요", i))
        if any("사" in n for n in names):
            items.append(_item("단정한 복장(사찰 예절)", "사찰/등산", "법당 출입 시", i))
        for s in d.get("stops", []):
            p = places.get(s.get("place_id")) or {}
            if p.get("fee") and re.search(r"\d", p["fee"] or ""):
                items.append(_item(f"{s['name']} 입장료", "경비", (p["fee"] or "")[:40], i))
        by_day.append({"date": d["date"], "day": i, "weather": w, "items": items})
    custom = [dict(c) for c in getattr(spec, "pack_custom", []) or []]
    allx = common + [x for d in by_day for x in d["items"]] + custom
    for x in allx:
        x["checked"] = x["id"] in checked
    return {"common": common, "by_day": by_day, "custom": custom,
            "done": sum(1 for x in allx if x["checked"]), "total": len(allx)}


def apply(pk: dict, op: dict) -> dict:
    """체크·추가·삭제를 packing dict에 바로 반영(세션·공유 페이지 공용)."""
    allx = pk["common"] + [x for d in pk["by_day"] for x in d["items"]] + pk["custom"]
    kind = op.get("op")
    if kind == "toggle":
        for x in allx:
            if x["id"] == op.get("id"):
                x["checked"] = bool(op.get("checked", not x.get("checked")))
    elif kind == "add":
        label = re.sub(r"\s+", " ", str(op.get("label") or "")).strip()[:40]
        if label:
            pk["custom"].append({"id": "u" + hashlib.sha1(f"{label}{len(pk['custom'])}".encode()).hexdigest()[:6],
                                 "label": label, "category": "직접 추가", "reason": "", "day": op.get("day"), "checked": False})
    elif kind == "remove":
        pk["custom"] = [x for x in pk["custom"] if x["id"] != op.get("id")]
    allx = pk["common"] + [x for d in pk["by_day"] for x in d["items"]] + pk["custom"]
    pk["done"], pk["total"] = sum(1 for x in allx if x.get("checked")), len(allx)
    return pk


def state_of(pk: dict) -> tuple[list[str], list[dict]]:
    allx = pk["common"] + [x for d in pk["by_day"] for x in d["items"]] + pk["custom"]
    return [x["id"] for x in allx if x.get("checked")], [dict(x) for x in pk["custom"]]
