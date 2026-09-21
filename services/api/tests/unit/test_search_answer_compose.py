"""Tests for the LLM answer-composition step.

The composer is the "use the model more" half of the assistant: retrieval
stays deterministic, but the sentence the user reads is written by the chat
model from the retrieved facts. It must never be able to *break* an answer,
so every failure path falls back to the deterministic draft.
"""
from __future__ import annotations

import pytest

from app.agents.search.answer import compose_answer
from app.ai.errors import AIProviderRefusedError, AIProviderTimeoutError
from app.ai.providers.mock import MockAIProvider

pytestmark = pytest.mark.asyncio

DRAFT = "数据线在 书房/书桌抽屉/第2格。"


async def test_returns_the_models_phrasing_when_it_answers() -> None:
    provider = MockAIProvider(chat_response="你的数据线在书桌抽屉第二格哦～")
    out = await compose_answer(provider, user_query="数据线在哪", draft=DRAFT)
    assert out == "你的数据线在书桌抽屉第二格哦～"


async def test_prompt_carries_the_draft_history_and_question() -> None:
    """The model can only rephrase what we hand it — pin all three inputs."""
    provider = MockAIProvider(chat_response="好的。")
    await compose_answer(
        provider,
        user_query="那它放卧室合适吗",
        draft=DRAFT,
        history="用户：数据线在哪\n助手：书桌抽屉",
    )
    _, kwargs = provider.recorded_calls[-1]
    prompt = kwargs["messages"][0]["content"]
    assert DRAFT in prompt
    assert "那它放卧室合适吗" in prompt
    assert "用户：数据线在哪" in prompt


async def test_empty_chat_reply_falls_back_to_the_draft() -> None:
    provider = MockAIProvider(chat_response="   ")
    assert await compose_answer(provider, user_query="q", draft=DRAFT) == DRAFT


async def test_timeout_falls_back_to_the_draft() -> None:
    """A chat hiccup degrades the wording, never the answer."""
    provider = MockAIProvider(chat_response="unused")
    provider.chat = _raising(AIProviderTimeoutError("boom"))  # type: ignore[method-assign]
    assert await compose_answer(provider, user_query="q", draft=DRAFT) == DRAFT


async def test_refusal_falls_back_to_the_draft() -> None:
    provider = MockAIProvider(chat_response="unused")
    provider.chat = _raising(AIProviderRefusedError("nope"))  # type: ignore[method-assign]
    assert await compose_answer(provider, user_query="q", draft=DRAFT) == DRAFT


async def test_no_call_is_made_for_an_empty_draft() -> None:
    provider = MockAIProvider(chat_response="should not be used")
    assert await compose_answer(provider, user_query="q", draft="  ") == "  "
    assert provider.call_count == 0


def _raising(exc: BaseException):
    async def _chat(messages, *, timeout_s=30.0):  # type: ignore[no-untyped-def]
        raise exc

    return _chat
