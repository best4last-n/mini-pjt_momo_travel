"""요청 예상 시간(2026-11-26). '30초쯤'이라는 고정 문구가 실제(수 분)와 크게 달라
실제 걸린 시간을 요청 종류별로 기록해 예상치를 스스로 맞춘다.

- 총 시간: 종류(start는 박수·꼭 갈 곳 수 구간까지) 최근 30건의 중앙값, 기록이 없으면 기본값.
- 진행 중: 에이전트가 단계(trace)를 남길 때마다 시각을 적고, '이 단계에서 끝까지 남은 시간'의
  과거 중앙값으로 남은 시간을 다시 계산한다(재생성이 한 번 더 돌면 예상도 늘어남).
"""
from __future__ import annotations

import contextvars
import json
import statistics
import threading
import time

from src.shared.config import ROOT  # noqa: E402

FILE = ROOT / "momo_eta.json"
KEEP = 30
# 기록이 없을 때의 기본값(초) — LLM 생성 1회 40~60초, 규칙 위반이면 최대 2회 더 생성
DEFAULT = {"start": 150, "chat": 90, "set": 90, "edit": 15, "cost": 10, "pack": 20, "approve": 15, "undo": 5}
STEP_LABEL = {
    "guardrail_in": "요청 확인", "parse_conditions": "요청 이해", "intake": "조건 확인", "start": "조건 정리",
    "retrieve": "장소 찾기", "rank": "장소 고르기", "generate_plan": "일정 짜기", "regenerate": "일정 다시 짜기",
    "validate": "운영시간·휴무일 검사", "tool:optimize_route": "동선 정리", "tool:estimate_travel": "이동 시간 계산",
    "plan_summary": "계획서 마무리", "guardrail_out": "마무리 검사",
}
_LOCK = threading.Lock()
CURRENT: contextvars.ContextVar[dict | None] = contextvars.ContextVar("momo_eta", default=None)


def _load() -> dict:
    try:
        return json.loads(FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save(d: dict) -> None:
    try:
        FILE.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def key_of(prep: dict) -> str:
    """요청 종류 키. 여행 시작은 박수(0·1·2·3+)와 꼭 갈 곳 수(0·1~5·6+)로 나눈다."""
    action = prep.get("action") or "chat"
    if action != "start":
        return action
    s = (prep.get("body") or {}).get("start") or {}
    try:
        from datetime import date
        n = (date.fromisoformat(s.get("end_date")) - date.fromisoformat(s.get("start_date"))).days
    except (TypeError, ValueError):
        n = 1
    wp = len(s.get("waypoints") or [])
    return f"start:n{min(max(n, 0), 3)}:w{0 if wp == 0 else 1 if wp <= 5 else 2}"


def _base(key: str) -> str:
    return key.split(":")[0]


def total(key: str) -> int:
    """예상 총 시간(초)."""
    d = _load()
    xs = (d.get("total") or {}).get(key) or (d.get("total") or {}).get(_base(key) + ":*") or []
    if len(xs) >= 2:
        return round(statistics.median(xs))
    if key.startswith("start:n"):          # 기록 전: 박수·꼭 갈 곳이 많을수록 생성·재생성이 길다
        _, n, w = key.split(":")
        return DEFAULT["start"] + 30 * int(n[1:]) + 40 * int(w[1:])
    return DEFAULT.get(_base(key), 60)


def begin(key: str) -> dict:
    job = {"key": key, "t0": time.time(), "steps": [], "eta": total(key)}
    return job


def mark(step: str) -> None:
    """에이전트가 단계를 남길 때 호출(agent._t). 진행 중 작업이 없으면 무시."""
    job = CURRENT.get()
    if job is None:
        return
    job["steps"].append((step, time.time() - job["t0"]))


def status(job: dict) -> dict:
    """진행 화면용: 지난 시간·예상 총 시간·지금 단계."""
    el = time.time() - job["t0"]
    eta = job["eta"]
    label = ""
    if job["steps"]:
        step, at = job["steps"][-1]
        label = STEP_LABEL.get(step, "")
        n_gen = sum(1 for s, _ in job["steps"] if s == "regenerate")
        if n_gen:
            label = f"{label} ({n_gen + 1}번째)" if step in ("regenerate", "validate") else label
        rem = (_load().get("remain") or {}).get(f"{_base(job['key'])}|{step}") or []
        if len(rem) >= 2:
            eta = max(eta if el < eta else 0, at + statistics.median(rem))
    if el > eta:          # 예상을 넘기면 남은 시간을 조금씩 늘려 '곧'으로 보이게
        eta = el + 15
    return {"elapsed_sec": round(el), "eta_sec": round(eta), "stage": label}


def finish(job: dict) -> None:
    """성공한 작업의 실제 시간을 기록."""
    dur = time.time() - job["t0"]
    with _LOCK:
        d = _load()
        tot = d.setdefault("total", {})
        for k in (job["key"], _base(job["key"]) + ":*"):
            tot[k] = (tot.get(k) or [])[-(KEEP - 1):] + [round(dur, 1)]
        rem = d.setdefault("remain", {})
        seen = set()
        for step, at in reversed(job["steps"]):      # 같은 단계가 여러 번이면 마지막 것 기준
            if step in seen:
                continue
            seen.add(step)
            k = f"{_base(job['key'])}|{step}"
            rem[k] = (rem.get(k) or [])[-(KEEP - 1):] + [round(dur - at, 1)]
        _save(d)
