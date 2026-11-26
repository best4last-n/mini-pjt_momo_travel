"""운영시간·휴무일 해석 (SERVICE.md 3-4d 규칙, 규칙 기반 — LLM 미사용).

parse_open(place, day) -> OpenInfo
  status: "확인됨" | "확인 필요" | "휴무"
  open/close: 분(0~1440), breaks: [(s, e)], notes: 경고 문구
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from datetime import date, timedelta

WEEKDAYS = "월화수목금토일"

# 공휴일·명절 표 (평가 기간 2026-12 ~ 2027-03 + 여유). 설 2027-02-06(토), 추석 2026-09-25 / 2027-09-15
HOLIDAYS = {date(2026, 12, 25), date(2027, 1, 1), date(2027, 2, 5), date(2027, 2, 6), date(2027, 2, 7),
            date(2027, 2, 8), date(2027, 3, 1), date(2026, 9, 24), date(2026, 9, 25), date(2026, 9, 26),
            date(2027, 9, 14), date(2027, 9, 15), date(2027, 9, 16)}
SEOL = {"당일": {date(2027, 2, 6)}, "전일": {date(2027, 2, 5)}, "연휴": {date(2027, 2, 5), date(2027, 2, 6),
        date(2027, 2, 7), date(2027, 2, 8)}}
CHUSEOK = {"당일": {date(2026, 9, 25), date(2027, 9, 15)}, "전일": {date(2026, 9, 24), date(2027, 9, 14)},
           "연휴": {date(2026, 9, 24), date(2026, 9, 25), date(2026, 9, 26), date(2027, 9, 14),
                  date(2027, 9, 15), date(2027, 9, 16)}}

TIME = r"(\d{1,2}):(\d{2})"
RANGE = re.compile(TIME + r"\s*[~\-–]\s*" + TIME)
BREAK_WORDS = re.compile(r"(준비\s*시간|휴게\s*시간|브레이크)")
LAST_WORDS = re.compile(r"(입장\s*마감|마지막\s*주문|주문\s*마감|라스트\s*오더)\s*:?\s*" + TIME)
UNSURE = re.compile(r"(상이|별로|문의|유동)")


@dataclass
class OpenInfo:
    status: str
    open: int = 0
    close: int = 1440
    breaks: list[tuple[int, int]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    raw: str = ""
    rest_raw: str = ""


def clean(t: str | None) -> str:
    t = html.unescape(re.sub(r"<[^>]+>", " ", t or ""))
    return re.sub(r"\s+", " ", t).strip()


def _m(h: str, mi: str) -> int:
    return int(h) * 60 + int(mi)


def _month_in(month: int, a: int, b: int) -> bool:
    return a <= month <= b if a <= b else (month >= a or month <= b)


def _season_segment(text: str, month: int) -> str:
    """계절 구간 표기가 있으면 여행 월에 맞는 부분만 돌려준다."""
    marks = []
    for m in re.finditer(r"(하절기|동절기)?\s*[\[(]?\s*(\d{1,2})월\s*~\s*(\d{1,2})월\s*[\])]?", text):
        marks.append((m.start(), m.end(), int(m.group(2)), int(m.group(3))))
    for m in re.finditer(r"[\[(]?(하절기|동절기)[\])]?(?!\s*[\[(]?\s*\d{1,2}월)", text):
        a, b = (3, 10) if m.group(1) == "하절기" else (11, 2)
        marks.append((m.start(), m.end(), a, b))
    if not marks:
        return text
    marks.sort()
    for i, (s, e, a, b) in enumerate(marks):
        if _month_in(month, a, b):
            end = marks[i + 1][0] if i + 1 < len(marks) else len(text)
            return text[e:end]
    return text[:marks[0][0]]   # 뒤에 붙은 메모성 월 표기(예: "(3월~10월 매주 토요일)")면 앞부분 사용


def _weekpart_segment(text: str, d: date) -> str:
    want = "주말" if d.weekday() >= 5 else "평일"
    parts = list(re.finditer(r"(평일|주말)", text))
    if not parts or not RANGE.search(text):
        return text
    for i, m in enumerate(parts):
        if m.group(1) == want:
            end = parts[i + 1].start() if i + 1 < len(parts) else len(text)
            seg = text[m.end():end]
            if RANGE.search(seg):
                return seg
    return text


def parse_hours_text(text: str, d: date) -> tuple[int, int, list, list[str]] | None:
    """(open, close, breaks, notes) 또는 해석 불가 None."""
    text = clean(text)
    if not text:
        return None
    if "상시" in text and "개방" in text:
        return 0, 1440, [], []
    seg = _weekpart_segment(_season_segment(text, d.month), d)
    if not seg.strip():
        return None
    notes = []
    if re.search(r"(조기\s*마감|소진|유동|문의)", text):
        notes.append("영업시간이 바뀔 수 있어요(방문 전 확인)")
    main, breaks = None, []
    for m in RANGE.finditer(seg):
        before = seg[max(0, m.start() - 8):m.start()]
        rng = (_m(m.group(1), m.group(2)), _m(m.group(3), m.group(4)))
        if BREAK_WORDS.search(before):
            breaks.append(rng)
        elif main is None and not re.search(r"(야간|연장)", before):
            main = rng
    if main is None:
        return None
    o, c = main
    if c <= o:            # 자정 넘김
        c = 1440
    lasts = [_m(m.group(2), m.group(3)) for m in LAST_WORDS.finditer(seg)]
    lasts = [x for x in lasts if o < x < c]
    if lasts:
        c = min(lasts)
    if "주말" in text and "준비시간 없이" in text and d.weekday() >= 5:
        breaks = []
    return o, c, breaks, notes


def _nth_weekday(d: date) -> int:
    return (d.day - 1) // 7 + 1


def rest_check(text: str, d: date) -> tuple[bool | None, list[str]]:
    """(휴무 여부, 메모). None = 판단 불가."""
    text = clean(text)
    if not text or "연중무휴" in text or text in ("없음", "무휴"):
        return False, []
    if UNSURE.search(text) and not re.search(r"요일|월 \d|설|추석|공휴일", text):
        return None, [f"휴무 정보 확인 필요: {text[:30]}"]
    closed, notes, understood = False, [], False
    for clause in re.split(r"[/\n]|※", text):
        c = clause.strip(" ,.-")
        if not c:
            continue
        # n번째 요일 (예: "3월, 11월 2번째 월요일")
        m = re.search(r"(\d)\s*(?:번째|째주|주)\s*([월화수목금토일])요일", c)
        if m:
            understood = True
            months = [int(x) for x in re.findall(r"(\d{1,2})월", c[:m.start()])]
            if (not months or d.month in months) and _nth_weekday(d) == int(m.group(1)) \
                    and WEEKDAYS[d.weekday()] == m.group(2):
                closed = True
            c = c[:m.start()] + c[m.end():]
        # 요일 범위 "화요일~수요일"
        for a, b in re.findall(r"([월화수목금토일])요일\s*~\s*([월화수목금토일])요일", c):
            understood = True
            i, j = WEEKDAYS.index(a), WEEKDAYS.index(b)
            span = range(i, j + 1) if i <= j else list(range(i, 7)) + list(range(0, j + 1))
            if d.weekday() in span:
                closed = True
        for w in re.findall(r"([월화수목금토일])요일", c):
            understood = True
            if WEEKDAYS[d.weekday()] == w:
                closed = True
        for mo, dy in re.findall(r"(\d{1,2})월\s*(\d{1,2})일", c):
            understood = True
            if d.month == int(mo) and d.day == int(dy):
                closed = True
        for name, table in (("설", SEOL), ("추석", CHUSEOK)):
            if name in c:
                understood = True
                key = "연휴" if "연휴" in c else ("전일" if "전일" in c else "당일")
                days = table[key] | (table["당일"] if "당일" in c else set())
                if d in days:
                    closed = True
        if "공휴일" in c:
            understood = True
            if d in HOLIDAYS:
                closed = True
    if not understood:
        return None, [f"휴무 정보 확인 필요: {text[:30]}"]
    return closed, notes


HOURS_KEYS = ("usetime", "usetimeculture", "opentimefood", "usetimeleports")
REST_KEYS = ("restdate", "restdateculture", "restdatefood", "restdateleports")


def parse_open(place: dict, d: date) -> OpenInfo:
    intro = place.get("intro") or {}
    kind = place.get("kind")
    if place.get("source") == "사용자 지정":
        return OpenInfo("확인 필요", notes=["사용자 지정 장소라 운영시간을 확인하지 못했어요"])
    if kind == "festival":
        s, e = place.get("event_start"), place.get("event_end")
        if s and e and not (s <= d.strftime("%Y%m%d") <= e):
            return OpenInfo("휴무", notes=["행사 기간이 아니에요"])
        return OpenInfo("확인됨", raw=clean(intro.get("playtime")))
    if kind == "lodging":
        t = clean(intro.get("checkintime"))
        m = re.search(r"(\d{1,2})\s*[:시]\s*(\d{2})?", t)
        if m:
            return OpenInfo("확인됨", open=_m(m.group(1), m.group(2) or "00"), close=1440, raw=t)
        return OpenInfo("확인 필요", open=15 * 60, raw=t, notes=["체크인 시간 확인 필요(기본 15:00)"])
    raw = next((clean(intro[k]) for k in HOURS_KEYS if clean(intro.get(k))), "")
    rest_raw = next((clean(intro[k]) for k in REST_KEYS if clean(intro.get(k))), "")
    closed, rnotes = rest_check(rest_raw, d)
    if closed:
        return OpenInfo("휴무", raw=raw, rest_raw=rest_raw, notes=[f"휴무일: {rest_raw}"])
    if UNSURE.search(raw) and not RANGE.search(raw) and "상시" not in raw:
        return OpenInfo("확인 필요", raw=raw, rest_raw=rest_raw, notes=[f"운영시간 확인 필요: {raw[:30]}"])
    parsed = parse_hours_text(raw, d)
    if parsed is None:
        return OpenInfo("확인 필요", raw=raw, rest_raw=rest_raw, notes=["운영시간 확인 필요"] + rnotes)
    o, c, br, notes = parsed
    status = "확인됨" if closed is False else "확인 필요"
    return OpenInfo(status, o, c, br, notes + rnotes, raw, rest_raw)


def fmt(minutes: int) -> str:
    minutes = max(0, min(minutes, 24 * 60))
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def parse_hm(s: str) -> int:
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def dates_of(start: date, days: int) -> list[date]:
    return [start + timedelta(days=i) for i in range(days)]
