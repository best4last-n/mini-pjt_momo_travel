"""확정 계획 저장 (save_plan) — 승인 노드만 호출. SQLite, 만료 없음. .ics 생성."""
from __future__ import annotations

import json
import secrets
import sqlite3
from datetime import datetime, timedelta
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


def _mconn() -> sqlite3.Connection:
    c = _conn()
    c.execute("""CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, role TEXT, kind TEXT, text TEXT,
        status TEXT, html TEXT, draft_version TEXT, created TEXT)""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_messages_sid ON messages(session_id, id)")
    cols = {r[1] for r in c.execute("PRAGMA table_info(messages)")}
    if "bump" not in cols:      # 새 계획 버전 시작 표시(여행 시작·재수립·크게 바뀜) — 작은 편집·되돌리기는 같은 버전
        c.execute("ALTER TABLE messages ADD COLUMN bump INTEGER DEFAULT 0")
    return c


def log_message(session_id: str, role: str, kind: str, text: str, status: str = "", html: str = "",
                draft_version: str | None = None, created: str | None = None, bump: bool = False) -> None:
    """상담 기록 한 줄(사용자 요청·모모 답변). 개인정보는 호출 전에 가려 둔다."""
    with _mconn() as c:
        c.execute("INSERT INTO messages (session_id, role, kind, text, status, html, draft_version, created, bump) VALUES (?,?,?,?,?,?,?,?,?)",
                  (session_id, role, kind, (text or "")[:4000], status or "", (html or "")[:200000], draft_version,
                   created or datetime.now(KST).isoformat(timespec="seconds"), 1 if bump else 0))


def list_messages(session_id: str, limit: int = 300) -> list[dict]:
    with _mconn() as c:
        base = c.execute("SELECT COUNT(*) FROM messages WHERE session_id=? AND bump=1 AND id < COALESCE("
                         "(SELECT MIN(id) FROM (SELECT id FROM messages WHERE session_id=? ORDER BY id DESC LIMIT ?)), 0)",
                         (session_id, session_id, limit)).fetchone()[0]
        rows = c.execute("SELECT id, role, kind, text, status, html, draft_version, created, bump FROM messages "
                         "WHERE session_id=? ORDER BY id DESC LIMIT ?", (session_id, limit)).fetchall()
    out, no = [], base
    pending = []          # 새 버전 답변 앞의 사용자 요청도 그 버전에 속함
    for r in reversed(rows):
        if r[1] == "momo" and r[8]:
            no += 1
            for x in pending:
                x["plan_no"] = no
            pending = []
        row = {"id": r[0], "role": r[1], "kind": r[2], "text": r[3], "status": r[4], "html": r[5],
               "draft_version": r[6], "plan_no": max(1, no), "created": r[7], "new_version": bool(r[8])}
        if r[1] == "user":
            pending = [row]
        out.append(row)
    return out


def delete_messages(session_id: str, mid: int | None = None) -> list[int]:
    """'내 요청 기록' 삭제. mid가 있으면 그 요청과 바로 뒤 모모 답변(다음 요청 전까지)만, 없으면 이 여행의 기록 전체.
    일정(계획) 자체는 바뀌지 않는다. 전체 삭제 뒤에는 체크포인트 이력에서 다시 복원하지 않도록 표시를 남긴다."""
    with _mconn() as c:
        c.execute("CREATE TABLE IF NOT EXISTS msg_cleared (session_id TEXT PRIMARY KEY, cleared TEXT)")
        if mid is None:
            ids = [r[0] for r in c.execute("SELECT id FROM messages WHERE session_id=?", (session_id,))]
            c.execute("DELETE FROM messages WHERE session_id=?", (session_id,))
            c.execute("INSERT OR REPLACE INTO msg_cleared VALUES (?,?)", (session_id, datetime.now(KST).isoformat(timespec="seconds")))
            return ids
        row = c.execute("SELECT role FROM messages WHERE session_id=? AND id=?", (session_id, int(mid))).fetchone()
        if not row:
            return []
        ids = [int(mid)]
        if row[0] == "user":
            nxt = c.execute("SELECT MIN(id) FROM messages WHERE session_id=? AND role='user' AND id>?", (session_id, int(mid))).fetchone()[0]
            q = "SELECT id FROM messages WHERE session_id=? AND role!='user' AND id>?" + (" AND id<?" if nxt else "")
            ids += [r[0] for r in c.execute(q, (session_id, int(mid), *([nxt] if nxt else [])))]
        c.execute(f"DELETE FROM messages WHERE session_id=? AND id IN ({','.join('?' * len(ids))})", (session_id, *ids))
        c.execute("INSERT OR REPLACE INTO msg_cleared VALUES (?,?)", (session_id, datetime.now(KST).isoformat(timespec="seconds")))
        return ids


def last_message_id(session_id: str) -> int:
    with _mconn() as c:
        return c.execute("SELECT COALESCE(MAX(id), 0) FROM messages WHERE session_id=?", (session_id,)).fetchone()[0]


def delete_messages_after(session_id: str, mid: int) -> None:
    """중지된 요청이 남긴 기록 지우기(그 요청 시작 시점의 마지막 id 뒤)."""
    with _mconn() as c:
        c.execute("DELETE FROM messages WHERE session_id=? AND id>?", (session_id, int(mid)))


def messages_cleared(session_id: str) -> bool:
    with _mconn() as c:
        c.execute("CREATE TABLE IF NOT EXISTS msg_cleared (session_id TEXT PRIMARY KEY, cleared TEXT)")
        return c.execute("SELECT 1 FROM msg_cleared WHERE session_id=?", (session_id,)).fetchone() is not None


def save_diff(session_id: str, draft_version: str, diff: dict) -> None:
    with _conn() as c:
        c.execute("CREATE TABLE IF NOT EXISTS diffs (session_id TEXT, draft_version TEXT, diff TEXT, PRIMARY KEY(session_id, draft_version))")
        c.execute("INSERT OR REPLACE INTO diffs VALUES (?,?,?)", (session_id, draft_version, json.dumps(diff, ensure_ascii=False)))


def get_diff(session_id: str, draft_version: str | None) -> dict | None:
    if not draft_version:
        return None
    with _conn() as c:
        c.execute("CREATE TABLE IF NOT EXISTS diffs (session_id TEXT, draft_version TEXT, diff TEXT, PRIMARY KEY(session_id, draft_version))")
        row = c.execute("SELECT diff FROM diffs WHERE session_id=? AND draft_version=?", (session_id, draft_version)).fetchone()
    return json.loads(row[0]) if row else None


def plan_no(session_id: str) -> int:
    """지금까지 나온 계획 버전 수(v1, v2…) — 모모 답변 기록의 서로 다른 draft_version 수."""
    with _mconn() as c:
        return max(1, c.execute("SELECT COUNT(*) FROM messages WHERE session_id=? AND role='momo' AND bump=1",
                                (session_id,)).fetchone()[0])


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
        base = datetime(y, m, d, tzinfo=KST)          # 자정 넘은 시각(24:30 = 다음 날 00:30)도 그대로 더함
        ev = Event()
        ev.add("uid", f"{plan_id}-{it['seq']}@momo")
        ev.add("summary", it["place_name"])
        ev.add("dtstart", base + timedelta(hours=sh, minutes=sm))
        ev.add("dtend", base + timedelta(hours=eh, minutes=em))
        ev.add("dtstamp", datetime.now(KST))
        desc = [it.get("reason") or "", f"출처: {it.get('source')}"]
        if it.get("hours_status") == "확인 필요":
            desc.append("운영시간 확인 필요")
        ev.add("description", "\n".join(x for x in desc if x))
        cal.add_component(ev)
    return cal.to_ical()
