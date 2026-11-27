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

from src.planning import brochure, hours, planner
from src.budget import costs
from src.shared import data, eta
from src.guardrails import guardrails
from src.counsel import render
from src.places import retrieve, tools
from src.memory import store
from src.shared.config import DEFAULT_DEPART, STATE_DB, today
from src.shared.travel import km as travel_km
from src.shared.llm import chat, structured
from src.counsel.prompts import INFO_SYSTEM, PARSE_SYSTEM
from src.counsel.options import MAX_NIGHTS
from src.planning.schemas import DayPick, PickItem
from src.counsel.schemas import ParsedRequest

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
    force_approve: bool | None  # 규칙 검사 결과를 확인하고 그대로 확정(요청마다 새로 넘김)
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
    eta.mark(step)          # 진행 화면의 '지금 단계'·남은 시간
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


MAX_WAYPOINTS = 200        # 100곳 넘게 적어도 받는다(일정에 들어갈 만큼만 넣고 나머지는 후보 — planner.must_ids)


def _local_title(n: str, region: str) -> dict | None:
    """색인 뒤에 수집된 장소(extra_places)까지 제목으로 맞춰 보기 — '사성암' ↔ '사성암(구례)'."""
    from src.places.placesearch import name_key
    k = name_key(n)
    if len(k) < 2:
        return None
    for pid, p in data.places(region).items():
        t = re.sub(r"\(.*?\)|\[.*?\]", "", p.get("title") or "")
        if name_key(t) == k:
            return {"doc_id": pid}
    return None


def _resolve_many(names: list[str], region: str, choices: list | None) -> tuple[list[str], list[dict], list[str]]:
    """꼭 갈 곳 여러 곳을 동시에(12개씩) 찾는다 — 곳당 1~3초라 100곳을 하나씩 찾으면 수 분. 결과 순서는 적은 순서 그대로.
    안내가 장소마다 한 줄씩 100줄이 되지 않게, 10곳 넘으면 '찾음 N · 직접 지정 N · 지점 고르기 N · 못 찾음 N'으로 묶는다."""
    import contextvars
    from concurrent.futures import ThreadPoolExecutor
    from src.shared.cancel import check as _cancel_check
    _cancel_check()

    def one(n):
        ch: list | None = [] if choices is not None else None
        r = resolve_waypoints([n], region, ch)
        return r, ch
    with ThreadPoolExecutor(max_workers=12) as ex:     # 중지 신호(contextvar)가 각 작업에도 전달되게 문맥 복사
        futs = [ex.submit(contextvars.copy_context().run, one, n) for n in names]
        results = [f.result() for f in futs]
    ids, users, notes = [], [], []
    found = picked = 0
    missing, branchy = [], []
    for n, ((i, u, nt), ch) in zip(names, results):
        ids += [x for x in i if x not in ids]
        users += [x for x in u if x["place_id"] not in {y["place_id"] for y in users}]
        found += len(i)
        picked += len(u)
        if ch:
            choices.extend(ch)
            branchy.append(n)
        elif not i and not u:
            missing.append(n)
        if len(names) <= 10:
            notes += nt
    if len(names) > 10:
        notes.append(f"꼭 갈 곳 {len(names)}곳 확인: 관광공사 자료 {found}곳 · 지도에서 찾아 직접 지정 {picked}곳"
                     + (f" · 지점 고르기 {len(branchy)}곳({', '.join(branchy[:5])}{' 외' if len(branchy) > 5 else ''})" if branchy else "")
                     + (f" · 못 찾음 {len(missing)}곳({', '.join(missing[:8])}{' 외' if len(missing) > 8 else ''}) — 정확한 이름이나 '직접 추가'로 넣어 주세요"
                        if missing else ""))
    return ids, users, notes



def clean_waypoints(raw) -> tuple[list[str], list[str]]:
    """시작 카드 '꼭 가고 싶은 곳' 정리(잘림 오류 2026-11-26): 쉼표·줄바꿈으로 나누고 '꼭 갈 곳' 머리말 제거,
    개인정보 가리기, 한 곳 60자, 중복 제거. MAX_WAYPOINTS를 넘는 곳은 버리지 않고 (받은 목록, 못 받은 목록)으로 돌려줘 안내한다."""
    items = raw if isinstance(raw, list) else [raw] if raw else []
    out: list[str] = []
    for it in items:
        for w in re.split(r"[,，、\n;]+", str(it or "")):
            w = re.sub(r"^\s*꼭\s*갈\s*곳\s*", "", w).strip()
            if w:
                w = guardrails.mask_pii(w[:60])[0]
                if w not in out:
                    out.append(w)
    return out[:MAX_WAYPOINTS], out[MAX_WAYPOINTS:]


def resolve_waypoints(names: list[str], region: str, choices: list | None = None) -> tuple[list[str], list[dict], list[str]]:
    """사용자가 이름으로 말한 장소 찾기. choices를 넘기면 지점이 여러 곳인 상호(프랜차이즈·정지영처럼)는 넣지 않고
    지점 선택지를 choices에 담는다(관광지·이름이 하나뿐인 곳은 그대로)."""
    if len(names) > 1:          # 여러 곳(100곳 넘게도)은 동시에 찾고, 안내는 묶어서
        return _resolve_many(names, region, choices)
    from src.shared.cancel import check as _cancel_check
    ids, users, notes = [], [], []
    for n in names:
        _cancel_check()          # 장소가 많으면 찾기가 길어져서, 한 곳마다 중지 확인
        d = retrieve.find_by_name(n, region) or _local_title(n, region)
        if choices is not None and (not d or (data.places(region).get(d["doc_id"]) or {}).get("kind") == "restaurant"):
            from src.places import placesearch
            br = placesearch.branches(n, region)
            if br:
                choices.append({"query": n, "question": f"{_josa(chr(39) + n + chr(39), '은는')} 근처에 지점이 {len(br)}곳 있어요. 어느 지점으로 갈까요?",
                                "options": [{**{k: v for k, v in o.items() if k != "candidate"},
                                             "edit": {"op": "add", "candidate": o["candidate"]}} for o in br]})
                notes.append(f"{_josa(chr(39) + n + chr(39), '은는')} 근처에 지점이 {len(br)}곳 있어요 — 어느 지점으로 갈지 골라 주시면 일정에 넣을게요")
                continue
        if d:
            ids.append(d["doc_id"])
            continue
        extra = next((p for p in data.places(region).values() if p.get("added_by_user")
                      and (retrieve.re.sub(r"\s+", "", n) in retrieve.re.sub(r"\s+|\(.*?\)", "", p["title"]))), None)
        if extra:
            ids.append(extra["contentid"])
            continue
        try:
            from src.places import tourlive
            t = tourlive.lookup(n, region)
        except Exception:
            t = None
        if t:   # 관광공사에 있는 정식 장소로 편입(운영시간·사진·요약 포함)
            ids.append(t["contentid"])
            notes.append(f"{_josa(chr(39) + t['title'] + chr(39), '을를')} 한국관광공사 자료에서 찾아 일정에 넣었어요")
            continue
        # 관광공사에 없으면: 브이월드(국가 POI, 개인 카페·식당 포함) 우선 → 카카오·네이버(키 있을 때) → OpenStreetMap 마지막
        from src.places import placesearch
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
                from src.places import tourlive
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


def _with_choices(out: State, choices: list) -> State:
    """지점 선택지(place_choices)를 응답에 싣는다 — 화면은 버튼으로, 고르면 edit op add(candidate)."""
    if choices:
        out = dict(out)
        out["extra"] = {**(out.get("extra") or {}), "place_choices": choices}
    return out


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
    sys = PARSE_SYSTEM.format(today=_today_label(), draft="있다" if has_draft else "없다")
    p: ParsedRequest = structured(ParsedRequest).invoke([("system", sys), ("human", f"<data>{q}</data>")])
    if p.intent == "modify" and not has_draft:
        p.intent = "plan"
    p.departure = _dep_with_gu(p.departure, q)
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
        from src.places import geo
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
    if data.requires_flight(region) and long_t != "비행기":    # 섬 지역은 육로·열차로 갈 수 없음
        notes.append(f"{data.region_label(region)}은(는) 비행기(또는 배)로 가야 해서 장거리 이동을 비행기로 계산했어요")
        long_t = "비행기"
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
    choices: list = []
    if old and old.get("region") == region:
        notes += _carry_user_edits(old, spec)       # 채팅으로 다시 짜도 화면에서 넣은 곳·설정은 그대로(2026-11-26)
    if p.waypoints:
        ids, users, wnotes = resolve_waypoints(p.waypoints, region, choices)
        spec.waypoint_ids = list(dict.fromkeys([*spec.waypoint_ids, *ids]))
        have = {w["place_id"] for w in spec.user_waypoints}
        spec.user_waypoints = [*spec.user_waypoints, *[u for u in users if u["place_id"] not in have]]
        notes += wnotes
    # 당일치기 현지 시간 경고 (3-4e)
    if nights == 0 and dep:
        w = planner.windows(spec)[0]
        if w[1] - w[0] < 60:
            return {"status": "정보", "answer": f"{dep}에서 {data.region_label(region)}까지 오가는 시간이 길어서 당일치기로는 현지에 "
                    "머물 시간이 1시간도 안 돼요. 더 이른 출발이나 1박 어때요?", "trace": _t(state, "plan", "시간 창", f"{w}")}
        if w[1] - w[0] < 180:
            notes.append("당일치기로는 빠듯해요. 1박 어때요?")
    return _with_choices(_run_plan(state, spec, q, notes, base=None), choices)


_CARRY_KEYS = ("waypoint_ids", "user_waypoints", "exclude_ids", "kind_override", "booked_stay", "title", "depart_stops",
               "return_stops", "long_override", "no_stay", "breakfast", "breakfast_skip_days", "custom_costs",
               "pack_checked", "pack_custom", "vehicle", "end_coords", "end_location", "end_label", "arrive_time")


def _carry_user_edits(old: dict, spec: planner.TripSpec) -> list[str]:
    """채팅으로 일정을 새로 짤 때, 화면에서 사용자가 직접 고른 것(추가한 장소·분류·예약 숙소·들를 곳·제목 등)을 새 spec에 이어 붙인다.
    같은 지역일 때만 호출. 출발지 등 새 요청이 말하지 않은 값도 이전 것을 쓴다."""
    for k in _CARRY_KEYS:
        v = old.get(k)
        if v not in (None, [], {}, "") and getattr(spec, k) in (None, [], {}, ""):
            setattr(spec, k, v)
    if not spec.departure and old.get("departure"):
        for k in ("departure", "origin_label", "origin_coords"):
            setattr(spec, k, old.get(k))
    if int(old.get("nights") or 0) == spec.nights and old.get("start") == spec.start.isoformat():
        spec.fixed_starts = dict(old.get("fixed_starts") or {})
    n = len(spec.waypoint_ids) + len(spec.user_waypoints)
    return [f"직접 넣으신 장소 {n}곳을 그대로 유지했어요"] if n else []


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
    over = planner.waypoint_overflow(spec)
    if over:
        nm = [(data.places(spec.region).get(x) or {}).get("title")
              or next((w["title"] for w in spec.user_waypoints if w["place_id"] == x), x) for x in over]
        notes.append(f"꼭 갈 곳이 많아 {spec.nights}박 {spec.nights + 1}일에 다 들어가지 않아요 — 적어 주신 순서대로 "
                     f"{len(planner.must_ids(spec))}곳을 넣고 {len(over)}곳({', '.join(nm[:6])}{' 외' if len(nm) > 6 else ''})은 후보로 남겼어요. "
                     "여행 강도를 '강행군'으로 바꾸거나 기간을 늘리면 더 넣을 수 있어요")
    gone = [x for x in planner.must_ids(spec) if x not in {it.get("place_id") for it in items}]
    if gone:        # 직접 넣은 곳이 빠졌다면 조용히 사라지지 않게 알린다
        nm = [(data.places(spec.region).get(x) or {}).get("title")
              or next((w["title"] for w in spec.user_waypoints if w["place_id"] == x), x) for x in gone]
        notes.append(f"직접 넣으신 {', '.join(nm[:5])}{' 외' if len(nm) > 5 else ''}은(는) 시간 창에 맞지 않아 이번 일정에서 빠졌어요 — 화면에서 다시 넣을 수 있어요")
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
    choices: list = []
    if p.waypoints:
        ids, users, wnotes = resolve_waypoints(p.waypoints, spec.region, choices)
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
    return _with_choices(_run_plan(state, spec, request, notes, base=base, locked=locked, prefix=prefix, indoor=indoor), choices)


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


def _fill_meals(spec: planner.TripSpec, days: list[list], log: list | None = None) -> list[list]:
    """식사 보완: 빈 식사 자리표시자 → 점심을 정오 자리로 → 연달아 겹친 식사 정리(사용자가 넣은 곳은 유지).
    정리로 빠진 자리가 생기면 한 번 더 채운다."""
    mw = planner.meal_windows(spec)

    def add_placeholders(days):
        for di, row in enumerate(days):
            for role, win in (("lunch", planner.LUNCH), ("dinner", planner.DINNER)):
                if (planner.meal_required(mw[di], win) and not any(x.role == role for x in row)
                        and not planner.route_meal_covers(spec, di, win)):        # 가는 길·오는 길 식당이 그 끼니를 대신
                    ph = PickItem(place_id=planner.MEAL_ID, role=role, stay_minutes=60, reason="")
                    if role == "lunch":
                        idx = min(len([x for x in row if x.role != "stay"]), max(1, len(row) // 2))
                    else:
                        idx = next((i for i, x in enumerate(row) if x.role == "stay"), len(row))
                    row.insert(idx, ph)
        return days

    for _ in range(2):
        days = planner.dedupe_meals(spec, planner.place_lunch(spec, add_placeholders(days)), log)
    return days


def route_whole(spec: planner.TripSpec, days: list[list]) -> tuple[list[list], list[dict]]:
    """전체 일정 동선 최적화: 날짜를 넘나들며 관광지를 옮기거나 맞바꿔 총 이동거리를 줄인다.
    식사·숙소는 그 날 그 자리에 고정, 그 날 휴무인 곳으로는 옮기지 않음, 하루 관광지 수는 원래 ±1곳(강도 상한 안, 빈 날 없음).
    한 곳 옮기기 / 두 곳 맞바꾸기를 개선이 없을 때까지 반복(최대 4바퀴). → (새 일정, 옮긴 목록)"""
    from src.shared.travel import km as _km
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
    from src.shared.travel import km as _km
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


def _place_info(spec: planner.TripSpec, pid: str) -> dict:
    return data.places(spec.region).get(planner.visit_base(pid) or pid) or next(
        (w for w in spec.user_waypoints if w["place_id"] == pid), {})


_KIND_TO_STOP = {"식사": "식당", "카페": "카페", "관광지": "관광지"}


def _put_route_stop(spec: planner.TripSpec, leg: str, stop: dict, index: int | None = None) -> str:
    """들를 곳을 가는 길(depart_stops)/오는 길(return_stops)에 넣는다. index가 있으면 그 자리(0=맨 앞), 없으면 출발지에서 가까운 순.
    들를 곳이 처음 생기면 '출발지 → 여행지' 한 구간이던 직접 정한 운전 시간은 구간이 나뉘므로 해제하고 그 사실을 문장으로 돌려준다."""
    lst_name = "return_stops" if leg == "back" else "depart_stops"
    old = list(getattr(spec, lst_name) or [])
    cur = [x for x in old if x.get("key") != stop["key"]]
    km = lambda x: x.get("km_from_start") or 0
    if index is None:       # 길 위 진행 순서(출발지에서 가까운 순). 사용자가 순서를 정한(user_order) 목록은 그 순서를 지키고 새 곳만 알맞은 자리에
        if any(x.get("user_order") for x in cur):
            at = max((i + 1 for i, x in enumerate(cur) if km(x) <= km(stop)), default=0)      # 더 가까운 곳들 바로 뒤
            cur.insert(at, stop)
        else:
            cur.append(stop)
            cur.sort(key=km)
    else:
        cur.insert(max(0, min(int(index), len(cur))), stop)
        cur = [{**x, "user_order": True} for x in cur]
    setattr(spec, lst_name, cur)
    note = ""
    k = "back" if leg == "back" else "out"
    lo = dict(spec.long_override or {})
    if not old and lo.get(k):
        mins = lo.pop(k)
        spec.long_override = lo
        note = (f" 직접 정하신 {'오는' if k == 'back' else '가는'} 길 운전 시간 {mins}분은 이동이 구간으로 나뉘어 해제했어요"
                " (이제 각 구간을 앞 지점에서 새로 계산해요. 마지막 긴 구간의 '✎ 이동 시간'으로 다시 정할 수 있어요).")
    return note


def _divert_to_route(spec: planner.TripSpec, pid: str, leg: str, index: int | None = None, kind: str | None = None) -> str | None:
    """여행지에서 먼 장소를 가는 길/오는 길 '들를 곳'(spec.depart_stops/return_stops)으로 옮기고 안내 문장을 돌려준다.
    5곳이 가득 차 있으면 None(호출한 쪽이 현지 일정 쪽 안내를 그대로 유지)."""
    p = _place_info(spec, pid)
    lst_name = "return_stops" if leg == "back" else "depart_stops"
    cur = list(getattr(spec, lst_name) or [])
    stop = planner.route_stop_from(spec, leg, {"name": p.get("title"), "lat": p.get("lat"), "lng": p.get("lng"), "kind": p.get("kind"),
                                               "key": pid, "place_id": pid if pid in data.places(spec.region) else None,
                                               "address": p.get("addr") or p.get("address") or "", "source": p.get("source")})
    if not stop or (len(cur) >= 5 and not any(s.get("key") == stop["key"] for s in cur)):
        return None
    stop["name"] = guardrails.mask_pii(stop["name"])[0]
    ko = kind or (spec.kind_override or {}).get(pid)
    if ko in _KIND_TO_STOP:        # 사용자가 정한 분류(식사/카페)를 들를 곳 종류에도
        stop["kind"] = _KIND_TO_STOP[ko]
        stop["stay_min"] = {"식당": 50, "카페": 30}.get(stop["kind"], 45)
        if kind:
            spec.kind_override = {**(spec.kind_override or {}), pid: ko}
    note = _put_route_stop(spec, leg, stop, index)
    o = planner.origin_point(spec) or {}
    near_home = o.get("lat") is not None and travel_km((o["lat"], o["lng"]), (p["lat"], p["lng"])) <= planner.ROUTE_NEAR_KM
    addr = str(p.get("addr") or p.get("address") or "").split()
    where = (o.get("name") if near_home else (addr[1] if len(addr) > 1 else "")) or "그곳"
    way = "오는 길" if leg == "back" else "가는 길"
    meal = " 이 시간대 끼니를 이곳에서 먹어요." if stop["kind"] == "식당" else ""
    return (f"{where}의 {_josa(stop['name'], '은는')} 여행지에서 멀어 '{way} 들를 곳'으로 넣었어요 "
            f"(앞 지점에서 이곳까지, 이곳에서 다음 지점까지 운전 시간을 따로 계산하고 머무는 {stop['stay_min']}분을 더해요).{meal}{note}")


def _divert_far(spec: planner.TripSpec, days: list[list], log: list[str]) -> None:
    """이미 일정에 들어 있는 먼 장소(예: 출발지 근처 식당)를 가는 길/오는 길 들를 곳으로 바로잡는다(기존 세션 보정)."""
    for di, row in enumerate(days):
        for pk in list(row):
            if pk.role == "stay" or pk.place_id == planner.MEAL_ID or pk.place_id.startswith((planner.FREE_PREFIX, planner.VISIT_PREFIX)):
                continue
            p = _place_info(spec, pk.place_id)
            if not p.get("lat") or p.get("kind") == "lodging":
                continue
            leg = planner.route_leg_for(spec, p["lat"], p["lng"], di)
            if leg in ("out", "back"):
                msg = _divert_to_route(spec, pk.place_id, leg)
                if msg:
                    row.remove(pk)
                    spec.waypoint_ids = [w for w in spec.waypoint_ids if w != pk.place_id]
                    log.append(f"{di + 1}일차: " + msg)


def _retag_meal_roles(days: list[list], items: list[dict]) -> bool:
    """고른 식당의 점심/저녁 역할이 실제 배치 시각(16:00 기준)과 다르면 바로잡는다. 바뀐 게 있으면 True."""
    changed = False
    for di, row in enumerate(days):
        ds = {it["place_id"]: it for it in items if it.get("place_id") and it["type"] == "meal"
              and (it["date"] == items_dates(items)[di] if di < len(items_dates(items)) else False)}
        for k, pk in enumerate(row):
            it = ds.get(pk.place_id)
            if it and pk.role in ("lunch", "dinner") and pk.place_id != planner.MEAL_ID:
                want = _role(it)
                if want in ("lunch", "dinner") and want != pk.role:
                    row[k] = pk.model_copy(update={"role": want})
                    changed = True
    return changed


def items_dates(items: list[dict]) -> list[str]:
    return sorted({it["date"] for it in items})


def _reschedule(state: State, spec: planner.TripSpec, days: list[list], changes: list[str], step: str,
                repair: bool = False) -> State:
    """화면 편집·조건 변경을 LLM 없이 반영: 배치 → 식사 보완 → 규칙 검사(→ 필요 시 보정) → plan 재구성."""
    allowed = set(state.get("allowed") or []) | {x.place_id for row in days for x in row} | {planner.MEAL_ID}
    dropped: list[str] = []
    days = _fill_meals(spec, days, dropped)
    changes[1:1] = [x for x in dict.fromkeys(dropped) if x not in changes]     # 첫 줄(요청한 변경) 바로 뒤 — 3줄 안에 보이게
    pick = planner.PlanPick(days=[DayPick(day=i + 1, items=row) for i, row in enumerate(days)])
    items = planner.schedule(spec, pick, {})
    if _retag_meal_roles(days, items):      # 앞 일정이 빠져(예: 먼 식당이 가는 길로) 시각이 당겨진 식당은 점심↔저녁 역할을 시각대로 다시
        days = _fill_meals(spec, [[x for x in row if x.place_id != planner.MEAL_ID] for row in days], dropped)
        pick = planner.PlanPick(days=[DayPick(day=i + 1, items=row) for i, row in enumerate(days)])
        items = planner.schedule(spec, pick, {})
    viol = planner.validate(spec, items, allowed)
    if repair and viol:
        was = {it.get("place_id"): it["place_name"] for it in items if it["type"] != "move" and it.get("place_id")}
        items = planner.repair(spec, items, viol, allowed, {pid: data.places(spec.region)[pid]
                                                            for pid in allowed if pid in data.places(spec.region)})
        viol = planner.validate(spec, items, allowed)
        left = {it.get("place_id") for it in items}
        cut = [n for pid_, n in was.items() if pid_ not in left]
        if cut:        # 규칙 보정으로 빠진 곳은 조용히 사라지지 않게 알린다
            changes.insert(min(1, len(changes)), f"규칙(운영시간·시간 창)에 맞추려고 {', '.join(cut[:3])}{' 외 ' + str(len(cut) - 3) + '곳' if len(cut) > 3 else ''}을(를) 뺐어요")
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

    def title_of(x) -> str:
        """수집 장소·직접 고른 곳(user:…) 모두 이름으로 — id가 문장에 그대로 나오지 않게."""
        if not x:
            return ""
        return ((data.places(spec.region).get(x) or {}).get("title")
                or next((w.get("title") for w in spec.user_waypoints if w.get("place_id") == x), None)
                or ("이 장소" if str(x).startswith(("user:", "osm:", "vw:")) else str(x)))

    name = title_of(pid)
    nd = len(days)
    changes: list[str] = []
    if op not in ("route_stop", "order"):       # 먼 장소가 현지 일정에 섞여 있으면 먼저 길 위 들를 곳으로(예: 판교 버거킹 → 1일차 340분 '이동')
        _divert_far(spec, days, changes)        # (order는 사용자가 정한 순서를 반영한 뒤에 같은 보정)

    def locate(x, day=None):
        """장소가 들어 있는 (날, 위치). 같은 숙소가 여러 밤에 있으면 day(1부터)로 그날을 먼저 찾는다."""
        try:
            dd = int(day) - 1 if day else None
        except (TypeError, ValueError):
            dd = None
        if dd is not None and 0 <= dd < len(days):
            for i, pk in enumerate(days[dd]):
                if pk.place_id == x:
                    return dd, i
        for di, row in enumerate(days):
            for i, pk in enumerate(row):
                if pk.place_id == x:
                    return di, i
        return None, None

    def insert(di, pk):
        row = days[di]
        idx = next((i for i, x in enumerate(row) if x.role in ("dinner", "stay")), len(row))
        row.insert(idx, pk)

    def find_rs(x):
        """가는 길/오는 길 들를 곳(key = 장소 id)으로 옮겨진 장소: (목록 이름, 'out'|'back', 위치)."""
        for ln, lg_ in (("depart_stops", "out"), ("return_stops", "back")):
            ix_ = next((i_ for i_, s_ in enumerate(getattr(spec, ln) or []) if s_.get("key") == x), None)
            if ix_ is not None:
                return ln, lg_, ix_
        return None

    rs = find_rs(pid) if pid and op in ("remove", "move", "reorder", "time", "stay", "kind") and locate(pid)[0] is None else None
    if rs:                       # 먼 장소가 들를 곳이 된 뒤에도 같은 편집(분류·시각·머무는 시간·빼기·옮기기)이 먹게
        ln, lg_, ix = rs
        cur_ = list(getattr(spec, ln))
        st_ = dict(cur_[ix])
        way_ = "오는 길" if lg_ == "back" else "가는 길"
        day_no = nd if lg_ == "back" else 1
        if op == "remove":
            setattr(spec, ln, [x for k_, x in enumerate(cur_) if k_ != ix])
            spec.waypoint_ids = [w for w in spec.waypoint_ids if w != pid]
            changes.append(f"{way_} 들를 곳에서 {_josa(st_['name'], '을를')} 뺐어요")
        elif op == "stay":
            try:
                mins = max(10, min(360, int(e.get("minutes"))))
            except (TypeError, ValueError):
                return {"status": "승인 대기", "answer": "머무는 시간을 분 단위 숫자로 알려 주세요.", "trace": _t(state, "edit", e, "형식")}
            cur_[ix] = {**st_, "stay_min": mins}
            setattr(spec, ln, cur_)
            changes.append(f"{way_} {st_['name']} 머무는 시간을 {mins}분으로 바꿨어요(뒤 이동 시각은 다시 계산했어요)")
        elif op == "time":
            key = f"{day_no}:{pid}"
            fs = dict(spec.fixed_starts or {})
            tv = e.get("time")
            if tv in (None, "", "auto"):
                fs.pop(key, None)
                changes.append(f"{way_} {st_['name']} 도착 시각을 자동으로 — 앞 지점에서 운전해 닿는 시각이에요")
            else:
                m_ = re.match(r"^\s*(\d{1,2}):(\d{2})\s*$", str(tv))
                if not m_ or not (5 * 60 <= int(m_.group(1)) * 60 + int(m_.group(2)) <= 26 * 60):
                    return {"status": "승인 대기", "answer": "시작 시각은 05:00~익일 02:00 사이 'HH:MM'으로 알려 주세요.", "trace": _t(state, "edit", e, "형식")}
                fs[key] = int(m_.group(1)) * 60 + int(m_.group(2))
                changes.append(f"{way_} {st_['name']} 도착을 {tv}(으)로 정했어요(운전해 닿는 시각이 더 늦으면 그 시각, 뒤 이동은 이어서 계산)")
            spec.fixed_starts = fs
            if e.get("minutes") not in (None, ""):
                try:
                    cur_[ix] = {**st_, "stay_min": max(10, min(360, int(e.get("minutes"))))}
                    setattr(spec, ln, cur_)
                except (TypeError, ValueError):
                    pass
        elif op == "kind":
            k = str(e.get("kind") or "")
            ko = dict(spec.kind_override or {})
            if k in _KIND_TO_STOP:
                ko[pid] = k
                cur_[ix] = {**st_, "kind": _KIND_TO_STOP[k]}
                changes.append(f"{_josa(st_['name'], '을를')} '{k}'(으)로 분류했어요" + (" — 가는 길/오는 길 끼니로 세요" if k == "식사" else ""))
            else:
                ko.pop(pid, None)
                p_ = _place_info(spec, pid)
                auto = planner.route_stop_from(spec, lg_, {"name": st_["name"], "lat": st_["lat"], "lng": st_["lng"], "kind": p_.get("kind"),
                                                           "key": pid}) or {}
                cur_[ix] = {**st_, "kind": auto.get("kind") or st_.get("kind")}
                changes.append(f"{st_['name']}의 분류를 자동으로 되돌렸어요")
            spec.kind_override = ko
            setattr(spec, ln, cur_)
        else:                    # move(다른 날로) / reorder(들르는 순서 앞뒤)
            if op == "reorder":
                j_ = max(0, min(len(cur_) - 1, ix + int(e.get("delta") or 0)))
                cur_.insert(j_, cur_.pop(ix))
                setattr(spec, ln, [{**x, "user_order": True} for x in cur_])
                changes.append(f"{way_} 들르는 순서를 바꿨어요: " + " → ".join(x["name"] for x in cur_))
            else:
                to = int(e.get("day") or (nd if lg_ == "out" else 1))
                dest = "out" if to == 1 else "back" if to == nd else None
                if dest is None:
                    return {"status": "승인 대기", "answer": "가는 길 들를 곳은 첫날(출발 때), 오는 길 들를 곳은 마지막 날(귀가 때)에만 둘 수 있어요.",
                            "trace": _t(state, "edit", e, "날짜")}
                if dest != lg_:
                    setattr(spec, ln, [x for k_, x in enumerate(cur_) if k_ != ix])
                    p_ = _place_info(spec, pid)
                    ns = planner.route_stop_from(spec, dest, {"name": st_["name"], "lat": st_["lat"], "lng": st_["lng"], "kind": p_.get("kind"),
                                                              "key": pid}) or st_
                    ns = {**ns, "stay_min": st_.get("stay_min", ns.get("stay_min")), "kind": st_.get("kind", ns.get("kind"))}
                    _put_route_stop(spec, dest, ns)
                    changes.append(f"{_josa(st_['name'], '을를')} {'오는' if dest == 'back' else '가는'} 길 들를 곳으로 옮겼어요")
                else:
                    changes.append(f"{st_['name']}은(는) 이미 {way_} 들를 곳이에요")
    elif op == "keep_over":       # 강도 권장보다 많아도 그대로(그날은 다시 묻지 않음)
        dn = int(e.get("day") or 0)
        spec.over_ok_days = sorted(set(spec.over_ok_days or []) | {dn})
        changes.append(f"{dn}일차는 지금처럼 {e.get('count') or ''}곳 그대로 둘게요".replace("  ", " "))
    elif op == "remove":
        di, i = locate(pid)
        if di is None:
            return {"status": "승인 대기", "answer": "그 장소는 일정에 없어요.", "trace": _t(state, "edit", e, "없음")}
        days[di].pop(i)
        spec.user_ordered_days = sorted(set(spec.user_ordered_days) | {di + 1})
        spec.waypoint_ids = [w for w in spec.waypoint_ids if w != pid]       # 다시 짜도 되살리지 않음
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
    elif op == "lodge_visit":    # {day, after?|before?} 그날(또는 전날 밤) 숙소에 낮에 다시 들르기 — 여러 번 가능
        try:
            to = max(1, min(nd, int(e.get("day") or 1))) - 1
        except (TypeError, ValueError):
            to = 0
        base = next((x.place_id for k in (to, to - 1) if 0 <= k < nd for x in days[k] if x.role == "stay"), None) or spec.booked_stay
        if not base:
            return {"status": "승인 대기", "answer": "들를 숙소가 아직 없어요. 먼저 숙소를 넣어 주세요.",
                    "trace": _t(state, "edit", e, "숙소 없음")}
        vid = f"{planner.VISIT_PREFIX}{base}:{uuid.uuid4().hex[:4]}"
        pk = PickItem(place_id=vid, role="place", stay_minutes=max(15, min(int(e.get("minutes") or 60), 360)),
                      reason="숙소에 들러 쉬었다가 다시 나가요")
        row = days[to]
        ai = next((i for i, x in enumerate(row) if x.place_id == (e.get("after") or e.get("before"))), None)
        if ai is not None:
            row.insert(ai if e.get("before") and not e.get("after") else ai + 1, pk)
        else:
            insert(to, pk)
        spec.user_ordered_days = sorted(set(spec.user_ordered_days) | {to + 1})
        changes.append(f"{to + 1}일차에 {_josa(title_of(base), '을를')} 한 번 더 들르게 했어요(머무는 시간은 바꿀 수 있어요)")
    elif op == "kind":           # {place_id, kind: 식사|카페|관광지|자동} 분류 직접 바꾸기(카페가 식사로 잡힌 경우 등)
        k = str(e.get("kind") or "")
        if locate(pid)[0] is None:
            return {"status": "승인 대기", "answer": "그 장소는 일정에 없어요.", "trace": _t(state, "edit", e, "없음")}
        ko = dict(spec.kind_override or {})
        if k in planner.KIND_OVERRIDES:
            ko[pid] = k
            changes.append(f"{_josa(name, '을를')} '{k}'(으)로 분류했어요" + (" — 식사 횟수 검사에서 빠져요" if k == "카페" else ""))
        else:
            ko.pop(pid, None)
            changes.append(f"{name}의 분류를 자동으로 되돌렸어요")
        spec.kind_override = ko
        if k in ("카페", "관광지"):      # 식사 역할이었으면 일반 장소로(식사 시간대까지 기다리지 않게)
            di_, i_ = locate(pid)
            if days[di_][i_].role in ("breakfast", "lunch", "dinner"):
                days[di_][i_] = days[di_][i_].model_copy(update={"role": "place"})
    elif op in ("add", "replace", "stay_all"):
        cand = e.get("candidate") if isinstance(e.get("candidate"), dict) else None
        man = e.get("manual") if isinstance(e.get("manual"), dict) else None
        tdi = ti = None
        if op == "stay_all" and not pid and not cand and e.get("target"):
            pid = e["target"]
        if op == "replace":      # 일정 속 장소를 대안으로 바꾸기(자리·역할·머무는 시간 유지)
            tdi, ti = locate(e.get("target"))
            if tdi is None:
                return {"status": "승인 대기", "answer": "바꿀 장소가 일정에 없어요.", "trace": _t(state, "edit", e, "대상 없음")}
            ch = e.get("choice") if isinstance(e.get("choice"), dict) else {}
            pid = ch.get("place_id") or pid
            cand = ch.get("candidate") if isinstance(ch.get("candidate"), dict) else cand
        if not pid and not cand and man:      # 검색에 없을 때 직접 추가(이름 + 주소 또는 지도 좌표)
            from src.places import geo, placesearch
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
                    from src.places import tourlive
                    try:
                        tourlive.fetch(pid, spec.region)
                    except Exception:
                        pass
                if pid not in data.places(spec.region):
                    return {"status": "승인 대기", "answer": "관광공사 자료를 불러오지 못했어요. 잠시 후 다시 시도해 주세요.",
                            "trace": _t(state, "edit", e, "관광공사 상세 실패")}
            else:
                from src.places import placesearch
                up = placesearch.user_place_from_candidate(cand)
                spec.user_waypoints = [w for w in spec.user_waypoints if w["place_id"] != up["place_id"]] + [up]
                pid = up["place_id"]
            pl = data.places(spec.region)
        if not pid and e.get("name"):
            br: list = []
            ids, users, notes = resolve_waypoints([e["name"]], spec.region, br)
            if br:            # 지점이 여러 곳 → 어느 지점인지 되묻기(고르면 같은 add 편집에 candidate로 다시 옴)
                for o in br[0]["options"]:
                    o["edit"] = {**o["edit"], **{k: e[k] for k in ("day", "after", "before", "insert") if e.get(k)}}
                return {"status": "되묻기", "answer": br[0]["question"], "changes": notes[:1],
                        "extra": {"place_choices": br}, "trace": _t(state, "edit", e, f"지점 {len(br[0]['options'])}곳")}
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
        if op == "stay_all":      # 예약한 숙소로 모든 밤을 한 번에(같은 지역 여행)
            spec.booked_stay = pid
            spec.waypoint_ids = [w for w in spec.waypoint_ids if w != pid]
            for row in days:       # 낮 일정에 같은 숙소가 관광지처럼 들어가 있으면 빼기
                row[:] = [x for x in row if not (x.place_id == pid and x.role != "stay")]
            planner.apply_booked_stay(spec, days)
            n = sum(1 for k in range(spec.nights) if str(k + 1) not in (spec.no_stay or {}))
            changes.append(f"예약하신 {_josa(title_of(pid), '을를')} {n}박 모두의 숙소로 넣었어요")
            return _reschedule(state, spec, days, changes, "edit")
        pkind = (pl.get(pid) or next((w for w in spec.user_waypoints if w["place_id"] == pid), {})).get("kind")
        if locate(pid)[0] is not None and pkind == "lodging" and op == "add":
            # 숙소에 들어갔다가 다시 나오는 경우: 같은 숙소를 '숙소 들르기'로 여러 번 넣을 수 있게(2026-11-26 사용자 요청)
            pid = f"{planner.VISIT_PREFIX}{pid}:{uuid.uuid4().hex[:4]}"
        elif locate(pid)[0] is not None:
            return {"status": "승인 대기", "answer": "이미 일정에 있는 곳이에요.", "trace": _t(state, "edit", e, "중복")}
        name = title_of(planner.visit_base(pid) or pid)
        np0 = _place_info(spec, pid)
        rt_ = e.get("route") if e.get("route") in ("out", "back") else None
        if rt_ and op == "add" and np0.get("lat") and np0.get("kind") != "lodging" and not planner.visit_base(pid):
            # 출발지~복귀 지점 사이 어느 자리든 끼워 넣기: 여행지 밖(출발지·길 위) 장소는 그 자리의 들를 곳으로,
            # 여행지 안 장소는 가는 길 → 첫날 맨 앞 / 오는 길 → 마지막 날 맨 끝 현지 일정으로
            di_r = 0 if rt_ == "out" else nd - 1
            if planner.route_leg_for(spec, np0["lat"], np0["lng"], di_r) is not None:
                try:
                    idx_ = int(e["index"]) if e.get("index") is not None else None
                except (TypeError, ValueError):
                    idx_ = None
                msg = _divert_to_route(spec, pid, rt_, idx_, e.get("kind") if e.get("kind") in ("식사", "카페") else None)
                if not msg:
                    return {"status": "승인 대기", "answer": f"{'오는' if rt_ == 'back' else '가는'} 길에 들를 곳은 5곳까지 넣을 수 있어요.",
                            "trace": _t(state, "edit", e, "상한")}
                spec.user_waypoints = [w for w in spec.user_waypoints if w["place_id"] != pid]
                changes.insert(0, msg)
                return _reschedule(state, spec, days, changes, "edit")
            row0 = days[di_r]
            ref0 = next((x.place_id for x in (row0 if rt_ == "out" else reversed(row0)) if x.role != "stay"), None)
            e = {**e, "insert": True, "after_day": di_r + 1, **({"before": ref0} if rt_ == "out" else {"after": ref0})} if ref0 else \
                {**e, "day": di_r + 1}
            changes.append(f"{title_of(pid)}은(는) 여행지 안이라 {'첫날 첫 장소 앞' if rt_ == 'out' else '마지막 날 마지막 장소 뒤'}에 넣었어요")
        elif np0.get("lat") and np0.get("kind") != "lodging" and not planner.visit_base(pid):
            ref = e.get("after") or e.get("before")
            hint = (locate(ref)[0] if ref and locate(ref)[0] is not None else None)
            if hint is None:
                hint = tdi if tdi is not None else (int(e.get("meal_day") or e.get("day") or 1) - 1)
            leg_ = planner.route_leg_for(spec, np0["lat"], np0["lng"], max(0, min(nd - 1, hint)))
            if leg_ in ("out", "back"):
                msg = _divert_to_route(spec, pid, leg_)
                if msg:
                    spec.user_waypoints = [w for w in spec.user_waypoints if w["place_id"] != pid]
                    changes.insert(0, msg + (" 바꾸려던 자리의 기존 장소는 그대로 두었어요." if op == "replace" else ""))
                    return _reschedule(state, spec, days, changes, "edit")
        meal_day, meal_role = e.get("meal_day"), e.get("meal_role")
        if op == "add" and meal_day and meal_role in ("breakfast", "lunch", "dinner"):
            # 식사 자리에서 '식당 고르기'(2026-11-26): 그날 그 끼니 자리표시자를 고른 식당으로, 아침은 그날 맨 앞
            to = max(1, min(nd, int(meal_day))) - 1
            row = days[to]
            k = next((i for i, x in enumerate(row) if x.place_id == planner.MEAL_ID and x.role == meal_role), None)
            pk = PickItem(place_id=pid, role=meal_role, stay_minutes=45 if meal_role == "breakfast" else 60,
                          reason=f"{ {'breakfast': '아침', 'lunch': '점심', 'dinner': '저녁'}[meal_role]}으로 직접 고르신 식당")
            if k is not None:
                row[k] = pk
            elif meal_role == "breakfast":
                row.insert(0, pk)
            else:
                insert(to, pk)
            spec.waypoint_ids = list(dict.fromkeys([*spec.waypoint_ids, pid]))
            spec.user_ordered_days = sorted(set(spec.user_ordered_days) | {to + 1})
            changes.append(f"{to + 1}일차 {({'breakfast': '아침', 'lunch': '점심', 'dinner': '저녁'})[meal_role]}을 {_josa(name, '으로로')} 정했어요")
            return _reschedule(state, spec, days, changes, "edit")
        if op == "replace":
            old = days[tdi][ti]
            oname = (pl.get(old.place_id) or {}).get("title") or next(
                (w["title"] for w in spec.user_waypoints if w["place_id"] == old.place_id), old.place_id)
            days[tdi][ti] = PickItem(place_id=pid, role=old.role, stay_minutes=old.stay_minutes,
                                     reason=f"{oname} 대신 직접 고르신 곳")
            spec.waypoint_ids = [w for w in spec.waypoint_ids if w != old.place_id]
            spec.user_ordered_days = sorted(set(spec.user_ordered_days) | {tdi + 1})
            to = tdi
        elif (e.get("after") and locate(e["after"])[0] is not None) or (e.get("before") and locate(e["before"])[0] is not None):
            # 일정 중간에 끼워 넣기(2026-11-26): '근처 먹거리'·'+ 여기에 넣기' → 그 바로 다음(after) 또는 바로 앞(before)
            before = not (e.get("after") and locate(e["after"])[0] is not None)
            to, ai = locate(e["before"] if before else e["after"], e.get("after_day"))
            kind = (pl.get(pid) or next((w for w in spec.user_waypoints if w["place_id"] == pid), {})).get("kind")
            want = e.get("kind") if e.get("kind") in ("식사", "카페") else None     # '+ 식사' 끼워 넣기(2026-11-26): 식사/카페로 분류
            if want:
                spec.kind_override = {**(spec.kind_override or {}), pid: want}
            days[to].insert(ai if before else ai + 1, PickItem(
                place_id=pid, role="place", stay_minutes=60 if (kind == "restaurant" or want) else 90,
                reason="일정 사이에 직접 끼워 넣으신 곳" if e.get("insert") else f"{title_of(e['after'])} 근처에서 고르신 곳"))
            spec.user_ordered_days = sorted(set(spec.user_ordered_days) | {to + 1})
        else:
            if e.get("day"):
                to = max(1, min(nd, int(e["day"]))) - 1
            else:   # 관광지가 가장 적은 날 — 그 장소가 쉬는 날은 빼고(사용자 테스트: 휴무 월요일에 넣어 확정 불가)
                pinfo = pl.get(pid)
                open_days = [k for k in range(nd) if not pinfo or hours.parse_open(pinfo, spec.days[k]).status != "휴무"] or list(range(nd))
                to = min(open_days, key=lambda k: sum(1 for x in days[k] if x.role == "place"))
            kind = (pl.get(pid) or {}).get("kind")
            insert(to, PickItem(place_id=pid, role="place", stay_minutes=60 if kind == "restaurant" else 90,
                                reason=next((r for p_, r in (state.get("ranked") or []) if p_ == pid), "") or "직접 추가하신 곳"))
        spec.waypoint_ids = list(dict.fromkeys([*spec.waypoint_ids, pid]))
        np_ = (pl.get(pid) or next((w for w in spec.user_waypoints if w["place_id"] == pid), {}))
        if np_.get("lat"):
            from src.places.placesearch import MAX_KM
            from src.shared.travel import km as _km
            far_km = _km(data.region_center(spec.region), (np_["lat"], np_["lng"]))
            if far_km > MAX_KM:
                changes.append(f"{np_.get('title', '이곳')}은(는) 여행 지역에서 약 {far_km:,.0f}km 떨어져 있어 오가는 이동이 길어요(추정)")
        if op == "replace":
            changes.append(f"{to + 1}일차 {oname} 자리에 {_josa(name, '을를')} 넣었어요(순서·머무는 시간은 그대로)")
        elif e.get("after") and locate(e["after"])[0] is not None and e["after"] != pid:
            changes.append(f"{to + 1}일차 {title_of(e['after'])} 바로 다음에 {_josa(name, '을를')} 넣었어요")
        elif e.get("before"):
            changes.append(f"{to + 1}일차 {title_of(e['before'])} 바로 앞에 {_josa(name, '을를')} 넣었어요")
        else:
            if not e.get("keep_order") and str(to + 1) not in map(str, spec.user_ordered_days):
                days[to] = planner.optimize_day(spec, days[to])   # 넣은 날은 동선 기준으로 자리 잡기
            changes.append(f"{to + 1}일차에 {_josa(name, '을를')} 추가했어요")
    elif op == "title":          # {title} 여행 제목(같은 곳을 여러 번 가도 구분되게). 빈 값이면 기본 제목으로
        t = guardrails.mask_pii(re.sub(r"\s+", " ", str(e.get("title") or "")).strip()[:40])[0]
        spec.title = t or None
        changes.append(f"여행 제목을 '{t}'(으)로 바꿨어요" if t else "여행 제목을 기본 제목으로 되돌렸어요")
    elif op == "long_time":      # {leg: out|back, minutes|null} 장거리 운전 시간 직접 정하기(null이면 자동 추정)
        leg = "back" if e.get("leg") == "back" else "out"
        lo = dict(spec.long_override or {})
        where = "오는 길(귀가)" if leg == "back" else "가는 길(출발)"
        if e.get("minutes") in (None, "", "auto"):
            lo.pop(leg, None)
            changes.append(f"{where} 운전 시간을 자동 추정으로 되돌렸어요(도로 경로·시간대 혼잡 기준)")
        else:
            try:
                mins = max(20, min(900, int(e.get("minutes"))))
            except (TypeError, ValueError):
                return {"status": "승인 대기", "answer": "운전 시간을 분 단위 숫자로 알려 주세요.", "trace": _t(state, "edit", e, "형식")}
            lo[leg] = mins
            changes.append(f"{where} 운전 시간을 {mins // 60}시간 {mins % 60}분으로 정했어요(들르는 곳·식사 시간은 따로 더해요)")
        spec.long_override = lo
    elif op == "time":           # {place_id, time: "HH:MM"|null, minutes?} 시작 시각 직접 정하기(null이면 앞 일정에 바로 붙이기)
        di, i = locate(pid)
        if di is None:
            return {"status": "승인 대기", "answer": "그 장소는 일정에 없어요.", "trace": _t(state, "edit", e, "없음")}
        key = f"{di + 1}:{pid}"
        fs = dict(spec.fixed_starts or {})
        old = days[di][i]
        nm = old.reason if old.place_id.startswith(planner.FREE_PREFIX) else name
        tv = e.get("time")
        if tv in (None, "", "auto"):
            fs.pop(key, None)
            changes.append(f"{di + 1}일차 {nm} 시작 시각을 자동으로 — 앞 일정이 끝나면 바로 이어져요")
        else:
            m_ = re.match(r"^\s*(\d{1,2}):(\d{2})\s*$", str(tv))
            if not m_ or not (5 * 60 <= int(m_.group(1)) * 60 + int(m_.group(2)) <= 26 * 60):
                return {"status": "승인 대기", "answer": "시작 시각은 05:00~익일 02:00 사이 'HH:MM'으로 알려 주세요.", "trace": _t(state, "edit", e, "형식")}
            mm = int(m_.group(1)) * 60 + int(m_.group(2))
            fs[key] = mm
            changes.append(f"{di + 1}일차 {nm} 시작을 {hours.fmt(mm)}로 정했어요(앞 일정이 그보다 늦게 끝나면 끝난 뒤로, 뒤 일정은 이어서 다시 계산)")
        spec.fixed_starts = fs
        if e.get("minutes") not in (None, "") and old.role != "stay":
            try:
                mins = max(15, min(360, int(e.get("minutes"))))
                if mins != old.stay_minutes:
                    days[di][i] = PickItem(place_id=old.place_id, role=old.role, stay_minutes=mins, reason=old.reason)
                    changes.append(f"{di + 1}일차 {nm} 머무는 시간을 {mins}분으로 바꿨어요")
            except (TypeError, ValueError):
                pass
        spec.user_ordered_days = sorted(set(spec.user_ordered_days) | {di + 1})     # 직접 시간을 정한 날은 순서도 그대로
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
                to, ai = locate(e["after"], e.get("after_day"))
                days[to].insert(ai + 1, blk)
                where = f"{title_of(e['after']) or '그 장소'} 다음에"
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
    elif op == "night":          # {day, mode: stay|차박|밤샘|야간 이동|숙박 필요 없음} 다음 날로 이어지는 밤의 숙박
        try:
            dn = int(e.get("day"))
        except (TypeError, ValueError):
            return {"status": "승인 대기", "answer": "몇 일차 밤인지 알려 주세요.", "trace": _t(state, "edit", e, "형식")}
        if not 1 <= dn <= spec.nights:
            return {"status": "승인 대기", "answer": "그날은 다음 날로 이어지는 밤이 아니에요(마지막 날은 숙박이 없어요).",
                    "trace": _t(state, "edit", e, "범위")}
        mode = str(e.get("mode") or "stay")
        row = days[dn - 1]
        if mode in planner.NO_STAY_MODES:
            spec.no_stay = {**(spec.no_stay or {}), str(dn): mode}
            had = [x for x in row if x.role == "stay"]
            days[dn - 1] = [x for x in row if x.role != "stay"]
            changes.append(f"{dn}일차 밤은 숙소 없이 '{mode}'(으)로 보낼게요"
                           + (f" — {title_of(had[0].place_id)}을(를) 뺐어요" if had else "")
                           + ". 다음 날 아침 숙소 식사·체크아웃 안내도 빠져요. 졸음운전과 안전한 쉴 곳을 꼭 챙겨 주세요")
        else:
            spec.no_stay = {k: v for k, v in (spec.no_stay or {}).items() if k != str(dn)}
            if not any(x.role == "stay" for x in row):
                # 앞·뒤 밤 숙소가 있으면 그 숙소(연박), 없으면 그날 마지막 장소에서 가장 가까운 숙소
                sid_ = next((x.place_id for k in (dn - 2, dn) if 0 <= k < nd for x in days[k] if x.role == "stay"), None)
                if not sid_:      # 그날 마지막 장소에서 가장 가까운 숙소(수집 데이터)
                    from src.shared.travel import km as _km
                    last = next((planner._pcoord(spec, x.place_id) for x in reversed(row)
                                 if x.place_id != planner.MEAL_ID and not x.place_id.startswith(planner.FREE_PREFIX)
                                 and planner._pcoord(spec, x.place_id)), None) or data.region_center(spec.region)
                    lodg = [(pid_, p) for pid_, p in pl.items() if p.get("kind") == "lodging" and p.get("lat")]
                    if not lodg:
                        return {"status": "승인 대기", "answer": "가까운 숙소 자료가 없어요. '장소 찾기'로 숙소를 직접 넣어 주세요.",
                                "trace": _t(state, "edit", e, "숙소 없음")}
                    sid_ = min(lodg, key=lambda kv: _km(last, (kv[1]["lat"], kv[1]["lng"])))[0]
                row.append(PickItem(place_id=sid_, role="stay", stay_minutes=0, reason="다음 날까지 이어지는 일정이라 숙소를 넣었어요"))
                changes.append(f"{dn}일차 밤에 {_josa(title_of(sid_), '을를')} 숙소로 넣었어요(다른 숙소로 바꿀 수 있어요)")
            else:
                changes.append(f"{dn}일차 밤은 숙소에서 묵어요")
    elif op == "route_stop":     # {leg: out|back, action: add|remove, candidate|stop} 출발 지점 다음·복귀 지점 앞에 들를 곳(전국 검색 결과)
        leg = "back" if e.get("leg") == "back" else "out"
        lst_name = "return_stops" if leg == "back" else "depart_stops"
        cur = list(getattr(spec, lst_name) or [])
        where = "오는 길" if leg == "back" else "가는 길"
        if e.get("action") in ("stay", "move"):          # 머무는 시간 바꾸기 · 순서 앞뒤로(동선에 포함된 들를 곳)
            k = str((e.get("stop") or {}).get("key") or e.get("key") or "")
            ix = next((i for i, s in enumerate(cur) if s.get("key") == k), None)
            if ix is None:
                return {"status": "승인 대기", "answer": f"{where}에 넣은 곳 중에서 찾지 못했어요.", "trace": _t(state, "edit", e, "없음")}
            if e.get("action") == "stay":
                try:
                    mins = max(10, min(360, int(e.get("minutes"))))
                except (TypeError, ValueError):
                    return {"status": "승인 대기", "answer": "머무는 시간을 분 단위 숫자로 알려 주세요.", "trace": _t(state, "edit", e, "형식")}
                cur[ix] = {**cur[ix], "stay_min": mins}
                changes.append(f"{where} {cur[ix]['name']} 머무는 시간을 {mins}분으로 바꿨어요(뒤 일정은 이어서 다시 계산)")
            else:
                j_ = ix + (1 if int(e.get("dir") or 1) > 0 else -1)
                if not 0 <= j_ < len(cur):
                    return {"status": "승인 대기", "answer": "더 옮길 수 없어요.", "trace": _t(state, "edit", e, "끝")}
                cur[ix], cur[j_] = cur[j_], cur[ix]
                cur = [{**x, "user_order": True} for x in cur]       # 직접 정한 순서는 그대로 지킨다
                changes.append(f"{where} 들르는 순서를 바꿨어요: " + " → ".join(s["name"] for s in cur))
            setattr(spec, lst_name, cur)
        elif e.get("action") == "remove":
            k = str((e.get("stop") or {}).get("key") or (e.get("stop") or {}).get("name") or e.get("key") or "")
            gone = [s for s in cur if k in (s.get("key"), s.get("name"))]
            if not gone:
                return {"status": "승인 대기", "answer": f"{where}에 넣은 곳 중에서 찾지 못했어요.", "trace": _t(state, "edit", e, "없음")}
            setattr(spec, lst_name, [s for s in cur if s not in gone])
            changes.append(f"{where} 들를 곳에서 {_josa(gone[0]['name'], '을를')} 뺐어요")
        else:
            c = e.get("candidate") if isinstance(e.get("candidate"), dict) else e.get("stop") if isinstance(e.get("stop"), dict) else {}
            if not planner.route_ends(spec).get(leg):
                return {"status": "승인 대기", "answer": ("출발지를 먼저 정해 주세요. 출발 지점이 있어야 그다음에 들를 곳을 넣을 수 있어요."
                                                     if leg == "out" else "복귀 지점(출발지 또는 여행 후 도착지)을 먼저 정해 주세요."),
                        "trace": _t(state, "edit", e, "양 끝 없음")}
            stop = planner.route_stop_from(spec, leg, c)
            if not stop:
                return {"status": "승인 대기", "answer": "들를 곳의 위치를 읽지 못했어요. 장소 찾기에서 다시 골라 주세요.", "trace": _t(state, "edit", e, "형식 오류")}
            stop["name"] = guardrails.mask_pii(stop["name"])[0]
            if len(cur) >= 5 and not any(s.get("key") == stop["key"] for s in cur):
                return {"status": "승인 대기", "answer": f"{where}에 들를 곳은 5곳까지 넣을 수 있어요.", "trace": _t(state, "edit", e, "상한")}
            if e.get("kind") in _KIND_TO_STOP:
                stop["kind"] = _KIND_TO_STOP[e["kind"]]
                stop["stay_min"] = {"식당": 50, "카페": 30}.get(stop["kind"], 45)
            try:
                idx_ = int(e["index"]) if e.get("index") is not None else None
            except (TypeError, ValueError):
                idx_ = None
            note = _put_route_stop(spec, leg, stop, idx_)
            meal = " · 이 시간대 끼니를 이곳에서 먹어요" if stop["kind"] == "식당" else ""
            changes.append(f"{where}에 {_josa(stop['name'], '을를')} 넣었어요(앞 지점에서 운전 시간을 따로 계산 + 머무는 {stop['stay_min']}분, 추정){meal}{note}")
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
            if len(spec.return_stops) >= 5:
                return {"status": "승인 대기", "answer": "귀가길에 들를 곳은 5곳까지만 넣을 수 있어요.", "trace": _t(state, "edit", e, "상한")}
            spec.return_stops = [s for s in spec.return_stops if s.get("key") != stop["key"]] + [stop]
            spec.return_stops.sort(key=lambda s: s.get("km_from_start") or 0)
            changes.append(f"돌아가는 길에 {_josa(stop['name'], '을를')} 들르도록 넣었어요(우회 약 {stop['detour_min']}분 + 머무는 {stop['stay_min']}분)")
    elif op == "order":          # 끌어서 바꾼 전체 순서(날짜 사이 이동 포함)
        by_id = {x.place_id: x for row in days for x in row if x.place_id != planner.MEAL_ID}
        want = {int(r.get("day") or 0): [str(x) for x in r.get("place_ids") or []] for r in e.get("days") or []}
        drop = [str(x) for x in e.get("remove") or []]          # 동선 편집에서 삭제한 장소
        if (not want and not drop) or any(i not in by_id for ids in want.values() for i in ids) or any(i not in by_id for i in drop):
            return {"status": "승인 대기", "answer": "순서를 바꿀 장소를 찾지 못했어요. 화면을 새로 고친 뒤 다시 해 주세요.",
                    "trace": _t(state, "edit", e, "모르는 id")}
        want = {dn: [i for i in ids if i not in drop] for dn, ids in want.items()}
        if drop:
            for di in range(nd):
                if any(x.place_id in drop for x in days[di]):
                    days[di] = [x for x in days[di] if x.place_id not in drop]
                    spec.user_ordered_days = sorted(set(spec.user_ordered_days) | {di + 1})
            spec.waypoint_ids = [w for w in spec.waypoint_ids if w not in drop]      # 다시 짜도 되살리지 않음
            names = [title_of(i) for i in drop]
            changes.append(f"{', '.join(names[:3])}{f' 외 {len(names) - 3}곳' if len(names) > 3 else ''}을(를) 일정에서 뺐어요")
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
            # 식사 자리표시자는 여기서 붙이지 않음(예전엔 숙소 뒤에 붙어 쌓였음) — _fill_meals가 필요한 자리에 다시 넣음
            # 화면의 순서 목록에 없는 장소(그 사이 새로 넣은 곳 등)는 지우지 않고 그날 숙소 앞에 남긴다 — 지우려면 '삭제'로
            keep_ = [x for x in days[dn - 1] if x.place_id != planner.MEAL_ID and x.place_id not in listed
                     and x.place_id not in drop and x.role != "stay" and not any(x.place_id in w_ for w_ in want.values())]
            days[dn - 1] = [by_id[i] for i in ids]
            if keep_:
                at = next((k_ for k_, x in enumerate(days[dn - 1]) if x.role == "stay"), len(days[dn - 1]))
                days[dn - 1][at:at] = keep_
                changes.append(f"{dn}일차에 순서 목록에 없던 {', '.join(title_of(x.place_id) for x in keep_[:3])}은(는) 그대로 남겨 두었어요")
        if moved or not drop:
            changes.append(f"{moved}개 날의 순서를 직접 정하신 대로 바꿨어요(직접 정한 순서를 최우선으로 지켜요)" if moved
                           else "순서는 그대로예요")
        meal_ids = [i for ids in want.values() for i in ids if _place_info(spec, i).get("kind") == "restaurant"]
        if meal_ids:       # 순서 목록에 직접 둔 식당은 '내가 고른 곳' — 모모가 고른 식당처럼 겹침 정리로 빠지지 않게
            spec.waypoint_ids = list(dict.fromkeys([*spec.waypoint_ids, *meal_ids]))
        _divert_far(spec, days, changes)      # 정한 순서대로 둔 뒤, 여행지 밖(출발지 근처) 장소는 길 위 들를 곳으로
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
    # 강도 권장 초과: 질문만 있고 고를 곳이 없었다(2026-11-27) → 계획서의 suggest(그날 카드 위 선택지)와 같은 기준으로 안내
    suggest = brochure.over_cap_suggest(spec, out.get("itinerary") or [])
    for sg in suggest:
        out["changes"].append(f"{sg['day']}일차 관광지가 {sg['count']}곳으로 '{sg['intensity']}' 강도 권장({sg['cap']}곳)보다 많아요. "
                              "그날 일정 위에서 후보로 돌릴 곳을 고르거나 '그대로 둘게요'를 눌러 주세요")
    out.setdefault("extra", {})["suggest"] = suggest
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
    from src.places import geo
    origin = s.get("origin")
    oloc = geo.locate(origin, s.get("origin_place")) if (origin or s.get("origin_place")) else None
    dep = oloc["sido"] if oloc else None
    if (origin or s.get("origin_place")) and not oloc:
        notes.append(f"출발지 '{origin}'의 위치를 찾지 못해 오가는 시간은 빼고 계산했어요. '여행 조건'에서 동·역 이름으로 다시 알려 주세요")
    elif not dep:
        notes.append("출발지가 없어 오가는 시간은 빼고 현지 도착 기준으로 계산했어요")
    eloc = geo.locate(s.get("end_location"), s.get("end_place")) if (s.get("end_location") or s.get("end_place")) else None
    long_t = s.get("long_transport") or "자차"
    if data.requires_flight(rid) and long_t != "비행기":
        notes.append(f"{data.region_label(rid)}은(는) 비행기(또는 배)로 가야 해서 장거리 이동을 비행기로 계산했어요")
        long_t = "비행기"
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
    choices: list = []
    wps, wdrop = clean_waypoints(s.get("waypoints"))
    if wdrop:
        notes.append(f"꼭 갈 곳은 {MAX_WAYPOINTS}곳까지 받아요 — 받지 못한 곳: {', '.join(wdrop[:5])}{' 외' if len(wdrop) > 5 else ''}. "
                     "일정이 나온 뒤 대화나 '장소 찾기'로 더해 주세요")
    if wps:
        ids, users, wnotes = resolve_waypoints(wps, rid, choices)
        spec.waypoint_ids, spec.user_waypoints = ids, users
        notes += wnotes
    booked = str(s.get("booked_stay") or "").strip()[:60]
    if booked:       # 숙소를 이미 예약했으면 모든 밤을 그 숙소로(같은 지역 여행, 2026-11-26 사용자 요청)
        if nights == 0:
            notes.append("당일치기라 예약하신 숙소는 넣지 않았어요")
        else:
            bids, busers, bnotes = resolve_waypoints([booked], rid)
            sid = (busers[0]["place_id"] if busers else bids[0] if bids else None)
            if sid:
                spec.user_waypoints = [*spec.user_waypoints, *[u for u in busers if u["place_id"] == sid]]
                spec.booked_stay = sid
                bname = (data.places(rid).get(sid) or {}).get("title") or (busers[0]["title"] if busers else booked)
                bp = data.places(rid).get(sid) or (busers[0] if busers else {})
                far = ""
                if bp.get("lat"):
                    from src.shared.travel import km as _km
                    dkm = _km(data.region_center(rid), (bp["lat"], bp["lng"]))
                    if dkm > 60:
                        far = f" — 여행 지역 중심에서 약 {dkm:,.0f}km 떨어져 있어요. 다른 숙소가 필요하면 그날 숙소를 바꿔 주세요"
                notes.append(f"예약하신 '{bname}'을(를) {nights}박 모두의 숙소로 넣었어요{far}")
            else:
                notes.append(f"예약하신 숙소 '{booked}'을(를) 찾지 못했어요. 일정이 나온 뒤 숙소 카드의 '다른 숙소로 바꾸기'에서 찾아 주세요"
                             + (" — " + bnotes[0] if bnotes else ""))
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
    return _with_choices(_run_plan(state, spec, request, notes, base=None), choices)


def pack_node(state: State) -> State:
    """준비물 체크리스트 체크·추가·삭제 — 일정·상태는 그대로, 즉시 반영(LLM 없음)."""
    from src.packing import packing
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
        from src.counsel.options import EXPENSE_CATEGORIES
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
    from src.places import geo
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
        if key == "long_transport" and value != "비행기" and data.requires_flight(spec.region):
            return spec, False, value, f"{data.region_label(spec.region)}은(는) 섬이라 장거리 이동은 비행기만 고를 수 있어요."
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
            return spec, False, value, "여행 강도는 강행군·빡빡·보통·여유 중에서 골라 주세요."
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
    changes_in = [{"key": c.get("key"), "value": str(c.get("value") or "").strip(), "place": c.get("place"),
                   "confirm": bool(c.get("confirm"))} for c in changes_in if c.get("key")]
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
    # 모모가 가정해 둔 값을 사용자가 그대로 고른 경우(confirm): 값은 그대로, '가정' 표시만 지워 확정으로(다시 짜지 않음)
    confirmed = []
    for c in [c for c in changes_in if c["confirm"]]:
        akey = {"start_date": "date", "end_date": "length"}.get(c["key"], c["key"])
        if akey in a:
            a.discard(akey)
        spec.given = sorted(set(spec.given or []) | {c["key"]})
        confirmed.append(f"{_josa(SET_LABEL.get(c['key'], c['key']), '을를')} {_josa(chr(39) + c['value'] + chr(39), '으로로')}")
    changes_in = [c for c in changes_in if not c["confirm"]]
    if confirmed and not changes_in:
        spec.assumptions = sorted(a)
        out = _reschedule(state, spec, _day_picks(spec, state["itinerary"]), [", ".join(confirmed) + " 확정했어요"], "set")
        return out
    replan, done, errors = False, [], []
    done += [x + " 확정하고" for x in confirmed]
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


_SIDO_GU = re.compile(r"([가-힣]{2,8}?(?:특별자치시|특별자치도|특별시|광역시|시|도)?)\s*([가-힣]{1,6}(?:구|군))"
                      r"(?=$|[^가-힣]|에서|에|으로|로|부터|쪽|근처|집|이|은|는|의)")


def _dep_with_gu(dep: str | None, q: str) -> str | None:
    """LLM이 출발지를 시·도까지만 뽑았는데('서울') 질문에 같은 시·도의 구·군이 있으면('서울시 강남구') 구까지 채운다.
    출발지는 시·군·구 단위로만 쓴다(SERVICE.md 4절) — 2차 평가 #18 회귀(2026-11-27)."""
    if not dep or re.search(r"(구|군)$", dep.strip()) or not data.norm_sido(dep):
        return dep
    for m in _SIDO_GU.finditer(q or ""):
        if data.norm_sido(m.group(1)) == data.norm_sido(dep):
            return f"{m.group(1)} {m.group(2)}"
    return dep


def _dep_label(text: str | None) -> str:
    """출발지 표기: 시·도 + 시·군·구까지만(마스킹 자리표시자·상세 주소 제거)."""
    t = re.sub(r"\[[^\]]*\]", "", text or "").strip()
    words = t.split()
    keep = [w for w in words if re.search(r"(특별시|광역시|자치시|도|시|군|구)$", w) or data.norm_sido(w) == w]
    return " ".join(keep[:2]) or (words[0] if words else "")


def _role(it: dict) -> str:
    if it["type"] == "stay":
        return "stay"
    if "야간" in (it.get("notes") or []):       # 체크인 뒤 저녁·야간 일정은 끼니 자리가 아니다
        return "place"
    if it["type"] == "meal" and it.get("place_id") and hours.parse_hm(it["start_time"]) < planner.BREAKFAST_END:
        return "breakfast"
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


def info_node(state: State) -> State:
    from langchain.agents import create_agent
    from langchain_core.tools import tool

    p = ParsedRequest(**state["parsed"])
    region, why = resolve_region(p)
    if why in ("overseas", "unprepared", "unsupported", "missing") and p.info_topic not in ("hours",):
        return _region_reply(state, why, p)

    @tool
    def retrieve_places(query: str, region: str, count: int = 5) -> str:
        """지역 안의 관광지·음식점·숙박 장소를 검색한다. region은 지원 지역 id(gyeongju, suncheon_gurye, suwon, jeju, gangneung, jeonju, tongyeong_geoje).
        count는 돌려받을 장소 수(1~10). 사용자가 'N곳'을 원하면 N을 넣는다."""
        hits = retrieve.search(query, region if region in data.available_regions() else None, k=max(1, min(10, int(count or 5))))
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
    forced = bool(viol) and bool(state.get("force_approve"))
    if viol and not forced:
        return {"status": "승인 대기", "answer": "아직 규칙 검사에서 걸리는 부분이 있어요: "
                + "; ".join(f"{v['place_name']} {v['detail']}" for v in viol[:3])
                + ". 이 부분을 바꿔 드릴까요? 알고 계신 내용이면 '무시하고 확정'으로 그대로 확정할 수도 있어요.",
                "extra": {"draft_version": state.get("draft_version"), "violations": viol, "can_force": True},
                "trace": _t(state, "approve", "approve", f"위반 {len(viol)}건 → 승인 보류(무시하고 확정 가능)")}
    spec = state["spec"]
    payload = {"title": spec.get("title") or f"{data.region_label(spec['region'])} {'당일치기' if spec['nights'] == 0 else str(spec['nights']) + '박 ' + str(spec['nights'] + 1) + '일'} · 모모 여행 일정",
               "region": spec["region"], "region_label": data.region_label(spec["region"]),
               "departure_sido": spec.get("departure"), "nights": spec["nights"], "start": spec["start"],
               "itinerary": items, "draft_version": state["draft_version"], "plan": state.get("plan")}
    if forced:      # 사용자가 확인하고 넘긴 규칙 검사 항목은 계획서에 남겨 둔다
        payload["acknowledged_violations"] = viol
    pid = store.save_plan(sid, state["draft_version"], payload)
    has_check = any(it["hours_status"] == "확인 필요" for it in items)
    ans = ("확정했어요! 엽서를 꼭꼭 눌러 담아 두었어요. 공유 링크로 가족과 함께 보시고, HTML·Word·PDF로 내려받거나 캘린더(.ics)에 넣어 두세요. "
           "여행을 다녀온 뒤에는 공유 페이지에서 실제 쓴 경비를 적으면 '여행 결과 리포트'로 정리해 드려요."
           + (" '운영시간 확인 필요' 표시된 곳은 방문 전에 한 번 더 확인해 주세요." if has_check else "")
           + (f"\n\n규칙 검사에서 걸린 {len(viol)}건은 확인하신 대로 그대로 두었어요: "
              + "; ".join(f"{v['place_name']} {v['detail']}" for v in viol[:3])
              + (f" 외 {len(viol) - 3}건" if len(viol) > 3 else "") if forced else ""))
    return {"status": "확정", "answer": ans, "plan_id": pid,
            "extra": {"plan_id": pid, "share_url": f"/plan/{pid}", "ics_url": f"/plan/{pid}.ics",
                      "html_url": f"/plan/{pid}.html", "docx_url": f"/plan/{pid}.docx",
                      "print_url": f"/plan/{pid}?print=1", "report_url": f"/plan/{pid}/report",
                      "draft_version": state["draft_version"]},
            "stage": "refine",
            "trace": _t(state, "approve", f"session {sid[:6]}…", f"save_plan → plan_id {pid}"
                        + (f" · 규칙 검사 {len(viol)}건 무시(사용자 확인)" if forced else ""))}


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
                 replan: str | None = None, force: bool = False) -> dict:
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
                          "cost_req": cost_req, "pack_req": pack_req,
                          "force_approve": bool(force and action == "approve")}, cfg)
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
    from src.counsel import richtext
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
        bump = bool(out.get("draft_version")) and out.get("draft_version") != prev_dv and (
            not prev_items or action == "start" or replan == "update" or bool((resp.get("plan_diff") or {}).get("big")))
        store.log_message(sid, "momo", kind, answer, resp.get("status") or "", resp["answer_html"], resp.get("draft_version"),
                          bump=bump)
    resp["plan_no"] = store.plan_no(sid)
    if out.get("plan") and (out.get("plan_out") or out.get("status") == "확정"):
        store.upsert_trip(sid, out["plan"], out.get("status") or "", out.get("plan_id") if out.get("status") == "확정" else None)
    if new_session:
        resp["trace"].insert(0, {"step": "session", "input": "", "output": "새 세션 시작"})
    return resp


def backfill_messages(session_id: str) -> int:
    """기록 기능 전에 나눈 대화: 체크포인트 이력(실제 대화 실행분만)에서 요청·답변을 복원해 기록에 넣는다."""
    from datetime import datetime, timedelta, timezone
    if store.list_messages(session_id, 1) or store.messages_cleared(session_id):   # 사용자가 지운 기록은 되살리지 않음
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
    from src.counsel import richtext
    seen_plan = False
    for ts, v in reversed(runs):
        kind = v.get("action") or "chat"
        q = (v.get("question") or "").strip() or _action_label(v.get("action"), v.get("edit"), v.get("set_req"),
                                                              v.get("start_req"), v.get("cost_req"), v.get("pack_req"))
        if q:
            store.log_message(session_id, "user", kind, q, created=ts)
        bump = bool(v.get("draft_version")) and (kind == "start" or not seen_plan)
        seen_plan = seen_plan or bool(v.get("draft_version"))
        store.log_message(session_id, "momo", kind, v.get("answer") or "", v.get("status") or "",
                          richtext.md_to_html(v.get("answer") or ""), v.get("draft_version"), created=ts, bump=bump)
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
    if "suggest" not in plan and st.get("spec") and st.get("itinerary"):    # 예전에 저장된 계획에도 강도 초과 선택지
        try:
            plan = {**plan, "suggest": brochure.over_cap_suggest(spec_from_dict(st["spec"]), st["itinerary"])}
        except Exception:
            pass
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
