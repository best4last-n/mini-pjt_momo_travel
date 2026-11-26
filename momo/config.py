"""설정값 한곳 모음 (SERVICE.md 3-4b·3-4e 숫자 규칙)."""
from __future__ import annotations

import os
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

DATA = ROOT / "data"
INDEX_DOCS = DATA / "index" / "docs.jsonl"
CHROMA_DIR = ROOT / "chroma_db"
STATE_DB = ROOT / "momo_state.sqlite"          # 세션·확정 계획 (.gitignore: *.sqlite)

GEN_MODEL = os.environ.get("MOMO_GEN_MODEL", "us.anthropic.claude-sonnet-4-5-20250929-v1:0")
EMBED_MODEL = "amazon.titan-embed-text-v2:0"
AWS_REGION = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")

DAY_START = 9 * 60            # 하루 기본 09:00
DAY_END = 20 * 60             # 하루 기본 20:00
HOME_LIMIT = 22 * 60          # 22:00 귀가 상한(마지막 날 집 도착)
STAY_LIMIT = 26 * 60          # 숙소 도착은 다음 날 02:00까지 문제 없음(22:00~02:00 늦은 체크인 허용, 사용자 결정 2026-11-26)
BUFFER_MIN = 10               # 시간 겹침 여유
DEFAULT_DEPART = 8 * 60       # 기본 출발 08:00
LUNCH = (12 * 60, 14 * 60)
DINNER = (18 * 60, 20 * 60)
MAX_REGEN = 2
MAX_QUESTION_CHARS = 2000


def today() -> date:
    """평가 재현용: MOMO_TODAY=2026-11-25 로 고정 가능."""
    v = os.environ.get("MOMO_TODAY")
    return date.fromisoformat(v) if v else date.today()
