"""관광공사 실시간 조회가 수집 모듈을 제 위치(src/ingest)에서 불러오는지 — src/ 재구성 때 옛 scripts/ 경로가 남아
실시간 장소 편입이 조용히 실패하던 문제(2026-11-27). 네트워크 호출 없이 모듈 로딩만 확인."""
from src.places import tourlive


def test_tourapi_client_module_loads():
    tourlive._client.cache_clear()
    mod = tourlive._client()
    assert callable(mod.service_key) and callable(mod.call)          # placesearch·alternatives·stopovers가 쓰는 함수


def test_enrich_module_loads():
    mod = tourlive._enrich_module()
    assert mod.__name__.endswith("enrich_places")
