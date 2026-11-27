# AGENTS.md · 협업 규칙

사용자와 Claude(및 Claude가 띄우는 서브에이전트)가 이 미니 PJT를 진행할 때 지키는 규칙.

## 1. 문서 3종 관리

| 파일 | 담는 내용 | 갱신 시점 |
|---|---|---|
| `AGENTS.md` | 협업 규칙 (이 파일) | 사용자가 새 규칙을 주거나 기존 규칙을 바꿀 때 |
| `SERVICE.md` | 기획 내용 (가이드의 SERVICE.md 템플릿 준수) | 기획이 확정·변경될 때마다 |
| `HISTORY.md` | 사용자↔Claude 대화 요약 + 서브에이전트 작업 내역 | 매 턴 응답을 마칠 때 |

- `SERVICE.md`는 채점 대상이라 가이드 규약대로 대문자 파일명을 쓴다.
- 기획 결정은 대화에서만 끝내지 않고 반드시 `SERVICE.md`에 반영한다.

## 2. HISTORY.md 기록 규칙

- 매 턴마다 항목 하나를 **맨 아래에 추가**한다 (기존 항목은 수정하지 않음, 정정이 필요하면 새 항목으로).
- 항목 형식:
  ```
  ### [YYYY-MM-DD HH:MM] 제목
  - **사용자 요청**: 요청 요약
  - **Claude 작업/응답**: 한 일, 결정 사항, 만든·수정한 파일
  - **서브에이전트**: (사용했을 때만) 에이전트 종류 · 맡긴 일 · 결과 요약
  - **다음 할 일**: (있으면)
  ```
- 대화 원문 전체가 아니라 **결정·근거·결과 위주로 요약**한다.
- API 키, `.env` 내용, 개인정보는 기록하지 않는다.

## 3. 작업 방식

- 기준 문서: `guide/mini-pjt_guide.md` (일정·산출물 규약·채점 기준).
- 산출물 폴더 구조와 `POST /query` API 규약은 가이드를 따른다.
- 되돌리기 어려운 작업(파일 삭제·덮어쓰기, git push, 외부 업로드)은 먼저 확인받는다.
- 서브에이전트를 쓸 때는 무엇을 맡겼고 무엇을 돌려받았는지 `HISTORY.md`에 남긴다.
- 응답은 한국어로 한다.
- **Google Places API는 사용하지 않는다.** 호출·수집·키 사용 모두 하지 않는다.

## 4. 소스 구조 규칙 (`src/` 도메인별 폴더)

모든 소스 코드는 `src/` 아래에 둔다. 폴더는 기술 역할(utils·models)이 아니라 **에이전트의 업무 도메인**으로 나눈다.
루트에 남는 코드는 `tests/`(pytest)와 `evaluation/run_eval.py`(가이드가 정한 평가 폴더)뿐이다.

| 폴더 | 도메인 | 파일 |
|---|---|---|
| `src/counsel/` | 상담 에이전트 — LangGraph 그래프(입력 가드 → 조건 파싱 → 의도 분기 → 승인), 답변 말투·서식, 입력 조건 선택지 | `agent.py` `prompts.py` `schemas.py`(ParsedRequest) `options.py` `render.py` `richtext.py` |
| `src/planning/` | 일정 생성 — LLM 장소 고르기 + 코드 시각 계산·규칙 검사·재생성, 운영시간 해석, 계획서(브로셔), 장소 바꾸기, 귀가길 들를 곳 | `planner.py` `prompts.py` `schemas.py`(PlanPick·DayPick·PickItem·RankItem·ItineraryItem) `hours.py` `brochure.py` `alternatives.py` `stopovers.py` |
| `src/places/` | 장소 검색·RAG — 하이브리드 검색(BM25+Chroma), 장소 찾기, 관광공사 실시간 조회, 출발지 위치 찾기, ReAct 도메인 도구 | `retrieve.py` `placesearch.py` `tourlive.py` `geo.py` `tools.py` |
| `src/mobility/` | 장거리 이동 — 도로 경로·소요시간·교통 정보, 기차역·공항 거점 | `roads.py` `hubs.py` |
| `src/budget/` | 예상 경비·예산 비교 | `costs.py` |
| `src/packing/` | 날씨·준비물 체크리스트 | `packing.py` |
| `src/export/` | 확정 계획 내보내기(Word·HTML·지도 굽기) | `export.py` `export_assets.py` `map_bake.py` |
| `src/memory/` | 확정 계획·지출 저장(SQLite)·.ics | `store.py` |
| `src/guardrails/` | 개인정보 가리기·인젝션 차단·출력 점검 | `guardrails.py` |
| `src/api/` | FastAPI 진입점과 웹 화면(정적 파일) | `main.py` `static/` |
| `src/ingest/` | 오프라인 데이터 파이프라인(수집 → 요약 → 보강 → 색인, 평가 기준 정답 초안) | `collect_tourapi.py` `summarize_places.py` `enrich_places.py` `build_index.py` `draft_references.py` `make_sample_plan.py` |
| `src/shared/` | **여러 도메인이 함께 쓰는 것만** — LLM 클라이언트, 공용 페르소나, 설정, 수집 데이터 로딩, 직선거리·이동시간 추정, 요청 취소, 예상 시간 | `llm.py` `persona.py` `config.py` `data.py` `travel.py` `cancel.py` `eta.py` |

**파일 배치 규칙**
1. 새 코드는 먼저 "어느 업무 도메인인가"로 폴더를 고른다. 맞는 도메인이 없으면 새 도메인 폴더(`src/<도메인>/__init__.py`)를 만든다. `utils/`·`models/`·`helpers/` 같은 기술 역할 폴더는 만들지 않는다.
2. 도메인 폴더 안에 실행 로직(`<기능>.py`), 프롬프트(`prompts.py`), 입출력 스키마(`schemas.py`, Pydantic)를 함께 둔다. LLM 시스템 프롬프트를 로직 파일 안에 문자열로 새로 넣지 않는다.
3. `src/shared/`에는 두 개 이상 도메인이 쓰는 LLM 클라이언트·설정·유틸리티만 둔다. 한 도메인만 쓰는 코드는 그 도메인으로 옮긴다. `shared`는 다른 도메인을 import하지 않는다.
4. import는 항상 절대경로 `from src.<도메인> import <모듈>` / `from src.<도메인>.<모듈> import <이름>`을 쓴다(상대 import 금지).
5. 저장소 루트 경로는 `src.shared.config.ROOT`를 쓴다. `Path(__file__).parents[...]`로 루트를 새로 계산하지 않는다(폴더 깊이가 바뀌면 깨짐). 예외: `src/ingest/` 스크립트는 `sys.path`를 잡기 전이라 직접 계산한다.
6. 실행: 서버 `python -m uvicorn src.api.main:app --host 127.0.0.1 --port 8000`, 데이터 파이프라인 `python -m src.ingest.<스크립트> …`, 테스트 `python -m pytest`(모두 프로젝트 루트에서).
7. 모듈·파일을 문자열 경로로 불러오지 않는다(`__import__("…")`, `importlib`의 파일 경로 로딩 금지) — 구조를 바꾸면 테스트가 지나가지 않는 경로에서 조용히 깨진다(2026-11-27 시작 카드·관광공사 실시간 조회 버그). 다른 도메인 모듈은 `from src.<도메인> import <모듈>`로.

## 5. 규칙 변경 이력

- 2026-11-25: 최초 작성 (문서 3종 관리 규칙)
- 2026-11-25: Google Places API 사용 기한(11-27 금요일까지) 규칙 추가 → 같은 날 "사용 안 함"으로 변경
- 2026-11-27: 4절 소스 구조 규칙 추가 — `momo/`·`app/`·`scripts/`를 `src/` 도메인별 폴더로 재구성
- 2026-11-27: 4절 규칙 7 추가 — 문자열 경로로 모듈 불러오기 금지
