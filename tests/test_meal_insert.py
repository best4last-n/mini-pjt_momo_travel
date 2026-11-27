"""'+ 식사' 끼워 넣기(2026-11-26): 일정 사이에 넣은 곳을 식사/카페로 분류해 type=meal로 — LLM·네트워크 없이."""
from datetime import date

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.counsel import agent
from src.shared import data
from src.planning import planner
from src.memory import store
from src.planning.schemas import DayPick, PickItem, PlanPick

c = TestClient(app)
REGION = "suncheon_gurye"


def _id(title):
    return next(pid for pid, p in data.places(REGION).items() if p["title"].startswith(title))


@pytest.fixture()
def db(tmp_path, monkeypatch):
    f = tmp_path / "m.sqlite"
    monkeypatch.setattr(store, "STATE_DB", f)
    monkeypatch.setattr(agent, "STATE_DB", f)
    monkeypatch.setattr(agent, "_GRAPH", None)
    yield f
    agent._GRAPH = None


def _seed(sid):
    spec = planner.TripSpec(region=REGION, start=date(2026, 11, 28), nights=0)
    rows = [[PickItem(place_id=_id("사성암"), stay_minutes=60, reason="r"),
             PickItem(place_id=_id("화엄사"), stay_minutes=60, reason="r")]]
    items = planner.schedule(spec, PlanPick(days=[DayPick(day=1, items=rows[0])]), {})
    agent.graph().update_state({"configurable": {"thread_id": sid}}, {
        "itinerary": items, "spec": agent.spec_to_dict(spec), "draft_version": "v1", "violations": [],
        "session_id": sid}, as_node="guard_out")


def _edit(sid, edit):
    return c.post("/query", json={"question": "편집", "session_id": sid, "action": "edit", "edit": edit}).json()


def _state(sid):
    return agent.graph().get_state({"configurable": {"thread_id": sid}}).values


@pytest.mark.parametrize("kind", ["식사", "카페"])
def test_insert_with_kind_becomes_meal(db, kind):
    _seed("mi")
    pid = next(p for p, v in data.places(REGION).items()
               if v.get("kind") != "restaurant" and p not in (_id("사성암"), _id("화엄사")) and v.get("lat"))
    _edit("mi", {"op": "add", "place_id": pid, "after": _id("사성암"), "insert": True, "kind": kind})
    st = _state("mi")
    it = next(x for x in st["itinerary"] if x.get("place_id") == pid)
    assert it["type"] == "meal"
    assert agent.spec_from_dict(st["spec"]).kind_override.get(pid) == kind
    names = [x["place_name"] for x in st["itinerary"] if x["type"] != "move"]
    assert names.index(next(n for n in names if n.startswith("사성암"))) < names.index(it["place_name"]) < names.index("화엄사")


def test_insert_without_kind_stays_place(db):
    _seed("mi2")
    pid = next(p for p, v in data.places(REGION).items()
               if v.get("kind") != "restaurant" and p not in (_id("사성암"), _id("화엄사")) and v.get("lat"))
    _edit("mi2", {"op": "add", "place_id": pid, "after": _id("사성암"), "insert": True})
    it = next(x for x in _state("mi2")["itinerary"] if x.get("place_id") == pid)
    assert it["type"] == "place"
