"""고속도로 소요가 과하게 잡히지 않는지(2026-11-27 사용자 지적) — 평균 혼잡은 고속도로에 일부만."""
from datetime import date

from src.mobility import roads


def test_express_not_inflated(monkeypatch):
    route = {"distance_km": 300.0, "duration_min": 200.0, "path": [[37.4, 127.1], [34.9, 127.5]],
             "roads": [{"ref": "1", "name": "경부고속도로", "km": 290.0, "min": 190.0, "bbox": [0, 0, 0, 0]},
                       {"ref": "", "name": "시내 도로", "km": 10.0, "min": 10.0, "bbox": [0, 0, 0, 0]}]}
    monkeypatch.setattr(roads, "osrm", lambda a, b: route)
    monkeypatch.setattr(roads, "its_key", lambda: None)
    monkeypatch.setattr(roads, "road_alerts", lambda path, d: {"events": [], "cautions": [], "delay_min": 0})
    roads._drive_cached.cache_clear()
    # 토요일 오전 하행(평균 혼잡 1.25)
    r = roads.drive((37.4, 127.1), (34.9, 127.5), date(2026, 11, 28), 8 * 60, True)
    ex = next(s for s in r["segments"] if s["road"] == "경부고속도로")
    city = next(s for s in r["segments"] if s["road"] == "시내 도로")
    assert ex["min"] <= round(190 * roads.EXPRESS_FACTOR * 1.07)      # 고속도로: 혼잡 25%만 반영
    assert city["min"] == round(10 * 1.25)                            # 시내 도로는 그대로
    assert r["rests"] == 1                                            # 2시간 30분마다 휴게 1회
    assert r["total_min"] <= 200                                      # OSRM 200분보다 크게 잡지 않음
