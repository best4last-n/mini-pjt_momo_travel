"""에이전트 그래프 (SERVICE.md 3-4h C안 하이브리드).

guard_in → (action=approve → approve) | parse → intent 분기
  plan / modify : 고정 Plan-Execute (조건 → 검색 → 생성 → 규칙 검사·재생성 최대 2회) → 승인 대기
  info          : ReAct (도구 선택)
  approve_chat  : 채팅 승인 요구 → 저장하지 않고 버튼 안내
  reject        : 정중한 거절
→ guard_out
세션 상태는 LangGraph SQLite 체크포인터(thread_id = session_id)에 저장.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from datetime import date, timedelta
from typing import Any, TypedDict

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph

from . import brochure, costs, data, guardrails, hours, planner, render, retrieve, store, tools
from .config import DEFAULT_DEPART, STATE_DB, today
from .llm import MOMO_PERSONA, chat, structured
from .options import MAX_NIGHTS
from .schemas import DayPick, ParsedRequest, PickItem

def supported_label() -> str:
    return ", ".join(data.region_label(r) for r in data.available_regions())


def supported_ids() -> str:
    return ", ".join(data.available_regions())


class State(TypedDict, total=False):
    # 이번 요청
    question: str
    action: str | None
    draft_version_in: str | None
    replan: str | None           # 대량 재수립 확인 결과: "update"(이 계획을 새 버전으로)
    session_id: str
    parsed: dict
    status: str
    answer: str
    contexts: list
    trace: list
    theme: dict
    extra: dict
    # 세션에 남는 값
    spec: dict | None
    itinerary: list
    violations: list
    draft_version: str | None
    allowed: list
    last_request: str
    plan_id: str | None
    # 4단계 상담·브로셔·편집 (SERVICE.md 3-6)
    edit: dict | None
    set_req: dict | None
    start_req: dict | None
    cost_req: dict | None
    pack_req: dict | None
    pending: dict | None        # intake 중 모은 조건(다음 턴에 합침)
    plan: dict | None           # 최신 브로셔 plan (세션 유지)
    ranked: list                # 추천 1~10위 [(place_id, reason)]
    plan_out: dict | None       # 이번 응답에 실을 plan(바뀌지 않았으면 None)
    stage: str
    changes: list
    conditions: list | None


# ---------------- 공용 ----------------

def _today_label() -> str:
    t = today()
    return f"{t.isoformat()}({'월화수목금토일'[t.weekday()]}요일)"


def _t(state: State, step: str, inp: Any, out: Any) -> list:
    tr = list(state.get("trace") or [])
    tr.append({"step": step, "input": _s(inp), "output": _s(out)})
    return tr


def _s(x: Any) -> str:
    return x if isinstance(x, str) else json.dumps(x, ensure_ascii=False, default=str)[:1500]


def _version(items: list) -> str:
    return hashlib.sha1(json.dumps(items, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:12]


def spec_to_dict(s: planner.TripSpec) -> dict:
    d = s.__dict__.copy()
    d["start"] = s.start.isoformat()
    return d


def spec_from_dict(d: dict) -> planner.TripSpec:
    d = dict(d)
    d["start"] = date.fromisoformat(d["start"])
    return planner.TripSpec(**d)


def resolve_region(p: ParsedRequest) -> tuple[str | None, str | None]:
    """(region_id, 안내 사유). 사유: overseas / unprepared / unsupported / missing."""
    if p.overseas:
        return None, "overseas"
    text = p.region_text or ""
    if not text.strip():
        return None, "missing"
    for k, rid in data.REGION_ALIASES.items():
        if k in text:
            return (rid, None) if rid in data.available_regions() else (None, "unprepared")
    return None, "unsupported"


def resolve_date(p: ParsedRequest, question: str) -> tuple[date | None, str | None, list[str]]:
    """(시작일, 오류, 안내). 연도 없는 날짜는 오늘 이후 가장 가까운 날짜."""
    t0, notes = today(), []
    if p.month and p.day:
        cands = []
        for y in (t0.year, t0.year + 1):
            try:
                cands.append(date(y, p.month, p.day))
            except ValueError:
                pass
        if not cands:
            return None, f"{p.month}월 {p.day}일은 달력에 없는 날짜예요", []
        d = next((c for c in cands if c >= t0), cands[-1])
        m = re.search(r"([월화수목금토일])요일", question)
        if m and "월화수목금토일"[d.weekday()] != m.group(1):
            notes.append(f"{d.month}월 {d.day}일은 {'월화수목금토일'[d.weekday()]}요일이에요(말씀하신 요일과 달라 날짜 기준으로 짰어요)")
        return d, None, notes
    sat = t0 + timedelta(days=(5 - t0.weekday()) % 7 or 7)
    if p.relative_date and "다음" in p.relative_date:
        return sat + timedelta(days=7) if (sat - t0).days < 7 else sat, None, []
    if p.relative_date and "내일" in p.relative_date:
        return t0 + timedelta(days=1), None, []
    if p.relative_date:
        return sat, None, []
    notes.append(f"날짜를 말씀하지 않아 가장 가까운 토요일({sat.month}월 {sat.day}일)로 짰어요(기본값 적용)")
    return sat, None, notes


def resolve_waypoints(names: list[str], region: str) -> tuple[list[str], list[dict], list[str]]:
    ids, users, notes = [], [], []
    for n in names:
        d = retrieve.find_by_name(n, region)
        if d:
            ids.append(d["doc_id"])
            continue
        extra = next((p for p in data.places(region).values() if p.get("added_by_user")
                      and (retrieve.re.sub(r"\s+", "", n) in retrieve.re.sub(r"\s+|\(.*?\)", "", p["title"]))), None)
        if extra:
            ids.append(extra["contentid"])
            continue
        try:
            from . import tourlive
            t = tourlive.lookup(n, region)
        except Exception:
            t = None
        if t:   # 관광공사에 있는 정식 장소로 편입(운영시간·사진·요약 포함)
            ids.append(t["contentid"])
            notes.append(f"{_josa(chr(39) + t['title'] + chr(39), '을를')} 한국관광공사 자료에서 찾아 일정에 넣었어요")
            continue
        # 관광공사에 없으면: 브이월드(국가 POI, 개인 카페·식당 포함) 우선 → 카카오·네이버(키 있을 때) → OpenStreetMap 마지막
        from . import placesearch
        try:
            res = placesearch.search(n, region, limit=8, expand=True)
        except Exception:
            res = {"results": [], "far": []}
        order = {"관광공사(수집)": 0, "관광공사": 0, "브이월드": 1, "카카오맵": 2, "네이버": 2, "OpenStreetMap": 3}
        nk = placesearch.name_key(n)
        pool = [c for c in (res.get("results") or []) + (res.get("far") or [])
                if (c.get("similarity") or 0) >= 0.8 and (c.get("distance_km") or 0) <= 150]
        pool.sort(key=lambda c: (placesearch.name_key(c["name"]) != nk, order.get(c["source"], 3),   # 이름이 똑같은 곳 먼저(편의점 'OO점' 뒤로)
                                 -(c.get("similarity") or 0), c.get("distance_km") or 0))
        best = pool[0] if pool else None
        if best and best.get("place_id") and best["source"].startswith("관광공사"):
            try:
                from . import tourlive
                tourlive.fetch(str(best["place_id"]), region)
            except Exception:
                pass
            if str(best["place_id"]) in data.places(region):
                ids.append(str(best["place_id"]))
                notes.append(f"'{best['name']}'을(를) 한국관광공사 자료에서 찾아 일정에 넣었어요"
                             + (f" — 여행 지역에서 약 {best['distance_km']:,.0f}km 떨어져 있어 오가는 이동이 길어요(빼려면 '빼기')"
                                if (best.get("distance_km") or 0) > placesearch.MAX_KM else ""))
                continue
        if best:
            up = placesearch.user_place_from_candidate(best)
            users.append(up)
            far = (best.get("distance_km") or 0) > placesearch.MAX_KM
            notes.append(f"'{n}' → {best['source']}에서 '{best['name']}'({(best.get('address') or '')[:24]})을(를) 찾아 '사용자 지정' 장소로 넣었어요. "
                         + (f"여행 지역에서 약 {best['distance_km']:,.0f}km 떨어져 있어 오가는 이동이 길어요(빼려면 '빼기'). " if far else "")
                         + "운영시간은 확인 필요예요")
            continue
        similar = [c["name"] for c in (res.get("results") or []) if (c.get("similarity") or 0) >= 0.5][:3]
        notes.append(f"'{n}'은(는) 관광공사·브이월드·지도에서 찾지 못했어요. "
                     + (f"이름이 비슷한 곳: {', '.join(similar)}. " if similar else "")
                     + "정확한 이름이나 주소를 알려 주시거나, 장소 찾기의 '직접 추가'로 넣어 주세요.")
    return ids, users, notes


# ---------------- 노드 ----------------

def guard_in(state: State) -> State:
    q = state.get("question") or ""
    out: State = {"trace": [], "contexts": [], "extra": {}, "theme": {}, "status": "", "answer": "", "parsed": {},
                  "plan_out": None, "stage": "", "changes": [], "conditions": None}
    if guardrails.detect_injection(q):
        out["status"] = "거절"
        out["answer"] = ("앗, 그 부탁은 들어드릴 수 없어요. 저는 여행 상담만 하는 우체부 철새라 내부 지시문이나 키 같은 건 "
                         "보여 드리지 않아요. 대신 가고 싶은 지역이랑 날짜를 알려 주시면 멋진 일정을 엽서처럼 정리해 드릴게요!")
    out["trace"] = [{"step": "guardrail_in", "input": q[:300], "output": "거절(인젝션·비밀 요구)" if out["status"] else "통과"}]
    return out


def parse(state: State) -> State:
    q = state["question"]
    has_draft = bool(state.get("itinerary"))
    sys = (MOMO_PERSONA + f"\n오늘 날짜는 {_today_label()}이다. 사용자 질문을 구조화한다. "
           f"현재 세션에 확정 전 일정 초안이 {'있다' if has_draft else '없다'}. "
           "초안이 있고 일부를 바꾸거나 장소를 추가해 달라면 modify, 새 지역·날짜 일정이면 plan.")
    p: ParsedRequest = structured(ParsedRequest).invoke([("system", sys), ("human", f"<data>{q}</data>")])
    if p.intent == "modify" and not has_draft:
        p.intent = "plan"
    pend = state.get("pending") or {}
    if pend and not has_draft and p.intent != "reject":
        merged = p.model_dump()
        for k, v in pend.items():
            if k in ("intent", "info_topic", "modify_instruction", "use_defaults"):
                continue
            if merged.get(k) in (None, [], False, "") and v not in (None, [], False, ""):
                merged[k] = v
        merged["intent"] = "plan" if p.intent in ("info", "modify", "plan") else p.intent
        p = ParsedRequest(**merged)
    return {"parsed": p.model_dump(), "trace": _t(state, "parse_conditions", q[:300], p.model_dump(exclude_defaults=True))}


def route_parsed(state: State) -> str:
    return state["parsed"]["intent"]


def plan_node(state: State) -> State:
    p = ParsedRequest(**state["parsed"])
    q = state["question"]
    region, why = resolve_region(p)
    if why:
        out = _region_reply(state, why, p)
        if why == "missing":
            out["pending"] = p.model_dump()
        return out
    # intake: 날짜와 여행 길이가 둘 다 없고 '알아서'도 아니면 확정 요소를 먼저 묻는다(가이드 3절, 한 번에 2~3개)
    if not p.use_defaults and not (p.month and p.day) and not p.relative_date and p.nights is None:
        return _intake_reply(state, p, region)
    start, err, notes = resolve_date(p, q)
    if err:
        return {"status": "되묻기", "answer": f"앗, {err}. 혹시 어느 날짜를 말씀하신 걸까요? 날짜를 다시 알려 주시면 바로 짜 드릴게요!",
                "extra": {"chips": ["2월 28일", "3월 1일", "다른 날짜 입력"]},
                "trace": _t(state, "parse_conditions", "날짜 검증", err)}
    old = state.get("spec") if state.get("itinerary") else None
    if old and state.get("replan") != "update":
        diffs = []
        if old.get("region") != region:
            diffs.append(f"지역 {data.region_label(old['region'])} → {data.region_label(region)}")
        if (p.month and p.day or p.relative_date) and old.get("start") != start.isoformat():
            diffs.append(f"출발일 {old['start']} → {start.isoformat()}")
        if p.nights is not None and int(old.get("nights") or 0) != p.nights:
            diffs.append(f"기간 {old.get('nights')}박 → {p.nights}박")
        if diffs:
            return {"status": "되묻기", "stage": "plan",
                    "answer": ("지금 만들어 둔 일정이 있어요. 이번 요청은 " + ", ".join(diffs) + "(으)로 일정을 크게 새로 짜는 내용이에요. "
                               "어떻게 할까요?\n\n- **이 계획을 새 버전으로 고치기**: 지금 계획을 바탕으로 다시 짜요(바뀐 점을 한눈에 보여 드려요, 되돌리기 가능)\n"
                               "- **새 여행으로 따로 시작하기**: 지금 계획은 '내 여행 계획'에 그대로 두고 새 상담을 열어요"),
                    "extra": {"replan_choice": {"question": q, "diffs": diffs,
                                                "options": [{"key": "update", "label": "이 계획을 새 버전으로 고치기"},
                                                            {"key": "new", "label": "새 여행으로 따로 시작하기"}]},
                              "chips": ["이 계획을 새 버전으로 고치기", "새 여행으로 따로 시작하기"]},
                    "trace": _t(state, "replan_guard", q[:120], "초안 있음 + 지역·날짜·기간 변경 → 사용자에게 확인")}
    nights = p.nights
    if nights is None:
        nights = 1
        notes.append("여행 길이를 말씀하지 않아 1박 2일로 짰어요(기본값 적용)")
    elif nights > MAX_NIGHTS:
        notes.append(f"{nights}박 {nights + 1}일은 아직 지원하지 않아요. 최대 {MAX_NIGHTS}박 {MAX_NIGHTS + 1}일까지 꼼꼼히 검증할 수 있어서, "
                     f"우선 {MAX_NIGHTS}박 {MAX_NIGHTS + 1}일로 줄여 제안드려요")
        nights = MAX_NIGHTS
    assumed = []
    if not (p.month and p.day) and not p.relative_date:
        assumed.append("date")
    if p.nights is None:
        assumed.append("length")
    dep = data.norm_sido(p.departure)
    oloc = None
    if p.departure:
        from . import geo
        oloc = geo.locate(p.departure)
        dep = (oloc or {}).get("sido") or dep
    if p.departure and not dep:
        notes.append("출발지를 알아보지 못해 오가는 시간은 빼고 현지 도착 기준으로 짰어요")
    elif not dep:
        notes.append("출발지를 말씀하지 않아 오가는 시간은 빼고 현지 도착 기준으로 계산했어요(오른쪽 '출발지'를 눌러 알려 주세요)")
    else:
        notes.append(f"출발지는 {_dep_label(p.departure) or p.departure} 기준으로 쓰고, 오가는 시간은 {dep} 대표 지점에서 대략 추정했어요")
    long_t = p.long_transport or ("KTX·SRT" if p.transport == "대중교통" and re.search(r"KTX|SRT|기차|열차", q) else
                                  "고속·시외버스" if p.transport == "대중교통" and "버스" in q else "자차")
    local_t = p.local_transport or ("대중교통" if p.transport == "대중교통" else ("렌터카" if long_t != "자차" else "자차"))
    if not p.long_transport and not p.transport:
        assumed.append("long_transport")
    if not p.local_transport and not p.transport:
        assumed.append("local_transport")
    transport = "대중교통" if local_t == "대중교통" else "자차"
    if assumed and ("long_transport" in assumed or "local_transport" in assumed):
        notes.append(f"이동수단은 {long_t}{' + ' + local_t if local_t != long_t else ''}로 계산했어요(기본값 적용)")
    elif local_t == "대중교통":
        notes.append("대중교통 시간은 추정이에요(노선·배차는 지도 앱에서 확인해 주세요)")
    ctype = p.companion_type or _infer_companion(p.companions)
    if not p.companion_type and not p.companions:
        assumed.append("companions")
    intensity = p.intensity or ("빡빡" if p.many_places else "보통")
    if not p.intensity and not p.many_places:
        assumed.append("intensity")
    budget, per = costs.parse_budget(p.budget_text)
    if p.send_request:
        notes.append("문자·메일 발송은 할 수 없어요. 확정 후 공유 링크와 캘린더 파일(.ics)로 전해 드릴게요")
    if p.past_trip_reference:
        notes.append("지난 여행 기록(LifeGalaxy add-on)이 꺼져 있어 지난 기록은 볼 수 없어요. 대신 새로 일정을 짜 드렸어요")
    if p.many_places:
        notes.append("하루 10곳 넘게는 이동만 하다 끝나서 무리예요. 이동이 짧은 곳 위주로 하루 최대 7곳까지 넣었어요")
    depart_time = hours.parse_hm(p.depart_time) if p.depart_time and re.fullmatch(r"\d{1,2}:\d{2}", p.depart_time) else DEFAULT_DEPART
    spec = planner.TripSpec(region=region, start=start, nights=nights, departure=dep, depart_time=depart_time,
                            transport=transport, companions=p.companions, preferences=p.preferences,
                            many_places=p.many_places, intensity=intensity, long_transport=long_t,
                            local_transport=local_t, companion_type=ctype, companion_count=p.companion_count,
                            origin_label=(oloc or {}).get("label") or (_dep_label(p.departure) if dep else None),
                            origin_coords=[oloc["lat"], oloc["lng"]] if oloc else None, assumptions=assumed,
                            budget=budget, budget_per_person=per, budget_label=p.budget_text if budget else None)
    if p.waypoints:
        ids, users, wnotes = resolve_waypoints(p.waypoints, region)
        spec.waypoint_ids, spec.user_waypoints = ids, users
        notes += wnotes
    # 당일치기 현지 시간 경고 (3-4e)
    if nights == 0 and dep:
        w = planner.windows(spec)[0]
        if w[1] - w[0] < 60:
            return {"status": "정보", "answer": f"{dep}에서 {data.region_label(region)}까지 오가는 시간이 길어서 당일치기로는 현지에 "
                    "머물 시간이 1시간도 안 돼요. 더 이른 출발이나 1박 어때요?", "trace": _t(state, "plan", "시간 창", f"{w}")}
        if w[1] - w[0] < 180:
            notes.append("당일치기로는 빠듯해요. 1박 어때요?")
    return _run_plan(state, spec, q, notes, base=None)


def _run_plan(state: State, spec: planner.TripSpec, request: str, notes: list[str], base: list | None,
              locked: dict | None = None, prefix: dict | None = None, indoor: bool = False) -> State:
    tr = list(state.get("trace") or [])
    token = tools.CONTEXTS.set([])
    try:
        w = tools.weather(spec.region, spec.days)
        tr.append({"step": "tool:get_weather", "input": f"{spec.region} {spec.start}", "output": w["text"]})
        res = planner.build_plan(spec, request, base=base, trace=tr, locked=locked, prefix=prefix, indoor=indoor)
        items = res["itinerary"]
        for it in items:
            p = data.places(spec.region).get(it.get("place_id") or "")
            if p:
                tools.add_context(p["contentid"], tools.place_doc_text(p))
        _add_tool_contexts(spec, items, res, notes, w)
        contexts = tools.CONTEXTS.get()
    finally:
        tools.CONTEXTS.reset(token)
    changed = None
    if base is not None:
        old = {(it["date"], it.get("place_id") or it["place_name"], it["start_time"]) for it in base}
        changed = {(it["date"], it.get("place_id") or it["place_name"]) for it in items
                   if (it["date"], it.get("place_id") or it["place_name"], it["start_time"]) not in old and it["type"] != "move"}
    viol = res["violations"]
    if viol:
        notes.append("규칙 검사에서 아직 고치지 못한 부분이 있어 확정 전에 조정이 필요해요: "
                     + "; ".join(f"{v['place_name']} {v['detail']}" for v in viol[:3]))
    if spec.many_places and items:
        notes.append("장소 사이 이동시간도 함께 적어 두었어요")
    answer = render.plan_answer(spec.region, spec.nights, items, notes, w["text"], changed, modified=base is not None)
    version = _version(items)
    tr.append({"step": "plan_summary", "input": f"재생성 {res['regenerations']}회 · 보정 {res['repaired']}",
               "output": f"항목 {len(items)}개 · 위반 {len(viol)}건 · draft_version {version}"})
    plan = brochure.build(spec, items, res["ranked"], viol, notes)
    n_in = sum(1 for x in plan["places"] if x["in_itinerary"] and x["rank"])
    n_rank = sum(1 for x in plan["places"] if x["rank"])
    if n_rank > n_in:
        answer = answer.replace("\n\n", f"\n\n추천 {n_rank}곳 중 {n_in}곳을 일정에 넣었고, 나머지는 '후보'로 남겨 두었어요. "
                                "화면에서 바로 바꿔 넣을 수 있어요.\n\n", 1)
    if plan["costs"]["total"]:
        c = plan["costs"]
        answer += (f"\n예상 경비는 약 {costs.fmt_won(c['total'])}(1인 약 {costs.fmt_won(c['per_person'])}, 추정)이에요"
                   + (f" — 예산 {costs.fmt_won(c['budget'])}을 넘어요." if c["over_budget"] else
                      (f" — 예산 {costs.fmt_won(c['budget'])} 안이에요." if c["budget"] else ". 최대 예산을 알려 주시면 비교해 드릴게요.")))
    return {
        "status": "승인 대기", "answer": answer, "contexts": contexts, "trace": tr,
        "spec": spec_to_dict(spec), "itinerary": items, "violations": viol, "draft_version": version,
        "allowed": list(res["candidates"].keys()), "last_request": request,
        "plan": plan, "plan_out": plan, "ranked": res["ranked"], "pending": None,
        "stage": "refine" if base is not None else "plan", "conditions": plan["trip"]["checklist"],
        "changes": [], 
        "theme": {"season": tools.season_of(spec.start), "scope": "domestic",
                  "weather": "rain" if w.get("rain") else ("snow" if spec.start.month in (12, 1, 2) else "clear")},
        "extra": {"draft_version": version, "violations": viol,
                  "quick_replies": ["더 여유롭게 바꿔 줘", "비 오면 실내 위주로", "맛집 위주로 바꿔 줘"]},
    }


def _add_tool_contexts(spec: planner.TripSpec, items: list, res: dict, notes: list[str], w: dict) -> None:
    """일정을 만들 때 쓴 도구·코드 판정 결과도 근거 문서로 남긴다(장소 문서 외의 근거: 조건·날씨·이동·운영 판정)."""
    tools.add_context("agent:conditions", (
        f"조건 구조화 결과: 지역 {data.region_label(spec.region)}, 시작일 {spec.start.isoformat()}"
        f"({'월화수목금토일'[spec.start.weekday()]}), {spec.nights}박 {spec.nights + 1}일, 출발지 {spec.departure or '모름'}, "
        f"이동수단 {spec.transport}, 동행 {spec.companions or '정보 없음'}, 취향 {', '.join(spec.preferences) or '없음'}. "
        f"적용한 안내: {' / '.join(notes) or '없음'}"))
    tools.add_context("tool:get_weather", f"날씨 도구 결과: {w['text']}")
    legs = []
    for it in items:
        if it["type"] == "move":
            legs.append(f"{it['place_name']} 이동 {it['start_time']}~{it['end_time']} (직선거리×1.3÷80km/h 추정)")
        elif it["travel_minutes_from_prev"]:
            legs.append(f"{it['date']} {it['place_name']}까지 이동 약 {it['travel_minutes_from_prev']}분 ({it['travel_source']}, 직선거리×1.3÷30km/h 추정)")
    tools.add_context("tool:estimate_travel", "이동시간 추정 결과: " + " / ".join(legs))
    checks = [f"{it['date']} {it['start_time']}~{it['end_time']} {it['place_name']}: 운영 {it.get('hours_text') or it['hours_status']}"
              f" ({it['hours_status']})" for it in items if it["type"] != "move"]
    tools.add_context("tool:validate_plan", f"규칙 검사 결과(위반 {len(res['violations'])}건, 재생성 {res['regenerations']}회): "
                      + " / ".join(checks))


def modify_node(state: State) -> State:
    p = ParsedRequest(**state["parsed"])
    spec = spec_from_dict(state["spec"])
    notes: list[str] = []
    instr = p.modify_instruction or state["question"]
    if p.preferences:
        spec.preferences = list(dict.fromkeys([*spec.preferences, *p.preferences]))
    if p.waypoints:
        ids, users, wnotes = resolve_waypoints(p.waypoints, spec.region)
        spec.waypoint_ids = list(dict.fromkeys([*spec.waypoint_ids, *ids]))
        spec.user_waypoints = [*spec.user_waypoints, *users]
        notes += wnotes
    if p.transport and p.transport != spec.transport:
        spec.transport = p.transport
    base = state["itinerary"]
    # 수정 대상 날짜가 분명하면 나머지 날은 그대로 고정
    target = set()
    for i, pat in ((1, r"(첫\s*(째|날)|1\s*일\s*차|첫날)"), (2, r"(둘\s*째|2\s*일\s*차|이튿날|마지막\s*날)")):
        if re.search(pat, state["question"]):
            target.add(i)
    locked = {}
    if target:
        for di, d in enumerate(spec.days, 1):
            if di not in target:
                locked[di] = [PickItem(place_id=it["place_id"], role=_role(it),
                                       stay_minutes=hours.parse_hm(it["end_time"]) - hours.parse_hm(it["start_time"]),
                                       reason=it["reason"])
                              for it in base if it["date"] == d.isoformat() and it.get("place_id")]
    # '오후'만 바꾸라면 대상 날의 오전 항목과 점심은 고정
    prefix = {}
    if target and re.search(r"오후", state["question"]):
        for di, d in enumerate(spec.days, 1):
            if di in target:
                prefix[di] = [PickItem(place_id=it["place_id"], role=_role(it),
                                       stay_minutes=hours.parse_hm(it["end_time"]) - hours.parse_hm(it["start_time"]),
                                       reason=it["reason"])
                              for it in base if it["date"] == d.isoformat() and it.get("place_id") and it["type"] != "stay"
                              and (hours.parse_hm(it["start_time"]) < 12 * 60 or _role(it) == "lunch")]
    indoor = "실내" in state["question"] or any("실내" in x for x in p.preferences)
    request = f"{state.get('last_request', '')}\n[수정 요청] {instr}"
    return _run_plan(state, spec, request, notes, base=base, locked=locked, prefix=prefix, indoor=indoor)


def _infer_companion(text: str | None) -> str:
    t = text or ""
    if re.search(r"(아이|어린이|유아|아기|살\b|\d+\s*살|초등|유치원)", t):
        return "어린이·유아"
    if re.search(r"(부모님|어르신|노약자|할머니|할아버지|어머니|아버지|노부모)", t):
        return "노약자"
    return "보통"


def _intake_reply(state: State, p: ParsedRequest, region: str) -> State:
    """확정 요소가 부족할 때: 한 번에 2~3개만, 선택지 + 지역 맞춤 가이드(가이드 3절)."""
    label = data.region_label(region)
    qs, chips = [], []
    qs.append("**언제, 며칠** 다녀오실까요? 당일치기부터 8박 9일까지 휴무일·운영시간을 꼼꼼히 맞춰 드려요. "
              "위쪽 '여행 시작' 카드의 달력에서 고르셔도 돼요.")
    chips += ["이번 주말 1박 2일", "다음 주말 2박 3일", "당일치기"]
    if not p.departure:
        qs.append("**어디서 출발**하세요? 오가는 시간을 계산해 첫날·마지막 날 일정을 맞춰요.")
        chips += ["서울에서 출발", "대전에서 출발"]
    if not (p.long_transport or p.local_transport or p.transport):
        hint = brochure.TRANSPORT_HINT.get(region, "")
        qs.append(f"**이동수단**은 어떻게 하실까요? {hint}")
        chips += ["KTX + 렌터카", "자차로 갈게요", "대중교통"]
    notes = []
    if p.waypoints:
        _, _, notes = resolve_waypoints(p.waypoints, region)
    head = f"좋아요, {label} 여행이군요! 딱 맞는 일정을 짜기 전에 {len(qs)}가지만 여쭤볼게요."
    body = "\n".join(f"{i}. {q}" for i, q in enumerate(qs, 1))
    tail = "모르시거나 상관없으면 '알아서 짜 줘'를 눌러 주세요. 무엇을 가정했는지 함께 알려 드릴게요."
    answer = "\n\n".join(x for x in (head, "\n".join(notes), body, tail) if x)
    spec = planner.TripSpec(region=region, start=today(), nights=1, departure=data.norm_sido(p.departure),
                            origin_label=_dep_label(p.departure) if p.departure else None,
                            companion_type=p.companion_type or _infer_companion(p.companions),
                            companions=p.companions, preferences=p.preferences,
                            assumptions=["long_transport", "local_transport", "intensity"]
                            + ([] if (p.companion_type or p.companions) else ["companions"]))
    conds = brochure.checklist(spec)
    for c in conds:
        if c["key"] in ("start_date", "length"):
            c.update(value="미정", status="missing")
    return {"status": "되묻기", "stage": "intake", "answer": answer, "pending": p.model_dump(), "conditions": conds,
            "extra": {"chips": chips + ["알아서 짜 줘"]},
            "trace": _t(state, "intake", "확정 요소 부족(기간)", f"질문 {len(qs)}개")}


def _day_picks(spec: planner.TripSpec, items: list) -> list[list]:
    days = []
    for d in spec.days:
        ds = d.isoformat()
        row = []
        for it in items:
            if it["date"] != ds or it["type"] == "move" or "조식" in (it.get("notes") or []):
                continue
            pid = it.get("place_id") or planner.MEAL_ID
            if pid == planner.MEAL_ID and it["type"] != "meal":
                continue
            row.append(PickItem(place_id=pid, role=_role(it), reason=it.get("reason") or "",
                                stay_minutes=max(30, hours.parse_hm(it["end_time"]) - hours.parse_hm(it["start_time"]))
                                if it["type"] != "stay" else 0))
        days.append(row)
    return days


def _fill_meals(spec: planner.TripSpec, days: list[list]) -> list[list]:
    """식사가 필요한 날에 식사 항목이 없으면 '주변에서 식사' 자리표시자를 넣는다."""
    mw = planner.meal_windows(spec)
    for di, row in enumerate(days):
        for role, win in (("lunch", planner.LUNCH), ("dinner", planner.DINNER)):
            if planner.meal_required(mw[di], win) and not any(x.role == role for x in row):
                ph = PickItem(place_id=planner.MEAL_ID, role=role, stay_minutes=60, reason="")
                if role == "lunch":
                    idx = min(len([x for x in row if x.role != "stay"]), max(1, len(row) // 2))
                    row.insert(idx, ph)
                else:
                    idx = next((i for i, x in enumerate(row) if x.role == "stay"), len(row))
                    row.insert(idx, ph)
    return days


def route_whole(spec: planner.TripSpec, days: list[list]) -> tuple[list[list], list[dict]]:
    """전체 일정 동선 최적화: 날짜를 넘나들며 관광지를 옮기거나 맞바꿔 총 이동거리를 줄인다.
    식사·숙소는 그 날 그 자리에 고정, 그 날 휴무인 곳으로는 옮기지 않음, 하루 관광지 수는 원래 ±1곳(강도 상한 안, 빈 날 없음).
    한 곳 옮기기 / 두 곳 맞바꾸기를 개선이 없을 때까지 반복(최대 4바퀴). → (새 일정, 옮긴 목록)"""
    from .travel import km as _km
    center = data.region_center(spec.region)
    nd = len(days)
    cap_base = planner.INTENSITY.get(spec.intensity, planner.INTENSITY["보통"])[2]
    orig = [sum(1 for x in row if (x.role == "place" and not x.place_id.startswith(planner.FREE_PREFIX))) for row in days]
    caps = [min(max(cap_base, o), o + 1) for o in orig]        # 하루 늘어도 1곳까지(강도 상한 안에서)
    floors = [max(1 if o else 0, o - 1) for o in orig]          # 하루 줄어도 1곳까지, 빈 날 없음
    pl = data.places(spec.region)

    def open_on(pid, di):
        p = pl.get(pid)
        return not p or hours.parse_open(p, spec.days[di]).status != "휴무"

    def starts(rows):
        out, last = [], None
        for row in rows:
            out.append(planner._pcoord(spec, last) if last else center)
            last = next((x.place_id for x in row if x.role == "stay"), None) or last
        return out

    def day_km(row, start, di):
        opt, k = planner.tsp_quick(spec, list(row), start, center if di == nd - 1 else None)
        return k, opt

    def insert_pos(row):
        return next((i for i, x in enumerate(row) if x.role == "stay"), len(row))

    rows = [list(r) for r in days]
    st = starts(rows)
    cost = [day_km(rows[i], st[i], i)[0] for i in range(nd)]
    origin_day = {x.place_id: i for i, r in enumerate(rows) for x in r if (x.role == "place" and not x.place_id.startswith(planner.FREE_PREFIX))}
    for _ in range(4):
        improved = False
        for i in range(nd):
            for j in range(nd):
                if i == j:
                    continue
                for a in [x for x in rows[i] if (x.role == "place" and not x.place_id.startswith(planner.FREE_PREFIX))]:
                    if not open_on(a.place_id, j):
                        continue
                    # (1) 옮기기
                    if (sum(1 for x in rows[j] if (x.role == "place" and not x.place_id.startswith(planner.FREE_PREFIX))) < caps[j]
                            and sum(1 for x in rows[i] if (x.role == "place" and not x.place_id.startswith(planner.FREE_PREFIX))) > floors[i]):
                        ri = [x for x in rows[i] if x is not a]
                        rj = list(rows[j])
                        rj.insert(insert_pos(rj), a)
                        ci, cj = day_km(ri, st[i], i)[0], day_km(rj, st[j], j)[0]
                        if ci + cj < cost[i] + cost[j] - 0.5:
                            rows[i], rows[j], cost[i], cost[j], improved = ri, rj, ci, cj, True
                            continue
                    # (2) 맞바꾸기
                    for b in [x for x in rows[j] if (x.role == "place" and not x.place_id.startswith(planner.FREE_PREFIX))]:
                        if not open_on(b.place_id, i):
                            continue
                        ri = [b if x is a else x for x in rows[i]]
                        rj = [a if x is b else x for x in rows[j]]
                        ci, cj = day_km(ri, st[i], i)[0], day_km(rj, st[j], j)[0]
                        if ci + cj < cost[i] + cost[j] - 0.5:
                            rows[i], rows[j], cost[i], cost[j], improved = ri, rj, ci, cj, True
                            break
        if not improved:
            break
    allowed = {x.place_id for r in rows for x in r} | {planner.MEAL_ID}
    for i in range(nd):
        rows[i] = planner.best_day_order(spec, rows, i, st[i], center if i == nd - 1 else None, allowed)
    moved = []
    for i, r in enumerate(rows):
        for x in r:
            if (x.role == "place" and not x.place_id.startswith(planner.FREE_PREFIX)) and origin_day.get(x.place_id, i) != i:
                moved.append({"place_id": x.place_id, "from_day": origin_day[x.place_id] + 1, "to_day": i + 1})
    return rows, moved


def route_proposal(spec: planner.TripSpec, days: list[list], scope: str = "day") -> dict:
    """날짜별 동선 최적화 제안(적용 전 미리 보기): 지금 순서 vs 제안 순서, 이동 분·km."""
    from .travel import km as _km
    center = data.region_center(spec.region)
    pl = data.places(spec.region)

    def nm(pid):
        return (pl.get(pid) or {}).get("title") or next(
            (w["title"] for w in spec.user_waypoints if w["place_id"] == pid), "주변 식사")

    def sched(rows):
        filled = _fill_meals(spec, [list(r) for r in rows])
        return planner.schedule(spec, planner.PlanPick(days=[DayPick(day=i + 1, items=r) for i, r in enumerate(filled)]), {})

    def dist(row, start):
        pts = [p for p in [start] + [planner._pcoord(spec, x.place_id) for x in row] if p]
        return round(sum(_km(a, b) for a, b in zip(pts, pts[1:])), 1)

    allowed_all = {x.place_id for r in days for x in r} | {planner.MEAL_ID}
    new_rows, last_stay, starts = [list(r) for r in days], None, []
    for row in days:
        starts.append(planner._pcoord(spec, last_stay) if last_stay else center)
        last_stay = next((x.place_id for x in row if x.role == "stay"), None) or last_stay
    for i in range(len(days)):
        new_rows[i] = planner.best_day_order(spec, new_rows, i, starts[i], center if i == len(days) - 1 else None, allowed_all)
    moved = []
    if scope == "all":
        new_rows, moved = route_whole(spec, days)
        allowed = {x.place_id for r in days for x in r} | {planner.MEAL_ID}
        vb = len(planner.validate(spec, sched(days), allowed))
        va = len(planner.validate(spec, sched(new_rows), allowed))
        tb, ta = sum(_travel_by_day(sched(days)).values()), sum(_travel_by_day(sched(new_rows)).values())
        if va > vb or ta >= tb:         # 규칙 위반이 늘거나 실제 이동이 줄지 않으면 일별 최적화로
            new_rows, moved = [list(r) for r in days], []
            for i in range(len(days)):
                new_rows[i] = planner.best_day_order(spec, new_rows, i, starts[i], center if i == len(days) - 1 else None, allowed_all)
    before, after = _travel_by_day(sched(days)), _travel_by_day(sched(new_rows))
    out = []
    for i, d in enumerate(spec.days):
        ds = d.isoformat()
        same = [x.place_id for x in days[i]] == [x.place_id for x in new_rows[i]]
        b = before.get(ds, 0)
        a = b if same else after.get(ds, 0)
        out.append({"day": i + 1, "date": ds, "before_min": b, "after_min": a, "saved_min": max(0, b - a),
                    "before_km": dist(days[i], starts[i]), "after_km": dist(new_rows[i] if not same else days[i], starts[i]),
                    "before": [{"place_id": x.place_id, "name": x.reason if x.place_id.startswith(planner.FREE_PREFIX) else nm(x.place_id), "role": x.role,
                                "free": x.place_id.startswith(planner.FREE_PREFIX)}
                               for x in days[i] if x.place_id != planner.MEAL_ID],
                    "after": [{"place_id": x.place_id, "name": x.reason if x.place_id.startswith(planner.FREE_PREFIX) else nm(x.place_id), "role": x.role,
                                "free": x.place_id.startswith(planner.FREE_PREFIX)}
                              for x in (days[i] if same else new_rows[i]) if x.place_id != planner.MEAL_ID],
                    "user_ordered": (i + 1) in spec.user_ordered_days, "_row": days[i] if same else new_rows[i]})
    nmv = {m["place_id"]: m for m in moved}
    for r in out:
        for x in r["after"]:
            if x["place_id"] in nmv:
                x["from_day"] = nmv[x["place_id"]]["from_day"]
    tb, ta = sum(r["before_min"] for r in out), sum(r["after_min"] for r in out)
    return {"scope": scope, "days": out, "total_saved_min": max(0, tb - ta), "total_before_min": tb, "total_after_min": ta,
            "total_before_km": round(sum(r["before_km"] for r in out), 1), "total_after_km": round(sum(r["after_km"] for r in out), 1),
            "moved": [{**m, "name": nm(m["place_id"])} for m in moved],
            "note": ("전체 일정 기준: 날짜를 넘나들며 가까운 곳끼리 묶어요. 식사·숙소 자리는 그대로, 그 날 쉬는 곳으로는 옮기지 않고, "
                     "하루 관광지 수는 여행 강도 상한을 지켜요(직선거리 기준 추정)." if scope == "all" else
                     "일별 기준: 날짜는 그대로 두고 그날 안에서 관광지 순서만 이동이 가장 짧게 바꿔요(직선거리 기준 추정).")}


def _travel_by_day(items: list) -> dict:
    out: dict = {}
    for it in items:
        if it["type"] != "move":
            out[it["date"]] = out.get(it["date"], 0) + (it["travel_minutes_from_prev"] or 0)
    return out


def _reschedule(state: State, spec: planner.TripSpec, days: list[list], changes: list[str], step: str,
                repair: bool = False) -> State:
    """화면 편집·조건 변경을 LLM 없이 반영: 배치 → 식사 보완 → 규칙 검사(→ 필요 시 보정) → plan 재구성."""
    allowed = set(state.get("allowed") or []) | {x.place_id for row in days for x in row} | {planner.MEAL_ID}
    days = _fill_meals(spec, days)
    pick = planner.PlanPick(days=[DayPick(day=i + 1, items=row) for i, row in enumerate(days)])
    items = planner.schedule(spec, pick, {})
    viol = planner.validate(spec, items, allowed)
    if repair and viol:
        items = planner.repair(spec, items, viol, allowed, {pid: data.places(spec.region)[pid]
                                                            for pid in allowed if pid in data.places(spec.region)})
        viol = planner.validate(spec, items, allowed)
    before = _travel_by_day(state.get("itinerary") or [])
    after = _travel_by_day(items)
    for i, d in enumerate(spec.days, 1):
        ds = d.isoformat()
        if before.get(ds) is not None and before.get(ds) != after.get(ds):
            diff = after.get(ds, 0) - before.get(ds, 0)
            changes.append(f"{i}일차 이동이 {abs(diff)}분 {'늘어' if diff > 0 else '줄어'} 총 {after.get(ds, 0)}분이 됐어요")
    notes = list((state.get("plan") or {}).get("trip", {}).get("assumptions") or [])
    plan = brochure.build(spec, items, state.get("ranked") or [], viol, notes)
    token = tools.CONTEXTS.set([])
    try:
        for it in items:
            pp = data.places(spec.region).get(it.get("place_id") or "")
            if pp:
                tools.add_context(pp["contentid"], tools.place_doc_text(pp))
        _add_tool_contexts(spec, items, {"violations": viol, "regenerations": 0}, notes,
                           tools.weather(spec.region, spec.days))
        contexts = tools.CONTEXTS.get()
    finally:
        tools.CONTEXTS.reset(token)
    warn = ""
    if viol:
        warn = ("\n\n다만 규칙 검사에서 걸리는 곳이 있어요(확정 전에 조정 필요): "
                + "; ".join(f"{v['place_name']} {v['detail']}" for v in viol[:3]))
    head = "\n".join(changes[:3]) or "말씀하신 대로 바꿨어요."
    answer = head + warn + "\n\n" + render.itinerary_text(items) + "\n\n장소·운영시간 정보 출처: 한국관광공사 TourAPI(공공누리). 이동시간은 추정이에요."
    version = _version(items)
    return {"status": "승인 대기", "stage": "refine", "answer": answer, "contexts": contexts,
            "spec": spec_to_dict(spec), "itinerary": items, "violations": viol, "draft_version": version,
            "allowed": sorted(allowed - {planner.MEAL_ID}), "plan": plan, "plan_out": plan,
            "conditions": plan["trip"]["checklist"], "changes": changes[:3],
            "extra": {"draft_version": version, "violations": viol},
            "trace": _t(state, step, json.dumps(state.get("edit") or state.get("set_req") or {}, ensure_ascii=False)[:200],
                        f"항목 {len(items)}개 · 위반 {len(viol)}건")}


def edit_node(state: State) -> State:
    """화면 편집(가이드의 [plan_edit]): add / remove / move / reorder — LLM 없이 코드로 재계산."""
    e = state.get("edit") or {}
    if not state.get("itinerary") or not state.get("spec"):
        return {"status": "오류", "answer": "앗, 고칠 일정이 아직 없어요. 먼저 일정을 만들어 볼까요?",
                "trace": _t(state, "edit", e, "초안 없음")}
    spec = spec_from_dict(state["spec"])
    days = _day_picks(spec, state["itinerary"])
    op, pid = e.get("op"), e.get("place_id")
    pl = data.places(spec.region)
    name = (pl.get(pid) or {}).get("title") or pid
    nd = len(days)
    changes: list[str] = []

    def locate(x):
        for di, row in enumerate(days):
            for i, pk in enumerate(row):
                if pk.place_id == x:
                    return di, i
        return None, None

    def insert(di, pk):
        row = days[di]
        idx = next((i for i, x in enumerate(row) if x.role in ("dinner", "stay")), len(row))
        row.insert(idx, pk)

    if op == "remove":
        di, i = locate(pid)
        if di is None:
            return {"status": "승인 대기", "answer": "그 장소는 일정에 없어요.", "trace": _t(state, "edit", e, "없음")}
        days[di].pop(i)
        spec.user_ordered_days = sorted(set(spec.user_ordered_days) | {di + 1})
        changes.append(f"{di + 1}일차에서 {_josa(name, '을를')} 뺐어요. 빈 시간에는 후보 중 한 곳을 넣어 보셔도 좋아요")
    elif op in ("move", "reorder"):
        di, i = locate(pid)
        if di is None:
            return {"status": "승인 대기", "answer": "그 장소는 일정에 없어요.", "trace": _t(state, "edit", e, "없음")}
        pk = days[di].pop(i)
        if op == "move":
            to = max(1, min(nd, int(e.get("day") or (2 if di == 0 else 1)))) - 1
            insert(to, pk)
            spec.user_ordered_days = sorted(set(spec.user_ordered_days) | {di + 1, to + 1})
            changes.append(f"{_josa(name, '을를')} {to + 1}일차로 옮겼어요")
        else:
            j = max(0, min(len(days[di]), i + int(e.get("delta") or 0)))
            days[di].insert(j, pk)
            spec.user_ordered_days = sorted(set(spec.user_ordered_days) | {di + 1})
            changes.append(f"{di + 1}일차에서 {name}의 순서를 바꿨어요(직접 정한 순서를 최우선으로 지켜요)")
    elif op in ("add", "replace"):
        cand = e.get("candidate") if isinstance(e.get("candidate"), dict) else None
        man = e.get("manual") if isinstance(e.get("manual"), dict) else None
        tdi = ti = None
        if op == "replace":      # 일정 속 장소를 대안으로 바꾸기(자리·역할·머무는 시간 유지)
            tdi, ti = locate(e.get("target"))
            if tdi is None:
                return {"status": "승인 대기", "answer": "바꿀 장소가 일정에 없어요.", "trace": _t(state, "edit", e, "대상 없음")}
            ch = e.get("choice") if isinstance(e.get("choice"), dict) else {}
            pid = ch.get("place_id") or pid
            cand = ch.get("candidate") if isinstance(ch.get("candidate"), dict) else cand
        if not pid and not cand and man:      # 검색에 없을 때 직접 추가(이름 + 주소 또는 지도 좌표)
            from . import geo, placesearch
            nm = guardrails.mask_pii(str(man.get("name") or "").strip()[:40])[0]
            lat, lng = man.get("lat"), man.get("lng")
            if not (lat and lng) and man.get("address"):
                loc = geo.locate(str(man["address"])[:80])
                if loc:
                    lat, lng = loc["lat"], loc["lng"]
            if not nm or not (lat and lng):
                return {"status": "승인 대기", "answer": "이름과 위치(주소 또는 지도에서 찍기)가 필요해요.",
                        "trace": _t(state, "edit", e, "직접 추가 정보 부족")}
            cand = {"name": nm, "lat": float(lat), "lng": float(lng), "address": str(man.get("address") or "")[:60],
                    "source": "직접 추가", "key": f"user:{uuid.uuid4().hex[:8]}"}
        if not pid and cand:      # 장소 찾기에서 고른 후보
            if cand.get("place_id"):
                pid = str(cand["place_id"])
                if pid not in data.places(spec.region):
                    from . import tourlive
                    try:
                        tourlive.fetch(pid, spec.region)
                    except Exception:
                        pass
                if pid not in data.places(spec.region):
                    return {"status": "승인 대기", "answer": "관광공사 자료를 불러오지 못했어요. 잠시 후 다시 시도해 주세요.",
                            "trace": _t(state, "edit", e, "관광공사 상세 실패")}
            else:
                from . import placesearch
                up = placesearch.user_place_from_candidate(cand)
                spec.user_waypoints = [w for w in spec.user_waypoints if w["place_id"] != up["place_id"]] + [up]
                pid = up["place_id"]
            pl = data.places(spec.region)
        if not pid and e.get("name"):
            ids, users, notes = resolve_waypoints([e["name"]], spec.region)
            if users:
                spec.user_waypoints = [*spec.user_waypoints, *users]
                pid = users[0]["place_id"]
            elif ids:
                pid = ids[0]
            else:
                return {"status": "승인 대기", "answer": " ".join(notes) or "그 장소를 찾지 못했어요.",
                        "changes": notes[:1], "trace": _t(state, "edit", e, "찾지 못함")}
            pl = data.places(spec.region)
        if not pid or (pid not in pl and pid not in {w["place_id"] for w in spec.user_waypoints}):
            return {"status": "승인 대기", "answer": "그 장소를 찾지 못했어요.", "trace": _t(state, "edit", e, "없는 id")}
        if locate(pid)[0] is not None:
            return {"status": "승인 대기", "answer": "이미 일정에 있는 곳이에요.", "trace": _t(state, "edit", e, "중복")}
        name = (pl.get(pid) or {}).get("title") or next((w["title"] for w in spec.user_waypoints if w["place_id"] == pid), pid)
        if op == "replace":
            old = days[tdi][ti]
            oname = (pl.get(old.place_id) or {}).get("title") or next(
                (w["title"] for w in spec.user_waypoints if w["place_id"] == old.place_id), old.place_id)
            days[tdi][ti] = PickItem(place_id=pid, role=old.role, stay_minutes=old.stay_minutes,
                                     reason=f"{oname} 대신 직접 고르신 곳")
            spec.waypoint_ids = [w for w in spec.waypoint_ids if w != old.place_id]
            spec.user_ordered_days = sorted(set(spec.user_ordered_days) | {tdi + 1})
            to = tdi
        elif e.get("after") and locate(e["after"])[0] is not None:     # 관광지 카드의 '근처 먹거리' → 그 바로 다음
            to, ai = locate(e["after"])
            kind = (pl.get(pid) or {}).get("kind")
            days[to].insert(ai + 1, PickItem(place_id=pid, role="place", stay_minutes=60 if kind == "restaurant" else 90,
                                             reason=f"{(pl.get(e['after']) or {}).get('title', '')} 근처에서 고르신 곳"))
            spec.user_ordered_days = sorted(set(spec.user_ordered_days) | {to + 1})
        else:
            if e.get("day"):
                to = max(1, min(nd, int(e["day"]))) - 1
            else:   # 관광지가 가장 적은 날
                to = min(range(nd), key=lambda k: sum(1 for x in days[k] if x.role == "place"))
            kind = (pl.get(pid) or {}).get("kind")
            insert(to, PickItem(place_id=pid, role="place", stay_minutes=60 if kind == "restaurant" else 90,
                                reason=next((r for p_, r in (state.get("ranked") or []) if p_ == pid), "") or "직접 추가하신 곳"))
        spec.waypoint_ids = list(dict.fromkeys([*spec.waypoint_ids, pid]))
        np_ = (pl.get(pid) or next((w for w in spec.user_waypoints if w["place_id"] == pid), {}))
        if np_.get("lat"):
            from .placesearch import MAX_KM
            from .travel import km as _km
            far_km = _km(data.region_center(spec.region), (np_["lat"], np_["lng"]))
            if far_km > MAX_KM:
                changes.append(f"{np_.get('title', '이곳')}은(는) 여행 지역에서 약 {far_km:,.0f}km 떨어져 있어 오가는 이동이 길어요(추정)")
        if op == "replace":
            changes.append(f"{to + 1}일차 {oname} 자리에 {_josa(name, '을를')} 넣었어요(순서·머무는 시간은 그대로)")
        elif e.get("after"):
            changes.append(f"{to + 1}일차 {(pl.get(e['after']) or {}).get('title', '')} 바로 다음에 {_josa(name, '을를')} 넣었어요")
        else:
            if not e.get("keep_order") and str(to + 1) not in map(str, spec.user_ordered_days):
                days[to] = planner.optimize_day(spec, days[to])   # 넣은 날은 동선 기준으로 자리 잡기
            changes.append(f"{to + 1}일차에 {_josa(name, '을를')} 추가했어요")
    elif op == "title":          # {title} 여행 제목(같은 곳을 여러 번 가도 구분되게). 빈 값이면 기본 제목으로
        t = guardrails.mask_pii(re.sub(r"\s+", " ", str(e.get("title") or "")).strip()[:40])[0]
        spec.title = t or None
        changes.append(f"여행 제목을 '{t}'(으)로 바꿨어요" if t else "여행 제목을 기본 제목으로 되돌렸어요")
    elif op == "stay":           # {place_id, minutes} 머무는 시간 직접 정하기(15~360분)
        di, i = locate(pid)
        if di is None:
            return {"status": "승인 대기", "answer": "그 장소는 일정에 없어요.", "trace": _t(state, "edit", e, "없음")}
        try:
            mins = max(15, min(360, int(e.get("minutes"))))
        except (TypeError, ValueError):
            return {"status": "승인 대기", "answer": "머무는 시간을 분 단위 숫자로 알려 주세요.", "trace": _t(state, "edit", e, "형식")}
        old = days[di][i]
        if old.role == "stay":
            return {"status": "승인 대기", "answer": "숙소는 머무는 시간 대신 체크인·체크아웃 시각을 따라요.", "trace": _t(state, "edit", e, "숙소")}
        days[di][i] = PickItem(place_id=old.place_id, role=old.role, stay_minutes=mins, reason=old.reason)
        nm = old.reason if old.place_id.startswith(planner.FREE_PREFIX) else name
        changes.append(f"{di + 1}일차 {nm} 머무는 시간을 {mins}분으로 바꿨어요(뒤 일정 시각은 다시 계산했어요)")
    elif op == "free":           # {action: add|remove, day?, after?: place_id, minutes, label} 개인 시간(휴식·자유 시간)
        if e.get("action") == "remove":
            di, i = locate(pid)
            if di is None or not pid.startswith(planner.FREE_PREFIX):
                return {"status": "승인 대기", "answer": "그 개인 시간은 일정에 없어요.", "trace": _t(state, "edit", e, "없음")}
            label = days[di].pop(i).reason
            changes.append(f"{di + 1}일차 '{label}'을(를) 뺐어요")
        else:
            try:
                mins = max(15, min(360, int(e.get("minutes") or 60)))
            except (TypeError, ValueError):
                mins = 60
            label = guardrails.mask_pii(str(e.get("label") or "개인 시간").strip()[:30])[0] or "개인 시간"
            blk = PickItem(place_id=f"{planner.FREE_PREFIX}{uuid.uuid4().hex[:8]}", role="place", stay_minutes=mins, reason=label)
            if e.get("after") and locate(e["after"])[0] is not None:
                to, ai = locate(e["after"])
                days[to].insert(ai + 1, blk)
                where = f"{(pl.get(e['after']) or {}).get('title') or '그 장소'} 다음에"
            else:
                to = max(1, min(nd, int(e.get("day") or 1))) - 1
                idx = next((k for k, x in enumerate(days[to]) if x.role == "stay"), len(days[to]))
                days[to].insert(idx, blk)
                where = "끝(숙소 전)에"
            changes.append(f"{to + 1}일차 {where} '{label}' {mins}분을 넣었어요(그 시간만큼 뒤 일정이 밀려요)")
    elif op == "breakfast":      # {mode?: 근처 식당|숙소 조식|안 먹음, day?: N, skip?: bool}
        if e.get("mode") in ("근처 식당", "숙소 조식", "안 먹음"):
            spec.breakfast = e["mode"]
            spec.breakfast_skip_days = []
            changes.append(f"아침 식사를 '{e['mode']}'(으)로 바꿨어요"
                           + (" — 숙소에서 바로 먹으니 이동이 없어 30분 늦게 시작해도 돼요" if e["mode"] == "숙소 조식" else ""))
        elif e.get("day"):
            dn = int(e["day"])
            if e.get("skip", True):
                spec.breakfast_skip_days = sorted(set(spec.breakfast_skip_days) | {dn})
                changes.append(f"{dn}일차 아침 식사를 뺐어요")
            else:
                spec.breakfast_skip_days = [x for x in spec.breakfast_skip_days if x != dn]
                changes.append(f"{dn}일차 아침 식사를 다시 넣었어요")
    elif op == "return_stop":    # {action: add|remove, stop: {...}} 귀가길에 잠깐 들를 곳
        st = e.get("stop") if isinstance(e.get("stop"), dict) else {}
        key_ = str(st.get("key") or st.get("name") or "")[:80]
        if e.get("action") == "remove":
            spec.return_stops = [s for s in spec.return_stops if s.get("key") != key_ and s.get("name") != key_]
            changes.append(f"귀가길 들를 곳에서 {_josa(str(st.get('name') or key_), '을를')} 뺐어요")
        else:
            try:
                stop = {"key": key_, "name": guardrails.mask_pii(str(st["name"])[:40])[0], "lat": float(st["lat"]), "lng": float(st["lng"]),
                        "detour_min": max(0, min(120, int(st.get("detour_min") or 0))), "stay_min": max(20, min(120, int(st.get("stay_min") or 40))),
                        "km_from_start": st.get("km_from_start"), "kind": st.get("kind"), "source": st.get("source") or "한국관광공사"}
            except (KeyError, TypeError, ValueError):
                return {"status": "승인 대기", "answer": "들를 곳 정보를 읽지 못했어요.", "trace": _t(state, "edit", e, "형식 오류")}
            if len(spec.return_stops) >= 3:
                return {"status": "승인 대기", "answer": "귀가길에 들를 곳은 3곳까지만 넣을 수 있어요.", "trace": _t(state, "edit", e, "상한")}
            spec.return_stops = [s for s in spec.return_stops if s.get("key") != stop["key"]] + [stop]
            spec.return_stops.sort(key=lambda s: s.get("km_from_start") or 0)
            changes.append(f"돌아가는 길에 {_josa(stop['name'], '을를')} 들르도록 넣었어요(우회 약 {stop['detour_min']}분 + 머무는 {stop['stay_min']}분)")
    elif op == "order":          # 끌어서 바꾼 전체 순서(날짜 사이 이동 포함)
        by_id = {x.place_id: x for row in days for x in row if x.place_id != planner.MEAL_ID}
        want = {int(r.get("day") or 0): [str(x) for x in r.get("place_ids") or []] for r in e.get("days") or []}
        if not want or any(i not in by_id for ids in want.values() for i in ids):
            return {"status": "승인 대기", "answer": "순서를 바꿀 장소를 찾지 못했어요. 화면을 새로 고친 뒤 다시 해 주세요.",
                    "trace": _t(state, "edit", e, "모르는 id")}
        listed = {i for ids in want.values() for i in ids}
        moved = 0
        for di in range(nd):        # 목록에 없는 날에서는 다른 날로 옮겨 간 장소만 뺀다
            if di + 1 not in want:
                days[di] = [x for x in days[di] if x.place_id == planner.MEAL_ID or x.place_id not in listed]
        for dn, ids in want.items():
            if not 1 <= dn <= nd:
                continue
            if [x.place_id for x in days[dn - 1] if x.place_id != planner.MEAL_ID] != ids:
                moved += 1
                spec.user_ordered_days = sorted(set(spec.user_ordered_days) | {dn})
            ph = [x for x in days[dn - 1] if x.place_id == planner.MEAL_ID]
            days[dn - 1] = [by_id[i] for i in ids] + ph
        changes.append(f"{moved}개 날의 순서를 직접 정하신 대로 바꿨어요(직접 정한 순서를 최우선으로 지켜요)" if moved
                       else "순서는 그대로예요")
    elif op == "optimize":       # 동선 최적화(식사·숙소 자리는 고정, 그 사이 관광지 순서만)
        whole = e.get("scope") == "all"
        targets = list(range(1, nd + 1)) if whole or not e.get("day") else [int(e["day"])]
        prop = route_proposal(spec, days, "all" if whole else "day")
        if whole:
            if prop["total_saved_min"] > 0:
                days[:] = [r["_row"] for r in prop["days"]]
            saved = prop["total_saved_min"]
        else:
            for r in prop["days"]:
                if r["day"] in targets and r["saved_min"] > 0:
                    days[r["day"] - 1] = r["_row"]
            saved = sum(r["saved_min"] for r in prop["days"] if r["day"] in targets)
        spec.user_ordered_days = [d for d in spec.user_ordered_days if d not in targets]
        if saved > 0:
            changes.append(f"{'전체 일정' if whole else '일별'} 동선을 최적화해 이동을 약 {saved}분 줄였어요")
            changes += [f"{m['name']}: {m['from_day']}일차 → {m['to_day']}일차" for m in prop.get("moved") or []][:6]
        else:
            changes.append("이미 가장 짧은 동선이에요")
    else:
        return {"status": "오류", "answer": "앗, 모르는 편집 요청이에요.", "trace": _t(state, "edit", e, "알 수 없는 op")}
    out = _reschedule(state, spec, days, changes, "edit")
    cap = planner.INTENSITY.get(spec.intensity, planner.INTENSITY["보통"])[2]
    for i, row in enumerate(days, 1):
        n = sum(1 for x in row if x.role == "place")
        if n > cap:
            out["changes"].append(f"{i}일차 관광지가 {n}곳으로 '{spec.intensity}' 강도 권장({cap}곳)보다 많아요. 한 곳을 후보로 돌릴까요?")
    return out


def _josa(word: str, pair: str) -> str:
    """받침에 따라 조사 선택: pair='을를' / '으로로' / '은는' / '이가'. 따옴표·괄호 뒤는 앞 글자 기준."""
    core = (word or " ").rstrip("'\")]) ")
    core = core[:-1] if core.endswith("(") else core
    ch = core[-1] if core else " "
    code = ord(ch) - 0xAC00
    has = 0 <= code < 11172 and code % 28 != 0
    if pair == "으로로":
        return word + ("으로" if has and code % 28 != 8 else "로")
    return word + (pair[0] if has else pair[1])


def _hm(v) -> int | None:
    m = re.search(r"(\d{1,2})\s*[:시]\s*(\d{2})?", str(v or ""))
    return int(m.group(1)) * 60 + int(m.group(2) or 0) if m else None


def start_node(state: State) -> State:
    """선택형 '여행 시작' 카드(SERVICE.md 3-6 (14)) — 구조화된 조건으로 바로 계획(자연어 해석 없이)."""
    s = state.get("start_req") or {}
    rid = s.get("region")
    if rid not in data.available_regions():
        rid = next((r for k, r in data.REGION_ALIASES.items() if k in str(rid or "")), None)
    if not rid or rid not in data.available_regions():
        return {"status": "되묻기", "stage": "intake", "answer": f"어느 지역으로 갈까요? 지금은 {supported_label()} 일정을 짤 수 있어요.",
                "extra": {"chips": [data.region_label(r) for r in data.available_regions()]},
                "trace": _t(state, "start", s, "지역 없음")}
    try:
        d0 = date.fromisoformat(s.get("start_date"))
    except (TypeError, ValueError):
        return {"status": "되묻기", "stage": "intake", "answer": "출발일을 달력에서 골라 주세요.",
                "trace": _t(state, "start", s, "날짜 없음")}
    try:
        d1 = date.fromisoformat(s.get("end_date") or s.get("start_date"))
    except ValueError:
        d1 = d0
    notes, assumed, given = [], [], []
    nights = max(0, (d1 - d0).days)
    if nights > MAX_NIGHTS:
        notes.append(f"최대 {MAX_NIGHTS}박 {MAX_NIGHTS + 1}일까지 검증할 수 있어 줄였어요")
        nights = MAX_NIGHTS
    from . import geo
    origin = s.get("origin")
    oloc = geo.locate(origin, s.get("origin_place")) if (origin or s.get("origin_place")) else None
    dep = oloc["sido"] if oloc else None
    if (origin or s.get("origin_place")) and not oloc:
        notes.append(f"출발지 '{origin}'의 위치를 찾지 못해 오가는 시간은 빼고 계산했어요. '여행 조건'에서 동·역 이름으로 다시 알려 주세요")
    elif not dep:
        notes.append("출발지가 없어 오가는 시간은 빼고 현지 도착 기준으로 계산했어요")
    eloc = geo.locate(s.get("end_location"), s.get("end_place")) if (s.get("end_location") or s.get("end_place")) else None
    long_t = s.get("long_transport") or "자차"
    local_t = s.get("local_transport") or ("렌터카" if long_t != "자차" else "자차")
    if not s.get("long_transport"):
        assumed.append("long_transport")
    if not s.get("local_transport"):
        assumed.append("local_transport")
    comp = s.get("companions") or {}
    if not comp.get("type"):
        assumed.append("companions")
    if not s.get("intensity"):
        assumed.append("intensity")
    dt = _hm(s.get("depart_time"))
    if dt is not None:
        given.append("depart_time")
    end_loc = s.get("end_location")
    budget, per = costs.parse_budget(s.get("budget"))
    spec = planner.TripSpec(
        region=rid, start=d0, nights=nights, departure=dep, depart_time=dt if dt is not None else DEFAULT_DEPART,
        transport="대중교통" if local_t == "대중교통" else "자차", companions=comp.get("note") or None,
        preferences=list(s.get("interests") or []), intensity=s.get("intensity") or "보통",
        long_transport=long_t, local_transport=local_t, companion_type=comp.get("type") or "보통",
        companion_count=comp.get("count"), origin_label=oloc["label"] if oloc else None,
        origin_coords=[oloc["lat"], oloc["lng"]] if oloc else None,
        end_coords=[eloc["lat"], eloc["lng"]] if eloc else None,
        assumptions=assumed, budget=budget, budget_per_person=per, budget_label=s.get("budget") if budget else None,
        vehicle=s.get("vehicle") or None, end_location=eloc["sido"] if eloc else None,
        end_label=eloc["label"] if eloc else None, arrive_time=_hm(s.get("arrive_time")), preset=s.get("preset"), given=given)
    if s.get("waypoints"):
        ids, users, wnotes = resolve_waypoints([w for w in s["waypoints"] if w], rid)
        spec.waypoint_ids, spec.user_waypoints = ids, users
        notes += wnotes
    if assumed:
        lab = {"long_transport": "장거리 이동", "local_transport": "현지 이동", "companions": "동반자", "intensity": "여행 강도"}
        notes.append(", ".join(lab[a] for a in assumed) + "은(는) 기본값으로 정했어요. 오른쪽 '여행 조건'에서 바꿀 수 있어요")
    preset = next((p for p in __import__("momo.options", fromlist=["x"]).STYLE_PRESETS if p["label"] == s.get("preset")), None)
    request = (state.get("question") or "").strip() or f"{data.region_label(rid)} 여행"
    if preset:
        request += f" · 여행 스타일: {preset['label']}({preset['desc']})"
    if nights == 0 and dep:
        w = planner.windows(spec)[0]
        if w[1] - w[0] < 60:
            return {"status": "정보", "stage": "intake", "answer": f"{dep}에서 {data.region_label(rid)}까지 오가는 시간이 길어서 당일치기로는 "
                    "현지에 머물 시간이 1시간도 안 돼요. 더 이른 출발이나 1박 어때요?", "trace": _t(state, "start", s, "시간 창 부족")}
    return _run_plan(state, spec, request, notes, base=None)


def pack_node(state: State) -> State:
    """준비물 체크리스트 체크·추가·삭제 — 일정·상태는 그대로, 즉시 반영(LLM 없음)."""
    from . import packing
    op = dict(state.get("pack_req") or {})
    plan = state.get("plan")
    if not plan or not state.get("spec") or not plan.get("packing"):
        return {"status": "오류", "answer": "앗, 준비물을 붙일 계획이 아직 없어요.", "trace": _t(state, "pack", op, "계획 없음")}
    if op.get("label"):
        op["label"] = guardrails.mask_pii(str(op["label"]))[0]
    plan = {**plan, "packing": packing.apply(json.loads(json.dumps(plan["packing"])), op)}
    spec = spec_from_dict(state["spec"])
    spec.pack_checked, spec.pack_custom = packing.state_of(plan["packing"])
    pk = plan["packing"]
    msg = {"toggle": "체크했어요", "add": "준비물을 추가했어요", "remove": "준비물을 뺐어요"}.get(op.get("op"), "반영했어요")
    if state.get("plan_id"):
        saved = store.get_plan(state["plan_id"])
        if saved:
            saved["plan"] = plan
            store.save_plan(state["session_id"], state.get("draft_version") or "", saved)
    extra = {"draft_version": state.get("draft_version")}
    if state.get("plan_id"):
        extra["plan_id"] = state["plan_id"]
    return {"status": state.get("status") or "승인 대기", "stage": "refine", "answer": f"{msg} (준비 {pk['done']}/{pk['total']})",
            "spec": spec_to_dict(spec), "plan": plan, "plan_out": plan, "conditions": plan["trip"]["checklist"],
            "changes": [], "extra": extra, "trace": _t(state, "pack", op, f"{pk['done']}/{pk['total']}")}


def cost_node(state: State) -> State:
    """계획 경비에 비용을 직접 추가·삭제(식사·디저트·기념품 등) — 일정은 그대로, 경비만 다시 계산."""
    c = state.get("cost_req") or {}
    if not state.get("spec") or not state.get("itinerary"):
        return {"status": "오류", "answer": "앗, 비용을 붙일 일정이 아직 없어요.", "trace": _t(state, "cost", c, "초안 없음")}
    spec = spec_from_dict(state["spec"])
    if c.get("op") == "add":
        try:
            amount = int(str(c.get("amount", "")).replace(",", ""))
            assert 0 <= amount <= 100_000_000
        except (ValueError, AssertionError):
            return {"status": "승인 대기", "answer": "금액을 숫자로 알려 주세요(예: 30000).", "trace": _t(state, "cost", c, "금액 오류")}
        from .options import EXPENSE_CATEGORIES
        cat = c.get("category") if c.get("category") in EXPENSE_CATEGORIES else "기타"
        label = guardrails.mask_pii(str(c.get("label") or cat)[:60])[0]
        spec.custom_costs = [*spec.custom_costs, {"id": uuid.uuid4().hex[:8], "category": cat, "label": label, "amount": amount}]
        change = f"{label} {amount:,}원을 예상 경비에 넣었어요"
    elif c.get("op") == "remove":
        before = len(spec.custom_costs)
        spec.custom_costs = [x for x in spec.custom_costs if x["id"] != c.get("id")]
        change = "직접 넣은 비용을 뺐어요" if len(spec.custom_costs) < before else "그 비용 항목을 찾지 못했어요"
    else:
        return {"status": "오류", "answer": "앗, 모르는 비용 요청이에요.", "trace": _t(state, "cost", c, "알 수 없는 op")}
    items = state["itinerary"]
    viol = state.get("violations") or []
    notes = list((state.get("plan") or {}).get("trip", {}).get("assumptions") or [])
    plan = brochure.build(spec, items, state.get("ranked") or [], viol, notes)
    total = plan["costs"]["total"]
    answer = f"{change}. 예상 경비는 이제 약 {total:,}원(1인 약 {plan['costs']['per_person']:,}원)이에요."
    return {"status": "승인 대기", "stage": "refine", "answer": answer, "spec": spec_to_dict(spec), "plan": plan,
            "plan_out": plan, "conditions": plan["trip"]["checklist"], "changes": [change],
            "extra": {"draft_version": state.get("draft_version")},
            "trace": _t(state, "cost", c, f"경비 합계 {total:,}원")}


SET_LABEL = {"depart_time": "출발 시각", "arrival": "여행 후 도착", "vehicle": "차량",
             "region": "여행 지역", "start_date": "출발일", "length": "여행 길이", "origin": "출발지",
             "long_transport": "장거리 이동", "local_transport": "현지 이동", "intensity": "여행 강도",
             "companions": "동반자", "budget": "최대 예산", "interests": "관심사", "breakfast": "아침 식사"}


def _apply_change(spec: planner.TripSpec, a: set, key: str, value: str, place: dict | None):
    """조건 하나를 spec에 반영. (spec, 다시 계획 필요?, 표시 값, 오류 문구)"""
    from . import geo
    label = SET_LABEL.get(key, key)
    if key == "region":
        rid = next((r for k, r in data.REGION_ALIASES.items() if k in value), None)
        if not rid or rid not in data.available_regions():
            return spec, False, value, f"'{value}'은(는) 아직 준비된 지역이 아니에요. 지금은 {supported_label()}을 짤 수 있어요."
        return planner.TripSpec(**{**spec.__dict__, "region": rid, "waypoint_ids": [], "user_waypoints": []}), True, value, None
    if key == "start_date":
        try:
            spec.start = date.fromisoformat(value)
        except ValueError:
            return spec, False, value, "날짜 형식을 알아보지 못했어요(예: 2026-12-12)."
        a.discard("date")
        return spec, True, value, None
    if key == "end_date":
        try:
            spec.nights = max(0, min(MAX_NIGHTS, (date.fromisoformat(value) - spec.start).days))
        except ValueError:
            return spec, False, value, "날짜 형식을 알아보지 못했어요(예: 2026-12-14)."
        a.discard("length")
        return spec, True, value, None
    if key == "length":
        m = re.search(r"(\d)\s*박", value)
        spec.nights = 0 if "당일" in value else min(MAX_NIGHTS, int(m.group(1))) if m else 1
        a.discard("length")
        return spec, True, value, None
    if key == "origin":
        loc = geo.locate(value, place)
        if not loc:
            return spec, False, value, f"'{value}'의 위치를 찾지 못했어요. 동·구 이름이나 역 이름으로 알려 주세요(예: 판교동, 수원역)."
        spec.departure, spec.origin_label, spec.origin_coords = loc["sido"], loc["label"], [loc["lat"], loc["lng"]]
        a.discard("origin")
        return spec, False, loc["label"], None
    if key in ("long_transport", "local_transport"):
        opts = brochure.CHECK_OPTIONS[key]
        if value not in opts:
            return spec, False, value, f"{label}은(는) {', '.join(opts)} 중에서 골라 주세요."
        setattr(spec, key, value)
        if key == "local_transport":
            spec.transport = "대중교통" if value == "대중교통" else "자차"
        a.discard(key)
        return spec, False, value, None
    if key == "breakfast":
        if value not in ("근처 식당", "숙소 조식", "안 먹음"):
            return spec, False, value, "아침은 근처 식당·숙소 조식·안 먹음 중에서 골라 주세요."
        spec.breakfast = value
        spec.breakfast_skip_days = []
        return spec, False, value, None
    if key == "intensity":
        if value not in brochure.CHECK_OPTIONS["intensity"]:
            return spec, False, value, "여행 강도는 빡빡·보통·여유 중에서 골라 주세요."
        spec.intensity = value
        a.discard("intensity")
        return spec, True, value, None
    if key == "companions":
        spec.companion_type = value if value in brochure.CHECK_OPTIONS["companions"] else _infer_companion(value)
        m = re.search(r"(\d+)\s*명", value)
        if m:
            spec.companion_count = int(m.group(1))
        a.discard("companions")
        return spec, True, value, None
    if key == "budget":
        b, per = costs.parse_budget(value)
        if not b:
            return spec, False, value, "예산 금액을 알아보지 못했어요(예: 60만 원, 1인 20만 원)."
        spec.budget, spec.budget_per_person, spec.budget_label = b, per, value
        return spec, False, value, None
    if key == "interests":
        spec.preferences = [x.strip() for x in re.split(r"[,·/]", value) if x.strip()]
        return spec, True, value, None
    if key == "depart_time":
        if not value.strip():      # '선택 안 함' → 기본 08:00, 선택 항목으로 되돌림
            spec.depart_time = DEFAULT_DEPART
            spec.given = [g for g in spec.given if g != "depart_time"]
            return spec, False, "선택 안 함(기본 08:00)", None
        t = _hm(value)
        if t is None:
            return spec, False, value, "출발 시각을 알아보지 못했어요(예: 07:30)."
        spec.depart_time = t
        spec.given = sorted(set(spec.given) | {"depart_time"})
        return spec, False, value, None
    if key == "arrival":
        t = _hm(value)
        where = re.sub(r"\d{1,2}\s*[:시]\s*(\d{2})?\s*(까지|분)?", "", value).replace("출발지로", "").strip(" ·,")
        if t is not None:
            spec.arrive_time = t
        elif not where and not place:     # 시각 지움
            spec.arrive_time = None
        if where or place:
            loc = geo.locate(where, place)
            if not loc:
                return spec, False, value, f"'{where}'의 위치를 찾지 못했어요. 동·구 이름이나 역 이름으로 알려 주세요."
            spec.end_location, spec.end_label, spec.end_coords = loc["sido"], loc["label"], [loc["lat"], loc["lng"]]
        elif "출발지" in value:
            spec.end_location, spec.end_label, spec.end_coords = None, None, None
        return spec, False, value, None
    if key == "vehicle":
        try:
            v = json.loads(value) if value.startswith("{") else {"type": value}
        except json.JSONDecodeError:
            v = {"type": value}
        spec.vehicle = {k: v.get(k) for k in ("type", "fuel", "efficiency_kmpl", "toll_discount", "parking_discount") if v.get(k) not in (None, "")}
        return spec, False, brochure._vehicle_label(spec.vehicle), None
    return spec, False, value, "앗, 모르는 항목이에요."


def set_node(state: State) -> State:
    """오른쪽 정보창에서 확정 요소를 바꿈 — 여러 건을 모아 한 번에(changes 배열) 반영하고 계획을 한 번만 다시 세운다."""
    sreq = state.get("set_req") or {}
    changes_in = sreq.get("changes") or ([{"key": sreq.get("key"), "value": sreq.get("value"), "place": sreq.get("place")}]
                                          if sreq.get("key") else [])
    changes_in = [{"key": c.get("key"), "value": str(c.get("value") or "").strip(), "place": c.get("place")} for c in changes_in if c.get("key")]
    if not changes_in:
        return {"status": "오류", "answer": "바꿀 조건이 없어요.", "trace": _t(state, "set", sreq, "변경 없음")}
    if not state.get("spec"):
        pend = dict(state.get("pending") or {})
        for c in changes_in:
            key, value = c["key"], c["value"]
            if key == "region":
                pend["region_text"] = value
            elif key == "start_date":
                try:
                    d = date.fromisoformat(value)
                    pend.update(month=d.month, day=d.day)
                except ValueError:
                    pass
            elif key == "length":
                m = re.search(r"(\d)\s*박", value)
                pend["nights"] = 0 if "당일" in value else min(MAX_NIGHTS, int(m.group(1))) if m else 1
            elif key == "origin":
                pend["departure"] = value
            elif key in ("long_transport", "local_transport", "intensity"):
                pend[key] = value
            elif key == "companions":
                pend["companion_type"] = value
            elif key == "budget":
                pend["budget_text"] = value
        return {"pending": pend, "status": "되묻기", "stage": "intake",
                "answer": "말씀하신 조건을 기억해 둘게요. 남은 조건을 알려 주시거나 '알아서 짜 줘'를 눌러 주세요.",
                "extra": {"chips": ["알아서 짜 줘"]}, "trace": _t(state, "set", sreq, "intake 조건 저장")}
    spec = spec_from_dict(state["spec"])
    a = set(spec.assumptions)
    replan, done, errors = False, [], []
    for c in changes_in:
        spec, need, shown, err = _apply_change(spec, a, c["key"], c["value"], c.get("place"))
        if err:
            errors.append(err)
            continue
        replan = replan or need
        done.append(f"{_josa(SET_LABEL.get(c['key'], c['key']), '을를')} {_josa(chr(39) + shown + chr(39), '으로로')}")
    spec.assumptions = sorted(a)
    if not done:
        return {"status": "승인 대기", "answer": " ".join(errors), "changes": errors[:2], "trace": _t(state, "set", sreq, "반영 0건")}
    change = ", ".join(done) + " 바꿨어요"
    if replan:
        notes = [change]
        out = _run_plan(state, spec, f"{state.get('last_request', '')}\n[조건 변경] {change}", notes, base=None)
        out["changes"] = [change + " — 일정을 새로 맞췄어요", *errors][:3]
        out["stage"] = "refine"
        return out
    out = _reschedule(state, spec, _day_picks(spec, state["itinerary"]), [change], "set", repair=True)
    out["changes"] = [*out["changes"], *errors][:3]
    return out


def _dep_label(text: str | None) -> str:
    """출발지 표기: 시·도 + 시·군·구까지만(마스킹 자리표시자·상세 주소 제거)."""
    t = re.sub(r"\[[^\]]*\]", "", text or "").strip()
    words = t.split()
    keep = [w for w in words if re.search(r"(특별시|광역시|자치시|도|시|군|구)$", w) or data.norm_sido(w) == w]
    return " ".join(keep[:2]) or (words[0] if words else "")


def _role(it: dict) -> str:
    if it["type"] == "stay":
        return "stay"
    if it["type"] == "meal":
        return "lunch" if hours.parse_hm(it["start_time"]) < 16 * 60 else "dinner"
    return "place"


def _region_reply(state: State, why: str, p: ParsedRequest) -> State:
    pre = ""
    if p.send_request:
        pre = "문자·메일로 직접 보내 드리는 건 할 수 없어요. 일정을 확정하면 공유 링크와 캘린더 파일(.ics)을 드릴게요. "
    if p.departure and data.norm_sido(p.departure):
        pre += f"출발지는 {_dep_label(p.departure)} 기준으로만 쓸게요(상세 주소는 저장하지 않아요). "
    if why == "missing":
        return {"status": "되묻기", "answer": pre + f"좋아요, 어디로 날아가 볼까요? 지금은 {supported_label()} 일정을 꼼꼼히 짜 드릴 수 있어요. "
                "가고 싶은 지역과 함께, 당일치기인지 1박 2일인지, 어디서 출발하시는지도 같이 알려 주시면 바로 정리해 드릴게요!",
                "extra": {"chips": [*[data.region_label(r) for r in data.available_regions()], "당일치기", "1박 2일", "자차", "대중교통"]},
                "trace": _t(state, "ask_back", "지역 없음", "되묻기 1회(지역·길이·출발지·이동수단)")}
    if why == "overseas":
        return {"status": "정보", "answer": f"{p.region_text or '해외'} 여행이군요! 해외 일정(가벼운 모드)은 아직 준비 중이에요. "
                "해외는 운영시간을 제가 검증할 수 없어서, 현지에서 꼭 한 번 더 확인해 주셔야 해요. "
                f"지금은 국내 {supported_label()} 일정을 꼼꼼히 짜 드릴 수 있어요.",
                "theme": {"scope": "overseas"},
                "trace": _t(state, "plan", "해외", "가벼운 모드 미구현 → 안내")}
    name = p.region_text or "그 지역"
    extra = " 수원은 데이터를 준비하고 있어서 곧 함께 안내해 드릴 수 있어요." if why == "unprepared" else ""
    return {"status": "정보", "answer": f"{name}은(는) 아직 제가 자세히 날아 본 곳이 아니라, 운영시간·휴무일까지 검증한 정밀 일정은 "
            f"준비된 지역({supported_label()})만 가능해요.{extra} 대신 준비된 지역으로 짜 드리거나, "
            f"{name}은 관광공사 '대한민국 구석구석'에서 기본 정보를 먼저 살펴보시는 걸 추천해요.",
            "extra": {"chips": [f"{data.region_label(r)} 1박 2일" for r in data.available_regions()]},
            "trace": _t(state, "plan", "지원 범위 밖 지역", why)}


INFO_SYSTEM = MOMO_PERSONA + """
정보 질문(운영시간·휴무일·축제·날씨·장소 추천·예약 문의)에 도구로 확인한 사실만 답한다. 오늘은 {today}.
- 지원 지역: {supported} (region 값: {region_ids}). 그 밖은 준비 안 된 지역이라고 안내.
- 운영시간·휴무일을 말할 때는 '출처: 한국관광공사'를 쓰고, 변동될 수 있으니 방문 전 확인을 권한다.
- 실시간 좌석 확인·예약·결제는 대신 할 수 없다고 먼저 말하고, 직접 확인 방법(전화·예약 앱)과 도구로 찾은 후보 식당(이름·운영시간·연락처)을 안내한다.
- 오늘 영업·휴무 여부는 도구 결과의 today_status(코드 판정: 확인됨/휴무/확인 필요)를 그대로 따른다. 휴무인 곳은 '오늘 휴무'라고 알리고 후보에서 뺀다.
- 축제가 없으면 없다고 말한다. 날씨는 예보 범위 밖이면 '계절 평년 안내'라고 밝힌다.
- 도구 결과에 없는 장소·시간·전화번호는 지어내지 않는다. 답은 모모 말투로 짧고 친절하게.
"""


def info_node(state: State) -> State:
    from langchain.agents import create_agent
    from langchain_core.tools import tool

    p = ParsedRequest(**state["parsed"])
    region, why = resolve_region(p)
    if why in ("overseas", "unprepared", "unsupported", "missing") and p.info_topic not in ("hours",):
        return _region_reply(state, why, p)

    @tool
    def retrieve_places(query: str, region: str) -> str:
        """지역 안의 관광지·음식점·숙박 장소를 검색한다. region은 지원 지역 id(gyeongju, suncheon_gurye, suwon)."""
        hits = retrieve.search(query, region if region in data.available_regions() else None, k=5)
        out = []
        for h in hits:
            pl = data.all_places()[h["doc_id"]]
            tools.add_context(pl["contentid"], tools.place_doc_text(pl))
            intro = pl.get("intro") or {}
            out.append({"name": pl["title"], "kind": pl["kind"], "summary": pl.get("summary"),
                        "hours": hours.clean(intro.get("usetime") or intro.get("opentimefood")),
                        "rest": hours.clean(intro.get("restdate") or intro.get("restdatefood")),
                        "tel": hours.clean(intro.get("infocenterfood") or intro.get("infocenter") or pl.get("tel")),
                        "today_status": hours.parse_open(pl, today()).status})
        return json.dumps(out, ensure_ascii=False)

    @tool
    def get_operating_info(place_name: str, on_date: str | None = None) -> str:
        """장소의 운영시간·휴무일·문의처를 조회한다. on_date(YYYY-MM-DD)를 주면 그날 운영 여부도 계산한다."""
        d = date.fromisoformat(on_date) if on_date else None
        r = tools.operating_info(place_name, region, d)
        return json.dumps(r or {"error": "수집 데이터에서 찾지 못함"}, ensure_ascii=False)

    @tool
    def search_festivals(region: str, start_date: str, end_date: str) -> str:
        """기간(YYYY-MM-DD) 안에 열리는 축제·행사를 조회한다(수집 스냅샷 2026-12-01~2027-02-28)."""
        if region not in data.available_regions():
            return json.dumps({"error": "준비 안 된 지역"}, ensure_ascii=False)
        return json.dumps(tools.festivals(region, date.fromisoformat(start_date), date.fromisoformat(end_date)),
                          ensure_ascii=False) or "[]"

    @tool
    def get_weather(region: str, start_date: str, end_date: str) -> str:
        """여행 지역의 날짜별 날씨(예보 범위 밖이면 계절 평년 안내)."""
        s, e = date.fromisoformat(start_date), date.fromisoformat(end_date)
        days = [s + timedelta(days=i) for i in range((e - s).days + 1)]
        return json.dumps(tools.weather(region, days), ensure_ascii=False)

    agent = create_agent(chat(0.2, 1500), tools=[retrieve_places, get_operating_info, search_festivals, get_weather],
                         system_prompt=INFO_SYSTEM.replace("{today}", _today_label()).replace("{supported}", supported_label())
                         .replace("{region_ids}", supported_ids()))
    token = tools.CONTEXTS.set([])
    try:
        res = agent.invoke({"messages": [("user", f"{state['question']}\n(추정 지역: {region or '미상'})")]},
                           {"recursion_limit": 12})
        contexts = tools.CONTEXTS.get()
    finally:
        tools.CONTEXTS.reset(token)
    tr = list(state.get("trace") or [])
    for m in res["messages"]:
        for tc in getattr(m, "tool_calls", None) or []:
            tr.append({"step": f"tool:{tc['name']}", "input": _s(tc["args"]), "output": "호출"})
        if m.type == "tool":
            tr.append({"step": f"tool_result:{m.name}", "input": "", "output": str(m.content)[:500]})
    content = res["messages"][-1].content
    answer = content if isinstance(content, str) else "".join(c.get("text", "") for c in content if isinstance(c, dict))
    if contexts and "한국관광공사" not in answer:
        answer += "\n\n(출처: 한국관광공사 TourAPI)"
    return {"status": "정보", "answer": answer, "contexts": contexts, "trace": tr,
            "theme": {"scope": "domestic", "season": tools.season_of(today())}}


def approve_chat_node(state: State) -> State:
    if not state.get("itinerary"):
        return {"status": "정보", "answer": "아직 확정할 일정이 없어요. 먼저 가고 싶은 지역과 날짜를 알려 주시면 일정을 짜 드릴게요!",
                "trace": _t(state, "approve_guard", "채팅 승인 요구", "초안 없음")}
    return {"status": "승인 대기", "answer": "저장하고 공유하는 건 꼭 직접 확인을 받은 뒤에만 할 수 있어요. 채팅 말씀만으로는 저장·공유하지 않아요. "
            "일정을 한 번 더 살펴보시고 괜찮으시면 화면의 '확정할게요' 버튼을 눌러 주세요. 그러면 공유 링크와 캘린더 파일(.ics)을 만들어 드릴게요!"
            "\n(일정 속 장소·운영시간 정보 출처: 한국관광공사 TourAPI)",
            "extra": {"draft_version": state.get("draft_version")},
            "trace": _t(state, "approve_guard", "채팅으로 확정·저장·공유 요구", "저장 안 함 · 승인 버튼 안내(save_plan 미호출)")}


def reject_node(state: State) -> State:
    return {"status": "거절", "answer": "죄송해요, 그 부탁은 여행 상담 범위를 벗어나서 도와드리기 어려워요. 저는 국내 여행 일정을 짜 드리는 "
            "우체부 철새 모모예요. 가고 싶은 곳이 생기면 언제든 편지 주세요!",
            "trace": _t(state, "reject", state["parsed"].get("intent"), "범위 밖 요청 거절")}


def approve_node(state: State) -> State:
    items = state.get("itinerary")
    sid = state["session_id"]
    if not items:
        return {"status": "오류", "answer": "앗, 확정할 일정 초안이 없어요. 먼저 일정을 만들어 볼까요?",
                "trace": _t(state, "approve", "approve", "초안 없음")}
    dv_in = state.get("draft_version_in")
    if dv_in and dv_in != state.get("draft_version"):
        return {"status": "승인 대기", "answer": "일정이 그사이 바뀌었어요. 최신 일정을 한 번 더 확인하신 뒤 '확정할게요'를 눌러 주세요.",
                "extra": {"draft_version": state.get("draft_version")},
                "trace": _t(state, "approve", dv_in, "오래된 draft_version → 거절")}
    viol = state.get("violations") or []
    if viol:
        return {"status": "승인 대기", "answer": "아직 규칙 검사에서 걸리는 부분이 있어 확정할 수 없어요: "
                + "; ".join(f"{v['place_name']} {v['detail']}" for v in viol[:3]) + ". 이 부분을 바꿔 드릴까요?",
                "extra": {"draft_version": state.get("draft_version"), "violations": viol},
                "trace": _t(state, "approve", "approve", f"위반 {len(viol)}건 → 승인 거부")}
    spec = state["spec"]
    payload = {"title": spec.get("title") or f"{data.region_label(spec['region'])} {'당일치기' if spec['nights'] == 0 else str(spec['nights']) + '박 ' + str(spec['nights'] + 1) + '일'} · 모모 여행 일정",
               "region": spec["region"], "region_label": data.region_label(spec["region"]),
               "departure_sido": spec.get("departure"), "nights": spec["nights"], "start": spec["start"],
               "itinerary": items, "draft_version": state["draft_version"], "plan": state.get("plan")}
    pid = store.save_plan(sid, state["draft_version"], payload)
    has_check = any(it["hours_status"] == "확인 필요" for it in items)
    ans = ("확정했어요! 엽서를 꼭꼭 눌러 담아 두었어요. 공유 링크로 가족과 함께 보시고, HTML·Word·PDF로 내려받거나 캘린더(.ics)에 넣어 두세요. "
           "여행을 다녀온 뒤에는 공유 페이지에서 실제 쓴 경비를 적으면 '여행 결과 리포트'로 정리해 드려요."
           + (" '운영시간 확인 필요' 표시된 곳은 방문 전에 한 번 더 확인해 주세요." if has_check else ""))
    return {"status": "확정", "answer": ans, "plan_id": pid,
            "extra": {"plan_id": pid, "share_url": f"/plan/{pid}", "ics_url": f"/plan/{pid}.ics",
                      "html_url": f"/plan/{pid}.html", "docx_url": f"/plan/{pid}.docx",
                      "print_url": f"/plan/{pid}?print=1", "report_url": f"/plan/{pid}/report",
                      "draft_version": state["draft_version"]},
            "stage": "refine",
            "trace": _t(state, "approve", f"session {sid[:6]}…", f"save_plan → plan_id {pid}")}


def guard_out(state: State) -> State:
    ans = state.get("answer") or ""
    ok = guardrails.check_output(ans) and guardrails.check_output(_s(state.get("trace")))
    if not ok:
        ans = "죄송해요, 그 내용은 보여 드릴 수 없어요. 여행 이야기를 계속해 볼까요?"
    masked, found = guardrails.mask_pii(ans, guardrails.public_contacts())
    tr = guardrails.mask_obj(list(state.get("trace") or []))
    tr.append({"step": "guardrail_out", "input": "answer·trace 검사", "output": ("차단 후 교체" if not ok else "통과")
               + (f" · 마스킹 {found}" if found else "")})
    return {"answer": masked if ok else ans, "trace": tr, "status": state.get("status") if ok else "거절"}


# ---------------- 그래프 ----------------

def build_graph(checkpointer=None):
    g = StateGraph(State)
    g.add_node("guard_in", guard_in)
    g.add_node("parse", parse)
    g.add_node("plan", plan_node)
    g.add_node("modify", modify_node)
    g.add_node("info", info_node)
    g.add_node("approve_chat", approve_chat_node)
    g.add_node("reject", reject_node)
    g.add_node("approve", approve_node)
    g.add_node("edit", edit_node)
    g.add_node("setcond", set_node)
    g.add_node("start", start_node)
    g.add_node("cost", cost_node)
    g.add_node("pack", pack_node)
    g.add_node("guard_out", guard_out)
    g.add_edge(START, "guard_in")
    g.add_conditional_edges("guard_in", lambda s: "guard_out" if s.get("status") == "거절"
                            else {"approve": "approve", "edit": "edit", "set": "setcond", "start": "start", "cost": "cost", "pack": "pack"}.get(s.get("action") or "", "parse"))
    g.add_conditional_edges("parse", route_parsed, {"plan": "plan", "modify": "modify", "info": "info",
                                                    "approve_chat": "approve_chat", "reject": "reject"})
    for n in ("plan", "modify", "info", "approve_chat", "reject", "approve", "edit", "setcond", "start", "cost", "pack"):
        g.add_edge(n, "guard_out")
    g.add_edge("guard_out", END)
    return g.compile(checkpointer=checkpointer)


_GRAPH = None


def graph():
    global _GRAPH
    if _GRAPH is None:
        conn = sqlite3.connect(STATE_DB, check_same_thread=False)
        _GRAPH = build_graph(SqliteSaver(conn))
    return _GRAPH


def handle_query(question: str, session_id: str | None = None, action: str | None = None,
                 draft_version: str | None = None, edit: dict | None = None, set_req: dict | None = None,
                 start_req: dict | None = None, cost_req: dict | None = None, pack_req: dict | None = None,
                 replan: str | None = None) -> dict:
    """POST /query 본체. 질문은 그래프에 들어가기 전에 마스킹(LangSmith 업로드 전 포함)."""
    new_session = not session_id
    sid = session_id or uuid.uuid4().hex
    masked, found = guardrails.mask_pii(question or "")
    cfg = {"configurable": {"thread_id": sid}, "run_name": "momo_query"}
    if action == "approve" and not graph().get_state(cfg).values.get("itinerary"):
        pass  # approve_node가 '초안 없음'으로 응답
    if set_req and isinstance(set_req.get("value"), str):
        set_req = {**set_req, "value": guardrails.mask_pii(set_req["value"])[0]}
    snap = None
    cur = graph().get_state(cfg).values or {}
    prev_items, prev_dv = cur.get("itinerary") or [], cur.get("draft_version")
    if action in ("edit", "set") or (replan == "update" and cur.get("itinerary")):   # 되돌리기용: 편집·재수립 직전 상태
        if cur.get("itinerary"):
            snap = {k: cur.get(k) for k in UNDO_KEYS}
    out = graph().invoke({"question": masked, "action": action, "draft_version_in": draft_version, "replan": replan,
                          "session_id": sid, "edit": edit, "set_req": set_req, "start_req": start_req,
                          "cost_req": cost_req, "pack_req": pack_req}, cfg)
    answer = out.get("answer", "")
    if found:
        answer = "개인정보(" + ", ".join(found) + ")는 가려 두었어요. 출발지는 시·구 단위로만 쓸게요.\n\n" + answer
    resp = {"answer": answer, "contexts": out.get("contexts") or [], "trace": out.get("trace") or [],
            "status": out.get("status"), "session_id": sid, "theme": out.get("theme") or {},
            "itinerary": out.get("itinerary") if out.get("status") in ("승인 대기", "확정") else [],
            "stage": out.get("stage") or ("refine" if out.get("plan") else "intake" if out.get("status") == "되묻기" else "plan"),
            "plan": out.get("plan_out"), "conditions": out.get("conditions"), "changes": out.get("changes") or [],
            "progress": brochure.progress(out["conditions"]) if out.get("conditions") else None}
    resp.update(out.get("extra") or {})
    if snap and out.get("draft_version") and out.get("draft_version") != snap.get("draft_version"):
        store.push_snapshot(sid, snap)          # 실제로 바뀐 편집만 되돌리기 목록에
    resp.update(_edit_meta(sid, out))
    if prev_items and out.get("itinerary") and out.get("draft_version") and out.get("draft_version") != prev_dv:
        dif = plan_diff(prev_items, out["itinerary"], spec_from_dict(out["spec"]) if out.get("spec") else None)
        store.save_diff(sid, out["draft_version"], dif)
        resp["plan_diff"] = dif
        if resp.get("plan"):
            _tag_plan(resp["plan"], dif)
    resp["quick_replies"] = resp.get("quick_replies") or resp.get("chips") or []
    from . import richtext
    if out.get("plan_out"):
        resp["answer_html"] = richtext.plan_html(out["plan_out"], answer, out.get("changes") or [], out.get("status") or "")
    else:
        resp["answer_html"] = richtext.md_to_html(answer)
    store.save_answer(sid, resp["answer_html"], (out.get("plan") or {}).get("trip", {}).get("title") or "모모의 답변")
    # 상담 기록: 사용자가 보낸 것(채팅·버튼 편집·조건 변경 모두)과 모모 답변을 그대로 남긴다(다시 보기·다시 보내기)
    kind = action or "chat"
    utext = masked.strip() or _action_label(action, edit, set_req, start_req, cost_req, pack_req)
    if utext and action != "pack":
        store.log_message(sid, "user", kind, utext)
    if action != "pack":
        store.log_message(sid, "momo", kind, answer, resp.get("status") or "", resp["answer_html"], resp.get("draft_version"))
    resp["plan_no"] = store.plan_no(sid)
    if out.get("plan") and (out.get("plan_out") or out.get("status") == "확정"):
        store.upsert_trip(sid, out["plan"], out.get("status") or "", out.get("plan_id") if out.get("status") == "확정" else None)
    if new_session:
        resp["trace"].insert(0, {"step": "session", "input": "", "output": "새 세션 시작"})
    return resp


def backfill_messages(session_id: str) -> int:
    """기록 기능 전에 나눈 대화: 체크포인트 이력(실제 대화 실행분만)에서 요청·답변을 복원해 기록에 넣는다."""
    from datetime import datetime, timedelta, timezone
    if store.list_messages(session_id, 1):
        return 0
    runs = []
    for s in graph().get_state_history({"configurable": {"thread_id": session_id}}):
        if s.next != () or (s.metadata or {}).get("source") != "loop":
            continue
        v = s.values or {}
        if not (v.get("answer") or v.get("question")) or v.get("action") == "pack":
            continue
        try:
            ts = datetime.fromisoformat(s.created_at).astimezone(timezone(timedelta(hours=9))).isoformat(timespec="seconds")
        except Exception:
            ts = None
        runs.append((ts, v))
    from . import richtext
    for ts, v in reversed(runs):
        kind = v.get("action") or "chat"
        q = (v.get("question") or "").strip() or _action_label(v.get("action"), v.get("edit"), v.get("set_req"),
                                                              v.get("start_req"), v.get("cost_req"), v.get("pack_req"))
        if q:
            store.log_message(session_id, "user", kind, q, created=ts)
        store.log_message(session_id, "momo", kind, v.get("answer") or "", v.get("status") or "",
                          richtext.md_to_html(v.get("answer") or ""), v.get("draft_version"), created=ts)
    return len(runs)


def _action_label(action, edit, set_req, start_req, cost_req, pack_req) -> str:
    """질문 글 없이 버튼으로 보낸 요청을 사람이 읽을 수 있게."""
    if action == "approve":
        return "[확정] 이 계획으로 확정할게요"
    if action == "edit" and edit:
        return f"[일정 편집] {edit.get('op')}"
    if action == "set" and set_req:
        ch = set_req.get("changes") or [set_req]
        return "[조건 변경] " + ", ".join(f"{SET_LABEL.get(c.get('key'), c.get('key'))}: {c.get('value')}" for c in ch if isinstance(c, dict))
    if action == "start" and start_req:
        return f"[여행 시작] {start_req.get('region')} {start_req.get('start_date')}~{start_req.get('end_date')}"
    if action == "cost":
        return "[경비] 비용 항목 변경"
    return ""


def _place_rows(items: list) -> dict:
    """{장소 키: (이름, 일차)} — 이동·아침·주변 식사 자리표시는 빼고."""
    days = sorted({it["date"] for it in items})
    out = {}
    for it in items:
        if it["type"] == "move" or "조식" in (it.get("notes") or []) or it.get("source") == "식사 안내":
            continue
        key = it.get("place_id") or it["place_name"]
        out.setdefault(key, (it["place_name"], days.index(it["date"]) + 1))
    return out


def plan_diff(prev: list, new: list, spec=None) -> dict:
    """이전 초안 → 새 초안: 추가·빠짐·날짜 이동·이동시간 변화(분)."""
    a, b = _place_rows(prev), _place_rows(new)
    added = [{"place_id": k, "name": v[0], "day": v[1]} for k, v in b.items() if k not in a]
    removed = [{"place_id": k, "name": v[0], "day": v[1]} for k, v in a.items() if k not in b]
    moved = [{"place_id": k, "name": v[0], "from_day": a[k][1], "to_day": v[1]} for k, v in b.items() if k in a and a[k][1] != v[1]]
    tb, ta = sum(_travel_by_day(prev).values()), sum(_travel_by_day(new).values())
    parts = ([f"추가 {len(added)}곳"] if added else []) + ([f"빠짐 {len(removed)}곳"] if removed else []) \
        + ([f"날짜 이동 {len(moved)}곳"] if moved else []) + ([f"이동시간 {'+' if ta > tb else '−'}{abs(ta - tb)}분"] if ta != tb else [])
    big = len(added) + len(removed) + len(moved) >= 3
    return {"added": added, "removed": removed, "moved": moved, "travel_before_min": tb, "travel_after_min": ta,
            "summary": " · ".join(parts) or "장소는 그대로예요(시각만 다시 계산)", "big": big}


def _tag_plan(plan: dict, dif: dict) -> None:
    """일정 카드에 표시용 꼬리표: diff="new" / "moved"(from_day). 빠진 곳은 plan["diff"]에."""
    new_ids = {x["place_id"] for x in dif["added"]}
    mv = {x["place_id"]: x["from_day"] for x in dif["moved"]}
    for d in plan.get("itinerary") or []:
        for s in d.get("stops") or []:
            k = s.get("place_id") or s.get("name")
            if k in new_ids:
                s["diff"] = "new"
            elif k in mv:
                s["diff"], s["from_day"] = "moved", mv[k]
    plan["diff"] = dif


UNDO_KEYS = ("itinerary", "spec", "plan", "draft_version", "violations", "ranked", "allowed", "answer")


def _edit_meta(sid: str, st: dict) -> dict:
    """확정 후 수정 여부·되돌리기 개수(계획서 편집 모드 표시용)."""
    pid = st.get("plan_id")
    cv = store.confirmed_version(sid) if pid else None
    return {"plan_id": pid, "confirmed_version": cv,
            "needs_reconfirm": bool(pid and cv and st.get("draft_version") and cv != st.get("draft_version")),
            "undo_count": store.undo_count(sid)}


def undo(session_id: str) -> dict | None:
    """직전 편집 되돌리기: 편집 직전 상태를 그대로 복원(LLM 없음)."""
    snap = store.pop_snapshot(session_id)
    if not snap:
        return None
    vals = {k: snap.get(k) for k in UNDO_KEYS}
    vals["status"] = "승인 대기"
    if not vals.get("plan") and vals.get("itinerary") and vals.get("spec"):
        vals["plan"] = brochure.build(spec_from_dict(vals["spec"]), vals["itinerary"], vals.get("ranked") or [],
                                      vals.get("violations") or [], [])
    graph().update_state({"configurable": {"thread_id": session_id}}, vals, as_node="guard_out")
    out = load_trip(session_id) or {}
    out["changes"] = ["직전 수정을 되돌렸어요"]
    out["status"] = "승인 대기"
    return out


def load_trip(session_id: str) -> dict | None:
    """저장된 계획을 이어서 작업할 상태로 돌려준다(GET /trips/{session_id})."""
    st = graph().get_state({"configurable": {"thread_id": session_id}}).values
    if not st or not st.get("plan"):
        return None
    plan = st["plan"]
    conds = plan["trip"]["checklist"]
    confirmed = bool(st.get("plan_id")) and (st.get("status") == "확정" or (
        st.get("draft_version") and store.confirmed_version(session_id) == st.get("draft_version")))
    out = {"session_id": session_id, "status": "확정" if confirmed else "승인 대기",
           "stage": "refine", "answer": st.get("answer") or "", "plan": plan, "conditions": conds,
           "progress": brochure.progress(conds), "draft_version": st.get("draft_version"), "plan_id": st.get("plan_id")}
    out.update(_edit_meta(session_id, st))
    dif = store.get_diff(session_id, st.get("draft_version"))
    if dif:
        out["plan_diff"] = dif
        _tag_plan(out["plan"], dif)
    out["plan_no"] = store.plan_no(session_id)
    if st.get("plan_id"):
        pid = st["plan_id"]
        out.update(share_url=f"/plan/{pid}", ics_url=f"/plan/{pid}.ics", html_url=f"/plan/{pid}.html",
                   docx_url=f"/plan/{pid}.docx", print_url=f"/plan/{pid}?print=1", report_url=f"/plan/{pid}/report")
    return out
