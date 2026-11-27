"""테스트는 네트워크 없이: OSRM 도로 경로는 로컬 캐시(data/route_cache.json)에 있을 때만 쓰고, 없으면 직선 추정으로 떨어진다
(들를 곳 구간마다 roads.drive를 부르게 되면서 테스트가 외부 서버를 두드리지 않게)."""
import pytest

from src.mobility import roads


@pytest.fixture(autouse=True)
def _osrm_cache_only(monkeypatch):
    def cache_only(a, b):
        key = f"{a[0]:.2f},{a[1]:.2f}>{b[0]:.2f},{b[1]:.2f}"
        c = roads._load().get(key)
        return c if c and c.get("roads") is not None else None
    monkeypatch.setattr(roads, "osrm", cache_only)
