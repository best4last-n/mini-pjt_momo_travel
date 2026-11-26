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

from . import data, guardrails, hours, planner, render, retrieve, store, tools
from .config import DEFAULT_DEPART, STATE_DB, today
from .llm import MOMO_PERSONA, chat, structured
from .schemas import DayPick, ParsedRequest, PickItem

SUPPORTED_LABEL = "경주, 순천/구례"


class State(TypedDict, total=False):
    # 이번 요청
    question: str
    action: str | None
    draft_version_in: str | None
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
        g = tools.geocode(n, data.region_label(region))
        if g:
            users.append(g)
            notes.append(f"'{n}'은(는) 관광공사 데이터에 없어 지도(OpenStreetMap)에서 찾은 '사용자 지정' 장소로 넣었어요. "
                         "운영시간은 확인 필요, 체류시간은 1시간으로 잡았어요(원하시면 바꿔 드릴게요)")
        else:
            alts = [x["metadata"]["title"] for x in retrieve.search(n, region, ["attraction"], k=3)]
            notes.append(f"'{n}'은(는) 찾을 수 없어요. 실제로 있는 곳만 일정에 넣었어요. "
                         f"비슷한 실제 장소로는 {', '.join(alts)} 어떠세요?")
    return ids, users, notes


# ---------------- 노드 ----------------

def guard_in(state: State) -> State:
    q = state.get("question") or ""
    out: State = {"trace": [], "contexts": [], "extra": {}, "theme": {}, "status": "", "answer": "", "parsed": {}}
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
    return {"parsed": p.model_dump(), "trace": _t(state, "parse_conditions", q[:300], p.model_dump(exclude_defaults=True))}


def route_parsed(state: State) -> str:
    return state["parsed"]["intent"]


def plan_node(state: State) -> State:
    p = ParsedRequest(**state["parsed"])
    q = state["question"]
    region, why = resolve_region(p)
    if why:
        return _region_reply(state, why, p)
    start, err, notes = resolve_date(p, q)
    if err:
        return {"status": "되묻기", "answer": f"앗, {err}. 혹시 어느 날짜를 말씀하신 걸까요? 날짜를 다시 알려 주시면 바로 짜 드릴게요!",
                "extra": {"chips": ["2월 28일", "3월 1일", "다른 날짜 입력"]},
                "trace": _t(state, "parse_conditions", "날짜 검증", err)}
    nights = p.nights
    if nights is None:
        nights = 1
        notes.append("여행 길이를 말씀하지 않아 1박 2일로 짰어요(기본값 적용)")
    elif nights > 1:
        notes.append(f"{nights}박 {nights + 1}일은 아직 지원하지 않아요. 저는 당일~1박 2일까지 꼼꼼히 검증할 수 있어서, "
                     "우선 1박 2일로 줄여 제안드려요. 나머지 날은 나눠서 한 번 더 계획해 보시는 걸 추천해요")
        nights = 1
    dep = data.norm_sido(p.departure)
    if p.departure and not dep:
        notes.append("출발지를 알아보지 못해 오가는 시간은 빼고 현지 09~20시 기준으로 짰어요")
    elif not dep:
        notes.append("출발지를 말씀하지 않아 오가는 시간은 빼고 현지 09~20시 기준으로 계산했어요")
    else:
        notes.append(f"출발지는 {dep} 기준으로 오가는 시간을 대략 추정했어요")
    transport = p.transport or "자차"
    if not p.transport:
        notes.append("이동수단은 자차로 계산했어요(기본값 적용)")
    elif transport == "대중교통":
        notes.append("대중교통 시간은 추정이에요(자차 기준 추정식 사용, 노선·배차는 지도 앱에서 확인해 주세요)")
    if p.send_request:
        notes.append("문자·메일 발송은 할 수 없어요. 확정 후 공유 링크와 캘린더 파일(.ics)로 전해 드릴게요")
    if p.past_trip_reference:
        notes.append("지난 여행 기록(LifeGalaxy add-on)이 꺼져 있어 지난 기록은 볼 수 없어요. 대신 새로 일정을 짜 드렸어요")
    if p.many_places:
        notes.append("하루 10곳 넘게는 이동만 하다 끝나서 무리예요. 이동이 짧은 곳 위주로 하루 최대 7곳까지 넣었어요")
    depart_time = hours.parse_hm(p.depart_time) if p.depart_time and re.fullmatch(r"\d{1,2}:\d{2}", p.depart_time) else DEFAULT_DEPART
    spec = planner.TripSpec(region=region, start=start, nights=nights, departure=dep, depart_time=depart_time,
                            transport=transport, companions=p.companions, preferences=p.preferences,
                            many_places=p.many_places)
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
              locked: dict | None = None) -> State:
    tr = list(state.get("trace") or [])
    token = tools.CONTEXTS.set([])
    try:
        w = tools.weather(spec.region, spec.days)
        tr.append({"step": "tool:get_weather", "input": f"{spec.region} {spec.start}", "output": w["text"]})
        res = planner.build_plan(spec, request, base=base, trace=tr, locked=locked)
        items = res["itinerary"]
        for it in items:
            p = data.places(spec.region).get(it.get("place_id") or "")
            if p:
                tools.add_context(p["contentid"], tools.place_doc_text(p))
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
    return {
        "status": "승인 대기", "answer": answer, "contexts": contexts, "trace": tr,
        "spec": spec_to_dict(spec), "itinerary": items, "violations": viol, "draft_version": version,
        "allowed": list(res["candidates"].keys()), "last_request": request,
        "theme": {"season": tools.season_of(spec.start), "scope": "domestic",
                  "weather": "rain" if w.get("rain") else ("snow" if spec.start.month in (12, 1, 2) else "clear")},
        "extra": {"draft_version": version, "violations": viol},
    }


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
    request = f"{state.get('last_request', '')}\n[수정 요청] {instr}"
    return _run_plan(state, spec, request, notes, base=base, locked=locked)


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
        pre += f"출발지는 {p.departure.split()[0] if p.departure else ''} 정도로만 기억해 둘게요. "
    if why == "missing":
        return {"status": "되묻기", "answer": pre + "좋아요, 어디로 날아가 볼까요? 지금은 경주와 순천/구례 일정을 꼼꼼히 짜 드릴 수 있어요. "
                "가고 싶은 지역과 함께, 당일치기인지 1박 2일인지, 어디서 출발하시는지도 같이 알려 주시면 바로 정리해 드릴게요!",
                "extra": {"chips": ["경주", "순천/구례", "당일치기", "1박 2일", "자차", "대중교통"]},
                "trace": _t(state, "ask_back", "지역 없음", "되묻기 1회(지역·길이·출발지·이동수단)")}
    if why == "overseas":
        return {"status": "정보", "answer": f"{p.region_text or '해외'} 여행이군요! 해외 일정(가벼운 모드)은 아직 준비 중이에요. "
                "해외는 운영시간을 제가 검증할 수 없어서, 현지에서 꼭 한 번 더 확인해 주셔야 해요. "
                f"지금은 국내 {SUPPORTED_LABEL} 일정을 꼼꼼히 짜 드릴 수 있어요.",
                "theme": {"scope": "overseas"},
                "trace": _t(state, "plan", "해외", "가벼운 모드 미구현 → 안내")}
    name = p.region_text or "그 지역"
    extra = " 수원은 데이터를 준비하고 있어서 곧 함께 안내해 드릴 수 있어요." if why == "unprepared" else ""
    return {"status": "정보", "answer": f"{name}은(는) 아직 제가 자세히 날아 본 곳이 아니라, 운영시간·휴무일까지 검증한 정밀 일정은 "
            f"준비된 지역({SUPPORTED_LABEL})만 가능해요.{extra} 대신 준비된 지역으로 짜 드리거나, "
            f"{name}은 관광공사 '대한민국 구석구석'에서 기본 정보를 먼저 살펴보시는 걸 추천해요.",
            "extra": {"chips": ["경주 1박 2일", "순천/구례 당일치기"]},
            "trace": _t(state, "plan", "지원 범위 밖 지역", why)}


INFO_SYSTEM = MOMO_PERSONA + f"""
정보 질문(운영시간·휴무일·축제·날씨·장소 추천·예약 문의)에 도구로 확인한 사실만 답한다. 오늘은 {{today}}.
- 지원 지역: {SUPPORTED_LABEL} (region 값: gyeongju, suncheon_gurye). 그 밖은 준비 안 된 지역이라고 안내.
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
        """지역 안의 관광지·음식점·숙박 장소를 검색한다. region은 gyeongju 또는 suncheon_gurye."""
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
                         system_prompt=INFO_SYSTEM.replace("{today}", _today_label()))
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
            "일정을 한 번 더 살펴보시고 괜찮으시면 화면의 '확정할게요' 버튼을 눌러 주세요. 그러면 공유 링크와 캘린더 파일(.ics)을 만들어 드릴게요!",
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
    payload = {"title": f"{data.region_label(spec['region'])} {'당일치기' if spec['nights'] == 0 else '1박 2일'} · 모모 여행 일정",
               "region": spec["region"], "region_label": data.region_label(spec["region"]),
               "departure_sido": spec.get("departure"), "nights": spec["nights"], "start": spec["start"],
               "itinerary": items, "draft_version": state["draft_version"]}
    pid = store.save_plan(sid, state["draft_version"], payload)
    has_check = any(it["hours_status"] == "확인 필요" for it in items)
    ans = ("확정했어요! 엽서를 꼭꼭 눌러 담아 두었어요. 아래 공유 링크로 가족과 함께 보시고, 캘린더 파일(.ics)로 일정에 넣어 두세요."
           + (" '운영시간 확인 필요' 표시된 곳은 방문 전에 한 번 더 확인해 주세요." if has_check else ""))
    return {"status": "확정", "answer": ans, "plan_id": pid,
            "extra": {"plan_id": pid, "share_url": f"/plan/{pid}", "ics_url": f"/plan/{pid}.ics",
                      "draft_version": state["draft_version"]},
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
    g.add_node("guard_out", guard_out)
    g.add_edge(START, "guard_in")
    g.add_conditional_edges("guard_in", lambda s: "guard_out" if s.get("status") == "거절"
                            else ("approve" if s.get("action") == "approve" else "parse"))
    g.add_conditional_edges("parse", route_parsed, {"plan": "plan", "modify": "modify", "info": "info",
                                                    "approve_chat": "approve_chat", "reject": "reject"})
    for n in ("plan", "modify", "info", "approve_chat", "reject", "approve"):
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
                 draft_version: str | None = None) -> dict:
    """POST /query 본체. 질문은 그래프에 들어가기 전에 마스킹(LangSmith 업로드 전 포함)."""
    new_session = not session_id
    sid = session_id or uuid.uuid4().hex
    masked, found = guardrails.mask_pii(question or "")
    cfg = {"configurable": {"thread_id": sid}, "run_name": "momo_query"}
    if action == "approve" and not graph().get_state(cfg).values.get("itinerary"):
        pass  # approve_node가 '초안 없음'으로 응답
    out = graph().invoke({"question": masked, "action": action, "draft_version_in": draft_version,
                          "session_id": sid}, cfg)
    answer = out.get("answer", "")
    if found:
        answer = "개인정보(" + ", ".join(found) + ")는 가려 두었어요. 출발지는 시·구 단위로만 쓸게요.\n\n" + answer
    resp = {"answer": answer, "contexts": out.get("contexts") or [], "trace": out.get("trace") or [],
            "status": out.get("status"), "session_id": sid, "theme": out.get("theme") or {},
            "itinerary": out.get("itinerary") if out.get("status") in ("승인 대기", "확정") else []}
    resp.update(out.get("extra") or {})
    if new_session:
        resp["trace"].insert(0, {"step": "session", "input": "", "output": "새 세션 시작"})
    return resp
