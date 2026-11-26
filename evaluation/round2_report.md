# Round 2 자체 평가 리포트

- 실행: `python evaluation/run_eval.py --round 2` · 평가 기준일 2026-11-25(MOMO_TODAY) · 채점 모델 us.anthropic.claude-sonnet-4-6(temperature 0)
- 채점 장치 고정 증거: test_queries.csv SHA-256 `1b4c351c0221e4a988151b347517de276d6901db19583627c425612a79424ab4` · 응답 수집 커밋 `555c891` · 채점 실행 커밋 `2c9b768`
- 수집 데이터: 순천/구례 60곳 + 경주 60곳 + 수원 61곳

## 결과 요약

| 항목 | 결과 | 목표 |
|---|---|---|
| 인-아웃 통과 | **17 / 20 (85%)** | 85% 이상(17건+) |
| guardrail | 3 / 3 | 100% |
| 규칙 위반(전체 응답) | 0건 | 0건 |
| 평균 응답 시간 | 19.2초 | — |

카테고리별: positive 8/8 · negative 4/4 · edge 2/5 · guardrail 3/3

## RAGAS (contexts가 있는 케이스 평균, 케이스 통과와 별도)

| 지표 | 값 | 프로젝트 기준 |
|---|---|---|
| faithfulness | 0.678 | ≥ 0.8 |
| answer_relevancy | 0.462 | ≥ 0.8 |
| context_precision | 0.526 | ≥ 0.7 |
| context_recall | 0.591 | ≥ 0.7 |

- 대상 13건, 제외(contexts 없음: 거절·되묻기·지원 밖 안내 등) #11, #13, #14, #16, #18, #19, #20

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
| 3 | positive | 승인 대기 | ✅ |  |
| 4 | positive | 정보 | ✅ |  |
| 5 | positive | 승인 대기 | ✅ |  |
| 6 | positive | 승인 대기 | ✅ |  |
| 7 | positive | 정보 | ✅ |  |
| 8 | positive | 승인 대기 | ✅ |  |
| 9 | negative | 승인 대기 | ✅ |  |
| 10 | negative | 정보 | ✅ |  |
| 11 | negative | 정보 | ✅ |  |
| 12 | negative | 승인 대기 | ✅ |  |
| 13 | edge | 되묻기 | ✅ |  |
| 14 | edge | 되묻기 | ✅ |  |
| 15 | edge | 승인 대기 | ❌ | 금지 검출: 이동시간 0분 처리 — itinerary에서 경주 오릉(이동 0분)과 경주 불국사(이동 0분)가 이동 0분으로 기록되어 있음. 첫 출발지는 0분이 자연스러울 수 있으나 두 일차 모두 첫 장소에 0분이 적용되어 있어 금지 조건에 해당함 |
| 16 | edge | 정보 | ❌ | 미충족: 해외(엽서) 테마 값 — theme scope가 overseas로 설정되어 있으나, 응답 본문에 해외 엽서 테마 관련 값이나 UI 요소가 표시되지 않음.<br>미충족: 날씨 정보 — 응답 어디에도 교토 11월 말 날씨 정보가 포함되어 있지 않음. |
| 17 | edge | 승인 대기 / 승인 대기 | ❌ | [add-on on] 미충족: add-on 켬: 지난 여행 기록 참고해 이미 간 곳 제외 — 응답에서 '지난 여행 기록(LifeGalaxy add-on)이 꺼져 있어 지난 기록은 볼 수 없어요'라고 명시하며 add-on이 꺼진 상태임을 안내했고, 실제로 지난 여행 기록을 참고해 이미 간 곳을 제외하지 않았다. |
| 18 | guardrail | 되묻기 | ✅ |  |
| 19 | guardrail | 거절 | ✅ |  |
| 20 | guardrail | 승인 대기 | ✅ |  |

## 참고: expected_tools (통과 판정에 미포함)

- #1 기대 `retrieve_places;get_operating_info;estimate_travel;validate_plan` · 사용 `estimate_travel, get_weather, retrieve_places, validate_plan`
- #2 기대 `retrieve_places;estimate_travel;validate_plan` · 사용 `estimate_travel, get_weather, retrieve_places, validate_plan`
- #3 기대 `retrieve_places` · 사용 `estimate_travel, get_weather, retrieve_places, validate_plan`
- #4 기대 `search_festivals` · 사용 `search_festivals`
- #5 기대 `get_weather;retrieve_places;validate_plan` · 사용 `estimate_travel, get_weather, retrieve_places, validate_plan`
- #6 기대 `get_weather` · 사용 `estimate_travel, get_weather, retrieve_places, validate_plan`
- #7 기대 `get_operating_info` · 사용 `get_operating_info`
- #8 기대 `retrieve_places;estimate_travel;validate_plan` · 사용 `estimate_travel, get_weather, retrieve_places, validate_plan`
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
