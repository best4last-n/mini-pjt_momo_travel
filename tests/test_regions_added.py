"""추가 지역(2026-11-27) 검사 — LLM·네트워크 없이 수집 결과와 등록 상태만 확인한다.

수집이 끝나 regions.json에서 enabled=true가 된 지역만 검사한다(덜 끝난 지역은 available_regions에서 빠져야 함).
"""
from __future__ import annotations

from collections import Counter
from datetime import date

import pytest

from src.shared import data
from src.mobility import hubs
from src.counsel import options
from src.planning import planner
from src.places import retrieve

ADDED = ["jeju", "gangneung", "jeonju", "tongyeong_geoje"]
MIN_PER_KIND = {"attraction": 25, "restaurant": 15, "lodging": 8}
DONE = [r for r in ADDED if r in data.available_regions()]


def test_disabled_region_not_available():
    """enabled=false인 지역은 places.json이 있어도 프리셋에 나오지 않는다."""
    cfg = data.regions_config()
    for r, c in cfg.items():
        if c.get("enabled", True) is False:
            assert r not in data.available_regions()


@pytest.mark.parametrize("rid", DONE)
def test_region_data(rid):
    assert rid in data.available_regions()
    pl = data.places(rid)
    kinds = Counter(p["kind"] for p in pl.values())
    for k, n in MIN_PER_KIND.items():
        assert kinds[k] >= n, (rid, k, kinds[k])
    assert all(p.get("summary") for p in pl.values())          # 요약 생성 완료
    assert sum(1 for p in pl.values() if p.get("images")) >= 0.8 * len(pl)      # 사진 보강 완료
    lat, lng = data.region_center(rid)
    assert 33 <= lat <= 38.7 and 124 <= lng <= 132
    assert any(p.get("must_include") for p in pl.values())
    assert rid in {r["id"] for r in options.all_options()["regions"]}
    assert rid in options.REGION_DESC and rid in hubs.REGION_HUBS


@pytest.mark.parametrize("rid", DONE)
def test_region_search_and_alias(rid):
    must = next(p for p in data.places(rid).values() if p.get("must_include"))
    hits = retrieve.search(must["title"], rid, k=3, use_vector=False)
    assert hits and all(h["metadata"]["region"] == rid for h in hits)
    assert any(v == rid for v in data.REGION_ALIASES.values())
    assert rid not in ("jeju",) or data.requires_flight(rid)


def test_no_disabled_docs_in_search():
    ok = set(data.available_regions())
    assert {d["metadata"]["region"] for d in retrieve._docs()} <= ok


def test_jeju_flight_only():
    if "jeju" not in DONE:
        pytest.skip("jeju 미완료")
    assert data.requires_flight("jeju") and not data.requires_flight("gyeongju")
    spec = planner.TripSpec(region="jeju", start=date(2026, 12, 12), nights=2, departure="서울",
                            long_transport="비행기", local_transport="렌터카")
    g = hubs.guide(spec, None)
    assert g["mode"] == "비행기" and "제주공항" in g["summary"]
    from src.counsel import agent
    _, _, _, err = agent._apply_change(spec, set(), "long_transport", "자차", None)
    assert err and "비행기" in err
