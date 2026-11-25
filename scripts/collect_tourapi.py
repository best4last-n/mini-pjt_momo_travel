"""TourAPI(KorService2) 수집 스크립트.

사용법:  python scripts/collect_tourapi.py suncheon_gurye [--dry-run]

- 설정: data/config/regions.json (지역 코드·수량·필수 포함 키워드)
- 원본 응답은 data/tourapi/raw/ 에 캐시 → 다시 실행해도 같은 요청은 API를 다시 부르지 않음(하루 1,000건 한도 보호)
- 결과: data/tourapi/<region>/places.json (정규화된 장소 목록)
- 키는 .env의 TOURAPI_SERVICE_KEY 에서만 읽고 출력·저장하지 않음
- LLM 작업(요약·임베딩)은 여기서 하지 않음 → scripts/summarize_places.py (Bedrock)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.parse
from pathlib import Path

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
BASE = "https://apis.data.go.kr/B551011/KorService2/"
RAW_DIR = ROOT / "data" / "tourapi" / "raw"
CALLS = {"api": 0, "cache": 0}


def service_key() -> str:
    load_dotenv(ROOT / ".env")
    key = os.environ.get("TOURAPI_SERVICE_KEY", "")
    if not key:
        sys.exit("TOURAPI_SERVICE_KEY 가 .env 에 없습니다.")
    return urllib.parse.unquote(key) if "%" in key else key


def call(endpoint: str, **params) -> list[dict]:
    """TourAPI 호출(캐시 우선). item 목록을 돌려준다."""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    tag = hashlib.sha1(json.dumps([endpoint, params], sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]
    path = RAW_DIR / f"{endpoint}_{tag}.json"
    if path.exists():
        CALLS["cache"] += 1
        data = json.loads(path.read_text(encoding="utf-8"))
    else:
        q = dict(serviceKey=service_key(), MobileOS="ETC", MobileApp="momo", _type="json", **params)
        for attempt in range(3):
            try:
                r = requests.get(BASE + endpoint, params=q, timeout=20)
                data = r.json()
                break
            except (requests.RequestException, ValueError) as e:  # XML 오류 응답 등
                if attempt == 2:
                    raise RuntimeError(f"{endpoint} 호출 실패: {type(e).__name__}") from None
                time.sleep(1.5 * (attempt + 1))
        CALLS["api"] += 1
        header = data.get("response", {}).get("header", {})
        if header.get("resultCode") not in ("0000", None):
            raise RuntimeError(f"{endpoint} 오류: {header.get('resultMsg')}")
        path.write_text(json.dumps({"endpoint": endpoint, "params": params, "data": data}, ensure_ascii=False), encoding="utf-8")
        time.sleep(0.2)
        data = {"data": data}
    body = data["data"]["response"]["body"]
    items = body.get("items") or {}
    items = items.get("item", []) if isinstance(items, dict) else []
    return items if isinstance(items, list) else [items]


def kind_of(contenttypeid: str, types: dict) -> str:
    for kind, ids in types.items():
        if contenttypeid in ids:
            return kind
    return "festival" if contenttypeid == "15" else "other"


def collect(region_id: str, dry_run: bool = False) -> None:
    cfg = json.loads((ROOT / "data" / "config" / "regions.json").read_text(encoding="utf-8"))
    region = cfg["regions"][region_id]
    types = cfg["content_types"]
    picked: dict[str, dict] = {}  # contentid -> item

    def add(item: dict, kind: str, sigungu: str, must: bool = False) -> None:
        cid = item["contentid"]
        if cid not in picked:
            picked[cid] = {**item, "_kind": kind, "_sigungu": sigungu, "_must": must}

    # 1) 필수 포함 키워드
    for m in region["must_include"]:
        for sg in region["sigungu"]:
            hits = call("searchKeyword2", keyword=m["keyword"], lDongRegnCd=sg["regn"], lDongSignguCd=sg["signgu"], numOfRows=5, pageNo=1)
            # 같은 유형 중 이름이 가장 짧은 항목(예: '낙안읍성 벚꽃길'보다 '순천 낙안읍성')
            ok = [it for it in hits if kind_of(it["contenttypeid"], types) == m["type"]]
            if ok:
                add(min(ok, key=lambda it: len(it["title"])), m["type"], sg["name"], must=True)

    # 2) 유형별 목록(이미지 있는 항목, 수정일순) → 시군 배분 비율로 채움
    for kind, ids in types.items():
        quota = region["quota"][kind]
        for sg in region["sigungu"]:
            target = round(quota * sg["share"])
            have = sum(1 for p in picked.values() if p["_kind"] == kind and p["_sigungu"] == sg["name"])
            pool: list[dict] = []
            for ctid in ids:
                pool += call("areaBasedList2", contentTypeId=ctid, lDongRegnCd=sg["regn"], lDongSignguCd=sg["signgu"], arrange="Q", numOfRows=100, pageNo=1)
            for it in pool:
                if have >= target:
                    break
                if it["contentid"] not in picked and it.get("mapx") and it.get("mapy"):
                    add(it, kind, sg["name"])
                    have += 1

    # 3) 축제(여행 기간 스냅샷)
    fw = cfg["festival_window"]
    fests: list[dict] = []
    for sg in region["sigungu"]:
        fests += [{**f, "_sg": sg["name"]} for f in call("searchFestival2", eventStartDate=fw["start"], eventEndDate=fw["end"], lDongRegnCd=sg["regn"], lDongSignguCd=sg["signgu"], numOfRows=50, pageNo=1)]
    for f in fests[: region["quota"]["festival"]]:
        add(f, "festival", f["_sg"])

    print(f"[선정] {len(picked)}곳 (필수 {sum(p['_must'] for p in picked.values())}) · API {CALLS['api']}건 · 캐시 {CALLS['cache']}건")
    if dry_run:
        return

    # 4) 상세: 공통(개요·홈페이지) + 소개(운영시간·휴무 등)
    places = []
    for cid, it in picked.items():
        common = call("detailCommon2", contentId=cid)
        intro = call("detailIntro2", contentId=cid, contentTypeId=it["contenttypeid"])
        c = common[0] if common else {}
        places.append({
            "contentid": cid,
            "title": it.get("title"),
            "kind": it["_kind"],
            "contenttypeid": it["contenttypeid"],
            "region": region_id,
            "sigungu": it["_sigungu"],
            "must_include": it["_must"],
            "addr": " ".join(x for x in (it.get("addr1"), it.get("addr2")) if x),
            "lat": float(it["mapy"]) if it.get("mapy") else None,
            "lng": float(it["mapx"]) if it.get("mapx") else None,
            "tel": it.get("tel"),
            "image_url": it.get("firstimage") or None,
            "image_license": it.get("cpyrhtDivCd") or c.get("cpyrhtDivCd"),
            "overview": c.get("overview"),
            "homepage": c.get("homepage"),
            "intro": intro[0] if intro else {},
            "event_start": it.get("eventstartdate"),
            "event_end": it.get("eventenddate"),
            "source": "한국관광공사 TourAPI",
        })
    out = ROOT / "data" / "tourapi" / region_id / "places.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(places, ensure_ascii=False, indent=1), encoding="utf-8")
    kinds = {}
    for p in places:
        kinds[p["kind"]] = kinds.get(p["kind"], 0) + 1
    print(f"[저장] {out.relative_to(ROOT)} · {kinds} · 이미지 {sum(1 for p in places if p['image_url'])}/{len(places)} · API 누적 {CALLS['api']}건")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("region")
    ap.add_argument("--dry-run", action="store_true", help="선정만 하고 상세 호출은 하지 않음")
    a = ap.parse_args()
    collect(a.region, a.dry_run)
