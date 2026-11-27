"""브이월드 우선·OSM 보조(2026-11-26) + 숙소 들르기(AGAIN:) 장소 찾기 회귀 테스트 — 네트워크 없이(monkeypatch).

G1 /geo/search: 브이월드 결과가 있으면 Nominatim·Photon을 부르지 않고, 비었을 때만 OSM으로 보조
G2 placesearch.search: 브이월드 후보가 있으면 OSM(Photon·Nominatim)을 부르지 않고 sources에도 안 올림
G3 /geo/reverse: 브이월드 주소 우선, 없으면 Nominatim
G4 'AGAIN:<숙소 id>:<4자리>'는 원래 숙소 장소로 찾는다(brochure 내보내기)
"""
import json

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.export import export
from src.places import geo, placesearch, retrieve, tourlive

c = TestClient(app)

VW_ITEM = json.dumps({"id": "1", "title": "수원역", "point": {"x": "127.0016", "y": "37.2656"},
                      "address": {"road": "경기도 수원시 팔달구 덕영대로 924"}}, ensure_ascii=False)
NOM_ROW = {"lat": "37.27", "lon": "127.0", "address": {"state": "경기도", "city": "수원시", "borough": "팔달구"}}


def _boom(*a, **k):
    raise AssertionError("OSM을 불러서는 안 됨")


def test_geo_search_prefers_vworld(monkeypatch):
    monkeypatch.setattr(placesearch, "vworld_search", lambda *a, **k: (VW_ITEM,))
    monkeypatch.setattr(geo, "_nominatim", _boom)
    monkeypatch.setattr(geo, "_photon", _boom)
    res = geo.search("수원역")
    assert res and all(r["source"] == "브이월드" for r in res)
    assert set(res[0]) >= {"label", "sido", "lat", "lng", "source"}
    assert res[0]["sido"] == "경기"


def test_geo_search_falls_back_to_osm_only_when_vworld_empty(monkeypatch):
    monkeypatch.setattr(placesearch, "vworld_search", lambda *a, **k: ())
    calls = []
    monkeypatch.setattr(geo, "_nominatim", lambda q: calls.append(q) or json.dumps([NOM_ROW]))
    monkeypatch.setattr(geo, "_photon", lambda q: "[]")
    res = geo.search("팔달구청")
    assert calls and res and res[0]["source"] == "OpenStreetMap"


def _stub_search(monkeypatch, vw_rows, osm_called):
    monkeypatch.setattr(placesearch, "vworld_key", lambda: "k")
    monkeypatch.setattr(placesearch, "_warm", lambda *a, **k: None)
    monkeypatch.setattr(retrieve, "search", lambda *a, **k: [])
    monkeypatch.setattr(tourlive, "search_candidates", lambda *a, **k: [])
    monkeypatch.setattr(placesearch, "_tour_nation", lambda v: ())
    monkeypatch.setattr(placesearch, "extra_sources", lambda *a, **k: [])
    monkeypatch.setattr(placesearch, "vworld_sources", lambda *a, **k: list(vw_rows))
    monkeypatch.setattr(placesearch, "_photon", lambda *a, **k: osm_called.append("photon") or ())
    monkeypatch.setattr(placesearch, "_nominatim", lambda *a, **k: osm_called.append("nominatim") or ())
    monkeypatch.setattr(placesearch, "_wikimedia_images", lambda refs: {})
    monkeypatch.setattr(placesearch, "kakao_key", lambda: None)
    monkeypatch.setattr(placesearch, "naver_keys", lambda: None)


def test_placesearch_skips_osm_when_vworld_has_results(monkeypatch):
    osm = []
    cand = placesearch._cand("브이월드", "스타벅스 순천점", 34.95, 127.49, "전남 순천시", (34.95, 127.49), 0,
                            category="restaurant", key="vworld:1")
    _stub_search(monkeypatch, [cand], osm)
    r = placesearch.search("스타벅스 순천점", "suncheon_gurye")
    assert osm == []
    assert "OpenStreetMap" not in r["sources"] and "브이월드" in r["sources"]


def test_placesearch_uses_osm_when_vworld_empty(monkeypatch):
    osm = []
    _stub_search(monkeypatch, [], osm)
    r = placesearch.search("없는가게이름", "suncheon_gurye")
    assert osm and "OpenStreetMap" in r["sources"]


def test_geo_reverse_prefers_vworld(monkeypatch):
    monkeypatch.setattr(placesearch, "vworld_address", lambda lat, lng: "경기도 수원시 팔달구 덕영대로 924")
    monkeypatch.setattr(geo, "_nominatim_reverse", _boom)
    p = c.get("/geo/reverse", params={"lat": 37.2656, "lng": 127.0016}).json()["place"]
    assert p["source"] == "브이월드" and p["sido"] == "경기" and p["lat"] == 37.27
    assert "수원시" in p["label"]


def test_geo_reverse_falls_back_and_rejects_outside_korea(monkeypatch):
    monkeypatch.setattr(placesearch, "vworld_address", lambda lat, lng: "")
    monkeypatch.setattr(geo, "_nominatim_reverse", lambda lat, lng: json.dumps({"address": NOM_ROW["address"]}))
    p = c.get("/geo/reverse", params={"lat": 37.27, "lng": 127.0}).json()["place"]
    assert p and p["source"] == "OpenStreetMap"
    assert c.get("/geo/reverse", params={"lat": 10, "lng": 10}).json()["place"] is None


def test_again_id_resolves_to_base_lodging():
    plan = {"places": [{"id": "3076322", "name": "호텔라움", "images": [{"url": "u"}]}]}
    places = export._places(plan)
    assert places.get("AGAIN:3076322:ab12")["name"] == "호텔라움"
    assert places.get("3076322")["images"]
    assert places.get("AGAIN:없는곳:ab12") is None
