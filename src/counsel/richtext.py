"""모모 답변 서식(HTML) — 이모지·굵기·색·구분·표·사진 (SERVICE.md 3-6 (17)).

LLM이 HTML을 쓰지 않는다. 서버 코드가 plan·answer 텍스트를 받아 **모든 글자를 escape한 뒤** 정해진 태그·class로만 조립한다.
평가·RAGAS는 기존 평문 `answer`를 그대로 쓰고, 화면은 `answer_html`을 쓴다.
"""
from __future__ import annotations

import html
import re

e = html.escape
TYPE_TAG = {"place": ("🏛", "관광", "t-place"), "meal": ("🍽", "식사", "t-meal"), "stay": ("🛏", "숙박", "t-stay"),
            "move": ("🚗", "이동", "t-move")}
CAT_EMOJI = {"장거리 교통": "🚄", "현지 교통": "🚌", "유류비": "⛽", "통행료": "🛣", "주차비": "🅿", "렌터카": "🚙",
             "식비": "🍚", "카페·디저트": "☕", "간식·음료": "🧃", "입장료": "🎫", "체험": "🎨", "숙박": "🛏",
             "기념품": "🎁", "기타": "🧾"}
SAFE_IMG = re.compile(r"^https://(tong\.visitkorea\.or\.kr|commons\.wikimedia\.org|upload\.wikimedia\.org)/")


def _img(url: str | None, alt: str) -> str:
    if url and SAFE_IMG.match(url):
        return f'<img class="mm-thumb" src="{e(url)}" alt="{e(alt)}" loading="lazy">'
    return ""


def _inline(t: str) -> str:
    """escape 후 **굵게**만 허용."""
    t = e(t)
    return re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", t)


NUM_RE = re.compile(r"^\s*(\d+)\.\s+(.*)")
SUB_RE = re.compile(r"^\s*[-*•]\s+(.*)")
# 카드 안 한 줄의 종류: (이모지, 라벨, 판정 정규식) — 라벨이 있으면 라벨로, 없으면 내용으로 판정
ROW_KINDS = [
    ("📅", "휴무", re.compile(r"휴무|무휴|쉬는 ?날|정기 ?휴")),
    ("🕘", "운영", re.compile(r"운영|영업|이용 ?시간|마지막 ?주문|브레이크|준비 ?시간|\d{1,2}:\d{2}")),
    ("📞", "전화", re.compile(r"전화|연락|문의|0\d{1,3}-\d{3,4}-\d{4}|\[전화번호\]")),
    ("📍", "위치", re.compile(r"주소|위치|^[가-힣]+[시군구]\s")),
    ("💰", "가격", re.compile(r"가격|요금|입장료|\d[\d,]*\s*원")),
    ("🅿", "주차", re.compile(r"주차")),
    ("📌", "예약", re.compile(r"예약")),
    ("✨", "대표", re.compile(r"")),
]
ROW_CLASS = {"휴무": "rest", "운영": "hours", "전화": "tel", "위치": "addr", "가격": "price", "주차": "park", "예약": "book", "대표": "main"}
LABEL_RE = re.compile(r"^(대표|메뉴|특징|운영|영업|운영시간|휴무|휴무일|전화|연락처|주소|위치|가격|요금|주차|예약)\s*[:：]\s*")


def _numbered(lines: list[str], i: int) -> tuple[list[tuple[int, str, list[str]]], int]:
    """번호 목록과 그 아래 하위 줄(들여쓴·이어지는 '-' 줄)을 함께 모은다. 항목 사이 빈 줄은 건너뛴다."""
    items: list[tuple[int, str, list[str]]] = []
    while i < len(lines):
        m = NUM_RE.match(lines[i])
        if not m:
            break
        num, title, sub = int(m.group(1)), m.group(2).strip(), []
        i += 1
        while i < len(lines):
            sm = SUB_RE.match(lines[i])
            if sm:
                sub.append(sm.group(1).strip())
                i += 1
            elif lines[i].strip() == "":
                j = i
                while j < len(lines) and not lines[j].strip():
                    j += 1
                if j < len(lines) and (NUM_RE.match(lines[j]) or (SUB_RE.match(lines[j]) and lines[j][:1].isspace())):
                    i = j
                    if NUM_RE.match(lines[j]):
                        break
                else:
                    break
            elif lines[i][:1].isspace() and not NUM_RE.match(lines[i]):
                sub.append(lines[i].strip())      # 들여쓴 이어지는 줄
                i += 1
            else:
                break
        items.append((num if not items else items[-1][0] + 1, title, sub))   # LLM이 1. 1. 1.로 써도 번호를 이어 붙인다
    return items, i


def _row(text: str) -> tuple[str, str, str]:
    """(이모지, 라벨, HTML) — 휴무·연중무휴는 색 배지."""
    lm = LABEL_RE.match(text)
    body = text[lm.end():] if lm else text
    probe = lm.group(1) if lm else text
    emo, label = next((em, lb) for em, lb, rx in ROW_KINDS if rx.search(probe))
    h = _inline(body)
    if label == "휴무":
        if re.search(r"연중\s*무휴|없음", h):
            h = re.sub(r"(연중\s*무휴|없음)", r'<span class="mm-badge ok">\1</span>', h, count=1)
        else:
            h = f'<span class="mm-badge off">{h}</span>'      # '매주 화요일', '매주 화요일 휴무' 모두
    return emo, label, h


def _cards(items: list[tuple[int, str, list[str]]]) -> str:
    """장소 추천 목록 → 번호 카드. 한 줄에 ' / '·' | '로 묶인 정보는 나눠서 종류별 줄로."""
    out = ['<div class="mm-cards">']
    for num, title, sub in items:
        tm = re.match(r"^(.*?)\s*[(（]([^()（）]{1,20})[)）]\s*$", title)
        name, area = (tm.group(1), tm.group(2)) if tm else (title, "")
        rows: list[tuple[str, str, str]] = []
        for line in sub:
            for seg in re.split(r"\s+[/|]\s+", line):
                if not seg.strip():
                    continue
                emo, label, h = _row(seg.strip())
                if rows and rows[-1][1] == label:
                    rows[-1] = (emo, label, rows[-1][2] + " · " + h)
                else:
                    rows.append((emo, label, h))
        out.append('<div class="mm-card"><div class="mm-card-h">'
                   f'<span class="mm-num">{num}</span><span class="mm-card-t">{_inline(name)}</span>'
                   + (f'<span class="mm-tag">{e(area)}</span>' if area else "") + "</div>"
                   + ('<div class="mm-card-b">' + "".join(
                       f'<div class="mm-row r-{ROW_CLASS[lbl]}"><span class="mm-ico">{emo}</span><span class="mm-val">{h}</span></div>'
                       for emo, lbl, h in rows)
                      + "</div>" if rows else "")
                   + "</div>")
    out.append("</div>")
    return "".join(out)


def md_to_html(text: str) -> str:
    """정보 답변(LLM 마크다운 비슷한 글) → 안전한 HTML: 제목·목록·표·굵게·구분선."""
    lines = (text or "").splitlines()
    out, i = ['<div class="mm">'], 0
    while i < len(lines):
        ln = lines[i].rstrip()
        s = ln.strip()
        if not s:
            i += 1
            continue
        if s.startswith("|") and i + 1 < len(lines) and re.match(r"^\|?\s*:?-{2,}", lines[i + 1].strip()):
            head = [c.strip() for c in s.strip("|").split("|")]
            i += 2
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            out.append('<div class="mm-scroll"><table class="mm-table"><thead><tr>' + "".join(f"<th>{_inline(h)}</th>" for h in head)
                       + "</tr></thead><tbody>" + "".join("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in r) + "</tr>" for r in rows)
                       + "</tbody></table></div>")
            continue
        m = re.match(r"^(#{1,4})\s+(.*)", s)
        if m:
            out.append(f'<h4 class="mm-head">{_inline(m.group(2))}</h4>')
        elif re.match(r"^(-{3,}|_{3,})$", s):
            out.append('<hr class="mm-hr">')
        elif re.match(r"^([-*•])\s+", s):
            items = []
            while i < len(lines) and re.match(r"^\s*([-*•])\s+", lines[i]):
                items.append(re.sub(r"^\s*[-*•]\s+", "", lines[i]))
                i += 1
            out.append("<ul>" + "".join(f"<li>{_inline(x)}</li>" for x in items) + "</ul>")
            continue
        elif re.match(r"^\d+\.\s+", s):
            items, i = _numbered(lines, i)
            if any(sub for _, _, sub in items):
                out.append(_cards(items))
            else:
                out.append(f'<ol start="{items[0][0]}">' + "".join(f"<li>{_inline(t)}</li>" for _, t, _ in items) + "</ol>")
            continue
        elif re.fullmatch(r"\*\*[^*]+\*\*:?", s):
            out.append(f'<h4 class="mm-head">{_inline(s.rstrip(":"))}</h4>')
        elif s.startswith(("※", "⚠")):
            out.append(f'<p class="mm-note warn">{_inline(s)}</p>')
        elif s.startswith("☁"):
            out.append(f'<p class="mm-note">{_inline(s)}</p>')
        elif "출처" in s and len(s) < 120:
            out.append(f'<p class="mm-src">{_inline(s)}</p>')
        else:
            out.append(f"<p>{_inline(s)}</p>")
        i += 1
    out.append("</div>")
    return "".join(out)


def _won(n) -> str:
    return "확인 필요" if n is None else f"{n:,}원"


def plan_html(plan: dict, answer: str, changes: list[str] | None = None, status: str = "") -> str:
    """일정 응답: 제목 → 바뀐 점 → 안내·가정 → 조건 요약 → 일자별 표(사진·이동·운영) → 경비 → 주의 → 출처."""
    t = plan.get("trip") or {}
    places = {p["id"]: p for p in plan.get("places", [])}
    parts = ['<div class="mm">']
    title = ("📮 계획서를 다시 맞췄어요" if changes else "✉️ 계획서를 엽서처럼 정리했어요")
    parts.append(f'<h3 class="mm-head">{title} · {e(t.get("title", ""))}</h3>')
    for c in changes or []:
        parts.append(f'<p class="mm-note ok">✅ <strong>{e(c)}</strong></p>')
    notes = [ln.strip()[1:].strip() for ln in (answer or "").splitlines() if ln.strip().startswith("※")]
    weather = next((ln.strip()[1:].strip() for ln in (answer or "").splitlines() if ln.strip().startswith("☁")), None)
    rank_line = next((ln.strip() for ln in (answer or "").splitlines() if ln.strip().startswith("추천 ")), None)
    if rank_line:
        parts.append(f'<p class="mm-note">⭐ {e(rank_line)}</p>')
    for n in notes[:5]:
        parts.append(f'<p class="mm-note warn">💡 {e(n)}</p>')
    if weather:
        parts.append(f'<p class="mm-note">🌤 {e(weather)}</p>')
    prog = (t.get("progress") or {})
    kv = [("📅 기간", f"{t.get('start_date', '')} · {'당일치기' if not t.get('nights') else str(t['nights']) + '박 ' + str(t['nights'] + 1) + '일'}"),
          ("🚩 출발", t.get("origin") or "미정"), ("🚆 이동", f"{t.get('transport', {}).get('long_distance', '')} + {t.get('transport', {}).get('local', '')}"),
          ("🧭 강도", t.get("intensity", "")), ("👨‍👩‍👧 동반", f"{t.get('companions', {}).get('type', '')} {t.get('companions', {}).get('count') or ''}명".replace(" 명", "")),
          ("✅ 확정", prog.get("label", ""))]
    parts.append('<table class="mm-kv"><tbody>' + "".join(f"<tr><th>{e(k)}</th><td>{e(str(v))}</td></tr>" for k, v in kv) + "</tbody></table>")
    long = (plan.get("totals") or {}).get("long_distance") or {}
    for d in plan.get("itinerary", []):
        rows = []
        n_days = len(plan.get("itinerary", []))
        if d["day"] == 1:           # 출발 지점은 항상 첫 줄(모르면 미정)
            sp = d.get("start_point") or {}
            rows.append(f'<tr class="mm-home"><td>{e(sp.get("time") or "—")}</td><td><span class="mm-tag t-move">🏠 출발</span></td>'
                        f'<td><strong>{e(sp.get("name") or "출발지 미정")}</strong>'
                        f'{"<br><small class=mm-muted>" + e(sp["label"]) + "</small>" if sp.get("label") and sp.get("label") != sp.get("name") else ""}'
                        f'{"" if sp else "<br><small class=mm-muted>여행 조건에서 출발지를 정하면 오가는 시간까지 넣어요</small>"}</td><td>—</td><td class="mm-muted">출발 지점</td></tr>')
        if d["day"] == 1 and long.get("outbound"):
            o = long["outbound"]
            rows.append(f'<tr><td>{e(o["depart"])}–{e(o["arrive"])}</td><td><span class="mm-tag t-move">🚗 이동</span></td>'
                        f'<td><strong>{e(o["label"])}</strong></td><td>약 {o["duration_min"]}분</td><td class="mm-muted">{e(o["mode"])} · 추정</td></tr>')
        legs = {lg.get("to"): lg for lg in d.get("legs", [])}
        for s in d.get("stops", []):
            emo, lab, cls = TYPE_TAG.get(s["type"], TYPE_TAG["place"])
            p = places.get(s.get("place_id")) or {}
            img = _img(((p.get("images") or [{}])[0].get("thumb") or (p.get("images") or [{}])[0].get("url")) if p.get("images") else None, s["name"])
            lg = legs.get(s.get("place_id"))
            move = f"약 {lg['duration_min']}분 · {lg['distance_km']}km" if lg else "—"
            hours = p.get("hours") or ("운영시간 확인 필요" if s.get("hours_status") == "확인 필요" else "")
            badge = f' <span class="mm-tag t-place">⭐ {p["rank"]}위</span>' if p.get("rank") else ""
            rows.append(f'<tr><td>{e(s.get("arrive_label") or s["arrive"])}–{e(s.get("depart_label") or s["depart"])}</td><td><span class="mm-tag {cls}">{emo} {lab}</span></td>'
                        f'<td>{img}<strong>{e(s["name"])}</strong>{badge}<br><small class="mm-muted">{e(s.get("reason") or "")}</small></td>'
                        f'<td>{e(move)}</td><td class="mm-muted">{e(hours)}</td></tr>')
        if d["day"] == len(plan.get("itinerary", [])) and long.get("inbound"):
            i_ = long["inbound"]
            rows.append(f'<tr><td>{e(i_["depart"])}–{e(i_["arrive"])}</td><td><span class="mm-tag t-move">🏠 귀가</span></td>'
                        f'<td><strong>{e(i_["label"])}</strong></td><td>약 {i_["duration_min"]}분</td><td class="mm-muted">{e(i_["mode"])} · 추정</td></tr>')
        if d["day"] == n_days:      # 최종 복귀 지점은 항상 마지막 줄(모르면 미정)
            ep = d.get("end_point") or {}
            rows.append(f'<tr class="mm-home"><td>{e(ep.get("time") or "—")}</td><td><span class="mm-tag t-move">🏁 복귀</span></td>'
                        f'<td><strong>{e(ep.get("name") or "복귀 지점 미정")}</strong>{" 도착" if ep.get("time") else ""}'
                        f'{"<br><small class=mm-muted>" + e(ep["label"]) + "</small>" if ep.get("label") and ep.get("label") != ep.get("name") else ""}</td>'
                        f'<td>—</td><td class="mm-muted">최종 복귀 지점</td></tr>')
        parts.append(f'<section class="mm-day"><h4>🗓 {d["day"]}일차 · {e(d["date"])}({e(d.get("weekday", ""))}) '
                     f'<small class="mm-muted">{e(d.get("theme", ""))} · 이동 약 {d.get("day_travel_min", 0)}분</small></h4>'
                     '<div class="mm-scroll"><table class="mm-table"><thead><tr><th>시간</th><th>구분</th><th>장소</th><th>이동</th><th>운영</th></tr></thead>'
                     f'<tbody>{"".join(rows)}</tbody></table></div></section>')
    c = plan.get("costs")
    if c:
        rows = "".join(f'<tr><td>{CAT_EMOJI.get(i["category"], "🧾")} {e(i["category"])}</td><td>{e(i["label"])}</td>'
                       f'<td class="num">{_won(i["amount"])}</td></tr>' for i in c["items"])
        budget = (f' · 예산 {_won(c["budget"])} 대비 <strong class="{"warn" if c.get("over_budget") else "ok"}">'
                  f'{round(100 * c["total"] / c["budget"]) if c.get("budget") else 0}%</strong>') if c.get("budget") else ""
        parts.append('<details class="mm-cost"><summary>💰 예상 경비 <strong class="mm-total">'
                     f'{_won(c["total"])}</strong> · 1인 {_won(c["per_person"])}{budget}</summary>'
                     f'<div class="mm-scroll"><table class="mm-table"><tbody>{rows}</tbody></table></div>'
                     f'<p class="mm-muted">{e(c.get("note", ""))}</p></details>')
    for w in (plan.get("warnings") or [])[:4]:
        parts.append(f'<p class="mm-note warn">⚠️ {e(w)}</p>')
    if status == "승인 대기":
        parts.append('<p class="mm-note ok">📬 마음에 드시면 <strong>확정</strong>을 눌러 주세요. 바꾸고 싶은 곳은 카드나 오른쪽 조건에서 바로 고칠 수 있어요.</p>')
    parts.append('<p class="mm-src">출처: 한국관광공사 TourAPI(공공누리) · 이동시간·경비는 추정이에요.</p></div>')
    return "".join(parts)


def page(fragment: str, title: str = "모모의 답변") -> str:
    """새 창으로 보기용 독립 페이지."""
    return f"""<!doctype html><html lang=ko><head><meta charset=utf-8><meta name=viewport content="width=device-width, initial-scale=1">
<title>{e(title)}</title><link rel=stylesheet href="https://fonts.googleapis.com/css2?family=Jua&family=Noto+Sans+KR:wght@400;500;700&display=swap">
<style>
:root{{--bg:#f3f7fb;--paper:#fffdf8;--ink:#23324a;--muted:#5f6f86;--line:#d9e3ee;--brand:#3a5a8c;--warn:#b5651d;--warn-soft:#fdf1e3;--ok:#2f8a5b;--ok-soft:#e6f4ec;--info-soft:#e8f2fb}}
@media (prefers-color-scheme:dark){{:root{{--bg:#10171e;--paper:#18212b;--ink:#e6edf5;--muted:#a3b2c4;--line:#2c3b4a;--brand:#9cc3f0;--warn:#f2a65a;--warn-soft:#3a2a18;--ok:#6fcf97;--ok-soft:#173226;--info-soft:#1c2b3a}}}}
body{{margin:0;background:var(--bg);color:var(--ink);font-family:"Noto Sans KR","Malgun Gothic",sans-serif;line-height:1.6;padding:24px max(16px,env(safe-area-inset-left))}}
.mm{{max-width:900px;margin:0 auto;background:var(--paper);border:1px solid var(--line);border-radius:16px;padding:20px 22px}}
.mm-head{{font-family:Jua,"Noto Sans KR",sans-serif;font-weight:400;font-size:1.35rem;margin:0 0 10px}}
.mm-note{{background:var(--info-soft);border-radius:10px;padding:8px 12px;margin:6px 0}}.mm-note.warn{{background:var(--warn-soft)}}.mm-note.ok{{background:var(--ok-soft)}}
.mm-kv{{border-collapse:collapse;margin:10px 0;font-size:.92rem}}.mm-kv th{{text-align:left;color:var(--muted);font-weight:500;padding:3px 14px 3px 0}}
.mm-day h4{{margin:18px 0 6px}}.mm-scroll{{overflow-x:auto}}.mm-table{{width:100%;border-collapse:collapse;font-size:.9rem}}
.mm-table th,.mm-table td{{border-bottom:1px solid var(--line);padding:7px 8px;text-align:left;vertical-align:top}}.mm-table td.num{{text-align:right;font-variant-numeric:tabular-nums}}
.mm-tag{{font-size:.75rem;border-radius:999px;padding:1px 8px;background:var(--info-soft);white-space:nowrap}}.t-meal{{background:var(--warn-soft)}}.t-stay{{background:var(--ok-soft)}}
.mm-thumb{{width:36px;height:36px;border-radius:8px;object-fit:cover;vertical-align:middle;margin-right:8px}}
.mm-muted{{color:var(--muted)}}.mm-total{{font-size:1.1rem}}.warn{{color:var(--warn)}}.ok{{color:var(--ok)}}.mm-src{{color:var(--muted);font-size:.8rem;margin-top:14px}}
details.mm-cost{{margin:12px 0}}summary{{cursor:pointer}}
</style></head><body>{fragment}</body></html>"""
