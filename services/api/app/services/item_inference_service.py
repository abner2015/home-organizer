"""Fill an item's attributes from its *name* alone (no image involved).

The Web app's add-item flow lets the user skip the photo. Today that means
hand-typing category / subcategory / size / description and ticking the
sensitivity boxes. This service asks the model to guess them instead, so the
user only confirms.

Two properties worth noting:

- **Grounded.** The prompt carries the caller's real category vocabulary (see
  :func:`build_home_context_for`). An invented category matches no storage
  slot, and `candidate_gen` drops slots whose `allowed_categories` omit the
  item's category — so an ungrounded guess would silently make the item
  unplaceable.
- **Read-only.** One `AgentTrace` row is written for observability; no `Item`
  row is created. The caller decides whether to persist anything.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import cast

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.prompts import render
from app.agents.search.context import build_home_context
from app.ai.provider import AIProvider, ItemInferenceOutput
from app.models import AgentTrace
from app.tools.home_tools import get_storage_slots
from app.tools.item_tools import get_items

# --------------------------------------------------------------------------- value objects


@dataclass(slots=True)
class InferenceResult:
    """Public-shaped inference outcome for one run."""

    output: ItemInferenceOutput
    trace_id: uuid.UUID | None


# --------------------------------------------------------------------------- context


async def build_home_context_for(db: AsyncSession, *, home_id: uuid.UUID) -> str:
    """Render the caller's real slots + items as a prompt grounding block.

    Shared by the vision and inference paths so both see the same vocabulary.
    Two read-only queries, no LLM call.
    """
    slots = await get_storage_slots(db=db, home_id=home_id)
    items = await get_items(db=db, home_id=home_id)
    return build_home_context(slots=slots, items=items)


# --------------------------------------------------------------------------- service


async def infer_attributes(
    db: AsyncSession,
    *,
    provider: AIProvider,
    home_id: uuid.UUID,
    user_id: uuid.UUID,
    name: str,
    description: str | None = None,
    timeout_s: float = 30.0,
) -> InferenceResult:
    """Guess an item's attributes from ``name`` (+ optional ``description``).

    Raises:
        AIProviderError subclasses: bubbled up from the provider.
        AIOutputParseError: the model's answer didn't match the schema.
    """
    home_context = await build_home_context_for(db, home_id=home_id)
    prompt = render(
        "infer",
        version=1,
        item_name=name,
        item_description=(description or ""),
        home_context=home_context,
    )

    # `structured_output` is typed as returning `BaseModel`; the provider
    # contract guarantees it is the schema we passed.
    output = cast(
        ItemInferenceOutput,
        await provider.structured_output(
            prompt, ItemInferenceOutput, timeout_s=timeout_s
        ),
    )

    trace = AgentTrace(
        home_id=home_id,
        user_id=user_id,
        item_id=None,  # no item exists yet — this endpoint is pre-creation
        steps=[
            {
                "state": "item_inference",
                "status": "success",
                "name": output.name,
                "category": output.category or "",
            }
        ],
        final_status="success",
        total_duration_ms=0,
        error=None,
    )
    db.add(trace)
    await db.flush()

    return InferenceResult(output=output, trace_id=trace.id)


__all__ = ["InferenceResult", "build_home_context_for", "infer_attributes"]
