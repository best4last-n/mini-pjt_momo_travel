"""단일 파일 HTML 내보내기: CSS·JS·Leaflet·지도 타일·사진 인라인, OSM·키·서버 경로 없음 (네트워크는 모두 가짜)."""
import io
import json
import re
from pathlib import Path

import pytest

from src.api import main
from src.export import export_assets, map_bake

STATIC = Path(main.STATIC)
FAKE_KEY = "TESTKEY-not-a-real-key-12345"


def _png(color=(200, 220, 180)) -> bytes:
    from PIL import Image
    b = io.BytesIO()
    Image.new("RGB", (256, 256), color).save(b, "PNG")
    return b.getvalue()


@pytest.fixture
def export(monkeypatch):
    monkeypatch.setenv("VWORLD_API_KEY", FAKE_KEY)
    monkeypatch.setattr(main, "_vworld_tile_bytes", lambda layer, z, y, x: _png())
    monkeypatch.setattr(export_assets, "_photo", lambda u: "data:image/jpeg;base64,/9j/AAAA")
    monkeypatch.setattr(export_assets, "leaflet_assets", lambda: ("/*leaflet-css*/.leaflet-pane{}", "window.L={};/*leaflet-js*/"))
    plan = json.loads((STATIC / "sample_plan.json").read_text(encoding="utf-8"))
    payload = {"title": "테스트 · 초안", "plan": plan, "view": True, "session_id": "t1"}
    return main._build_export("view-t1", payload)


def test_assets_inlined_no_server_refs(export):
    assert "--color-" in export or "--font-body" in export          # tokens.css 인라인
    assert "/*leaflet-css*/" in export and "/*leaflet-js*/" in export
    assert not re.search(r'(?:href|src)="/static/', export)
    head = export.split("<body")[0]
    assert "<link" not in head
    assert not re.search(r'<script[^>]+\ssrc=', export.split("<script>")[0] + export[-400:])
    assert "fonts.googleapis" not in export and "unpkg.com" not in export
    assert 'href="/"' not in export


def test_no_osm_no_key(export):
    assert "{s}.tile.openstreetmap.org" in export        # app.js 의 일반 모드 코드에만 있고
    assert "tile.openstreetmap.org/" not in export.replace("{s}.tile.openstreetmap.org", "")
    assert FAKE_KEY not in export and "api.vworld.kr" not in export.split("window.MOMO_BOOT")[1][:50]


def test_baked_tiles_and_photos(export):
    boot = export[export.index('window.MOMO_BOOT'):]
    assert '"export":true' in boot and '"layer":"Base"' in boot
    assert '"t":{"' in boot and "data:image/" in boot
    assert not re.search(r'"(?:url|thumb|image_url)":"https?://tong', export)   # 사진 원격 주소 없음


def test_bake_failure_marks_failed(monkeypatch):
    plan = json.loads((STATIC / "sample_plan.json").read_text(encoding="utf-8"))
    assert map_bake.bake_tiles(plan, lambda *a: None) is None


def test_view_bounds_and_budget():
    plan = json.loads((STATIC / "sample_plan.json").read_text(encoding="utf-8"))
    v = map_bake.view_bounds(plan)
    assert "all" in v
    items, _ = map_bake.plan_tiles(v)
    assert 0 < len(items) <= 2000
