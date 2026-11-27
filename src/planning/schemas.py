"""일정 도메인 입출력 스키마 — 장소 선택(LLM 구조화 출력)과 일정 항목(API 출력)."""
from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class PickItem(BaseModel):
    place_id: str = Field(description="후보 목록의 id 그대로")
    stay_minutes: int = Field(description="체류 시간(분). 관광지 60~120, 식사 60, 숙박 0")
    role: Literal["place", "breakfast", "lunch", "dinner", "stay"] = "place"
    reason: str = Field(description="추천 이유 한 줄(후보 정보에 근거, 지어내지 말 것)")


class RankItem(BaseModel):
    place_id: str = Field(description="후보 목록의 관광지·축제 id")
    reason: str = Field(description="이 조건에서 이 순위인 이유 한 문장(동반자 적합성·대표성·시기·동선·관심사)")


class DayPick(BaseModel):
    day: int = Field(description="1부터")
    items: list[PickItem]


class PlanPick(BaseModel):
    """LLM은 장소 선택·순서·체류시간·이유만 정한다. 시각 계산·규칙 검사는 코드가 한다."""
    days: list[DayPick]
    ranked: list[RankItem] = Field(default_factory=list, description="추천 1~10위(관광지·축제만, 일정에 넣은 곳 포함, 우선순위 순)")
    note: str | None = Field(None, description="사용자에게 전할 짧은 메모(선택)")

    @field_validator("days", "ranked", mode="before")
    @classmethod
    def _json_string(cls, v):
        """모델이 목록을 JSON 문자열로 돌려주는 경우를 풀어서 받는다."""
        if isinstance(v, str):
            try:
                return json.loads(v)
            except json.JSONDecodeError:
                return []
        return v


class ItineraryItem(BaseModel):
    seq: int
    date: str
    type: Literal["place", "meal", "stay", "move"]
    place_id: str | None = None
    place_name: str
    start_time: str
    end_time: str
    travel_minutes_from_prev: int = 0
    transport_mode: str = "car"
    travel_source: str = "estimate"
    reason: str = ""
    source: str = "한국관광공사 TourAPI"
    hours_status: str = "확인됨"
    hours_text: str | None = None
    lat: float | None = None
    lng: float | None = None
    image_url: str | None = None
    image_license: str | None = None
    summary: str | None = None
    notes: list[str] = Field(default_factory=list)
