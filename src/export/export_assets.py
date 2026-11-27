"""HTML 내보내기용 자산 인라인 — Leaflet(JS·CSS)·장소 사진을 파일 안으로 넣는다(외부 요청 0).

내려받은 HTML은 휴대폰에서 오프라인·샌드박스 뷰어로 열릴 수 있다. 그래서 CDN·원격 사진을 쓰지 않는다.
Leaflet은 처음 한 번만 내려받아 data/vendor 에 둔다.
"""
from __future__ import annotations

import base64
import copy
import io
import re
import time
from concurrent.futures import ThreadPoolExecutor

from src.shared.config import ROOT  # noqa: E402

VENDOR = ROOT / "data" / "vendor"
LEAFLET = "https://unpkg.com/leaflet@1.9.4/dist/"
BLANK = "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"
PHOTO_W, PHOTO_BUDGET, PHOTO_TIME = 480, 5_500_000, 40


def leaflet_assets() -> tuple[str, str] | None:
    """(css, js) 텍스트. 캐시에 없으면 한 번 내려받는다. 못 구하면 None."""
    out = []
    for name in ("leaflet.css", "leaflet.js"):
        f = VENDOR / name
        if not f.exists():
            try:
                import requests
                r = requests.get(LEAFLET + name, timeout=20)
                if r.status_code != 200 or len(r.content) < 5000:
                    return None
                VENDOR.mkdir(parents=True, exist_ok=True)
                f.write_bytes(r.content)
            except Exception:
                return None
        out.append(f.read_text(encoding="utf-8"))
    css = re.sub(r"url\((?!data:)[^)]*\)", f"url({BLANK})", out[0])        # 기본 마커·레이어 아이콘은 안 쓴다
    js = re.sub(r"//# sourceMappingURL=\S+", "", out[1]).replace("</script", r"<\/script")
    return css, js


def _photo(url: str) -> str | None:
    try:
        import requests
        from PIL import Image
        r = requests.get(url.replace("http://", "https://", 1) if url.startswith("http://") else url, timeout=10,
                         headers={"User-Agent": "Mozilla/5.0 momo-export"})
        if r.status_code != 200 or not r.headers.get("content-type", "").startswith("image"):
            return None
        im = Image.open(io.BytesIO(r.content)).convert("RGB")
        if im.width > PHOTO_W:
            im = im.resize((PHOTO_W, round(im.height * PHOTO_W / im.width)), Image.LANCZOS)
        b = io.BytesIO()
        im.save(b, "JPEG", quality=70, optimize=True)
        return "data:image/jpeg;base64," + base64.b64encode(b.getvalue()).decode("ascii")
    except Exception:
        return None


def inline_photos(payload: dict, fetch=None) -> dict:
    """payload 복사본의 사진 URL을 data URI로 바꾼다. 못 받은 사진은 항목째 뺀다(모모 그림으로 대체됨)."""
    p = copy.deepcopy(payload)
    first: list[str] = []      # 장소 대표 사진 먼저
    rest: list[str] = []

    def scan(o, hero=False):
        if isinstance(o, dict):
            for k, v in o.items():
                if k in ("image_url", "image") and isinstance(v, str) and v.startswith("http"):
                    first.append(v)
                elif k == "images" and isinstance(v, list):
                    for i, im in enumerate(v):
                        if isinstance(im, dict):
                            for kk in ("url", "thumb"):
                                if isinstance(im.get(kk), str) and im[kk].startswith("http"):
                                    (first if i == 0 else rest).append(im[kk])
                else:
                    scan(v)
        elif isinstance(o, list):
            for v in o:
                scan(v)
    scan(p)
    urls = list(dict.fromkeys(first + rest))
    t0, size, got = time.monotonic(), 0, {}

    def work(u):
        return u, ((fetch or _photo)(u) if time.monotonic() - t0 < PHOTO_TIME else None)
    with ThreadPoolExecutor(max_workers=8) as ex:
        for u, d in ex.map(work, urls):
            if d and size + len(d) <= PHOTO_BUDGET:
                got[u] = d
                size += len(d)

    def fix(o):
        if isinstance(o, dict):
            for k in list(o):
                v = o[k]
                if k in ("image_url", "image") and isinstance(v, str) and v.startswith("http"):
                    if v in got:
                        o[k] = got[v]
                    else:
                        del o[k]
                elif k == "images" and isinstance(v, list):
                    keep = []
                    for im in v:
                        if not isinstance(im, dict):
                            continue
                        for kk in ("url", "thumb"):
                            if isinstance(im.get(kk), str) and im[kk].startswith("http"):
                                if im[kk] in got:
                                    im[kk] = got[im[kk]]
                                else:
                                    im.pop(kk)
                        if im.get("url") or im.get("thumb"):
                            if not im.get("url"): im["url"] = im["thumb"]
                            if not im.get("thumb"): im["thumb"] = im["url"]
                            keep.append(im)
                    o[k] = keep
                else:
                    fix(v)
        elif isinstance(o, list):
            for v in o:
                fix(v)
    fix(p)
    return p
