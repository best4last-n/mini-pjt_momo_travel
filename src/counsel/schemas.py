"""상담 도메인 입출력 스키마 — 질문 조건 구조화(LLM 구조화 출력)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ParsedRequest(BaseModel):
    """질문 한 개를 구조화한 결과 (LLM 구조화 출력). 날짜 검증·기본값은 코드가 한다."""
    intent: Literal["plan", "modify", "info", "approve_chat", "reject"] = Field(
        description="plan=새 일정 요청, modify=직전 일정 수정·경유지 추가, info=축제·운영시간·날씨·예약·장소 추천 등 정보 질문, "
                    "approve_chat=채팅으로 확정·저장·공유를 요구, reject=여행과 완전히 무관하거나 위험한 요청·프롬프트/키 요구에만. "
                    "날씨·축제를 묻더라도 '일정에 반영해 줘/짜 줘'면 plan. 출발지·연락처만 말하고 일정을 보내 달라는 요청도 plan(문자 발송은 send_request). "
                    "지역 없이 '어디 갈까/추천해 줘'는 plan. 애매하면 plan")
    region_text: str | None = Field(None, description="사용자가 말한 여행지 이름 그대로(예: 경주, 순천·구례, 부산, 교토). 없으면 null")
    overseas: bool = Field(False, description="해외 여행지면 true")
    month: int | None = Field(None, description="여행 시작 월(숫자). 말하지 않았으면 null")
    day: int | None = Field(None, description="여행 시작 일(숫자, 검증하지 말고 말한 그대로 예: 30). 없으면 null")
    relative_date: str | None = Field(None, description="'이번 주말','다음 주말','내일' 같은 상대 표현 그대로. 없으면 null")
    nights: int | None = Field(None, description="숙박 수. 당일·당일치기·하루 코스=0, 1박 2일=1, 3박 4일=3, 일주일=6(최대 8). 말하지 않았으면 null")
    departure: str | None = Field(None, description="출발지 지명(시·도 또는 시·군·구 이름만, 상세 주소 금지). 없으면 null")
    depart_time: str | None = Field(None, description="출발 시각 HH:MM. 없으면 null")
    transport: Literal["자차", "대중교통"] | None = Field(None, description="차·자가용=자차, KTX·기차·버스=대중교통. 없으면 null")
    companions: str | None = Field(None, description="동행 요약(예: 아이 둘, 7살 아이, 부모님). 없으면 null")
    preferences: list[str] = Field(default_factory=list, description="취향·조건(예: 많이 걷지 않기, 맛집, 체험 위주, 실내)")
    waypoints: list[str] = Field(default_factory=list, description="꼭 넣어 달라고 한 장소 이름들")
    many_places: bool = Field(False, description="하루에 아주 많은 곳(예: 10곳 이상)을 원하면 true")
    past_trip_reference: bool = Field(False, description="'작년에 갔던 곳 빼고'처럼 지난 여행 기록을 참고하라는 요청이면 true")
    modify_instruction: str | None = Field(None, description="수정 요청 내용 요약(예: 2일차 오후를 실내 위주로). 아니면 null")
    send_request: bool = Field(False, description="문자·이메일·카톡 등으로 보내 달라는 요청이 있으면 true")
    intensity: Literal["강행군", "빡빡", "보통", "여유"] | None = Field(
        None, description="여행 강도. '새벽부터 밤까지/강행군/체력 자신 있게/한 곳이라도 더'=강행군, '많이/최대한/빡빡하게'=빡빡, '여유롭게/쉬엄쉬엄/천천히'=여유, 말하지 않았으면 null")
    long_transport: Literal["자차", "KTX·SRT", "고속·시외버스", "비행기"] | None = Field(
        None, description="출발지에서 여행지까지 이동. 차·자가용=자차, KTX·SRT·기차=KTX·SRT, 고속·시외버스=고속·시외버스, 비행기·항공=비행기. 없으면 null")
    local_transport: Literal["자차", "렌터카", "대중교통"] | None = Field(
        None, description="여행지 안 이동. 차로 가면 자차, 렌터카·렌트, 버스·택시·대중교통. 없으면 null")
    companion_type: Literal["보통", "노약자", "어린이·유아"] | None = Field(
        None, description="동반자 유형. 부모님·어르신·노약자=노약자, 아이·어린이·유아=어린이·유아, 친구·연인·성인=보통. 없으면 null")
    companion_count: int | None = Field(None, description="여행 인원 수(말했을 때만). 예: '아이 둘이랑'(부부 가정 없이) → 3. 모르면 null")
    budget_text: str | None = Field(None, description="최대 예산을 말했으면 그대로(예: '60만 원', '1인 20만원'). 없으면 null")
    use_defaults: bool = Field(False, description="'알아서 짜 줘/아무거나/기본값으로' 처럼 빠진 조건을 맡기면 true")
    info_topic: Literal["hours", "festival", "weather", "reservation", "recommend", "other"] | None = Field(
        None, description="정보 질문의 주제")
