"""검색 색인 생성 — Chroma(Titan v2 임베딩) + BM25용 문서 목록.

사용법:  python -m src.ingest.build_index [region ...]     (생략하면 places.json이 있는 모든 지역)

- 입력: data/tourapi/<region>/places.json (collect_tourapi.py 결과)
- 출력: data/index/docs.jsonl  — 검색 문서 원본(BM25는 실행 시 kiwipiepy로 이 파일에서 만듦)
        chroma_db/              — 벡터 색인(컬렉션 momo_places, id = contentid), .gitignore 대상
- 임베딩 문장은 관광공사 overview 원문 기반(요약과 무관) → 문서 해시가 같으면 다시 임베딩하지 않음
- overview는 데이터로만 취급. 색인에는 그대로 넣되, 프롬프트에 넣을 때는 구분 태그로 감싼다(SERVICE.md 4-1)
"""
from __future__ import annotations

import hashlib
import html
import json
import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
INDEX_DIR = ROOT / "data" / "index"
CHROMA_DIR = ROOT / "chroma_db"
COLLECTION = "momo_places"
EMBED_MODEL = "amazon.titan-embed-text-v2:0"
KIND_KO = {"attraction": "관광지", "restaurant": "음식점", "lodging": "숙박", "festival": "축제"}
HOURS_KEYS = ("usetime", "usetimeculture", "opentimefood", "checkintime", "playtime",
              "restdate", "restdateculture", "restdatefood", "infocenter", "infocenterfood")


def clean(text: str | None) -> str:
    text = html.unescape(re.sub(r"<[^>]+>", " ", text or ""))
    return re.sub(r"\s+", " ", text).strip()


def to_doc(p: dict, label: str) -> dict:
    intro = p.get("intro") or {}
    hours = " / ".join(f"{k}: {clean(intro[k])}" for k in HOURS_KEYS if clean(intro.get(k)))
    overview = clean(p.get("overview"))
    text = "\n".join(x for x in (
        f"[{label}] {p['title']} ({KIND_KO.get(p['kind'], p['kind'])})",
        f"주소: {p.get('addr') or ''}",
        f"소개: {overview[:1500]}",
        f"운영 정보: {hours}" if hours else "",
    ) if x)
    return {
        "doc_id": p["contentid"],
        "text": text,
        "metadata": {
            "region": p["region"], "region_label": label, "kind": p["kind"], "title": p["title"],
            "lat": p.get("lat"), "lng": p.get("lng"), "must_include": bool(p.get("must_include")),
            "has_image": bool(p.get("image_url")),
        },
        "sha": hashlib.sha1(text.encode()).hexdigest()[:12],
    }


def main(regions: list[str]) -> None:
    load_dotenv(ROOT / ".env")
    cfg = json.loads((ROOT / "data" / "config" / "regions.json").read_text(encoding="utf-8"))["regions"]
    if not regions:
        regions = [r for r in cfg if (ROOT / "data" / "tourapi" / r / "places.json").exists()]
    docs = []
    for r in regions:
        places = json.loads((ROOT / "data" / "tourapi" / r / "places.json").read_text(encoding="utf-8"))
        docs += [to_doc(p, cfg[r]["label"]) for p in places]
        print(f"[문서] {cfg[r]['label']}: {len(places)}건")

    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    with (INDEX_DIR / "docs.jsonl").open("w", encoding="utf-8") as f:
        for d in docs:
            f.write(json.dumps(d, ensure_ascii=False) + "\n")

    import boto3
    import chromadb
    from langchain_aws import BedrockEmbeddings

    emb = BedrockEmbeddings(model_id=EMBED_MODEL, client=boto3.client(
        "bedrock-runtime", region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-1")))
    col = chromadb.PersistentClient(path=str(CHROMA_DIR)).get_or_create_collection(
        COLLECTION, metadata={"hnsw:space": "cosine"})
    have = col.get(ids=[d["doc_id"] for d in docs], include=["metadatas"])
    old = {i: (m or {}).get("sha") for i, m in zip(have["ids"], have["metadatas"])}
    todo = [d for d in docs if old.get(d["doc_id"]) != d["sha"]]
    print(f"[임베딩] 대상 {len(todo)}건 (재사용 {len(docs) - len(todo)}건)")
    for i in range(0, len(todo), 16):
        batch = todo[i:i + 16]
        vecs = emb.embed_documents([d["text"] for d in batch])
        col.upsert(ids=[d["doc_id"] for d in batch], embeddings=vecs, documents=[d["text"] for d in batch],
                   metadatas=[{**{k: v for k, v in d["metadata"].items() if v is not None}, "sha": d["sha"]} for d in batch])
    print(f"[완료] docs.jsonl {len(docs)}건 · Chroma {col.count()}건 · 지역 {regions}")


if __name__ == "__main__":
    main(sys.argv[1:])
