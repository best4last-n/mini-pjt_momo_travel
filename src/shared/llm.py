"""LLM 공용 (Bedrock Claude Sonnet 4.5, ChatBedrockConverse)."""
from __future__ import annotations

from functools import lru_cache

from src.shared.config import AWS_REGION, GEN_MODEL


@lru_cache
def _chat(temperature: float, max_tokens: int):
    from langchain_aws import ChatBedrockConverse
    return ChatBedrockConverse(model=GEN_MODEL, region_name=AWS_REGION, temperature=temperature, max_tokens=max_tokens)


def chat(temperature: float = 0.2, max_tokens: int = 2000):
    from src.shared.cancel import check
    check()                      # 사용자가 요청을 중지했으면 다음 LLM 호출 전에 멈춤
    return _chat(temperature, max_tokens)


def structured(schema, temperature: float = 0.0, max_tokens: int = 2000):
    return chat(temperature, max_tokens).with_structured_output(schema)
