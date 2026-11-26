"""FastAPI: POST /query(가이드 표준 규약) + 단일 페이지 + 공유 링크·.ics.

실행:  uvicorn app.main:app --port 8000
"""
from __future__ import annotations

import html
import json
import logging
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from momo import agent, guardrails, store
from momo.config import MAX_QUESTION_CHARS

app = FastAPI(title="모모의 여행 상담소")
STATIC = Path(__file__).parent / "static"
log = logging.getLogger("momo")
app.mount("/static", StaticFiles(directory=STATIC), name="static")


class InvalidRequest(Exception):
    def __init__(self, kind: str, answer: str, code: int = 400, summary: str = ""):
        self.kind, self.answer, self.code, self.summary = kind, answer, code, summary


@app.exception_handler(InvalidRequest)
async def _invalid(_: Request, e: InvalidRequest):
    return JSONResponse(status_code=e.code, content={
        "answer": e.answer, "contexts": [], "status": "오류",
        "trace": [{"step": "error", "input": guardrails.mask_pii(e.summary)[0][:200], "output": e.kind}]})


@app.exception_handler(Exception)
async def _unexpected(_: Request, e: Exception):
    log.exception("unexpected error")          # 스택은 서버 로그에만
    return JSONResponse(status_code=500, content={
        "answer": "앗, 편지를 전하다가 바람에 날려 버렸어요. 잠시 후 다시 보내 주실래요?", "contexts": [], "status": "오류",
        "trace": [{"step": "error", "input": "", "output": type(e).__name__}]})


def _prepare(body) -> dict:
    """요청 검증(빠름). 실패하면 InvalidRequest. 통과하면 실행 인자."""
    if not isinstance(body, dict) or not isinstance(body.get("question", ""), str)             or not isinstance(body.get("session_id") or "", str) or not isinstance(body.get("action") or "", str):
        raise InvalidRequest("형식 오류", "앗, 편지 형식이 조금 달라서 읽을 수가 없었어요. 질문을 글로 담아 다시 보내 주실래요?",
                             summary=str(body)[:200])
    q = (body.get("question") or "").strip()
    action = body.get("action") or None
    sid = body.get("session_id") or None
    if action not in (None, "approve", "edit", "set", "start", "cost", "pack"):
        raise InvalidRequest("알 수 없는 action", "앗, 모르는 요청이에요. 확정은 화면의 '확정할게요' 버튼으로 해 주세요.", summary=str(action))
    if len(q) > MAX_QUESTION_CHARS:
        raise InvalidRequest("길이 초과", f"앗, 편지가 너무 길어서 가방에 안 들어가요. {MAX_QUESTION_CHARS}자 안으로 줄여서 다시 보내 주실래요?",
                             code=413, summary=f"{len(q)}자")
    if action in ("edit", "set", "start", "cost", "pack") and not isinstance(body.get(action), dict):
        raise InvalidRequest("형식 오류", "앗, 바꿀 내용을 알아보지 못했어요. 화면에서 다시 눌러 주실래요?", summary=f"{action} 본문 없음")
    if action in ("approve", "edit", "set", "cost", "pack"):
        if not sid:
            raise InvalidRequest("세션 없음", "앗, 어떤 일정을 확정할지 몰라요. 일정을 먼저 만들어 볼까요?", summary="approve without session")
        state = agent.graph().get_state({"configurable": {"thread_id": sid}}).values
        if not state:
            raise InvalidRequest("없는 세션", "앗, 그 상담 기록을 찾지 못했어요. 일정을 새로 만들어 볼까요?", summary="unknown session")
    elif not q and action != "start":
        raise InvalidRequest("빈 질문", "앗, 질문이 비어 있어서 편지를 쓸 수 없었어요. 가고 싶은 지역이랑 날짜를 담아 다시 보내 주실래요?",
                             summary="empty")
    notice = None
    if sid and action is None and not agent.graph().get_state({"configurable": {"thread_id": sid}}).values:
        notice = "이전 상담 기록을 찾지 못해 새 상담으로 시작했어요."
    return {"q": q, "sid": sid, "action": action, "body": body, "notice": notice}


def _run(prep: dict) -> dict:
    body, action = prep["body"], prep["action"]
    resp = agent.handle_query(prep["q"], prep["sid"], action, body.get("draft_version"),
                              edit=body.get("edit") if action == "edit" else None,
                              set_req=body.get("set") if action == "set" else None,
                              start_req=body.get("start") if action == "start" else None,
                              cost_req=body.get("cost") if action == "cost" else None,
                              pack_req=body.get("pack") if action == "pack" else None)
    if prep["notice"]:
        resp["answer"] = prep["notice"] + "\n\n" + resp["answer"]
    return resp


async def _body(request: Request):
    try:
        return json.loads(await request.body() or b"null")
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise InvalidRequest("형식 오류", "앗, 편지 봉투가 찢어져서 읽을 수가 없었어요. 질문을 다시 보내 주실래요?", summary="JSON 아님")


@app.post("/query")
async def query(request: Request):
    return _run(_prepare(await _body(request)))


# ---- 백그라운드 작업(느린 재계획을 화면을 막지 않고) ----
_EXEC = ThreadPoolExecutor(max_workers=3)
_JOBS: dict[str, dict] = {}
_RUNNING: dict[str, str] = {}      # session_id → job_id
_LOCK = threading.Lock()


def _job(job_id: str, prep: dict) -> None:
    try:
        _JOBS[job_id]["response"] = _run(prep)
        _JOBS[job_id]["state"] = "done"
    except InvalidRequest as e:
        _JOBS[job_id].update(state="error", response={"answer": e.answer, "status": "오류", "contexts": [],
                                                      "trace": [{"step": "error", "input": "", "output": e.kind}]})
    except Exception as e:  # 스택은 로그로만
        log.exception("job failed")
        _JOBS[job_id].update(state="error", response={"answer": "앗, 편지를 전하다가 바람에 날려 버렸어요. 잠시 후 다시 보내 주실래요?",
                                                      "status": "오류", "contexts": [],
                                                      "trace": [{"step": "error", "input": "", "output": type(e).__name__}]})
    finally:
        with _LOCK:
            if _RUNNING.get(prep["sid"]) == job_id:
                _RUNNING.pop(prep["sid"], None)


@app.post("/query/async")
async def query_async(request: Request):
    """/query와 같은 본문. 즉시 job_id를 주고 뒤에서 처리 — GET /jobs/{job_id}로 결과."""
    prep = _prepare(await _body(request))
    if not prep["sid"]:
        prep["sid"] = uuid.uuid4().hex      # 새 상담도 세션 번호를 먼저 알려 줌
    with _LOCK:
        running = _RUNNING.get(prep["sid"])
        if running and _JOBS.get(running, {}).get("state") == "running":
            return JSONResponse(status_code=409, content={"answer": "이전 요청을 처리하는 중이에요", "job_id": running,
                                                          "session_id": prep["sid"], "state": "running"})
        job_id = uuid.uuid4().hex[:12]
        _JOBS[job_id] = {"state": "running", "started": time.time(), "response": None, "session_id": prep["sid"]}
        _RUNNING[prep["sid"]] = job_id
    _EXEC.submit(_job, job_id, prep)
    return {"job_id": job_id, "session_id": prep["sid"], "state": "running"}


@app.get("/jobs/{job_id}")
async def job_status(job_id: str):
    j = _JOBS.get(job_id)
    if not j:
        return JSONResponse(status_code=404, content={"state": "error", "response": {"answer": "작업을 찾지 못했어요.", "status": "오류"}})
    return {"state": j["state"], "elapsed_sec": round(time.time() - j["started"]), "session_id": j["session_id"],
            "response": j["response"] if j["state"] != "running" else None}


@app.get("/options")
async def options():
    """선택형 입력 옵션(지역·기간·스타일 프리셋·동반자·이동 조합·차량·강도·관심사·예산)."""
    from momo import options as O
    return O.all_options()


@app.get("/answer/{session_id}", response_class=HTMLResponse)
async def answer_page(session_id: str):
    """최신 모모 답변을 새 창으로(서식 있는 HTML)."""
    from momo import richtext
    a = store.get_answer(session_id)
    if not a:
        return HTMLResponse("<p>답변을 찾지 못했어요.</p>", status_code=404)
    return richtext.page(a[0], a[1] + " · 모모의 답변")


@app.get("/places/search")
async def places_search(q: str = "", region: str = "", session_id: str = ""):
    """장소 찾기(유사 이름·가까운 순·거리·주소·사진 미리보기)."""
    from momo import placesearch
    near = None
    if session_id:
        st = agent.graph().get_state({"configurable": {"thread_id": session_id}}).values
        if st and st.get("spec"):
            region = region or st["spec"]["region"]
            pts = [(it["lat"], it["lng"]) for it in st.get("itinerary") or [] if it.get("lat")]
            if pts:
                near = (sum(a for a, _ in pts) / len(pts), sum(b for _, b in pts) / len(pts))
    if region not in data_regions():
        return JSONResponse(status_code=400, content={"error": "지역을 알려 주세요", "results": []})
    return placesearch.search(q[:40], region, near)


def data_regions():
    from momo import data
    return data.available_regions()


@app.get("/geo/search")
async def geo_search(q: str = ""):
    """출발지·도착지 자동완성(동·역·구 이름)."""
    from momo import geo
    return {"results": geo.search(q[:40])}


@app.get("/trips")
async def trips(ids: str = ""):
    """내 여행 계획 목록 — 브라우저가 가진 session_id들 중 서버에 있는 것만(로그인 없이, 다른 사람 목록은 볼 수 없음)."""
    wanted = [x for x in ids.split(",") if x and len(x) <= 64][:50]
    return {"trips": store.list_trips(wanted)}


@app.get("/trips/{session_id}")
async def trip(session_id: str):
    t = agent.load_trip(session_id)
    if not t:
        return JSONResponse(status_code=404, content={"answer": "저장된 계획을 찾지 못했어요.", "status": "오류"})
    return t


@app.get("/")
async def index():
    return FileResponse(STATIC / "index.html")


@app.get("/plan/{plan_id}.ics")
async def plan_ics(plan_id: str):
    p = store.get_plan(plan_id)
    if not p:
        return JSONResponse(status_code=404, content={"error": "없는 계획"})
    return Response(store.to_ics(plan_id, p), media_type="text/calendar",
                    headers={"Content-Disposition": f'attachment; filename="momo-{plan_id}.ics"'})


def _expenses(plan_id: str, payload: dict) -> dict:
    from momo import costs
    actual = store.list_expenses(plan_id)
    planned = (payload.get("plan") or {}).get("costs")
    from momo.options import EXPENSE_CATEGORIES
    return {"planned": planned, "actual": actual, "summary": costs.by_category(planned, actual),
            "categories": EXPENSE_CATEGORIES}


@app.get("/plan/{plan_id}.docx")
async def plan_docx(plan_id: str):
    from momo import export
    p = store.get_plan(plan_id)
    if not p:
        return JSONResponse(status_code=404, content={"error": "없는 계획"})
    data = export.to_docx(plan_id, p, _expenses(plan_id, p)["summary"])
    return Response(data, media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    headers={"Content-Disposition": f'attachment; filename="momo-{plan_id}.docx"'})


@app.get("/plan/{plan_id}.html")
async def plan_html_export(plan_id: str):
    """단일 파일 HTML 내보내기: 공유 페이지에 CSS·JS를 인라인(지도 라이브러리·글꼴만 CDN)."""
    import re as _re
    p = store.get_plan(plan_id)
    if not p:
        return HTMLResponse("<p>없는 계획이에요.</p>", status_code=404)
    page = _render_share(plan_id, p)
    css = (STATIC / "style.css").read_text(encoding="utf-8")
    page = _re.sub(r'<link[^>]+href="/static/style\.css"[^>]*>', lambda m: f"<style>{css}</style>", page)
    for js in ("app.js", "cards.js"):
        f = STATIC / js
        if f.exists():
            code = f.read_text(encoding="utf-8").replace("</script", r"<\/script")
            page = _re.sub(r'<script[^>]+src="/static/' + _re.escape(js) + r'"[^>]*></script>',
                           lambda m, c=code: f"<script>{c}</script>", page)
    return Response(page, media_type="text/html; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="momo-{plan_id}.html"'})


@app.get("/plan/{plan_id}/expenses")
async def get_expenses(plan_id: str):
    p = store.get_plan(plan_id)
    if not p:
        return JSONResponse(status_code=404, content={"error": "없는 계획"})
    return _expenses(plan_id, p)


@app.post("/plan/{plan_id}/expenses")
async def post_expense(plan_id: str, request: Request):
    p = store.get_plan(plan_id)
    if not p:
        return JSONResponse(status_code=404, content={"error": "없는 계획"})
    try:
        b = json.loads(await request.body() or b"{}")
        amount = int(str(b.get("amount", "")).replace(",", ""))
        if amount < 0 or amount > 100_000_000:
            raise ValueError
    except (ValueError, json.JSONDecodeError):
        return JSONResponse(status_code=400, content={"error": "금액을 숫자로 입력해 주세요"})
    cat = b.get("category") if b.get("category") in store.EXPENSE_CATEGORIES else "기타"
    label = guardrails.mask_pii(str(b.get("label") or cat)[:80])[0]
    memo = guardrails.mask_pii(str(b.get("memo") or "")[:200])[0]
    store.add_expense(plan_id, str(b.get("date") or "")[:10], cat, label, amount, memo)
    return _expenses(plan_id, p)


@app.delete("/plan/{plan_id}/expenses/{eid}")
async def del_expense(plan_id: str, eid: str):
    p = store.get_plan(plan_id)
    if not p:
        return JSONResponse(status_code=404, content={"error": "없는 계획"})
    store.delete_expense(plan_id, eid)
    return _expenses(plan_id, p)


@app.post("/plan/{plan_id}/packing")
async def plan_packing(plan_id: str, request: Request):
    """공유 페이지에서 준비물 체크·추가·삭제."""
    from momo import packing
    p = store.get_plan(plan_id)
    if not p or not (p.get("plan") or {}).get("packing"):
        return JSONResponse(status_code=404, content={"error": "없는 계획"})
    try:
        op = json.loads(await request.body() or b"{}")
    except json.JSONDecodeError:
        return JSONResponse(status_code=400, content={"error": "형식 오류"})
    if op.get("label"):
        op["label"] = guardrails.mask_pii(str(op["label"]))[0]
    p["plan"]["packing"] = packing.apply(p["plan"]["packing"], op)
    with store._conn() as c:
        c.execute("UPDATE plans SET payload=? WHERE plan_id=?", (json.dumps(p, ensure_ascii=False), plan_id))
    return {"packing": p["plan"]["packing"]}


def _view_payload(session_id: str) -> dict | None:
    st = agent.graph().get_state({"configurable": {"thread_id": session_id}}).values
    if not st or not st.get("plan"):
        return None
    t = st["plan"]["trip"]
    return {"title": t.get("title", "여행 계획") + " · 초안", "region": t.get("region_id"),
            "region_label": (t.get("regions") or [""])[0], "departure_sido": (st.get("spec") or {}).get("departure"),
            "nights": t.get("nights"), "start": t.get("start_date"), "itinerary": st.get("itinerary") or [],
            "plan": st["plan"], "view": True, "session_id": session_id, "plan_id": st.get("plan_id")}


@app.get("/view/{session_id}.html")
async def view_html(session_id: str):
    """초안 계획서 HTML 내려받기(CSS·JS 인라인)."""
    import re as _re
    p = _view_payload(session_id)
    if not p:
        return HTMLResponse("<p>계획을 찾지 못했어요.</p>", status_code=404)
    page = _render_share(f"view-{session_id[:8]}", p)
    css = (STATIC / "style.css").read_text(encoding="utf-8")
    page = _re.sub(r'<link[^>]+href="/static/style\.css"[^>]*>', lambda m: f"<style>{css}</style>", page)
    code = (STATIC / "app.js").read_text(encoding="utf-8").replace("</script", r"<\/script")
    page = _re.sub(r'<script[^>]+src="/static/app\.js"[^>]*></script>', lambda m: f"<script>{code}</script>", page)
    return Response(page, media_type="text/html; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="momo-plan-{session_id[:8]}.html"'})


@app.get("/view/{session_id}", response_class=HTMLResponse)
async def view_page(session_id: str):
    """확정 전 초안도 전체 화면으로(읽기 전용 계획서)."""
    p = _view_payload(session_id)
    if not p:
        return HTMLResponse("<p>계획을 찾지 못했어요.</p>", status_code=404)
    return _render_share(f"view-{session_id[:8]}", p)


@app.get("/plan/{plan_id}/report", response_class=HTMLResponse)
async def plan_report(plan_id: str):
    from momo import export
    p = store.get_plan(plan_id)
    if not p:
        return HTMLResponse("<p>없는 계획이에요.</p>", status_code=404)
    return export.report_html(plan_id, p, _expenses(plan_id, p))


def _render_share(plan_id: str, p: dict) -> str:
    tpl = (STATIC / "share.html").read_text(encoding="utf-8")
    data = json.dumps(p, ensure_ascii=False).replace("</", r"<\/")
    return tpl.replace("__TITLE__", html.escape(p["title"])).replace("__DATA__", data).replace("__PLAN_ID__", plan_id)


@app.get("/plan/{plan_id}", response_class=HTMLResponse)
async def plan_page(plan_id: str):
    p = store.get_plan(plan_id)
    if not p:
        return HTMLResponse("<p>없는 계획이에요.</p>", status_code=404)
    return _render_share(plan_id, p)
