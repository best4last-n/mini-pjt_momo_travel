"""상태 DB 비대화 방지(2026-11-27, 483MB): 세션별 최근 체크포인트만 남기고 최신 상태는 그대로 읽힌다."""
import sqlite3
from typing import TypedDict

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, StateGraph

from src.memory import store


class S(TypedDict):
    n: int


def _graph(conn):
    g = StateGraph(S)
    g.add_node("inc", lambda s: {"n": s["n"] + 1})
    g.set_entry_point("inc")
    g.add_edge("inc", END)
    return g.compile(checkpointer=SqliteSaver(conn))


def test_prune_keeps_latest_state_per_thread(tmp_path, monkeypatch):
    f = tmp_path / "state.sqlite"
    monkeypatch.setattr(store, "STATE_DB", f)
    conn = sqlite3.connect(f, check_same_thread=False)
    g = _graph(conn)
    for tid in ("a", "b"):
        for i in range(5):
            g.invoke({"n": i * 10}, {"configurable": {"thread_id": tid}})
    before = conn.execute("SELECT thread_id, count(*) FROM checkpoints GROUP BY thread_id").fetchall()
    assert all(n > 2 for _, n in before)

    assert store.prune_checkpoints("a") > 0                       # 한 세션만
    counts = dict(conn.execute("SELECT thread_id, count(*) FROM checkpoints GROUP BY thread_id").fetchall())
    assert counts["a"] == store.CHECKPOINT_KEEP and counts["b"] > store.CHECKPOINT_KEEP

    store.prune_checkpoints()                                     # 전체
    counts = dict(conn.execute("SELECT thread_id, count(*) FROM checkpoints GROUP BY thread_id").fetchall())
    assert counts == {"a": store.CHECKPOINT_KEEP, "b": store.CHECKPOINT_KEEP}
    orphan = conn.execute("""SELECT count(*) FROM writes w WHERE NOT EXISTS (SELECT 1 FROM checkpoints k
                             WHERE k.thread_id=w.thread_id AND k.checkpoint_ns=w.checkpoint_ns AND k.checkpoint_id=w.checkpoint_id)""").fetchone()[0]
    assert orphan == 0
    for tid in ("a", "b"):                                        # 최신 상태는 그대로, 이어서 실행도 됨
        assert g.get_state({"configurable": {"thread_id": tid}}).values == {"n": 41}
        assert g.invoke({"n": 100}, {"configurable": {"thread_id": tid}}) == {"n": 101}


def test_prune_on_empty_db_is_noop(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "STATE_DB", tmp_path / "empty.sqlite")
    assert store.prune_checkpoints() == 0
