"""사용자 지정 장소의 관광공사 실시간 조회 (SERVICE.md 3-6 (5)).

수집 데이터에 없는 장소를 사용자가 말하면 TourAPI 키워드 검색(해당 지역 법정동 코드로 제한)으로 찾아
상세(개요·운영시간)까지 받아 정식 장소로 편입한다. 결과는 data/tourapi/<region>/extra_places.json에 저장(캐시),
원본 응답은 수집 스크립트와 같은 raw 캐시를 쓴다.
"""
from __future__ import annotations

import importlib.util
import json
import re
from functools import lru_cache

from . import data, hours
from .config import DATA, ROOT

KIND = {"12": "attraction", "14": "attraction", "28": "attraction", "38": "attraction",
        "39": "restaurant", "32": "lodging", "15": "festival"}


@lru_cache
def _client():
    spec = importlib.util.spec_from_file_location("collect_tourapi", ROOT / "scripts" / "collect_tourapi.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _norm(t: str) -> str:
    return re.sub(r"\s+|\(.*?\)|\[.*?\]", "", t or "")


def _summary(overview: str) -> str | None:
    ov = hours.clean(overview)
    if not ov:
        return None
    out = ""
    for s in re.split(r"(?<=[.!?다요])\s+", ov):
        if len(out) + len(s) > 180:
            break
        out += s + " "
    return (out.strip() or ov[:180]) + " (관광공사 소개 인용)"


def lookup(name: str, region: str) -> dict | None:
    """지역 안에서 이름으로 관광공사 장소를 찾아 정식 장소 dict로 돌려준다(없으면 None)."""
    cfg = data.regions_config().get(region)
    if not cfg:
        return None
    tour = _client()
    want = _norm(name)
    best = None
    for sg in cfg["sigungu"]:
        try:
            hits = tour.call("searchKeyword2", keyword=name, lDongRegnCd=sg["regn"], lDongSignguCd=sg["signgu"],
                             numOfRows=10, pageNo=1)
        except Exception:
            continue
        for h in hits:
            if h.get("contenttypeid") not in KIND:
                continue
            t = _norm(h.get("title"))
            score = 0 if t == want else (1 if want in t or t in want else 2)
            if score < 2 and (best is None or score < best[0]):
                best = (score, h, sg["name"])
    if not best:
        return None
    _, h, sg_name = best
    cid = h["contentid"]
    common = tour.call("detailCommon2", contentId=cid)
    intro = tour.call("detailIntro2", contentId=cid, contentTypeId=h["contenttypeid"])
    c = common[0] if common else {}
    p = {
        "contentid": cid, "title": h.get("title"), "kind": KIND[h["contenttypeid"]],
        "contenttypeid": h["contenttypeid"], "region": region, "sigungu": sg_name, "must_include": False,
        "addr": " ".join(x for x in (h.get("addr1"), h.get("addr2")) if x),
        "lat": float(h["mapy"]) if h.get("mapy") else None, "lng": float(h["mapx"]) if h.get("mapx") else None,
        "tel": h.get("tel"), "image_url": h.get("firstimage") or None,
        "image_license": h.get("cpyrhtDivCd") or c.get("cpyrhtDivCd"),
        "overview": c.get("overview"), "homepage": c.get("homepage"), "intro": intro[0] if intro else {},
        "event_start": h.get("eventstartdate"), "event_end": h.get("eventenddate"),
        "source": "한국관광공사 TourAPI", "added_by_user": True,
    }
    p["summary"] = _summary(p["overview"])
    _enrich(p)
    save_extra(region, p)
    return p


def _enrich(p: dict) -> None:
    """수집 장소와 같은 수준으로: 사진 2~4장 + (관광지면) 특장점·관람 팁·동반자 메모. 실패해도 편입은 진행."""
    try:
        spec = importlib.util.spec_from_file_location("enrich_places", ROOT / "scripts" / "enrich_places.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        p["images"] = mod.images(p)
        if p["kind"] in ("attraction", "festival"):
            import boto3
            from .config import AWS_REGION
            p.update(mod.text(boto3.client("bedrock-runtime", region_name=AWS_REGION), p))
    except Exception:
        pass


def save_extra(region: str, p: dict) -> None:
    path = DATA / "tourapi" / region / "extra_places.json"
    rows = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    rows = [r for r in rows if r["contentid"] != p["contentid"]] + [p]
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    data.places.cache_clear()
    data.all_places.cache_clear()
