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
            items = []
            while i < len(lines) and re.match(r"^\s*\d+\.\s+", lines[i]):
                items.append(re.sub(r"^\s*\d+\.\s+", "", lines[i]))
                i += 1
            out.append("<ol>" + "".join(f"<li>{_inline(x)}</li>" for x in items) + "</ol>")
            continue
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
            rows.append(f'<tr><td>{e(s["arrive"])}–{e(s["depart"])}</td><td><span class="mm-tag {cls}">{emo} {lab}</span></td>'
                        f'<td>{img}<strong>{e(s["name"])}</strong>{badge}<br><small class="mm-muted">{e(s.get("reason") or "")}</small></td>'
                        f'<td>{e(move)}</td><td class="mm-muted">{e(hours)}</td></tr>')
        if d["day"] == len(plan.get("itinerary", [])) and long.get("inbound"):
            i_ = long["inbound"]
            rows.append(f'<tr><td>{e(i_["depart"])}–{e(i_["arrive"])}</td><td><span class="mm-tag t-move">🏠 귀가</span></td>'
                        f'<td><strong>{e(i_["label"])}</strong></td><td>약 {i_["duration_min"]}분</td><td class="mm-muted">{e(i_["mode"])} · 추정</td></tr>')
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
