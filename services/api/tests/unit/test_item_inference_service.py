"""Tests for ``app.services.item_inference_service``.

The service is read-only apart from one ``AgentTrace`` row: it guesses an
item's attributes from its name so the Web app can prefill a form, and the
caller decides whether an ``Item`` ever gets created.
"""
from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.ai.provider import ItemInferenceOutput
from app.ai.providers.mock import MockAIProvider
from app.models import AgentTrace
from app.models.item import Item
from app.services import item_inference_service

pytestmark = pytest.mark.asyncio


_FULL = {
    "name": "雨伞",
    "category": "misc",
    "subcategory": "雨具",
    "description": "折叠长柄伞，放在门口方便拿取",
    "estimated_size": "medium",
    "is_sensitive": False,
    "needs_lock": False,
}


async def _count(db_session, model) -> int:
    return int((await db_session.execute(select(func.count()).select_from(model))).scalar_one())


async def test_infer_attributes_returns_output_and_traces(
    db_session, seeded_actor
) -> None:
    provider = MockAIProvider(structured_output_response=_FULL)
    result = await item_inference_service.infer_attributes(
        db_session,
        provider=provider,
        home_id=seeded_actor.home_id,
        user_id=seeded_actor.user_id,
        name="雨伞",
    )
    await db_session.commit()

    assert result.output.name == "雨伞"
    assert result.output.category == "misc"
    assert result.output.estimated_size == "medium"
    assert result.trace_id is not None

    trace = (
        await db_session.execute(
            select(AgentTrace).where(AgentTrace.id == result.trace_id)
        )
    ).scalar_one()
    assert trace.home_id == seeded_actor.home_id
    assert trace.item_id is None  # nothing has been created yet
    assert trace.final_status == "success"
    assert trace.steps[0]["state"] == "item_inference"


async def test_infer_attributes_creates_no_item(db_session, seeded_actor) -> None:
    """The endpoint is pre-creation; it must never persist an Item."""
    before = await _count(db_session, Item)
    provider = MockAIProvider(structured_output_response=_FULL)
    await item_inference_service.infer_attributes(
        db_session,
        provider=provider,
        home_id=seeded_actor.home_id,
        user_id=seeded_actor.user_id,
        name="雨伞",
    )
    await db_session.commit()
    assert await _count(db_session, Item) == before


async def test_explicit_nulls_are_tolerated(db_session, seeded_actor) -> None:
    """The prompt tells the model to leave fields blank; it may answer ``null``.

    ``ItemInferenceOutput`` is nullable for exactly this reason — a non-optional
    field would turn a valid "I don't know" into an ``AIOutputParseError``.
    """
    provider = MockAIProvider(
        structured_output_response={
            "name": "某种东西",
            "category": None,
            "subcategory": None,
            "description": None,
            "estimated_size": None,
            "is_sensitive": False,
            "needs_lock": False,
        }
    )
    result = await item_inference_service.infer_attributes(
        db_session,
        provider=provider,
        home_id=seeded_actor.home_id,
        user_id=seeded_actor.user_id,
        name="某种东西",
    )
    assert result.output.category is None
    assert result.output.estimated_size is None


async def test_prompt_is_grounded_in_the_caller_home(
    db_session, seeded_actor, storage_hierarchy
) -> None:
    """The prompt must carry real categories — an invented one matches no slot."""
    provider = MockAIProvider(structured_output_response=_FULL)
    await item_inference_service.infer_attributes(
        db_session,
        provider=provider,
        home_id=seeded_actor.home_id,
        user_id=seeded_actor.user_id,
        name="雨伞",
    )
    prompt = provider.recorded_calls[-1][1]["prompt"]
    assert "雨伞" in prompt
    # `storage_hierarchy`'s slots allow decor/books/utensil/food/medicine/misc,
    # and its items contribute utensil + medicine.
    assert "utensil" in prompt
    assert "medicine" in prompt
    assert provider.recorded_calls[-1][1]["schema"] == "ItemInferenceOutput"


async def test_null_tolerant_schema_defaults_sensitivity_to_false() -> None:
    out = ItemInferenceOutput.model_validate({"name": "杯子"})
    assert out.category is None
    assert out.is_sensitive is False
    assert out.needs_lock is False
