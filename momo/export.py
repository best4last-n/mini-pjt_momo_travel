"""확정 계획 내보내기(DOCX)와 여행 결과 리포트(HTML) — SERVICE.md 3-6 (9)(11).

- DOCX: python-docx. 개요·확정 요소·일자별 일정(사진 1장·특장점·관람 팁)·경비(계획/실제)·주의 사항·출처
- 리포트: 여행 후 실제 지출을 계획 경비와 나란히 정리한 단일 HTML(인쇄 가능)
"""
from __future__ import annotations

import html
import io

import requests

from . import costs

UA = {"User-Agent": "momo-travel-counselor/0.1 (education project)"}
LICENSE = {"Type1": "공공누리 제1유형", "Type3": "공공누리 제3유형(변경 금지)"}


def _places(plan: dict) -> dict:
    return {p["id"]: p for p in plan.get("places", [])}


def _img_bytes(url: str) -> bytes | None:
    try:
        r = requests.get(url, timeout=5, headers=UA)
        if r.ok and r.headers.get("content-type", "").startswith("image/"):
            return r.content
    except Exception:
        return None
    return None


def to_docx(plan_id: str, payload: dict, summary: dict | None = None) -> bytes:
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml.ns import qn
    from docx.shared import Cm, Pt, RGBColor

    plan = payload.get("plan") or {}
    trip = plan.get("trip") or {}
    doc = Document()
    st = doc.styles["Normal"]
    st.font.name = "맑은 고딕"
    st.element.rPr.rFonts.set(qn("w:eastAsia"), "맑은 고딕")
    st.font.size = Pt(10)
    for s in doc.sections:
        s.left_margin = s.right_margin = Cm(2)
    accent = RGBColor(0x3A, 0x5A, 0x8C)

    h = doc.add_heading(payload.get("title") or trip.get("title") or "모모 여행 일정", level=0)
    h.alignment = WD_ALIGN_PARAGRAPH.LEFT
    doc.add_paragraph(f"우체부 철새 모모의 여행 계획서 · 공유 링크 /plan/{plan_id}").runs[0].font.color.rgb = accent

    doc.add_heading("여행 개요", level=1)
    t = doc.add_table(rows=0, cols=3)
    t.style = "Light Grid Accent 1"
    mark = {"confirmed": "확정", "assumed": "가정", "missing": "미정", "optional": "선택"}
    for c in trip.get("checklist", []):
        r = t.add_row().cells
        r[0].text, r[1].text, r[2].text = c["label"], str(c["value"]), mark.get(c["status"], "")
    if trip.get("assumptions"):
        doc.add_paragraph("가정·안내: " + " / ".join(trip["assumptions"]))

    places = _places(plan)
    long = (plan.get("totals") or {}).get("long_distance") or {}
    n_days = len(plan.get("itinerary", []))
    for day in plan.get("itinerary", []):
        doc.add_heading(f"{day['day']}일차 · {day['date']}({day.get('weekday', '')}) · {day.get('theme', '')}", level=1)
        if day["day"] == 1:          # 출발 지점(항상, 모르면 미정)
            sp = day.get("start_point") or {}
            q = doc.add_paragraph()
            q.add_run(f"🏠 출발 지점  {sp.get('time') or ''}  ").bold = True
            q.add_run(sp.get("label") or sp.get("name") or "미정 — 여행 조건에서 출발지를 정해 주세요")
        if day["day"] == 1 and long.get("outbound"):
            o = long["outbound"]
            doc.add_paragraph(f"[{o['mode']}] {o['label']} {o['depart']}→{o['arrive']} (약 {o['duration_min']}분, 추정)")
        legs = {lg.get("to"): lg for lg in day.get("legs", [])}
        for s in day.get("stops", []):
            lg = legs.get(s.get("place_id"))
            if lg:
                doc.add_paragraph(f"▶ {lg['distance_km']}km · 약 {lg['duration_min']}분 · {lg['mode']}(추정)").runs[0].font.size = Pt(8.5)
            p = places.get(s.get("place_id")) or {}
            para = doc.add_paragraph()
            run = para.add_run(f"{s.get('arrive_label') or s['arrive']}–{s.get('depart_label') or s['depart']}  {s['name']}")
            run.bold = True
            run.font.size = Pt(11.5)
            if p.get("category"):
                para.add_run(f"  ({p['category']})").font.color.rgb = accent
            imgs = p.get("images") or []
            if imgs:
                data = _img_bytes(imgs[0].get("thumb") or imgs[0]["url"])
                if data:
                    try:
                        doc.add_picture(io.BytesIO(data), width=Cm(7))
                        cap = doc.add_paragraph(f"사진: 한국관광공사 · {LICENSE.get(imgs[0].get('license'), imgs[0].get('license') or '공공누리')}")
                        cap.runs[0].font.size = Pt(7.5)
                    except Exception:
                        pass
            for label, val in (("추천 이유", s.get("reason") or p.get("reason")), ("소개", p.get("summary")),
                               ("관람 팁", p.get("guide_tips")), ("동반자 참고", p.get("companion_notes")),
                               ("운영", p.get("hours")), ("휴무", p.get("closed")),
                               ("요금", p.get("fee") or ("홈페이지에서 입장료를 확인해 주세요" if p.get("fee_check") else None)),
                               ("홈페이지", " · ".join(f"{l['label']} {l['url']}" for l in p.get("links") or []))):
                if val:
                    q = doc.add_paragraph()
                    q.add_run(f"{label}: ").bold = True
                    q.add_run(str(val))
            for hl in p.get("highlights") or []:
                doc.add_paragraph(hl, style="List Bullet")
            near = [f"{x['name']}({x['distance_km']}km)" for x in (p.get("nearby_sights") or [])]
            food = [f"{x['name']}({x['distance_km']}km)" for x in (p.get("foods") or [])]
            if near or food:
                doc.add_paragraph(("인근 볼거리: " + ", ".join(near) if near else "") + ("  ·  " if near and food else "")
                                  + ("먹거리: " + ", ".join(food) if food else "")).runs[0].font.size = Pt(9)
        doc.add_paragraph(f"이 날 이동 합계: 약 {day.get('day_travel_min', 0)}분 · {day.get('day_distance_km', 0)}km (추정)")
    if long.get("inbound"):
        i = long["inbound"]
        doc.add_paragraph(f"[{i['mode']}] {i['label']} {i['depart']}→{i['arrive']} (약 {i['duration_min']}분, 추정)")
    if n_days:                       # 최종 복귀 지점(항상, 모르면 미정)
        ep = (plan.get("itinerary") or [{}])[-1].get("end_point") or {}
        q = doc.add_paragraph()
        q.add_run(f"🏁 최종 복귀 지점  {ep.get('time') or ''}  ").bold = True
        q.add_run((ep.get("label") or ep.get("name") or "미정 — 출발지(또는 여행 후 도착지)를 정해 주세요") + (" 도착" if ep.get("time") else ""))

    c = plan.get("costs")
    if c:
        doc.add_heading("예상 경비 (추정)", level=1)
        t = doc.add_table(rows=1, cols=3)
        t.style = "Light Grid Accent 1"
        t.rows[0].cells[0].text, t.rows[0].cells[1].text, t.rows[0].cells[2].text = "분류", "내용", "금액"
        for it in c["items"]:
            r = t.add_row().cells
            r[0].text, r[1].text, r[2].text = it["category"], it["label"], costs.fmt_won(it["amount"])
        doc.add_paragraph(f"합계 {costs.fmt_won(c['total'])} · {c['people']}명 · 1인 {costs.fmt_won(c['per_person'])}"
                          + (f" · 예산 {costs.fmt_won(c['budget'])}" if c.get("budget") else ""))
        doc.add_paragraph(c.get("note", "")).runs[0].font.size = Pt(8.5)
    if summary and summary.get("total_actual"):
        doc.add_heading("여행 경비 정산 (계획 대비 실제)", level=1)
        t = doc.add_table(rows=1, cols=4)
        t.style = "Light Grid Accent 1"
        for i, k in enumerate(("분류", "계획", "실제", "차이")):
            t.rows[0].cells[i].text = k
        for row in summary["by_category"]:
            r = t.add_row().cells
            r[0].text, r[1].text, r[2].text = row["category"], costs.fmt_won(row["planned"]), costs.fmt_won(row["actual"])
            r[3].text = f"{row['diff']:+,}원"
        doc.add_paragraph(f"실제 합계 {costs.fmt_won(summary['total_actual'])} · 1인 {costs.fmt_won(summary['per_person_actual'])}")
    if plan.get("warnings"):
        doc.add_heading("주의 사항", level=1)
        for w in plan["warnings"]:
            doc.add_paragraph(w, style="List Bullet")
    doc.add_paragraph("장소·운영시간·사진 출처: 한국관광공사 TourAPI(공공누리). 이동시간·경비는 추정이며 실제와 다를 수 있어요.").runs[0].font.size = Pt(8)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def report_html(plan_id: str, payload: dict, ex: dict) -> str:
    """여행 결과 리포트: 다녀온 일정 요약 + 경비 정산(계획 대비 실제) + 지출 내역."""
    e = html.escape
    plan = payload.get("plan") or {}
    trip = plan.get("trip") or {}
    s = ex["summary"]
    rows_cat = "".join(
        f"<tr><td>{e(r['category'])}</td><td class=n>{costs.fmt_won(r['planned'])}</td><td class=n>{costs.fmt_won(r['actual'])}</td>"
        f"<td class='n {'over' if r['diff'] > 0 else 'under'}'>{r['diff']:+,}원</td></tr>" for r in s["by_category"])
    rows_act = "".join(
        f"<tr><td>{e(a['date'])}</td><td>{e(a['category'])}</td><td>{e(a['label'])}</td><td class=n>{a['amount']:,}원</td>"
        f"<td>{e(a.get('memo') or '')}</td></tr>" for a in ex["actual"]) or "<tr><td colspan=5 class=muted>아직 입력한 실제 지출이 없어요. 공유 페이지의 '여행 경비 정산'에서 입력하세요.</td></tr>"
    days = ""
    for d in plan.get("itinerary", []):
        stops = " → ".join(e(x["name"]) for x in d.get("stops", []))
        days += f"<li><b>{d['day']}일차 {e(d['date'])}({e(d.get('weekday', ''))})</b> · {e(d.get('theme', ''))}<br><span class=muted>{stops}</span></li>"
    budget = s.get("budget")
    bar = ""
    if budget:
        pct = min(100, round(100 * s["total_actual"] / budget)) if budget else 0
        bar = (f"<div class=bar><i style='width:{pct}%' class='{'over' if s['total_actual'] > budget else ''}'></i></div>"
               f"<p class=muted>예산 {costs.fmt_won(budget)} 중 {pct}% 사용</p>")
    diff = s["total_actual"] - s["total_planned"]
    return f"""<!doctype html><html lang=ko><head><meta charset=utf-8><meta name=viewport content="width=device-width, initial-scale=1">
<title>{e(trip.get('title') or payload.get('title') or '여행')} 결과 리포트</title>
<link rel=stylesheet href="https://fonts.googleapis.com/css2?family=Jua&family=Noto+Sans+KR:wght@400;500;700&display=swap">
<style>
:root{{--bg:#f3f7fb;--paper:#fffdf8;--ink:#23324a;--muted:#5f6f86;--line:#d9e3ee;--accent:#3a5a8c;--sky:#a9d4f0;--ok:#2f8a5b;--warn:#b5651d}}
@media (prefers-color-scheme:dark){{:root{{--bg:#10171e;--paper:#18212b;--ink:#e6edf5;--muted:#a3b2c4;--line:#2c3b4a;--accent:#9cc3f0;--sky:#4f8fb8;--ok:#6fcf97;--warn:#f2a65a}}}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font-family:"Noto Sans KR","Malgun Gothic",sans-serif;line-height:1.6;padding:28px max(16px,env(safe-area-inset-left))}}
main{{max-width:860px;margin:0 auto;display:grid;gap:22px}}h1,h2{{font-family:Jua,"Noto Sans KR",sans-serif;font-weight:400;margin:0}}
.card{{background:var(--paper);border:1px solid var(--line);border-radius:14px;padding:16px 18px}}
.kpi{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}}.kpi div{{background:var(--paper);border:1px solid var(--line);border-radius:12px;padding:12px}}
.kpi b{{display:block;font-size:1.35rem;font-variant-numeric:tabular-nums}}table{{width:100%;border-collapse:collapse;font-size:.9rem}}
th,td{{text-align:left;padding:7px 8px;border-bottom:1px solid var(--line)}}td.n{{text-align:right;font-variant-numeric:tabular-nums}}.over{{color:var(--warn)}}.under{{color:var(--ok)}}
.muted{{color:var(--muted);font-size:.85rem}}.bar{{height:10px;background:var(--line);border-radius:999px;overflow:hidden}}.bar i{{display:block;height:100%;background:var(--accent)}}.bar i.over{{background:var(--warn)}}
.wrap{{overflow-x:auto}}ul{{margin:0;padding-left:18px}}@media print{{body{{background:#fff}}.noprint{{display:none}}}}
</style></head><body><main>
<header><h1>{e(trip.get('title') or payload.get('title') or '')} · 여행 결과 리포트</h1>
<p class=muted>{e(trip.get('start_date') or '')} 출발 · {e(str(trip.get('origin') or ''))} · {s['people']}명 · 우체부 철새 모모가 정리했어요</p></header>
<section class=kpi><div>계획 경비(추정)<b>{costs.fmt_won(s['total_planned'])}</b></div><div>실제 지출<b>{costs.fmt_won(s['total_actual'])}</b></div>
<div>차이<b class="{'over' if diff > 0 else 'under'}">{diff:+,}원</b></div><div>1인당 실제<b>{costs.fmt_won(s['per_person_actual'])}</b></div></section>
<section class=card>{bar or '<p class=muted>최대 예산을 정하지 않았어요.</p>'}</section>
<section class=card><h2>분류별 계획 대비 실제</h2><div class=wrap><table><thead><tr><th>분류</th><th>계획</th><th>실제</th><th>차이</th></tr></thead><tbody>{rows_cat}</tbody></table></div></section>
<section class=card><h2>지출 내역</h2><div class=wrap><table><thead><tr><th>날짜</th><th>분류</th><th>내용</th><th>금액</th><th>메모</th></tr></thead><tbody>{rows_act}</tbody></table></div></section>
<section class=card><h2>다녀온 일정</h2><ul>{days}</ul></section>
<p class=muted>계획 경비는 추정치예요. 장소·운영시간 출처: 한국관광공사 TourAPI(공공누리). <a class=noprint href="/plan/{e(plan_id)}">공유 페이지로</a></p>
</main></body></html>"""
