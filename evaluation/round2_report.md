# Round 2 자체 평가 리포트

- 실행: `python evaluation/run_eval.py --round 2` · 평가 기준일 2026-11-25(MOMO_TODAY) · 채점 모델 us.anthropic.claude-sonnet-4-6(temperature 0)
- 채점 장치 고정 증거: test_queries.csv SHA-256 `1b4c351c0221e4a988151b347517de276d6901db19583627c425612a79424ab4` · 응답 수집 커밋 `f3a71f6` · 채점 실행 커밋 `f3a71f6`
- 수집 데이터: 순천/구례 70곳 + 경주 60곳 + 수원 62곳 + 제주 60곳 + 강릉·동해·삼척 61곳 + 전주·완주·군산 60곳 + 통영·거제 60곳

## 결과 요약

| 항목 | 결과 | 목표 |
|---|---|---|
| 인-아웃 통과 | **15 / 20 (75%)** | 85% 이상(17건+) |
| guardrail | 3 / 3 | 100% |
| 규칙 위반(전체 응답) | 2건 | 0건 |
| 평균 응답 시간 | 26.4초 | — |

카테고리별: positive 7/8 · negative 3/4 · edge 2/5 · guardrail 3/3

## RAGAS (contexts가 있는 케이스 평균, 케이스 통과와 별도)

| 지표 | 값 | 프로젝트 기준 |
|---|---|---|
| faithfulness | 0.525 | ≥ 0.8 |
| answer_relevancy | 0.562 | ≥ 0.8 |
| context_precision | 0.508 | ≥ 0.7 |
| context_recall | 0.439 | ≥ 0.7 |

- 대상 11건, 제외(contexts 없음: 거절·되묻기·지원 밖 안내 등) #9, #11, #13, #14, #16, #17, #18, #19, #20

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
| 8 | positive | 승인 대기 | ❌ | 금지 검출: 운영시간 밖 방문 — 미식가의주방 운영시간이 11:00~20:00인데 방문 시간이 18:00~19:00으로 운영시간 내이나, 화성행궁 운영시간이 09:00~17:00인데 방문 종료 시간이 14:45로 운영시간 내임. 그러나 저녁 식사 장소인 미식가의주방의 경우 이동 5분을 감안하면 16:30 종료 후 18:00 도착으로 문제없음. 단, 수원전통문화관 10:30 종료 후 다음 일정 시작이 12:00으로 약 90분 공백이 있으나 운영시간 위반은 아님. 모든 방문 시간대가 각 장소의 운영시간 내에 있어 위반 없음. |
| 9 | negative | 되묻기 | ✅ |  |
| 10 | negative | 정보 | ✅ |  |
| 11 | negative | 정보 | ✅ |  |
| 12 | negative | 승인 대기 | ❌ | 미충족: 당일~1박 2일까지만 지원한다는 안내 — 응답 어디에도 '당일 또는 1박 2일까지만 지원한다'는 안내가 없다. 오히려 3박 4일 일정을 그대로 제공했다.<br>미충족: 1박 2일 일정으로 줄여 제안하거나 나눠서 계획 제안 — 1박 2일로 축소하거나 나눠서 제안하는 내용이 전혀 없다. 요청된 3박 4일 일정을 그대로 제시했다.<br>금지 검출: 3박 4일 일정을 검증된 것처럼 제시 — 응답이 3박 4일(11월 28일~12월 1일) 전체 일정을 한국관광공사 TourAPI 출처와 함께 확인된 정보인 것처럼 상세히 제시하고 있다. |
| 13 | edge | 되묻기 | ✅ |  |
| 14 | edge | 되묻기 | ✅ |  |
| 15 | edge | 승인 대기 | ❌ | 이동시간 0분: 아침 식사 · 주변 식당 → 주변에서 점심 식사<br>이동시간 0분: 숙소 근처 아침 식사 → 주변에서 점심 식사 |
| 16 | edge | 정보 | ❌ | 미충족: 해외(엽서) 테마 값 — theme scope가 overseas로 설정되어 있으나, 응답 내에 엽서 테마 관련 UI 요소나 언급이 없음.<br>미충족: 날씨 정보 — 응답 어디에도 교토 11월 날씨 정보가 포함되어 있지 않음. |
| 17 | edge | 되묻기 / 되묻기 | ❌ | [add-on on] 미충족: add-on 켬: 지난 여행 기록 참고해 이미 간 곳 제외 — 응답은 출발지·일정·이동수단을 되묻는 내용만 있고, 지난 여행 기록을 참고해 이미 간 곳을 제외하겠다는 언급이 전혀 없다.<br>[add-on off] 미충족: add-on 꺼짐: 지난 기록을 볼 수 없다는 안내 후 일반 일정 — 응답에 '지난 기록을 볼 수 없다'는 안내가 전혀 없고, 일반 일정도 제공되지 않았다. 대신 여행 조건을 되묻는 내용만 있다. |
| 18 | guardrail | 되묻기 | ✅ |  |
| 19 | guardrail | 거절 | ✅ |  |
| 20 | guardrail | 승인 대기 | ✅ |  |

## 참고: expected_tools (통과 판정에 미포함)

- #1 기대 `retrieve_places;get_operating_info;estimate_travel;validate_plan` · 사용 `estimate_travel, get_weather, retrieve_places, validate_plan`
- #2 기대 `retrieve_places;estimate_travel;validate_plan` · 사용 `estimate_travel, get_weather, optimize_route, retrieve_places, validate_plan`
- #3 기대 `retrieve_places` · 사용 `estimate_travel, get_weather, optimize_route, retrieve_places, validate_plan`
- #4 기대 `search_festivals` · 사용 `search_festivals`
- #5 기대 `get_weather;retrieve_places;validate_plan` · 사용 `estimate_travel, get_weather, retrieve_places, validate_plan`
- #6 기대 `get_weather` · 사용 `estimate_travel, get_weather, optimize_route, retrieve_places, validate_plan`
- #7 기대 `get_operating_info` · 사용 `get_operating_info`
- #8 기대 `retrieve_places;estimate_travel;validate_plan` · 사용 `estimate_travel, get_weather, retrieve_places, validate_plan`
- #9 기대 `retrieve_places` · 사용 `-`
- #10 기대 `retrieve_places` · 사용 `retrieve_places`
- #11 기대 `-` · 사용 `-`
- #12 기대 `-` · 사용 `estimate_travel, get_weather, optimize_route, retrieve_places, validate_plan`
- #13 기대 `-` · 사용 `-`
- #14 기대 `-` · 사용 `-`
- #15 기대 `retrieve_places;estimate_travel;validate_plan` · 사용 `estimate_travel, get_weather, optimize_route, retrieve_places, validate_plan`
- #16 기대 `geocode_place;get_weather` · 사용 `-`
- #17 기대 `recall_past_trips;retrieve_places` · 사용 `-`
- #18 기대 `-` · 사용 `-`
- #19 기대 `-` · 사용 `-`
- #20 기대 `-` · 사용 `-`
