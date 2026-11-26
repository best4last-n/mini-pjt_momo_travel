"""확정 계획 저장 (save_plan) — 승인 노드만 호출. SQLite, 만료 없음. .ics 생성."""
from __future__ import annotations

import json
import secrets
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

from .config import STATE_DB

KST = ZoneInfo("Asia/Seoul")


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(STATE_DB)
    c.execute("""CREATE TABLE IF NOT EXISTS plans (
        plan_id TEXT PRIMARY KEY, session_id TEXT UNIQUE, draft_version TEXT, payload TEXT,
        created TEXT, updated TEXT)""")
    return c


def save_plan(session_id: str, draft_version: str, payload: dict) -> str:
    """같은 세션은 같은 plan_id에 덮어쓴다(SERVICE.md 4-1 (3))."""
    now = datetime.now(KST).isoformat(timespec="seconds")
    with _conn() as c:
        row = c.execute("SELECT plan_id FROM plans WHERE session_id=?", (session_id,)).fetchone()
        if row:
            c.execute("UPDATE plans SET draft_version=?, payload=?, updated=? WHERE plan_id=?",
                      (draft_version, json.dumps(payload, ensure_ascii=False), now, row[0]))
            return row[0]
        pid = secrets.token_urlsafe(12)
        c.execute("INSERT INTO plans VALUES (?,?,?,?,?,?)",
                  (pid, session_id, draft_version, json.dumps(payload, ensure_ascii=False), now, now))
        return pid


def confirmed_version(session_id: str) -> str | None:
    """확정(저장)된 초안 버전 — 지금 초안과 다르면 '확정 후 수정됨'."""
    with _conn() as c:
        row = c.execute("SELECT draft_version FROM plans WHERE session_id=?", (session_id,)).fetchone()
    return row[0] if row else None


UNDO_MAX = 20


def _uconn() -> sqlite3.Connection:
    c = _conn()
    c.execute("CREATE TABLE IF NOT EXISTS undo (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, blob TEXT, created TEXT)")
    return c


def push_snapshot(session_id: str, values: dict) -> None:
    """편집 직전 상태(되돌리기용). 세션마다 최근 UNDO_MAX개만."""
    with _uconn() as c:
        c.execute("INSERT INTO undo (session_id, blob, created) VALUES (?,?,?)",
                  (session_id, json.dumps(values, ensure_ascii=False, default=str), datetime.now(KST).isoformat(timespec="seconds")))
        c.execute("DELETE FROM undo WHERE session_id=? AND id NOT IN (SELECT id FROM undo WHERE session_id=? ORDER BY id DESC LIMIT ?)",
                  (session_id, session_id, UNDO_MAX))


def pop_snapshot(session_id: str) -> dict | None:
    with _uconn() as c:
        row = c.execute("SELECT id, blob FROM undo WHERE session_id=? ORDER BY id DESC LIMIT 1", (session_id,)).fetchone()
        if not row:
            return None
        c.execute("DELETE FROM undo WHERE id=?", (row[0],))
    return json.loads(row[1])


def undo_count(session_id: str) -> int:
    with _uconn() as c:
        return c.execute("SELECT COUNT(*) FROM undo WHERE session_id=?", (session_id,)).fetchone()[0]


def _econn() -> sqlite3.Connection:
    c = _conn()
    c.execute("""CREATE TABLE IF NOT EXISTS expenses (
        id TEXT PRIMARY KEY, plan_id TEXT, date TEXT, category TEXT, label TEXT, amount INTEGER, memo TEXT, created TEXT)""")
    return c


def save_answer(session_id: str, html_frag: str, title: str) -> None:
    """최신 답변 HTML(새 창으로 보기용)."""
    with _conn() as c:
        c.execute("CREATE TABLE IF NOT EXISTS answers (session_id TEXT PRIMARY KEY, html TEXT, title TEXT, updated TEXT)")
        c.execute("INSERT OR REPLACE INTO answers VALUES (?,?,?,?)",
                  (session_id, html_frag, title, datetime.now(KST).isoformat(timespec="seconds")))


def get_answer(session_id: str) -> tuple[str, str] | None:
    with _conn() as c:
        c.execute("CREATE TABLE IF NOT EXISTS answers (session_id TEXT PRIMARY KEY, html TEXT, title TEXT, updated TEXT)")
        row = c.execute("SELECT html, title FROM answers WHERE session_id=?", (session_id,)).fetchone()
    return (row[0], row[1]) if row else None


def _tconn() -> sqlite3.Connection:
    c = _conn()
    c.execute("""CREATE TABLE IF NOT EXISTS trips (
        session_id TEXT PRIMARY KEY, title TEXT, region TEXT, start_date TEXT, nights INTEGER, status TEXT,
        plan_id TEXT, progress INTEGER, updated TEXT)""")
    return c


def upsert_trip(session_id: str, plan: dict, status: str, plan_id: str | None = None) -> None:
    """세운 계획 목록(초안·확정) — 언제든 다시 불러와 고칠 수 있게(SERVICE.md 3-6 (13))."""
    t = plan.get("trip") or {}
    now = datetime.now(KST).isoformat(timespec="seconds")
    with _tconn() as c:
        old = c.execute("SELECT plan_id, status FROM trips WHERE session_id=?", (session_id,)).fetchone()
        pid = plan_id or (old[0] if old else None)
        st = "확정" if status == "확정" else ("확정" if old and old[1] == "확정" and not plan_id and status != "승인 대기" else "초안")
        if old and old[1] == "확정" and status == "승인 대기":
            st = "수정 중"
        c.execute("INSERT OR REPLACE INTO trips VALUES (?,?,?,?,?,?,?,?,?)",
                  (session_id, t.get("title"), (t.get("regions") or [""])[0], t.get("start_date"), t.get("nights"),
                   st, pid, (t.get("progress") or {}).get("percent"), now))


def list_trips(ids: list[str]) -> list[dict]:
    if not ids:
        return []
    with _tconn() as c:
        q = ",".join("?" * len(ids))
        rows = c.execute(f"SELECT session_id, title, region, start_date, nights, status, plan_id, progress, updated "
                         f"FROM trips WHERE session_id IN ({q}) ORDER BY updated DESC", ids).fetchall()
    keys = ("session_id", "title", "region", "start_date", "nights", "status", "plan_id", "progress", "updated")
    return [dict(zip(keys, r)) for r in rows]


from .options import EXPENSE_CATEGORIES  # noqa: E402  (정산 분류는 options에서 관리)


def list_expenses(plan_id: str) -> list[dict]:
    with _econn() as c:
        rows = c.execute("SELECT id, date, category, label, amount, memo FROM expenses WHERE plan_id=? ORDER BY date, created",
                         (plan_id,)).fetchall()
    return [dict(zip(("id", "date", "category", "label", "amount", "memo"), r)) for r in rows]


def add_expense(plan_id: str, date: str, category: str, label: str, amount: int, memo: str = "") -> None:
    with _econn() as c:
        c.execute("INSERT INTO expenses VALUES (?,?,?,?,?,?,?,?)",
                  (secrets.token_urlsafe(6), plan_id, date, category, label, int(amount), memo,
                   datetime.now(KST).isoformat(timespec="seconds")))


def delete_expense(plan_id: str, eid: str) -> None:
    with _econn() as c:
        c.execute("DELETE FROM expenses WHERE plan_id=? AND id=?", (plan_id, eid))


def get_plan(plan_id: str) -> dict | None:
    with _conn() as c:
        row = c.execute("SELECT payload FROM plans WHERE plan_id=?", (plan_id,)).fetchone()
    return json.loads(row[0]) if row else None


def to_ics(plan_id: str, payload: dict) -> bytes:
    from icalendar import Calendar, Event
    cal = Calendar()
    cal.add("prodid", "-//Momo Travel Counselor//KO")
    cal.add("version", "2.0")
    cal.add("X-WR-CALNAME", payload.get("title", "모모 여행 일정"))
    for it in payload["itinerary"]:
        y, m, d = map(int, it["date"].split("-"))
        sh, sm = map(int, it["start_time"].split(":"))
        eh, em = map(int, it["end_time"].split(":"))
        ev = Event()
        ev.add("uid", f"{plan_id}-{it['seq']}@momo")
        ev.add("summary", it["place_name"])
        ev.add("dtstart", datetime(y, m, d, sh, sm, tzinfo=KST))
        ev.add("dtend", datetime(y, m, d, min(eh, 23), em if eh < 24 else 59, tzinfo=KST))
        ev.add("dtstamp", datetime.now(KST))
        desc = [it.get("reason") or "", f"출처: {it.get('source')}"]
        if it.get("hours_status") == "확인 필요":
            desc.append("운영시간 확인 필요")
        ev.add("description", "\n".join(x for x in desc if x))
        cal.add_component(ev)
    return cal.to_ical()
