"""Tests for the search-agent intent extraction (Phase 6).

These tests exercise :func:`extract_intent` against scripted
``MockAIProvider`` responses. The agent is read-only — no DB needed.
"""
from __future__ import annotations

import pytest

from app.agents.search.intent import (
    ExtractedSearchIntent,
    SearchIntentKind,
    extract_intent,
)
from app.ai.errors import (
    AIOutputParseError,
    AIProviderTransportError,
)
from app.ai.providers.mock import MockAIProvider

# ---------------------------------------------------------------------- helpers


def _scripted(payload: dict[str, object]) -> MockAIProvider:
    """Provider that returns ``payload`` once (single-call)."""
    return MockAIProvider(structured_output_response=payload)


def _scripted_seq(payloads: list[dict[str, object] | Exception]) -> MockAIProvider:
    """Provider that pops through ``payloads`` across multiple calls.

    Each entry may be a dict (parsed) or a ``BaseException`` (raised).
    """
    return MockAIProvider(structured_output_responses=list(payloads))


# ---------------------------------------------------------------------- enum tests


def test_search_intent_kind_values_match_documented_strings() -> None:
    """Enum string values must match the prompt schema — UI uses them verbatim."""
    assert SearchIntentKind.FIND_ITEM.value == "find_item"
    assert SearchIntentKind.FIND_ITEMS.value == "find_items"
    assert SearchIntentKind.FIND_LOCATION.value == "find_location"
    assert SearchIntentKind.CHECK_EXISTENCE.value == "check_existence"
    assert SearchIntentKind.LIST_CATEGORY.value == "list_category"
    assert SearchIntentKind.UNKNOWN.value == "unknown"


def test_extracted_intent_rejects_unknown_field() -> None:
    """``extra="forbid"`` keeps the LLM honest — stray fields are rejected."""
    with pytest.raises(Exception):  # ValidationError
        ExtractedSearchIntent.model_validate(
            {
                "intent": "find_item",
                "query": "马克杯",
                "rogue_field": "hello",  # type: ignore[typeddict-item]
            }
        )


# ---------------------------------------------------------------------- find_item


async def test_extract_find_item_intent() -> None:
    provider = _scripted(
        {
            "intent": "find_item",
            "query": "数据线",
            "category": "",
            "location_hint": "",
            "clarification_needed": False,
            "question": "",
        }
    )
    intent = await extract_intent(provider, user_query="我的数据线在哪里？")
    assert intent.intent == SearchIntentKind.FIND_ITEM
    assert intent.query == "数据线"
    assert intent.clarification_needed is False
    assert intent.question == ""


# ---------------------------------------------------------------------- find_items


async def test_extract_find_items_intent() -> None:
    provider = _scripted(
        {
            "intent": "find_items",
            "query": "工具",
            "category": "tool",
            "location_hint": "厨房",
            "clarification_needed": False,
            "question": "",
        }
    )
    intent = await extract_intent(provider, user_query="厨房里有什么工具？")
    assert intent.intent == SearchIntentKind.FIND_ITEMS
    assert intent.query == "工具"
    assert intent.category == "tool"
    assert intent.location_hint == "厨房"
    assert intent.clarification_needed is False


# ---------------------------------------------------------------------- find_location


async def test_extract_find_location_intent() -> None:
    provider = _scripted(
        {
            "intent": "find_location",
            "query": "",
            "category": "",
            "location_hint": "客厅装饰柜L1",
            "clarification_needed": False,
            "question": "",
        }
    )
    intent = await extract_intent(provider, user_query="客厅装饰柜L1放了什么？")
    assert intent.intent == SearchIntentKind.FIND_LOCATION
    assert intent.location_hint == "客厅装饰柜L1"
    assert intent.query == ""


# ---------------------------------------------------------------------- check_existence


async def test_extract_check_existence_intent() -> None:
    provider = _scripted(
        {
            "intent": "check_existence",
            "query": "电池",
            "category": "",
            "location_hint": "",
            "clarification_needed": False,
            "question": "",
        }
    )
    intent = await extract_intent(provider, user_query="我家还有没有备用电池？")
    assert intent.intent == SearchIntentKind.CHECK_EXISTENCE
    assert intent.query == "电池"


# ---------------------------------------------------------------------- list_category


async def test_extract_list_category_intent() -> None:
    provider = _scripted(
        {
            "intent": "list_category",
            "query": "",
            "category": "kitchen",
            "location_hint": "",
            "clarification_needed": False,
            "question": "",
        }
    )
    intent = await extract_intent(provider, user_query="我家所有的厨房用品？")
    assert intent.intent == SearchIntentKind.LIST_CATEGORY
    assert intent.category == "kitchen"
    assert intent.query == ""


# ---------------------------------------------------------------------- unknown


async def test_unknown_intent_on_garbage() -> None:
    """LLM returns ``unknown`` with a clarifying follow-up question."""
    provider = _scripted(
        {
            "intent": "unknown",
            "query": "",
            "category": "",
            "location_hint": "",
            "clarification_needed": True,
            "question": "请问您想找什么物品？",
        }
    )
    intent = await extract_intent(provider, user_query="啊吧啊吧")
    assert intent.intent == SearchIntentKind.UNKNOWN
    assert intent.clarification_needed is True
    assert intent.question == "请问您想找什么物品？"


async def test_unknown_intent_without_clarification_flag_is_coerced() -> None:
    """LLM returned ``unknown`` but forgot to set ``clarification_needed`` —
    we coerce the flag so the UI always has a follow-up to show."""
    provider = _scripted(
        {
            "intent": "unknown",
            "query": "",
            "category": "",
            "location_hint": "",
            "clarification_needed": False,
            "question": "",
        }
    )
    intent = await extract_intent(provider, user_query="嗯？")
    assert intent.intent == SearchIntentKind.UNKNOWN
    assert intent.clarification_needed is True
    assert intent.question  # synthesised, non-empty


# ---------------------------------------------------------------------- error paths


async def test_provider_transport_error_bubbles_up() -> None:
    """Transport errors must NOT be swallowed — the agent surfaces them as
    ``state='error'``. Only parse errors collapse to UNKNOWN.
    """
    provider = _scripted_seq([AIProviderTransportError("connection reset by peer")])
    with pytest.raises(AIProviderTransportError):
        await extract_intent(provider, user_query="我的数据线在哪里？")


async def test_provider_parse_error_falls_back_to_unknown() -> None:
    """``AIOutputParseError`` (LLM responded but garbage) → UNKNOWN fallback."""
    provider = _scripted_seq(
        [
            AIOutputParseError(
                "schema mismatch", snippet="bad", schema="ExtractedSearchIntent"
            )
        ]
    )
    intent = await extract_intent(provider, user_query="马克杯在哪？")
    assert intent.intent == SearchIntentKind.UNKNOWN
    assert intent.clarification_needed is True
    assert intent.question  # non-empty


async def test_malformed_payload_falls_back_to_unknown() -> None:
    """Provider returns something not shaped like ExtractedSearchIntent."""
    provider = _scripted(
        {"bogus": "shape"}  # missing ``intent`` → ValidationError
    )
    intent = await extract_intent(provider, user_query="anything")
    assert intent.intent == SearchIntentKind.UNKNOWN
    assert intent.clarification_needed is True


# ---------------------------------------------------------------------- multi-call


async def test_structured_output_responses_supports_multi_call() -> None:
    """``structured_output_responses`` pops one entry per call (Phase 6 pattern)."""
    provider = _scripted_seq(
        [
            {
                "intent": "find_item",
                "query": "数据线",
                "category": "",
                "location_hint": "",
                "clarification_needed": False,
                "question": "",
            },
            {
                "intent": "check_existence",
                "query": "电池",
                "category": "",
                "location_hint": "",
                "clarification_needed": False,
                "question": "",
            },
        ]
    )
    a = await extract_intent(provider, user_query="数据线在哪？")
    b = await extract_intent(provider, user_query="有没有电池？")
    assert a.intent == SearchIntentKind.FIND_ITEM
    assert b.intent == SearchIntentKind.CHECK_EXISTENCE
    assert provider.call_count == 2


async def test_structured_output_responses_exhausted_raises() -> None:
    """When the mock's scripted queue runs out, ``RuntimeError`` propagates.

    ``extract_intent`` no longer catches ``RuntimeError`` (it only swallows
    ``AIOutputParseError``-class failures); the agent layer catches it and
    turns it into ``state='error'``. The test asserts the mock behaviour
    + that the queue is empty after the first pop.
    """
    provider = _scripted_seq(
        [
            {
                "intent": "find_item",
                "query": "数据线",
                "category": "",
                "location_hint": "",
                "clarification_needed": False,
                "question": "",
            }
        ]
    )
    # First call: scripted entry → find_item.
    first = await extract_intent(provider, user_query="数据线在哪？")
    assert first.intent == SearchIntentKind.FIND_ITEM

    # Second call: mock raises RuntimeError; extract_intent lets it bubble.
    assert provider.structured_output_responses == []
    with pytest.raises(RuntimeError, match="exhausted"):
        await extract_intent(provider, user_query="再一次？")
