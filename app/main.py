"""FastAPI: POST /query(가이드 표준 규약) + 단일 페이지 + 공유 링크·.ics.

실행:  uvicorn app.main:app --port 8000
"""
from __future__ import annotations

import html
import json
import logging
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


@app.post("/query")
async def query(request: Request):
    try:
        body = json.loads(await request.body() or b"null")
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise InvalidRequest("형식 오류", "앗, 편지 봉투가 찢어져서 읽을 수가 없었어요. 질문을 다시 보내 주실래요?", summary="JSON 아님")
    if not isinstance(body, dict) or not isinstance(body.get("question", ""), str) \
            or not isinstance(body.get("session_id") or "", str) or not isinstance(body.get("action") or "", str):
        raise InvalidRequest("형식 오류", "앗, 편지 형식이 조금 달라서 읽을 수가 없었어요. 질문을 글로 담아 다시 보내 주실래요?",
                             summary=str(body)[:200])
    q = (body.get("question") or "").strip()
    action = body.get("action") or None
    sid = body.get("session_id") or None
    if action not in (None, "approve"):
        raise InvalidRequest("알 수 없는 action", "앗, 모르는 요청이에요. 확정은 화면의 '확정할게요' 버튼으로 해 주세요.", summary=str(action))
    if len(q) > MAX_QUESTION_CHARS:
        raise InvalidRequest("길이 초과", f"앗, 편지가 너무 길어서 가방에 안 들어가요. {MAX_QUESTION_CHARS}자 안으로 줄여서 다시 보내 주실래요?",
                             code=413, summary=f"{len(q)}자")
    if action == "approve":
        if not sid:
            raise InvalidRequest("세션 없음", "앗, 어떤 일정을 확정할지 몰라요. 일정을 먼저 만들어 볼까요?", summary="approve without session")
        state = agent.graph().get_state({"configurable": {"thread_id": sid}}).values
        if not state:
            raise InvalidRequest("없는 세션", "앗, 그 상담 기록을 찾지 못했어요. 일정을 새로 만들어 볼까요?", summary="unknown session")
    elif not q:
        raise InvalidRequest("빈 질문", "앗, 질문이 비어 있어서 편지를 쓸 수 없었어요. 가고 싶은 지역이랑 날짜를 담아 다시 보내 주실래요?",
                             summary="empty")
    notice = None
    if sid and action != "approve" and not agent.graph().get_state({"configurable": {"thread_id": sid}}).values:
        notice = "이전 상담 기록을 찾지 못해 새 상담으로 시작했어요."
    resp = agent.handle_query(q, sid, action, body.get("draft_version"))
    if notice:
        resp["answer"] = notice + "\n\n" + resp["answer"]
    return resp


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


@app.get("/plan/{plan_id}", response_class=HTMLResponse)
async def plan_page(plan_id: str):
    p = store.get_plan(plan_id)
    if not p:
        return HTMLResponse("<p>없는 계획이에요.</p>", status_code=404)
    tpl = (STATIC / "share.html").read_text(encoding="utf-8")
    data = json.dumps(p, ensure_ascii=False).replace("</", "<\\/")
    return tpl.replace("__TITLE__", html.escape(p["title"])).replace("__DATA__", data).replace("__PLAN_ID__", plan_id)
