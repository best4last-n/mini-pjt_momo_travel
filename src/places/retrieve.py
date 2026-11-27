"""하이브리드 검색: BM25(kiwipiepy) + Chroma 벡터(Titan v2) → RRF 결합.

- 질의의 지역명·일반어(경주, 운영시간 등)는 BM25에서 빼서 흔한 단어에 끌려가지 않게 한다.
- 제목 일치에 가산점(장소명을 직접 말한 경우).
"""
from __future__ import annotations

import json
import re
from functools import lru_cache

from src.shared import data
from src.shared.config import AWS_REGION, CHROMA_DIR, EMBED_MODEL, INDEX_DOCS

STOP = {"경주", "순천", "구례", "수원", "제주", "서귀포", "강릉", "동해", "삼척", "전주", "완주", "군산", "통영", "거제", "운영", "시간", "운영시간", "휴무", "휴무일", "쉬", "날", "알리", "주",
        "곳", "추천", "일정", "여행", "가", "하", "있", "되", "보", "싶", "짜", "근처", "어디", "좀", "요"}


@lru_cache
def _docs() -> list[dict]:
    rows = [json.loads(line) for line in INDEX_DOCS.read_text(encoding="utf-8").splitlines() if line.strip()]
    ok = set(data.available_regions())     # 수집이 덜 끝난(enabled=false) 지역 문서는 검색에서 제외
    return [d for d in rows if d["metadata"]["region"] in ok]


@lru_cache
def _kiwi():
    from kiwipiepy import Kiwi
    return Kiwi()


def tokens(text: str) -> list[str]:
    return [t.form for t in _kiwi().tokenize(text) if t.tag[0] in "NVSX" and t.form not in STOP]


@lru_cache
def _bm25():
    from rank_bm25 import BM25Okapi
    docs = _docs()
    # 제목을 두 번 넣어 제목 가중
    return BM25Okapi([tokens(d["metadata"]["title"] + " " + d["metadata"]["title"] + " " + d["text"]) for d in docs])


@lru_cache
def _vector():
    import boto3
    import chromadb
    from langchain_aws import BedrockEmbeddings
    emb = BedrockEmbeddings(model_id=EMBED_MODEL, client=boto3.client("bedrock-runtime", region_name=AWS_REGION))
    col = chromadb.PersistentClient(path=str(CHROMA_DIR)).get_collection("momo_places")
    return emb, col


def search(query: str, region: str | None = None, kinds: list[str] | None = None, k: int = 10,
           use_vector: bool = True) -> list[dict]:
    """문서 목록 [{doc_id, text, metadata, score}] (높은 순)."""
    docs = _docs()
    ok = [i for i, d in enumerate(docs)
          if (not region or d["metadata"]["region"] == region) and (not kinds or d["metadata"]["kind"] in kinds)]
    if not ok:
        return []
    ranks: dict[int, float] = {}
    q_tokens = tokens(query)
    if q_tokens:
        s = _bm25().get_scores(q_tokens)
        for r, i in enumerate(sorted(ok, key=lambda i: -s[i])[:30]):
            if s[i] > 0:
                ranks[i] = ranks.get(i, 0) + 1 / (60 + r)
    if use_vector:
        try:
            emb, col = _vector()
            where = {"region": region} if region else {"region": {"$in": list(data.available_regions())}}
            if kinds:
                kw = {"kind": {"$in": kinds}}
                where = {"$and": [where, kw]} if where else kw
            res = col.query(query_embeddings=[emb.embed_query(query)], n_results=min(30, len(ok)), where=where)
            pos = {d["doc_id"]: i for i, d in enumerate(docs)}
            for r, did in enumerate(res["ids"][0]):
                if did in pos:
                    ranks[pos[did]] = ranks.get(pos[did], 0) + 1 / (60 + r)
        except Exception:  # 벡터 실패 시 BM25만 (trace에는 호출부에서 기록)
            pass
    compact_q = re.sub(r"\s+", "", query)
    for i in ok:
        title = re.sub(r"\s+|\(.*?\)|\[.*?\]", "", docs[i]["metadata"]["title"]).replace("경주", "")
        if len(title) >= 2 and title in compact_q:
            ranks[i] = ranks.get(i, 0) + 0.05
    top = sorted(ranks, key=lambda i: -ranks[i])[:k]
    return [{**docs[i], "score": round(ranks[i], 4)} for i in top]


def find_by_name(name: str, region: str | None = None) -> dict | None:
    """사용자가 말한 장소명이 수집 데이터에 있는지(제목 포함 관계). 없으면 None."""
    n = re.sub(r"\s+", "", name)
    best, best_key = None, None
    for d in _docs():
        if region and d["metadata"]["region"] != region:
            continue
        t = re.sub(r"\s+|\[.*?\]", "", d["metadata"]["title"])
        t2 = re.sub(r"\(.*?\)", "", t)
        inner = re.findall(r"\((.*?)\)", t)
        cands = [c for c in {t, t2, *inner, t2.replace("경주", "").replace("순천", "").replace("구례", "")} if len(c) >= 2]
        if any(c == n for c in cands):
            rank = 0
        elif any(c in n for c in cands):
            rank = 1
        elif any(n in c for c in cands):
            rank = 2
        else:
            continue
        key = (rank, len(t))
        if best_key is None or key < best_key:
            best, best_key = d, key
    return best
