"""answer 문자열(모모 말투) — itinerary를 먼저 만들고 그것을 보고 작성(SERVICE.md 3-3).

일정 답변은 템플릿으로 만든다: 사실(장소·시각·이동·이유·출처)이 itinerary와 항상 일치하고 LLM 호출을 아낀다.
"""
from __future__ import annotations

from datetime import date

from . import data

WD = "월화수목금토일"
TYPE_LABEL = {"meal": "식사", "stay": "숙박", "place": "", "move": "이동"}


def itinerary_text(items: list[dict], changed: set | None = None) -> str:
    lines, cur = [], None
    days = sorted({it["date"] for it in items})
    for it in items:
        if it["date"] != cur:
            cur = it["date"]
            d = date.fromisoformat(cur)
            lines.append(f"\n■ {days.index(cur) + 1}일차 · {d.month}월 {d.day}일({WD[d.weekday()]})")
        if it["type"] == "move":
            mins = _mins(it)
            lines.append(f"- {it['start_time']}~{it['end_time']} {it['place_name']} 이동 (약 {mins}분, 직선거리 추정)")
            continue
        tag = f"[{TYPE_LABEL[it['type']]}] " if TYPE_LABEL[it["type"]] else ""
        mark = " (바뀜)" if changed and (it["date"], it.get("place_id") or it["place_name"]) in changed else ""
        move = f"이동 약 {it['travel_minutes_from_prev']}분 후 " if it["travel_minutes_from_prev"] else ""
        hours_part = ""
        if it["hours_status"] == "확인 필요":
            hours_part = " · 운영시간 확인 필요"
        elif it.get("hours_text"):
            hours_part = f" · 운영 {it['hours_text']}"
        lines.append(f"- {it['start_time']}~{it['end_time']} {move}{tag}{it['place_name']}{mark} — {it['reason']}"
                     f" (출처: {it['source']}{hours_part})")
    return "\n".join(lines).strip()


def _mins(it: dict) -> int:
    h1, m1 = map(int, it["start_time"].split(":"))
    h2, m2 = map(int, it["end_time"].split(":"))
    return (h2 * 60 + m2) - (h1 * 60 + m1)


def weather_tip(items: list[dict], weather: str) -> str:
    month = int(items[0]["date"][5:7]) if items else 0
    tips = []
    if month in (12, 1, 2):
        tips.append("따뜻한 외투·장갑·목도리를 챙기시고, 해가 일찍 지니 야외 장소는 낮 시간에 배치했어요")
    if "강수확률" in weather and any(int(x) >= 60 for x in __import__("re").findall(r"강수확률 (\d+)%", weather)):
        tips.append("비 소식이 있으니 우산을 챙기시고, 비가 오면 실내 위주로 바꿔 달라고 말씀해 주세요")
    else:
        tips.append("비가 오면 박물관 같은 실내 위주로 바꿔 드릴 수 있어요")
    return "준비물·코스 팁: " + " · ".join(tips)


def plan_answer(region: str, nights: int, items: list[dict], notes: list[str], weather: str | None,
                changed: set | None = None, modified: bool = False) -> str:
    label = data.region_label(region)
    length = "당일치기" if nights == 0 else f"{nights}박 {nights + 1}일"
    head = ("편지 받았어요! 말씀하신 부분만 고쳐서 다시 정리했어요. 바뀐 곳에는 (바뀜) 표시를 달아 두었어요."
            if modified else
            f"안녕하세요, 우체부 철새 모모예요! 제가 날아다니며 봐 둔 곳들로 {label} {length} 일정을 엽서처럼 정리해 드릴게요.")
    parts = [head]
    if notes:
        parts.append("\n".join(f"※ {n}" for n in notes))
    if weather:
        parts.append(f"☁ 날씨: {weather}\n{weather_tip(items, weather)}")
    parts.append(itinerary_text(items, changed))
    tips = []
    if any(it["hours_status"] == "확인 필요" for it in items):
        tips.append("'운영시간 확인 필요' 장소는 방문 전에 한 번 더 확인해 주세요.")
    if any(it["source"] == "식사 안내" for it in items):
        tips.append("식사 시간대에 운영시간이 확인된 식당이 맞지 않는 곳은 '주변에서 식사'로 비워 두었어요.")
    tips.append("이동시간은 대략적인 추정이에요. 실제는 교통 상황에 따라 달라요.")
    tips.append("장소·운영시간 정보 출처: 한국관광공사 TourAPI(공공누리).")
    tips.append("마음에 드시면 화면의 '확정할게요' 버튼을 눌러 주세요. 바꾸고 싶은 곳이 있으면 편하게 말씀해 주세요!")
    parts.append("\n".join(tips))
    return "\n\n".join(parts)
