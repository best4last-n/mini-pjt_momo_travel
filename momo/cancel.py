"""요청 중지·취소(사용자 요청 2026-11-26): 백그라운드 작업마다 취소 신호를 두고, LLM 호출·재생성·장소 찾기 직전에 확인한다.
진행 중인 LLM 호출 하나는 끊을 수 없어서, 그 호출이 끝나는 대로(보통 수 초~30초 안) 멈춘다. 되돌리기는 app/main.py의 작업 실행기가 한다."""
from __future__ import annotations

import threading
from contextvars import ContextVar

CURRENT: ContextVar[threading.Event | None] = ContextVar("momo_cancel", default=None)


class JobCancelled(Exception):
    """사용자가 요청을 중지함."""


def check() -> None:
    ev = CURRENT.get()
    if ev is not None and ev.is_set():
        raise JobCancelled()
