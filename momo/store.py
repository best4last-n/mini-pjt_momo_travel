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
