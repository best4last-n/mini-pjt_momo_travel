#!/usr/bin/env bash
# 모모의 여행 상담소 실행 스크립트 — 프로젝트 루트에서 `bash run.sh [serve|test|eval|index]`
#   serve (기본) : 가상환경 준비 → 서버 http://127.0.0.1:8000
#   test         : 회귀 테스트(LLM 호출 없음)
#   eval         : 2차 자체 평가(인-아웃 20건 + RAGAS, Bedrock 호출)
#   index        : 벡터 색인 생성(chroma_db/, 없으면 BM25만으로 동작)
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -f .env ]; then
  echo ".env가 없습니다. .env.example을 .env로 복사해 키를 채워 주세요." >&2
  exit 1
fi

if [ ! -d .venv ]; then
  python -m venv .venv
fi
if [ -f .venv/Scripts/python.exe ]; then PY=.venv/Scripts/python; else PY=.venv/bin/python; fi
"$PY" -m pip install -q --disable-pip-version-check -r requirements.txt

case "${1:-serve}" in
  serve) exec "$PY" -m uvicorn src.api.main:app --host 127.0.0.1 --port 8000 ;;
  test)  exec "$PY" -m pytest -q ;;
  eval)  exec "$PY" evaluation/run_eval.py --round 2 ;;
  index) exec "$PY" -m src.ingest.build_index ;;
  *) echo "사용법: bash run.sh [serve|test|eval|index]" >&2; exit 2 ;;
esac
