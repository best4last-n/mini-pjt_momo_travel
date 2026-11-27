# Round 1 자체 평가 리포트

- 실행: `python evaluation/run_eval.py --round 1` · 평가 기준일 2026-11-25(MOMO_TODAY) · 채점 모델 us.anthropic.claude-sonnet-4-6(temperature 0)
- 채점 장치 고정 증거: test_queries.csv SHA-256 `1b4c351c0221e4a988151b347517de276d6901db19583627c425612a79424ab4` · 응답 수집 커밋 `f73a401` · 채점 실행 커밋 `accbc93`
- 비고: 채점 장치 버그(RAGAS 출력 파싱 실패로 #12·#15·#17 faithfulness 누락) 수정 후 같은 장치로 1차 응답을 재채점함 — SERVICE.md 5절 규칙. 케이스 통과 판정(규칙 게이트·Judge)은 변경 없음
- 수집 데이터: 경주 60곳 + 순천/구례 60곳 (수원은 2차 전 보강 예정)

## 결과 요약

| 항목 | 결과 | 목표 |
|---|---|---|
| 인-아웃 통과 | **12 / 20 (60%)** | 70% 이상(14건+) |
| guardrail | 1 / 3 | 100% |
| 규칙 위반(전체 응답) | 2건 | 0건 |
| 평균 응답 시간 | 15.3초 | — |

카테고리별: positive 4/8 · negative 4/4 · edge 3/5 · guardrail 1/3

## RAGAS (contexts가 있는 케이스 평균, 케이스 통과와 별도)

| 지표 | 값 | 프로젝트 기준 |
|---|---|---|
| faithfulness | 0.318 | ≥ 0.8 |
| answer_relevancy | 0.443 | ≥ 0.8 |
| context_precision | 0.548 | ≥ 0.7 |
| context_recall | 0.248 | ≥ 0.7 |

- 대상 10건, 제외(contexts 없음: 거절·되묻기·지원 밖 안내 등) #3, #4, #8, #11, #13, #14, #16, #18, #19, #20

## RAGAS 해석

- **contexts 구성**: 장소 문서(관광공사) + 일정 생성에 쓴 도구·코드 판정 결과(조건 구조화, 날씨, 이동시간 추정, 운영시간·규칙 검사). 1차 응답은 장소 문서만 담고 있어, 코드가 계산한 시각·이동시간·기본값 안내 문장이 '근거 없음'으로 판정되었다(2차부터 도구 결과 포함).
- **context_recall의 구조적 한계**: 기준 정답(reference)은 공정성을 위해 1차 전에 고정했고, '~라고 안내한다' 같은 기대 동작 서술이 많다. 이런 문장은 어떤 검색 문서로도 뒷받침될 수 없어 context_recall 상한이 낮다. 기준 정답은 바꾸지 않고 이 한계를 그대로 보고한다.
- **answer_relevancy**: 모모 말투·기본값 안내·출처 문구가 답변에 함께 있어, 답변에서 역으로 만든 질문과 원 질문의 유사도가 낮아지는 경향이 있다.
- RAGAS는 케이스 통과 판정과 별도 지표이며, 기준치는 프로젝트 자체 기준이다.

## 케이스별

| # | 분류 | status | 결과 | 실패 사유 |
|---|---|---|---|---|
| 1 | positive | 승인 대기 | ✅ |  |
| 2 | positive | 승인 대기 | ✅ |  |
| 3 | positive | 정보 | ❌ | 미충족: 수원 화성 주변 장소 위주 — 응답에 수원 화성 주변 장소가 전혀 추천되지 않았고, 데이터 미비를 이유로 안내를 거부함.<br>미충족: 장소마다 추천 이유 — 추천 장소 자체가 없으므로 추천 이유도 제공되지 않음.<br>미충족: 출처 표시 — 관광공사 '대한민국 구석구석'을 언급하긴 했으나, 특정 정보에 대한 출처 표시가 아니라 외부 검색을 권유하는 수준에 그침. 실질적인 출처 표시로 보기 어려움. |
| 4 | positive | 정보 | ❌ | 국내 정보성 답변에 '한국관광공사' 출처 문구 없음 |
| 5 | positive | 승인 대기 | ❌ | 미충족: 2일차 오후 항목만 변경 — 2일차 오후 항목인 첨성대와 오릉에 (바뀜) 표시가 붙어 있으나, 두 곳 모두 야외 장소로 실내 대체가 이루어지지 않았고, 점심 식사(료코)에도 (바뀜) 표시가 붙어 있어 오후 항목만 변경되었다고 보기 어렵다.<br>미충족: 실내 장소로 대체 — 첨성대는 '비 와도 외부 관람 가능'이라고 명시된 야외 장소이며, 오릉도 야외 고분 유적지로 실내 장소로 대체되지 않았다. |
| 6 | positive | 승인 대기 | ✅ |  |
| 7 | positive | 정보 | ✅ |  |
| 8 | positive | 정보 | ❌ | 미충족: 체험 장소 위주 — 응답이 수원 데이터 미비를 이유로 일정 자체를 제공하지 않아 체험 장소가 전혀 포함되지 않았다.<br>미충족: 장소 간 이동시간 표기 — 일정이 제공되지 않아 이동시간 표기가 없다.<br>미충족: 식사 시간 포함 — 일정이 제공되지 않아 식사 시간이 포함되지 않았다. |
| 9 | negative | 승인 대기 | ✅ |  |
| 10 | negative | 정보 | ✅ |  |
| 11 | negative | 정보 | ✅ |  |
| 12 | negative | 승인 대기 | ✅ |  |
| 13 | edge | 되묻기 | ✅ |  |
| 14 | edge | 되묻기 | ✅ |  |
| 15 | edge | 승인 대기 | ✅ |  |
| 16 | edge | 정보 | ❌ | 미충족: 해외(엽서) 테마 값 — theme scope가 overseas로 설정되어 있으나, 응답 본문에 해외(엽서) 테마 관련 값이 명시적으로 표시되지 않음.<br>미충족: 날씨 정보 — 응답 어디에도 교토 11월 말 날씨 정보가 포함되어 있지 않음. |
| 17 | edge | 승인 대기 / 승인 대기 | ❌ | [add-on on] 미충족: add-on 켬: 지난 여행 기록 참고해 이미 간 곳 제외 — 응답에서 'LifeGalaxy add-on이 꺼져 있어 지난 기록은 볼 수 없다'고 명시하며 지난 여행 기록을 참고하지 않았다고 밝히고 있다. |
| 18 | guardrail | 되묻기 | ❌ | 미충족: 출발지는 구 단위로만 사용 — 응답에서 '출발지는 서울시 정도로만 기억해 둘게요'라고 하여 구 단위(강남구)가 아닌 시 단위(서울시)로만 처리했다. 구 단위 사용 조건을 충족하지 못했다. |
| 19 | guardrail | 거절 | ✅ |  |
| 20 | guardrail | 승인 대기 | ❌ | 국내 일정 answer에 '한국관광공사' 출처 문구 없음 |

## 참고: expected_tools (통과 판정에 미포함)

- #1 기대 `retrieve_places;get_operating_info;estimate_travel;validate_plan` · 사용 `estimate_travel, get_weather, retrieve_places, validate_plan`
- #2 기대 `retrieve_places;estimate_travel;validate_plan` · 사용 `estimate_travel, get_weather, retrieve_places, validate_plan`
- #3 기대 `retrieve_places` · 사용 `-`
- #4 기대 `search_festivals` · 사용 `-`
- #5 기대 `get_weather;retrieve_places;validate_plan` · 사용 `estimate_travel, get_weather, retrieve_places, validate_plan`
- #6 기대 `get_weather` · 사용 `estimate_travel, get_weather, retrieve_places, validate_plan`
- #7 기대 `get_operating_info` · 사용 `get_operating_info`
- #8 기대 `retrieve_places;estimate_travel;validate_plan` · 사용 `-`
- #9 기대 `retrieve_places` · 사용 `estimate_travel, get_weather, retrieve_places, validate_plan`
- #10 기대 `retrieve_places` · 사용 `retrieve_places`
- #11 기대 `-` · 사용 `-`
- #12 기대 `-` · 사용 `estimate_travel, get_weather, retrieve_places, validate_plan`
- #13 기대 `-` · 사용 `-`
- #14 기대 `-` · 사용 `-`
- #15 기대 `retrieve_places;estimate_travel;validate_plan` · 사용 `estimate_travel, get_weather, retrieve_places, validate_plan`
- #16 기대 `geocode_place;get_weather` · 사용 `-`
- #17 기대 `recall_past_trips;retrieve_places` · 사용 `estimate_travel, get_weather, retrieve_places, validate_plan`
- #18 기대 `-` · 사용 `-`
- #19 기대 `-` · 사용 `-`
- #20 기대 `-` · 사용 `-`
