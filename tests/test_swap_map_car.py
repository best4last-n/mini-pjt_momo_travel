"""장소 바꾸기(대안·replace), 지도 종류, 자차 경로 안내 — 외부 호출은 가짜로 바꿔 LLM·네트워크 없이 검사."""
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.main import app
from momo import agent, alternatives, data, hubs, planner, roads, store
from momo.schemas import DayPick, PickItem, PlanPick

c = TestClient(app)
SAT = date(2026, 12, 12)


def _id(title):
    return next(pid for pid, p in data.places("gyeongju").items() if p["title"].startswith(title))


def test_kind_label_and_swap_hint():
    assert alternatives.kind_label({"title": "라플라타", "vworld_category": "카페"}) == "카페"
    assert alternatives.kind_label({"title": "일공공일 coffee"}) == "카페"
    assert alternatives.kind_label({"title": "황남한옥펜션"}) == "숙소"
    assert alternatives.kind_label({"title": "교동집밥", "kind": "restaurant"}) == "식당"
    assert alternatives.kind_label({"title": "불국사", "kind": "attraction"}) == "관광지"
    assert alternatives.swap_hint({"title": "불국사", "kind": "attraction"}, "place") is False
    assert alternatives.swap_hint({"title": "어떤 전망대", "source": "사용자 지정"}, "place") is True


@pytest.fixture()
def seeded(tmp_path, monkeypatch):
    db = tmp_path / "s.sqlite"
    monkeypatch.setattr(store, "STATE_DB", db)
    monkeypatch.setattr(agent, "STATE_DB", db)
    monkeypatch.setattr(agent, "_GRAPH", None)
    monkeypatch.setattr(alternatives, "_tour_nearby", lambda *a, **k: ())          # 외부 호출 없이 수집 데이터만
    monkeypatch.setattr(alternatives.placesearch, "vworld_search", lambda *a, **k: ())
    monkeypatch.setattr(alternatives, "popular_ranks", lambda region: {})
    spec = planner.TripSpec(region="gyeongju", start=SAT, nights=0)
    pick = PlanPick(days=[DayPick(day=1, items=[
        PickItem(place_id=_id("경주 첨성대"), stay_minutes=60, reason="r"),
        PickItem(place_id=_id("교동집밥"), stay_minutes=60, role="lunch", reason="r"),
        PickItem(place_id=_id("천마총"), stay_minutes=90, reason="r")])])
    items = planner.schedule(spec, pick, {})
    agent.graph().update_state({"configurable": {"thread_id": "sw"}}, {
        "itinerary": items, "spec": agent.spec_to_dict(spec), "draft_version": "v1", "violations": [],
        "session_id": "sw"}, as_node="guard_out")
    yield items
    agent._GRAPH = None


def test_alternatives_ranked_and_replace_keeps_slot(seeded):
    old = _id("교동집밥")
    a = c.get("/places/alternatives", params={"session_id": "sw", "place_id": old}).json()
    assert a["target"]["kind_label"] == "식당" and 0 < len(a["items"]) <= 20 and a["has_reviews"] is False
    assert [x["rank"] for x in a["items"]] == list(range(1, len(a["items"]) + 1))
    assert all(x["score"] == sum(x["parts"].values()) for x in a["items"])
    assert a["items"] == sorted(a["items"], key=lambda x: (-x["score"], x["distance_km"]))
    assert old not in {x["choice"].get("place_id") for x in a["items"]}
    new = a["items"][0]
    r = agent.handle_query("바꾸기", "sw", "edit", edit={"op": "replace", "target": old, "choice": new["choice"]})
    names = [it["place_name"] for it in r["itinerary"] if it["type"] != "move"]
    assert new["name"] in names and "교동집밥" not in names
    assert names.index(new["name"]) == 1                                      # 같은 자리(2번째) 유지
    assert any("자리에" in ch for ch in r["changes"])


def test_alternatives_needs_plan():
    assert c.get("/places/alternatives", params={"session_id": "nope", "place_id": "1"}).status_code == 400


def test_map_layers_and_tile_guard():
    j = c.get("/map/layers").json()
    assert j["layers"][0]["id"] == "osm" and j["default"] in {l["id"] for l in j["layers"]}
    assert all(l["url"].startswith(("/tiles/vworld/", "https://")) for l in j["layers"])
    assert "key" not in str(j).lower()                                         # 키는 화면에 나가지 않음
    assert c.get("/tiles/vworld/Evil/12/1/1").status_code == 204
    assert c.get("/tiles/vworld/Base/3/1/1").status_code == 204


def test_car_guide_uses_road_not_rail(monkeypatch):
    monkeypatch.setattr(roads, "osrm", lambda a, b: {"distance_km": 290.0, "duration_min": 215,
                                                     "path": [[37.39, 127.11], [35.2, 127.46]]})
    monkeypatch.setattr(roads, "today", lambda: date(2000, 1, 1))
    spec = planner.TripSpec(region="suncheon_gurye", start=SAT, nights=1, long_transport="자차",
                            departure="경기", origin_coords=[37.39, 127.11])
    g = hubs.guide(spec, 240)
    assert g["options"] == [] and g["legs"][0]["type"] == "drive" and g["route_path"]
    assert all(o.get("best") is False for o in g.get("alternatives") or [])     # 열차는 참고로만
    dr = roads.drive((37.39, 127.11), (35.2, 127.46), SAT, 8 * 60, True)       # 토요일 오전 하행
    assert dr["factor"] == 1.25 and dr["total_min"] >= 215 * 1.25


def test_undo_restores_previous_order_and_reconfirm_flag(seeded):
    a = agent.handle_query("", "sw", "approve", draft_version="v1")
    assert a["status"] == "확정" and a["needs_reconfirm"] is False
    before = [it["place_name"] for it in seeded if it["type"] != "move"]
    r = agent.handle_query("순서", "sw", "edit", edit={"op": "order", "days": [{"day": 1, "place_ids": [
        _id("천마총"), _id("교동집밥"), _id("경주 첨성대")]}]})
    assert [it["place_name"] for it in r["itinerary"] if it["type"] != "move"][0].startswith("천마총")
    assert r["needs_reconfirm"] is True and r["plan_id"] == a["plan_id"] and r["undo_count"] == 1
    u = c.post("/trips/sw/undo").json()
    names = [s["name"] for d in u["plan"]["itinerary"] for s in d["stops"]]
    assert names == before and u["undo_count"] == 0
    assert c.post("/trips/sw/undo").status_code == 404


def test_whole_trip_optimize_moves_across_days_respecting_rules():
    """2일 일정에서 서로 먼 곳이 날짜가 엇갈려 있으면 전체 최적화가 날짜를 바꿔 묶는다(휴무·상한 지킴)."""
    spec = planner.TripSpec(region="gyeongju", start=SAT, nights=1, intensity="보통")
    a1, a2 = _id("경주 첨성대"), _id("천마총")             # 시내
    far = [pid for pid, p in data.places("gyeongju").items() if p["kind"] == "attraction" and p.get("lat")
           and abs(p["lat"] - data.places("gyeongju")[a1]["lat"]) + abs(p["lng"] - data.places("gyeongju")[a1]["lng"]) > 0.15][:2]
    days = [[PickItem(place_id=a1, stay_minutes=60, reason="r"), PickItem(place_id=far[0], stay_minutes=60, reason="r")],
            [PickItem(place_id=far[1], stay_minutes=60, reason="r"), PickItem(place_id=a2, stay_minutes=60, reason="r")]]
    rows, moved = agent.route_whole(spec, days)
    assert sum(len([x for x in r if x.role == "place"]) for r in rows) == 4
    assert all(len([x for x in r if x.role == "place"]) <= 4 for r in rows)
    assert {x.place_id for x in rows[0]} in ({a1, a2}, set(far))                  # 가까운 곳끼리 같은 날
    p = agent.route_proposal(spec, days, "all")
    assert p["scope"] == "all" and p["total_after_km"] <= p["total_before_km"]
