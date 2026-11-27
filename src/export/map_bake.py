"""HTML 내보내기용 지도 굽기 — 브이월드 일반지도(Base) 타일을 내려받아 data URI로 파일에 넣는다.

내려받은 파일(file://)에는 서버가 없어서 /tiles 프록시도, 키도 쓸 수 없다. OSM 타일은 Referer 없는 요청을
막는다("Access Blocked"). 그래서 내보낼 때 서버가 지도 화면(전체·일자별·가는 길)이 덮는 영역의
타일을 미리 받아 작은 JPEG/PNG로 줄여 JS 객체에 넣는다. 키·키가 들어간 URL은 결과에 나오지 않는다.
"""
from __future__ import annotations

import base64
import io
import math
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

LAYER = "Base"
Z_MIN, Z_MAX = 6, 19          # 브이월드가 주는 줌 범위
FIT_MAX = 14                  # 화면 맞춤 최대 줌(app.js fitBounds maxZoom 과 같게)
VIEW_W, VIEW_H = 264, 204     # 가장 작은 지도(휴대폰) 화면에서 패딩을 뺀 크기 → 안전하게 낮은 줌부터 굽는다
BUDGET_BYTES = 6_000_000      # data URI 전체 글자 수 상한
MAX_TILES = 900
TIME_BUDGET_S = 75


def _isdrive(g) -> bool:
    return bool(g) and (g.get("mode") == "자차" or any((l or {}).get("type") == "drive" for l in g.get("legs") or []))


def _pt(lat, lng):
    try:
        a, b = float(lat), float(lng)
    except (TypeError, ValueError):
        return None
    return (a, b) if -90 <= a <= 90 and -180 <= b <= 180 and not (a == 0 and b == 0) else None


def view_bounds(plan: dict) -> dict[str, list[tuple[float, float]]]:
    """app.js renderMap 과 같은 규칙으로 지도 화면별 좌표 목록(all·일자·route)을 만든다."""
    places = {str(p.get("id")): p for p in (plan.get("places") or [])}
    its = plan.get("itinerary") or []
    views: dict[str, list] = {}
    allp: list = []
    for d in its:
        pts: list = []
        for i, s in enumerate(d.get("stops") or []):
            if i == 0:
                lg = next((l for l in (d.get("legs") or []) if (s.get("place_id") is not None and str(l.get("to")) == str(s.get("place_id")))
                           or (l.get("to_name") and l.get("to_name") == s.get("name"))), None)
                path = (lg or {}).get("path") or []
                c = _pt(path[0][0], path[0][1]) if len(path) > 1 and len(path[0]) >= 2 else None
                if c:
                    pts.append(c)
            c = _pt(s.get("lat"), s.get("lng"))
            if not c:
                p = places.get(str(s.get("place_id")))
                c = _pt(p.get("lat"), p.get("lng")) if p else None
            if c:
                pts.append(c)
        if pts:
            views[f"d{d.get('day')}"] = pts
            allp += pts
    if allp:
        views["all"] = allp
    # 가는 길(출발·도착 집 + 자차 경로)
    t = plan.get("trip") or {}
    op = t.get("origin_point") or {}
    ep = t.get("end_point") or op
    st = (its[0].get("start_point") if its else None) or op
    en = (its[-1].get("end_point") if its else None) or ep
    rb: list = []
    for pt in (st, en):
        c = _pt((pt or {}).get("lat"), (pt or {}).get("lng"))
        if c:
            rb.append(c)
    g = plan.get("transport_guide") or {}
    if _isdrive(g):
        for q in (g.get("route_path") or []):
            c = _pt(q[0], q[1]) if len(q) >= 2 else None
            if c:
                rb.append(c)
    if len(rb) >= 2:
        views["route"] = rb
    return views


def _merc_y(lat: float) -> float:
    s = math.sin(math.radians(max(-85.0, min(85.0, lat))))
    return 0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)


def _tile_xy(lat: float, lng: float, z: int) -> tuple[int, int]:
    n = 2 ** z
    x = int(math.floor((lng + 180) / 360 * n))
    y = int(math.floor(_merc_y(lat) * n))
    return max(0, min(n - 1, x)), max(0, min(n - 1, y))


def fit_zoom(pts: list) -> int:
    lats = [p[0] for p in pts]
    lngs = [p[1] for p in pts]
    dx = max((max(lngs) - min(lngs)) / 360, 1e-9)
    dy = max(abs(_merc_y(min(lats)) - _merc_y(max(lats))), 1e-9)
    z = math.floor(math.log2(min(VIEW_W / (dx * 256), VIEW_H / (dy * 256))))
    return max(Z_MIN, min(FIT_MAX, z))


def _grow(pts: list, frac: float):
    lats = [p[0] for p in pts]
    lngs = [p[1] for p in pts]
    dlat = max(max(lats) - min(lats), 0.004) * frac
    dlng = max(max(lngs) - min(lngs), 0.004) * frac
    return min(lats) - dlat, min(lngs) - dlng, max(lats) + dlat, max(lngs) + dlng


def plan_tiles(views: dict) -> tuple[list[tuple[int, int, int, int]], list]:
    """(우선순위, z, x, y) 목록(중복 제거)과 전체 영역 bounds. 우선순위가 낮을수록 먼저 받는다."""
    seen: dict[tuple[int, int, int], int] = {}
    total = []
    for name, pts in views.items():
        z0 = fit_zoom(pts)
        total += pts
        levels = [(z0, 0, 0.6), (z0 + 1, 1, 0.5), (z0 + 2, 2, 0.35), (z0 + 3, 3, 0.2)]
        if name in ("all", "route") and z0 - 1 >= Z_MIN:
            levels.insert(0, (z0 - 1, 0, 0.5))
        if name == "route":
            levels = [lv for lv in levels if lv[1] < 2]
        for z, pr, frac in levels:
            if z > Z_MAX:
                continue
            s, w, n, e = _grow(pts, frac)
            x0, y1 = _tile_xy(s, w, z)
            x1, y0 = _tile_xy(n, e, z)
            for x in range(x0, x1 + 1):
                for y in range(y0, y1 + 1):
                    k = (z, x, y)
                    if k not in seen or pr < seen[k]:
                        seen[k] = pr
    items = sorted(((pr, z, x, y) for (z, x, y), pr in seen.items()), key=lambda t: (t[0], t[1], t[2], t[3]))
    return items, (_grow(total, 0.45) if total else None)


def _shrink(raw: bytes) -> tuple[str, bytes]:
    """타일을 더 작은 JPEG/팔레트 PNG로(Pillow가 없거나 실패하면 원본)."""
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(raw)).convert("RGB")
        a = io.BytesIO()
        im.save(a, "JPEG", quality=68, optimize=True)
        b = io.BytesIO()
        im.quantize(colors=48, method=Image.Quantize.MEDIANCUT).save(b, "PNG", optimize=True)
        return ("image/jpeg", a.getvalue()) if a.tell() <= b.tell() else ("image/png", b.getvalue())
    except Exception:
        return ("image/png" if raw[:4] == b"\x89PNG" else "image/jpeg"), raw


def bake_tiles(plan: dict, fetch: Callable[[str, int, int, int], bytes | None]) -> dict | None:
    """{layer,min,max,bounds,attribution,t:{"z/x/y": dataURI}} — 하나도 못 받으면 None."""
    views = view_bounds(plan)
    if not views:
        return None
    items, bounds = plan_tiles(views)
    items = items[:MAX_TILES]
    t0 = time.monotonic()
    out: dict[str, str] = {}
    size = 0

    def work(it):
        _, z, x, y = it
        if time.monotonic() - t0 > TIME_BUDGET_S:
            return it, None
        try:
            raw = fetch(LAYER, z, y, x)   # 서버 프록시와 같은 함수(캐시·키 처리)
        except Exception:
            raw = None
        return it, (_shrink(raw) if raw else None)

    with ThreadPoolExecutor(max_workers=10) as ex:
        for pr in (0, 1, 2, 3):
            group = [it for it in items if it[0] == pr]
            if not group:
                continue
            for it, r in ex.map(work, group):
                if not r:
                    continue
                uri = f"data:{r[0]};base64," + base64.b64encode(r[1]).decode("ascii")
                if size + len(uri) > BUDGET_BYTES:
                    continue
                out[f"{it[1]}/{it[2]}/{it[3]}"] = uri
                size += len(uri)
    if not out:
        return None
    zs = [int(k.split("/")[0]) for k in out]
    s, w, n, e = bounds
    return {"layer": LAYER, "min": min(zs), "max": max(zs), "bounds": [[s, w], [n, e]],
            "attribution": "© 브이월드(국토교통부)", "t": out}
