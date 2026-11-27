"""FastAPI: POST /query(가이드 표준 규약) + 단일 페이지 + 공유 링크·.ics.

실행:  uvicorn src.api.main:app --port 8000
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
from fastapi.concurrency import run_in_threadpool
from fastapi.staticfiles import StaticFiles

from src.counsel import agent
from src.shared import eta
from src.guardrails import guardrails
from src.memory import store
from src.shared.config import MAX_QUESTION_CHARS, ROOT

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
                              pack_req=body.get("pack") if action == "pack" else None,
                              replan="update" if body.get("replan") == "update" else None,
                              force=action == "approve" and body.get("force") is True)
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


def _cancelled_reply(prep: dict) -> dict:
    return {"answer": "요청을 멈췄어요. 일정은 요청하기 전 그대로예요. 고쳐서 다시 보내 주세요.", "status": "취소",
            "contexts": [], "session_id": prep["sid"], "changes": [], "cancelled": True,
            "trace": [{"step": "cancel", "input": "", "output": "사용자 중지 — 요청 전 상태로 되돌림"}]}


def _rollback(prep: dict, before: dict, msg_id: int, undo_n: int) -> None:
    """중지된 요청이 남긴 흔적을 지운다: 상태(체크포인트)·상담 기록·되돌리기 목록을 요청 전으로. 취소 사실만 기록."""
    from src.memory import store
    sid = prep["sid"]
    cfg = {"configurable": {"thread_id": sid}}
    after = agent.graph().get_state(cfg).values or {}
    if before or after:
        agent.graph().update_state(cfg, {k: before.get(k) for k in set(before) | set(after)}, as_node="guard_out")
    store.delete_messages_after(sid, msg_id)
    while store.undo_count(sid) > undo_n:
        store.pop_snapshot(sid)
    kind = prep["action"] or "chat"
    store.log_message(sid, "user", kind, guardrails.mask_pii(prep["q"])[0], status="취소")
    store.log_message(sid, "momo", kind, "요청을 멈췄어요. 일정은 요청하기 전 그대로예요.", status="취소")


def _job(job_id: str, prep: dict) -> None:
    from src.shared import cancel, eta
    from src.memory import store
    ev = _JOBS[job_id]["cancel"]
    token = cancel.CURRENT.set(ev)
    etok = eta.CURRENT.set(_JOBS[job_id]["eta"])
    sid = prep["sid"]
    before = agent.graph().get_state({"configurable": {"thread_id": sid}}).values or {}
    msg_id, undo_n = store.last_message_id(sid), store.undo_count(sid)
    try:
        resp = _run(prep)
        if ev.is_set():          # 멈춤을 누른 뒤에 끝났어도 결과를 쓰지 않고 되돌림
            raise cancel.JobCancelled()
        _JOBS[job_id]["response"] = resp
        _JOBS[job_id]["state"] = "done"
        if (resp or {}).get("status") != "오류":
            eta.finish(_JOBS[job_id]["eta"])
    except cancel.JobCancelled:
        try:
            _rollback(prep, before, msg_id, undo_n)
        except Exception:
            log.exception("rollback failed")
        _JOBS[job_id].update(state="cancelled", response=_cancelled_reply(prep))
    except InvalidRequest as e:
        _JOBS[job_id].update(state="error", response={"answer": e.answer, "status": "오류", "contexts": [],
                                                      "trace": [{"step": "error", "input": "", "output": e.kind}]})
    except Exception as e:  # 스택은 로그로만
        log.exception("job failed")
        _JOBS[job_id].update(state="error", response={"answer": "앗, 편지를 전하다가 바람에 날려 버렸어요. 잠시 후 다시 보내 주실래요?",
                                                      "status": "오류", "contexts": [],
                                                      "trace": [{"step": "error", "input": "", "output": type(e).__name__}]})
    finally:
        cancel.CURRENT.reset(token)
        eta.CURRENT.reset(etok)
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
        _JOBS[job_id] = {"state": "running", "started": time.time(), "response": None, "session_id": prep["sid"],
                         "cancel": threading.Event(), "eta": eta.begin(eta.key_of(prep))}
        _RUNNING[prep["sid"]] = job_id
    _EXEC.submit(_job, job_id, prep)
    return {"job_id": job_id, "session_id": prep["sid"], "state": "running"}


@app.get("/jobs/{job_id}")
async def job_status(job_id: str):
    j = _JOBS.get(job_id)
    if not j:
        return JSONResponse(status_code=404, content={"state": "error", "response": {"answer": "작업을 찾지 못했어요.", "status": "오류"}})
    st = eta.status(j["eta"]) if j.get("eta") else {"elapsed_sec": round(time.time() - j["started"])}
    return {"state": j["state"], **st, "session_id": j["session_id"],
            "response": j["response"] if j["state"] != "running" else None}


@app.post("/jobs/{job_id}/cancel")
async def job_cancel(job_id: str):
    """요청 중지: 다음 단계(LLM 호출·재생성·장소 찾기) 직전에 멈추고 요청 전 상태로 되돌린다.
    이미 끝난 요청은 멈출 수 없으니 '되돌리기'를 안내."""
    j = _JOBS.get(job_id)
    if not j:
        return JSONResponse(status_code=404, content={"state": "error", "answer": "작업을 찾지 못했어요."})
    if j["state"] != "running":
        return {"state": j["state"], "answer": "이미 끝난 요청이라 멈출 수 없어요. 바뀐 내용은 '되돌리기'로 되돌릴 수 있어요."}
    j["cancel"].set()
    return {"state": "cancelling", "answer": "멈추는 중이에요. 지금 단계가 끝나는 대로 요청 전 상태로 되돌려요."}


@app.get("/options")
async def options():
    """선택형 입력 옵션(지역·기간·스타일 프리셋·동반자·이동 조합·차량·강도·관심사·예산)."""
    from src.counsel import options as O
    return O.all_options()


@app.get("/answer/{session_id}", response_class=HTMLResponse)
async def answer_page(session_id: str):
    """최신 모모 답변을 새 창으로(서식 있는 HTML)."""
    from src.counsel import richtext
    a = store.get_answer(session_id)
    if not a:
        return HTMLResponse("<p>답변을 찾지 못했어요.</p>", status_code=404)
    return richtext.page(a[0], a[1] + " · 모모의 답변")


@app.get("/places/search")
async def places_search(q: str = "", region: str = "", session_id: str = "", leg: str = ""):
    """장소 찾기(유사 이름·가까운 순·거리·주소·사진 미리보기). 전국에서 찾는다(2026-11-26: 오가는 길의 식당·장소도).
    leg=out|back: 출발 지점 다음(가는 길)·복귀 지점 앞(오는 길)에 들를 곳 — 그 경로에서 가까운 순."""
    from src.places import placesearch
    from src.planning import planner
    near, spec = None, None
    if session_id:
        st = agent.graph().get_state({"configurable": {"thread_id": session_id}}).values
        if st and st.get("spec"):
            region = region or st["spec"]["region"]
            spec = agent.spec_from_dict(st["spec"])
            pts = [(it["lat"], it["lng"]) for it in st.get("itinerary") or [] if it.get("lat") and it.get("type") != "move"]
            if pts:
                near = (sum(a for a, _ in pts) / len(pts), sum(b for _, b in pts) / len(pts))
    if region not in data_regions():
        return JSONResponse(status_code=400, content={"error": "지역을 알려 주세요", "results": []})
    ends = planner.route_ends(spec) if spec else {}
    if leg in ("out", "back") and ends.get(leg):
        a, b = ends[leg]
        near = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
    res = placesearch.search(q[:40], region, near, expand=True)
    if spec:            # 먼 곳이어도 오가는 경로 가까이면 '가는 길/오는 길에 넣기'를 고를 수 있게
        for c in res["results"] + res["far"]:
            if c.get("lat") is not None:
                rk = planner.route_km(spec, (c["lat"], c["lng"]))
                c["route_km"] = rk
                c["route_legs"] = sorted((k for k in rk if rk[k] <= 25), key=lambda k: (rk[k], k != "out"))
    if leg in ("out", "back") and ends.get(leg):
        allc = [c for c in res["results"] + res["far"] if (c.get("route_km") or {}).get(leg) is not None]
        allc.sort(key=lambda c: (c["route_km"][leg] + (1 - c.get("similarity", 0)) * 40))
        res.update(results=[{**c, "far": False} for c in allc[:10]], far=[], far_message="", leg=leg,
                   note=("가는 길" if leg == "out" else "오는 길") + " 경로(직선)에서 가까운 순이에요. 전국에서 찾았어요.")
    return res


@app.get("/places/alternatives")
async def places_alternatives(session_id: str = "", place_id: str = "", q: str = "", limit: int = 20):
    """일정 속 장소의 대안 top N(거리·그날 운영·취향·인기·정보 점수). 바꾸기는 edit op=replace."""
    from src.planning import alternatives, planner
    st = agent.graph().get_state({"configurable": {"thread_id": session_id}}).values if session_id else None
    if not st or not st.get("spec") or not st.get("itinerary"):
        return JSONResponse(status_code=400, content={"error": "일정이 있는 상담에서만 바꿀 곳을 찾을 수 있어요.", "items": []})
    spec = agent.spec_from_dict(st["spec"])
    out = await run_in_threadpool(alternatives.alternatives, spec, st["itinerary"], place_id, q[:40], max(1, min(30, limit)))
    if out.get("error"):
        return JSONResponse(status_code=404, content=out)
    return out


@app.get("/trips/{session_id}/messages")
async def trip_messages(session_id: str, limit: int = 300):
    """상담 기록(사용자 요청·모모 답변) — 새로 고침·다시 열기 때 대화를 그대로 복원."""
    from src.memory import store
    await run_in_threadpool(agent.backfill_messages, session_id)      # 기록 기능 전 대화는 이력에서 한 번 복원
    return {"session_id": session_id, "messages": store.list_messages(session_id, max(1, min(1000, limit)))}


@app.delete("/trips/{session_id}/messages")
async def trip_messages_clear(session_id: str):
    """내 요청 기록 전체 삭제(대화 기록만 — 일정은 그대로)."""
    from src.memory import store
    ids = await run_in_threadpool(store.delete_messages, session_id, None)
    return {"session_id": session_id, "deleted": ids}


@app.delete("/trips/{session_id}/messages/{mid}")
async def trip_message_delete(session_id: str, mid: int):
    """요청 하나 삭제(그 요청에 대한 모모 답변도 함께 — 일정은 그대로)."""
    from src.memory import store
    ids = await run_in_threadpool(store.delete_messages, session_id, mid)
    if not ids:
        return JSONResponse(status_code=404, content={"error": "그 기록을 찾지 못했어요."})
    return {"session_id": session_id, "deleted": ids}


@app.post("/trips/{session_id}/undo")
async def trip_undo(session_id: str):
    """직전 편집 되돌리기(초안·계획서 편집 모드 공용)."""
    running = _RUNNING.get(session_id)
    if running and _JOBS.get(running, {}).get("state") == "running":
        return JSONResponse(status_code=409, content={"error": "다른 작업이 진행 중이에요. 끝난 뒤 다시 눌러 주세요."})
    out = await run_in_threadpool(agent.undo, session_id)
    if not out:
        return JSONResponse(status_code=404, content={"error": "되돌릴 수정이 없어요."})
    return out


@app.get("/route/optimize")
async def route_optimize(session_id: str = "", scope: str = "day"):
    """동선 최적화 미리 보기(저장 안 함). 적용은 edit op=optimize(제안대로) 또는 op=order(직접 끌어 정한 순서)."""
    st = agent.graph().get_state({"configurable": {"thread_id": session_id}}).values if session_id else None
    if not st or not st.get("spec") or not st.get("itinerary"):
        return JSONResponse(status_code=400, content={"error": "일정이 있는 상담에서만 동선을 볼 수 있어요.", "days": []})
    spec = agent.spec_from_dict(st["spec"])
    out = await run_in_threadpool(agent.route_proposal, spec, agent._day_picks(spec, st["itinerary"]),
                                  "all" if scope == "all" else "day")
    for r in out["days"]:
        r.pop("_row", None)
    return out


MAP_LAYERS = {"Base": "png", "Satellite": "jpeg", "Hybrid": "png", "white": "png", "midnight": "png"}
TILE_DIR = ROOT / "data" / "tile_cache"


@app.get("/map/layers")
async def map_layers():
    """지도 종류: OpenStreetMap + 브이월드(일반·위성·위성+지명·흰색·야간, 키가 있을 때). 브이월드 타일은 서버 경유(키 비노출)."""
    import os
    osm = {"id": "osm", "label": "OpenStreetMap", "url": "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
           "attribution": "© OpenStreetMap contributors", "max_zoom": 19}
    layers = [osm]
    if os.environ.get("VWORLD_API_KEY"):
        vw = {"attribution": "© 브이월드(국토교통부)", "min_zoom": 6, "max_zoom": 19}
        layers += [{"id": "vw-base", "label": "브이월드 일반", "url": "/tiles/vworld/Base/{z}/{y}/{x}", **vw},
                   {"id": "vw-sat", "label": "브이월드 위성", "url": "/tiles/vworld/Satellite/{z}/{y}/{x}", **vw},
                   {"id": "vw-hybrid", "label": "위성 + 지명", "url": "/tiles/vworld/Satellite/{z}/{y}/{x}",
                    "overlay_url": "/tiles/vworld/Hybrid/{z}/{y}/{x}", **vw},
                   {"id": "vw-white", "label": "브이월드 흰색", "url": "/tiles/vworld/white/{z}/{y}/{x}", **vw},
                   {"id": "vw-night", "label": "브이월드 야간", "url": "/tiles/vworld/midnight/{z}/{y}/{x}", **vw}]
    return {"layers": layers, "default": "vw-base" if len(layers) > 1 else "osm",
            "external_3d": {"label": "브이월드 3D로 보기", "url": "https://map.vworld.kr/map/ws3dmap.do"}}


def _vworld_tile_bytes(layer: str, z: int, y: int, x: int) -> bytes | None:
    """브이월드 타일 한 장(디스크 캐시 → 없으면 내려받아 저장). 키는 여기서만 쓴다. 실패하면 None. 동기 함수."""
    import os
    ext = MAP_LAYERS.get(layer)
    key = os.environ.get("VWORLD_API_KEY")
    if not ext or not key or not (6 <= z <= 19):
        return None
    f = TILE_DIR / layer / str(z) / str(y) / f"{x}.{ext}"
    if f.exists():
        return f.read_bytes()
    import requests
    try:
        r = requests.get(f"https://api.vworld.kr/req/wmts/1.0.0/{key}/{layer}/{z}/{y}/{x}.{ext}", timeout=20)
        body = r.content if r.status_code == 200 and r.headers.get("content-type", "").startswith("image") else None
    except Exception:
        body = None
    if body:
        try:
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(body)
        except Exception:
            pass
    return body


@app.get("/tiles/vworld/{layer}/{z}/{y}/{x}")
async def vworld_tile(layer: str, z: int, y: int, x: int):
    """브이월드 WMTS 타일 프록시(디스크 캐시). 실패하면 204 → 화면이 OSM으로 바꾼다."""
    ext = MAP_LAYERS.get(layer)
    if not ext:
        return Response(status_code=204)
    media = "image/jpeg" if ext == "jpeg" else "image/png"
    body = await run_in_threadpool(_vworld_tile_bytes, layer, z, y, x)
    if not body:
        return Response(status_code=204)
    return Response(body, media_type=media, headers={"Cache-Control": "public, max-age=604800"})


def data_regions():
    from src.shared import data
    return data.available_regions()


@app.get("/traffic/cctv")
async def traffic_cctv(session_id: str = ""):
    """자차 경로 주변 CCTV(국가교통정보센터). 키가 없으면 안내."""
    from src.mobility import roads
    st = agent.graph().get_state({"configurable": {"thread_id": session_id}}).values if session_id else None
    path = (((st or {}).get("plan") or {}).get("transport_guide") or {}).get("route_path")
    if not path:
        return {"available": False, "items": [], "message": "자차 경로가 있는 계획에서만 CCTV를 볼 수 있어요."}
    return roads.cctv_near(path)


@app.get("/geo/search")
async def geo_search(q: str = ""):
    """출발지·도착지 자동완성(동·역·구 이름)."""
    from src.places import geo
    return {"results": geo.search(q[:40])}


@app.get("/geo/reverse")
async def geo_reverse(lat: float, lng: float):
    """지도에서 찍은 좌표 → 위치 이름(브이월드 우선, 없으면 OSM). 한국 밖이면 null."""
    from src.places import geo
    if not (32.5 <= lat <= 39.0 and 123.5 <= lng <= 132.5):
        return {"place": None}
    return {"place": geo.reverse(lat, lng)}


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
    from src.budget import costs
    actual = store.list_expenses(plan_id)
    planned = (payload.get("plan") or {}).get("costs")
    from src.counsel.options import EXPENSE_CATEGORIES
    return {"planned": planned, "actual": actual, "summary": costs.by_category(planned, actual),
            "categories": EXPENSE_CATEGORIES}


@app.get("/plan/{plan_id}.docx")
async def plan_docx(plan_id: str):
    from src.export import export
    p = store.get_plan(plan_id)
    if not p:
        return JSONResponse(status_code=404, content={"error": "없는 계획"})
    data = export.to_docx(plan_id, p, _expenses(plan_id, p)["summary"])
    return Response(data, media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    headers={"Content-Disposition": f'attachment; filename="momo-{plan_id}.docx"'})


def _build_export(plan_id: str, p: dict) -> str:
    """단일 파일 HTML: 토큰·스타일·스크립트 인라인 + 브이월드 일반지도 타일 굽기(서버 없이·키 없이 열림).
    지도 라이브러리(Leaflet)·글꼴만 CDN. OSM 타일은 쓰지 않는다(Referer 없으면 차단)."""
    import re as _re
    from src.export import export_assets, map_bake
    # 같은 사진이 계획 데이터 여러 곳(장소·일정·후보…)에 반복돼 파일이 3배로 커졌다(2026-11-27, 21MB)
    # → 사진은 한 번만 MOMO_BOOT.imgs에 두고 데이터에는 'momo-img:N' 표시만, 열 때 되돌린다.
    imgs: list[str] = []
    idx: dict[str, int] = {}

    def dedupe(o):
        if isinstance(o, dict):
            return {k: dedupe(v) for k, v in o.items()}
        if isinstance(o, list):
            return [dedupe(v) for v in o]
        if isinstance(o, str) and o.startswith("data:image/"):
            if o not in idx:
                idx[o] = len(imgs)
                imgs.append(o)
            return f"momo-img:{idx[o]}"
        return o
    page = _render_share(plan_id, dedupe(export_assets.inline_photos(p)))
    img_js = ("(function(){var B=window.MOMO_BOOT||{},I=B.imgs||[];function f(o){if(Array.isArray(o)){for(var i=0;i<o.length;i++)o[i]=f(o[i]);return o}"
              "if(o&&typeof o==='object'){for(var k in o)o[k]=f(o[k]);return o}"
              "if(typeof o==='string'&&o.indexOf('momo-img:')===0){var v=I[+o.slice(9)];return v==null?o:v}return o}"
              "if(B.payload)f(B.payload);})();" + chr(10))

    def css(m):
        f = STATIC / m.group(1)
        return f"<style>{f.read_text(encoding='utf-8')}</style>" if f.exists() else ""

    def js(m):
        f = STATIC / m.group(1)
        pre = img_js if m.group(1) == "app.js" else ""      # app.js보다 먼저 사진 표시를 되돌림
        return "<script>" + pre + f.read_text(encoding="utf-8").replace("</script", r"<\/script") + "</script>" if f.exists() else ""

    page = _re.sub(r'<link[^>]+href="/static/([\w.\-]+\.css)"[^>]*>', css, page)
    page = _re.sub(r'<script[^>]+src="/static/([\w.\-]+\.js)"[^>]*></script>', js, page)
    lf = export_assets.leaflet_assets()          # Leaflet·글꼴 CDN 없이 — 외부 요청 0
    page = _re.sub(r'<link[^>]+leaflet\.css[^>]*>', lambda m: f"<style>{lf[0]}</style>" if lf else m.group(0), page)
    page = _re.sub(r'<script[^>]+leaflet\.js[^>]*></script>', lambda m: f"<script>{lf[1]}</script>" if lf else m.group(0), page)
    page = _re.sub(r'<link[^>]+(?:fonts\.googleapis|fonts\.gstatic)[^>]*>\s*', "", page)   # 시스템 글꼴 대체(tokens.css의 font-family)
    page = _re.sub(r'<nav class="desk-nav.*?</nav>', "", page, flags=_re.S)     # 서버 링크("나도 상담하기") 제거
    try:
        tiles = map_bake.bake_tiles(p.get("plan") or p, _vworld_tile_bytes)
    except Exception:
        log.exception("export tile bake failed")
        tiles = None
    boot = json.dumps({"export": True, "tiles": tiles or {"failed": True}, "imgs": imgs}, ensure_ascii=False, separators=(",", ":"))[1:-1]
    page = page.replace("window.MOMO_BOOT = { readOnly: true,", "window.MOMO_BOOT = { readOnly: true, " + boot.replace("</", r"<\/") + ",", 1)
    return page


@app.get("/plan/{plan_id}.html")
async def plan_html_export(plan_id: str):
    """단일 파일 HTML 내보내기(CSS·JS·지도 타일 인라인)."""
    p = store.get_plan(plan_id)
    if not p:
        return HTMLResponse("<p>없는 계획이에요.</p>", status_code=404)
    page = await run_in_threadpool(_build_export, plan_id, p)
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
    from src.packing import packing
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
    """초안 계획서 HTML 내려받기(CSS·JS·지도 타일 인라인)."""
    p = _view_payload(session_id)
    if not p:
        return HTMLResponse("<p>계획을 찾지 못했어요.</p>", status_code=404)
    page = await run_in_threadpool(_build_export, f"view-{session_id[:8]}", p)
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
    from src.export import export
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
