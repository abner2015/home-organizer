"""Ground the structure-proposal prompt in the caller's real home.

Two consumers, one load:

- The **prompt** needs the same grounding block the vision / inference paths
  use, so the model proposes room, furniture and position names that fit what
  this home already speaks.
- The **validator** needs the raw vocabulary — the category list, the existing
  room and unit names — to decide which of the model's answers to keep.

Both are derived from the same reads, so they cannot drift. Four read-only
queries, no LLM call.

A brand-new home is the interesting case: every one of these lists comes back
empty, and that is *correct* — it is what makes the first proposal a whole
structure rather than an extension of one.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.search.context import build_home_context, home_category_vocabulary
from app.tools.home_tools import get_rooms, get_storage_slots, get_storage_units
from app.tools.item_tools import get_items


@dataclass(slots=True)
class StructureContext:
    """What the proposal prompt and the validator each need about a home."""

    #: Rendered grounding block, injected into the prompt verbatim.
    text: str
    #: Sorted union of item categories and slot allowed_categories.
    categories: list[str]
    #: Rooms the home already has (used to flag a redundant proposal).
    room_names: list[str]
    #: Storage furniture the home already has, across all rooms.
    unit_names: list[str]


async def build_structure_context(
    db: AsyncSession, *, home_id: uuid.UUID
) -> StructureContext:
    """Load the caller's home and derive both views from a single pass.

    The grounding block is rendered by
    :func:`app.agents.search.context.build_home_context` itself rather than by
    delegating to ``item_inference_service.build_home_context_for`` — that
    wrapper would re-run the slots and items queries this function needs
    anyway.
    """
    slots = await get_storage_slots(db=db, home_id=home_id)
    items = await get_items(db=db, home_id=home_id)
    rooms = await get_rooms(db=db, home_id=home_id)
    units = await get_storage_units(db=db, home_id=home_id)

    return StructureContext(
        text=build_home_context(slots=slots, items=items),
        categories=home_category_vocabulary(slots, items),
        room_names=[str(room.get("name") or "") for room in rooms],
        unit_names=[str(unit.get("name") or "") for unit in units],
    )


__all__ = ["StructureContext", "build_structure_context"]
