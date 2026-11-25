# 모모의 여행 상담소, 무엇을 어떤 흐름으로 만드나 {#momo-build-plan}

**C안 하이브리드**를 권장합니다. 일정 요청은 고정 그래프로 만들고 규칙 검사로 막고, 정보 질문만 ReAct가 도구를 골라 답합니다.

- 일정은 규칙 위반 0건이 목표라서, 생성 뒤 반드시 검사하고 최대 2회 다시 만드는 고정 흐름이 맞습니다.
- 축제·운영시간·날씨 같은 단순 질문은 ReAct가 도구를 골라 답하면 빠르고 자연스럽습니다.
- 두 흐름을 합치면 ReAct·Plan-Execute·HITL·가드레일 패턴이 모두 실제 코드에 들어갑니다.

@answer scope="Day 9~10 구현 범위 · SERVICE.md·Seed 기준 · 개발 전 설계안"
@meta locale=ko

## 무엇으로 이루어지나 {#parts}

@architecture id=momo-system title="전체 구성 (C안 기준)"
| ui | 단일 페이지 화면 | frontend |
| tour | TourAPI 수집본 | external |
| api | FastAPI /query | backend |
| guard | 가드레일 | security |
| agent | LangGraph 에이전트 | backend |
| tools | 도메인 도구 | backend |
| rag | Chroma + BM25 | database |
| ext | Open-Meteo · OSM | external |
| store | SQLite 계획 저장 | database |
| trace | LangSmith | cloud |
| map | 지도 OSM · 브이월드 | external |
| ui > api | 질문·승인 | emphasis |
| api > guard | 입력 검사 | security |
| guard > agent | 통과 | |
| agent > tools | 도구 호출 | |
| tour > rag | 사전 수집 | dashed |
| tools > rag | 검색 | |
| tools > ext | 날씨·지명 | |
| agent > store | 승인 후 저장 | emphasis |
| agent > trace | 기록 | dashed |
| ui > map | 루트 미리보기 | |

@facts
| 화면 | 일정 카드 + 순서 마커·경로선 지도, 모바일 1열 / PC 2단 |
| 도구 | 장소 검색, 축제, 운영정보, 날씨, 이동시간, 일정 검사, 지명 검색, 저장 |
| 데이터 | 지역마다 관광 30·음식점 20·숙박 10·축제 최대 10곳 |
| 결과물 | 상세 일정표, .ics 파일, 읽기 전용 공유 링크 |

## 질문 하나가 어떻게 처리되나 {#request-flow}

@workflow id=momo-flow title="C안 요청 처리 흐름"
| lane in | 입력 |
| lane plan | 일정 만들기 |
| lane check | 검증 |
| lane ok | 승인 |
| q | 사용자 질문 | frontend | in |
| gin | 입력 가드레일 | security | in |
| route | 일정 요청인가? | backend | in |
| react | ReAct 정보 답변 | backend | plan |
| parse | 조건 구조화 | backend | plan |
| search | 장소 검색 | backend | plan |
| gen | 일정 생성 | backend | plan |
| val | 규칙 위반? | backend | check |
| gout | 출력 가드레일 | security | check |
| wait | 승인 대기 | frontend | ok |
| save | 확정·저장 | backend | ok |
| q > gin | |
| gin > route | 통과 |
| route > parse | 예 |
| route > react | 아니오 |
| parse > search | |
| search > gen | |
| gen > val | |
| val > gen | 예 | dashed |
| val > gout | 아니오 | emphasis |
| react > gout | |
| gout > wait | |
| wait > save | approve | emphasis |

@steps id=flow-steps
| s1 | 입력 가드레일 | 개인정보를 가리고, 인젝션·범위 밖 요청·잘못된 요청을 여기서 막습니다. | momo-flow | gin |
| s2 | 일정 요청인가? | 일정이면 고정 그래프로, 축제·운영시간 같은 정보 질문이면 ReAct로 보냅니다. | momo-flow | route |
| s3 | 조건 구조화 | 지역·날짜·길이·출발지·이동수단을 뽑습니다. 지역이 없으면 한 번 되묻습니다. | momo-flow | parse |
| s4 | 일정 생성 | 검색 결과 장소만으로 순서·시간·이동시간을 채운 일정을 만듭니다. | momo-flow | gen |
| s5 | 규칙 위반? | 휴무일·운영시간·시간 겹침·시간 창을 코드로 검사합니다. 위반이면 최대 2회 다시 만듭니다. | momo-flow | val |
| s6 | 승인 대기 | 확정 버튼만 승인으로 봅니다. 채팅 문장으로는 저장하지 않습니다. | momo-flow | wait |
| s7 | 확정·저장 | 같은 세션은 같은 plan_id에 덮어쓰고 .ics와 공유 링크를 돌려줍니다. | momo-flow | save |

## 세 가지 안 비교 {#options}

| 기준 | A 고정 파이프라인 | B ReAct 단일 | C 하이브리드 |
| --- | --- | --- | --- |
| 규칙 위반 0건 보장 | 강함 {good:보장} | 약함 {bad:LLM 의존} | 강함 {good:보장} |
| 정보 질문 대응 | 딱딱함 {caution:분기 많음} | 자연스러움 | 자연스러움 {good:ReAct} |
| 적용 패턴 | 9개 | 9개 | 10개 {good:최다} |
| Day 9 개발 부담 | 낮음 {good:가장 쉬움} | 중간 | 중간 {caution:분기 1개 추가} |
| 평가 통과 가능성 | 정보 케이스 약함 | 규칙 케이스 약함 {bad:위험} | 균형 {good:권장} |

@callout level=note title="패턴 수 세는 법"
C안 10개: LCEL+Pydantic, RAG, Trace, 평가(필수 4) + ReAct, 도구 다중, 가드레일, HITL, 미들웨어, Plan-Execute.
A안은 ReAct가, B안은 Plan-Execute와 규칙 루프가 빠집니다. 장기 메모리·MCP는 세 안 모두 추가 기능입니다.

## 언제 무엇을 만드나 {#build-order}

@steps id=build-steps
| b1 | 데이터 수집 (Day 9 오전) | 경주 먼저 TourAPI 수집, 이어서 순천/구례·수원. 요약·이미지·OSRM 캐시도 이때 만듭니다. |
| b2 | 검색·도구 (Day 9 오전) | Chroma + BM25 색인, 도구 8개, 규칙 검사 함수와 단위 테스트 |
| b3 | 에이전트 그래프 (Day 9 오후) | 위 흐름대로 LangGraph 구성, 가드레일·승인·멀티턴 연결 |
| b4 | API·화면 (Day 9 오후) | POST /query, 일정 카드, 루트 미리보기 지도, 브이월드 전환 |
| b5 | 1차 평가 (Day 9 18:00 컷라인) | 20건 + RAGAS + Judge, 목표 14건 이상 |
| b6 | 개선·2차 평가 (Day 10 오전) | 실패 케이스 수정, 목표 17건 이상·가드레일 3/3. 10:00 기능 동결 |
| b7 | 제출 (Day 10 15:00) | README, 시연 캡처, GitHub push 후 Google Form |
