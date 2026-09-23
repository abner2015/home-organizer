"""Write endpoints for manual item placement ("反向录入", P0.3).

Until this router existed the only way an item got a location was accepting an
AI recommendation (``POST /recommendations/{id}/accept``). For something the
user already owns and already knows where to keep, that meant walking the whole
photo → recognise → recommend → accept loop to record a fact they already had.

Three routes, no LLM anywhere on the path:

- ``POST /placements`` puts an item into a slot the user picked.
- ``PATCH /placements/{id}`` (P0.7) edits a placement: change its note, move
  it to a different slot, or both. Move preserves the old note by default;
  passing an explicit ``note`` alongside the new ``slot_id`` overrides it.
- ``DELETE /placements/{id}`` ends a placement. This is a soft close
  (``removed_at``), never a physical delete — the item's timeline and the
  "where was this before" answer read these rows.

Auth is the shared ``get_actor`` dependency, so the same 404-not-403 rule as
everywhere else applies: a non-member of the home a placement belongs to gets
404, not 403.

This is the write half of what ``items.py`` already exposes on the read side
(``GET /items/{id}`` carries ``current_placement``; ``GET /items/{id}/placements``
carries the history), which is why the endpoints reuse ``ItemPlacementView``
instead of inventing a shape — the Web client already has a matching type.
"""
from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import placement_service
from app.agents.placement_service import _NO_CHANGE
from app.api.deps import Actor, get_actor
from app.core.exceptions import ValidationFailedError
from app.db.session import get_db
from app.models.placement import ItemPlacement
from app.schemas.item import ItemPlacementView, PlaceItemRequest, PlacementUpdateRequest
from app.tools.home_tools import get_storage_slots

router = APIRouter(prefix="/placements", tags=["placements"])


async def _slot_path(
    db: AsyncSession, *, home_id: uuid.UUID, slot_id: uuid.UUID
) -> str | None:
    """The display path of one slot (``"厨房/吊柜/上层/左"``).

    ``ItemPlacementView`` wants a path, not an id, and the path is assembled
    from four tables — ``get_storage_slots`` already does that join for the
    whole home. Loading the home's slots to label one row is fine at this
    scale, and it keeps a single implementation of the path format.
    """
    slots = await get_storage_slots(db=db, home_id=home_id)
    for slot in slots:
        if str(slot["id"]) == str(slot_id):
            return str(slot.get("full_path") or "") or None
    return None


def _placement_view(row: ItemPlacement, slot_path: str | None) -> ItemPlacementView:
    return ItemPlacementView(
        id=row.id,
        item_id=row.item_id,
        slot_id=row.slot_id,
        slot_path=slot_path,
        source=row.source,
        placed_at=row.placed_at.isoformat(),
        removed_at=row.removed_at.isoformat() if row.removed_at else None,
        note=row.note,
    )


@router.post(
    "",
    response_model=ItemPlacementView,
    status_code=status.HTTP_201_CREATED,
    summary="Put an item into a slot (no AI)",
)
async def create_placement(
    body: PlaceItemRequest,
    actor: Annotated[Actor, Depends(get_actor)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ItemPlacementView:
    """Record that an item now lives in ``slot_id``.

    The item's previous active placement is closed automatically — one item
    has one location. Unknown or cross-home item/slot → 404.
    """
    placement = await placement_service.place_item(
        db,
        home_id=actor.home_id,
        user_id=actor.user_id,
        item_id=body.item_id,
        slot_id=body.slot_id,
        note=body.note,
    )
    return _placement_view(
        placement,
        await _slot_path(db, home_id=actor.home_id, slot_id=placement.slot_id),
    )


@router.delete(
    "/{placement_id}",
    response_model=ItemPlacementView,
    summary="Take an item out of its slot (soft close)",
)
async def delete_placement(
    placement_id: uuid.UUID,
    actor: Annotated[Actor, Depends(get_actor)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ItemPlacementView:
    """End a placement by stamping ``removed_at``. The row is kept.

    Returns the closed row (200, not 204) so the caller can render
    ``removed_at`` without a follow-up GET. Repeating the call → 409.
    """
    placement = await placement_service.unplace_item(
        db, placement_id=placement_id, home_id=actor.home_id
    )
    return _placement_view(
        placement,
        await _slot_path(db, home_id=actor.home_id, slot_id=placement.slot_id),
    )


@router.patch(
    "/{placement_id}",
    response_model=ItemPlacementView,
    summary="Edit a placement's note and/or move it to a different slot",
)
async def patch_placement(
    placement_id: uuid.UUID,
    body: PlacementUpdateRequest,
    actor: Annotated[Actor, Depends(get_actor)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ItemPlacementView:
    """Edit one active placement (P0.7).

    Body shape — both fields optional, at least one required (``{}`` → 422):

    - ``note``: omitted → leave as-is, string → set, ``null`` → clear
    - ``slot_id``: omitted → leave as-is, UUID → move (close old + insert
      new row). A same-slot PATCH is a no-op for the slot and only touches
      the note.

    When only ``slot_id`` is given, the new row inherits the old row's
    ``note`` so "放到别处" preserves the annotation. When both are given,
    the explicit new ``note`` wins.

    The move path goes through :func:`app.tools.write_tools._create_placement`,
    so P0.5's partial-unique-index 409 fallback covers a concurrent edit
    for the same item (409 → retry). A PATCH on an already-closed placement
    returns 409 — history is read-only; edit a fresh row instead.
    """
    # Distinguish 「没传」 from 「传了 null」: Pydantic's ``model_fields_set``
    # only carries the names the client actually sent.
    provided = body.model_fields_set
    if "note" not in provided and "slot_id" not in provided:
        raise ValidationFailedError(
            "At least one of 'note' or 'slot_id' must be provided"
        )

    placement = await placement_service.update_placement(
        db,
        placement_id=placement_id,
        home_id=actor.home_id,
        user_id=actor.user_id,
        set_note=body.note if "note" in provided else _NO_CHANGE,
        set_slot_id=body.slot_id if "slot_id" in provided else _NO_CHANGE,
    )
    return _placement_view(
        placement,
        await _slot_path(db, home_id=actor.home_id, slot_id=placement.slot_id),
    )
