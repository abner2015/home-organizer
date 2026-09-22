"""Item endpoints (Phase 4+).

Phase 4 scope: only ``POST /items/recognize``. Phase 10 adds the read routes
the Web app needs — ``GET /items`` (paged + filtered), ``GET /items/{id}``,
``GET /items/{id}/placements`` and ``GET /items/{id}/candidates``.

The read routes enrich rows with the ``current_placement`` and image URLs the
frontend renders; neither is a column on ``items``.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.candidate_gen import generate_candidates, hard_filter
from app.agents.ranking import rank_slots
from app.ai.factory import get_provider
from app.ai.provider import AIProvider
from app.api.deps import Actor, get_actor
from app.core.exceptions import NotFoundError, ValidationFailedError
from app.db.session import get_db
from app.models.item import Item, ItemImage
from app.models.placement import ItemPlacement
from app.models.recommendation import Recommendation
from app.schemas.item import (
    CandidateListResponse,
    InferItemRequest,
    ItemCreateRequest,
    ItemInferenceResponse,
    ItemPlacementView,
    ItemUpdateRequest,
    ItemView,
    ItemVisionResponse,
    ItemVisionView,
    PaginatedItemsView,
    PlacementRefView,
)
from app.schemas.recognition import RecognizeRequest, RecognizeResponse
from app.services import (
    asset_service,
    item_inference_service,
    recognition_service,
    recommendation_service,
    vision_service,
)
from app.services.image_payload import to_data_uri
from app.storage.backend import get_storage
from app.tools.context_tools import get_home_rules, get_user_preferences
from app.tools.home_tools import get_storage_slots
from app.tools.item_tools import get_item_placements
from app.tools.recommendation_tools import get_rejected_slot_ids

router = APIRouter(prefix="/items", tags=["items"])


def _get_ai_provider() -> AIProvider:
    """FastAPI dependency. Overridden in tests to inject a mock."""
    return get_provider()


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt is not None else None


def _image_url(image: ItemImage) -> str:
    """Fresh presigned GET URL for a stored item image."""
    return asset_service.presigned_url_for_key(image.object_key)


def _check_object_key_belongs_to_home(object_key: str, home_id: uuid.UUID) -> None:
    """Reject image keys outside the caller's home prefix.

    Keys are minted by ``POST /uploads/presign`` as ``home/<home_uuid>/...``.
    Without this check a client could attach any key it happens to know,
    and the API would happily hand back a presigned GET for it.
    """
    if not object_key.startswith(f"home/{home_id}/"):
        raise ValidationFailedError(
            "image_object_key does not belong to this home",
            details={"object_key": object_key},
        )


async def _load_item(db: AsyncSession, *, item_id: uuid.UUID, home_id: uuid.UUID) -> Item:
    """Fetch one item in the caller's home (cross-home / unknown → 404)."""
    item = (
        await db.execute(select(Item).where(Item.id == item_id, Item.home_id == home_id))
    ).scalar_one_or_none()
    if item is None:
        raise NotFoundError("Item not found")
    return item


async def _slot_paths(db: AsyncSession, *, home_id: uuid.UUID) -> dict[str, str]:
    """``slot_id → "room/unit/section/code"`` for the whole home."""
    slots = await get_storage_slots(db=db, home_id=home_id)
    return {s["id"]: str(s.get("full_path") or "") for s in slots}


async def _placement_reasons(
    db: AsyncSession, rows: list[dict[str, Any]]
) -> dict[str, str]:
    """``placement_id → the reason recorded when it was placed``.

    Resolved through ``placement.recommendation_id``: the reason lives on the
    candidate entry of the recommendation that produced the placement. One
    batched ``IN`` query rather than a lookup per row.
    """
    rec_ids = {
        uuid.UUID(str(row["recommendation_id"]))
        for row in rows
        if row.get("recommendation_id")
    }
    if not rec_ids:
        return {}
    recs = (
        await db.execute(select(Recommendation).where(Recommendation.id.in_(rec_ids)))
    ).scalars().all()
    by_rec: dict[str, dict[str, str]] = {}
    for rec in recs:
        by_rec[str(rec.id)] = {
            str(c.get("slot_id")): str(c.get("reason") or "")
            for c in (rec.candidates or [])
            if isinstance(c, dict)
        }
    return {
        row["id"]: by_rec.get(str(row["recommendation_id"]), {}).get(row["slot_id"], "")
        for row in rows
        if row.get("recommendation_id")
    }


async def _active_placement_by_item(
    db: AsyncSession, *, item_ids: list[uuid.UUID]
) -> dict[uuid.UUID, ItemPlacement]:
    """One active placement per item (the partial unique index guarantees it)."""
    if not item_ids:
        return {}
    rows = (
        await db.execute(
            select(ItemPlacement).where(
                ItemPlacement.item_id.in_(item_ids),
                ItemPlacement.removed_at.is_(None),
            )
        )
    ).scalars().all()
    return {p.item_id: p for p in rows}


async def _item_views(
    db: AsyncSession, *, home_id: uuid.UUID, items: list[Item]
) -> list[ItemView]:
    """Project ORM items into the UI shape (placement + images + timestamps)."""
    paths = await _slot_paths(db, home_id=home_id)
    placements = await _active_placement_by_item(db, item_ids=[i.id for i in items])
    views: list[ItemView] = []
    for item in items:
        placement = placements.get(item.id)
        views.append(
            ItemView(
                id=item.id,
                home_id=item.home_id,
                name=item.name,
                description=item.description,
                category=item.category,
                subcategory=item.subcategory,
                estimated_size=item.estimated_size,
                is_sensitive=item.is_sensitive,
                needs_lock=item.needs_lock,
                # Re-sign rather than serving `item_images.url`, which is a
                # snapshot taken when the row was written (TTL ~1h).
                primary_image_url=(
                    _image_url(item.primary_image)
                    if item.primary_image is not None
                    else None
                ),
                image_urls=[_image_url(img) for img in (item.images or [])],
                current_placement=(
                    PlacementRefView(
                        slot_id=placement.slot_id,
                        slot_path=paths.get(str(placement.slot_id), ""),
                    )
                    if placement is not None
                    else None
                ),
                created_at=_iso(item.created_at),
                updated_at=_iso(item.updated_at),
            )
        )
    return views


@router.post(
    "/recognize",
    response_model=RecognizeResponse,
    status_code=status.HTTP_200_OK,
    summary="Recognize an item from a previously uploaded image",
)
async def recognize_item(
    payload: RecognizeRequest,
    actor: Annotated[Actor, Depends(get_actor)],
    db: Annotated[AsyncSession, Depends(get_db)],
    provider: Annotated[AIProvider, Depends(_get_ai_provider)],
) -> RecognizeResponse:
    """Identify an item from ``asset_id`` and return structured fields.

    The asset must have been uploaded via ``POST /api/v1/assets/upload``
    and be in ``ready`` status. The asset must belong to the calling
    home; cross-home access returns 404.

    Failures:

    - ``404 not_found`` — asset missing or wrong home.
    - ``400 validation_error`` — asset not yet ready.
    - ``503 ai_*`` — provider unavailable / parse failure / timeout
      / auth error. See :mod:`app.ai.errors` for the taxonomy.
    """
    result = await recognition_service.recognize_from_asset(
        db,
        provider=provider,
        home_id=actor.home_id,
        user_id=actor.user_id,
        asset_id=payload.asset_id,
        description=payload.description,
        context=await item_inference_service.build_home_context_for(
            db, home_id=actor.home_id
        ),
    )
    await db.commit()
    return RecognizeResponse(
        result=result.output.model_dump(),  # type: ignore[arg-type]
        trace_id=result.trace_id,
        attempts=result.attempts,
        duration_ms=result.total_duration_ms,
        provider=provider.name,
    )


# ------------------------------------------------------------------ write routes


@router.post(
    "",
    response_model=ItemView,
    status_code=status.HTTP_201_CREATED,
    summary="Create an item",
)
async def create_item(
    payload: ItemCreateRequest,
    actor: Annotated[Actor, Depends(get_actor)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ItemView:
    """Create an item, optionally attaching already-uploaded images.

    ``image_object_keys`` come from ``POST /uploads/presign``. Each must sit
    under the caller's home prefix; anything else is a 400. The item may be
    created with no images at all (the Web app's manual path).
    """
    item = Item(
        home_id=actor.home_id,
        name=payload.name,
        description=payload.description,
        category=payload.category,
        subcategory=payload.subcategory,
        estimated_size=payload.estimated_size,
        is_sensitive=payload.is_sensitive,
        needs_lock=payload.needs_lock,
        created_by=actor.user_id,
    )
    db.add(item)
    await db.flush()

    primary_key = payload.primary_image_object_key
    images: list[ItemImage] = []
    for object_key in payload.image_object_keys:
        _check_object_key_belongs_to_home(object_key, actor.home_id)
        is_primary = primary_key is not None and object_key == primary_key
        image = ItemImage(
            item_id=item.id,
            object_key=object_key,
            url=asset_service.presigned_url_for_key(object_key),
            is_primary=is_primary,
        )
        db.add(image)
        images.append(image)
    await db.flush()

    if primary_key is not None:
        primary = next((i for i in images if i.is_primary), None)
        if primary is None:
            raise ValidationFailedError(
                "primary_image_object_key is not one of image_object_keys",
                details={"primary_image_object_key": primary_key},
            )
        item.primary_image_id = primary.id

    await db.commit()
    await db.refresh(item)
    return (await _item_views(db, home_id=actor.home_id, items=[item]))[0]


@router.patch(
    "/{item_id}",
    response_model=ItemView,
    summary="Update an item's attributes",
)
async def update_item(
    item_id: uuid.UUID,
    payload: ItemUpdateRequest,
    actor: Annotated[Actor, Depends(get_actor)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ItemView:
    """Apply the provided fields; omitted fields are left untouched.

    The Web app uses this after the vision-confirm step so the user's edits
    survive into the recommendation run.
    """
    item = await _load_item(db, item_id=item_id, home_id=actor.home_id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(item, field, value)
    await db.commit()
    await db.refresh(item)
    return (await _item_views(db, home_id=actor.home_id, items=[item]))[0]


@router.post(
    "/{item_id}/vision",
    response_model=ItemVisionResponse,
    summary="Recognize an item's image and apply the result",
)
async def recognize_item_vision(
    item_id: uuid.UUID,
    actor: Annotated[Actor, Depends(get_actor)],
    db: Annotated[AsyncSession, Depends(get_db)],
    provider: Annotated[AIProvider, Depends(_get_ai_provider)],
) -> ItemVisionResponse:
    """Run Vision on the item's primary image.

    The recognised ``name`` / ``category`` / ``subcategory`` / ``estimated_size``
    / ``is_sensitive`` / ``needs_lock`` are written back onto the item
    (``description`` only when the user hasn't set one) so the result is visible
    from ``GET /items`` too, not just in this response.

    ``confidence`` is still not derived — the Phase 4 Vision schema has no such
    field, and the mock/real providers don't agree on one, so the UI is told
    ``null`` rather than shown a fabricated number.

    The image is inlined as a ``data:`` URI: the model must not be asked to
    fetch a URL (see :mod:`app.services.image_payload`).

    Failures mirror ``POST /items/recognize``: 404 unknown/cross-home item,
    400 item has no image, 503 for provider errors.
    """
    item = await _load_item(db, item_id=item_id, home_id=actor.home_id)
    if item.primary_image is None:
        raise ValidationFailedError("Item has no image to recognize")

    result = await vision_service.recognize_image(
        db,
        provider=provider,
        image_url=to_data_uri(get_storage().get(item.primary_image.object_key)),
        home_id=actor.home_id,
        user_id=actor.user_id,
        context=await item_inference_service.build_home_context_for(
            db, home_id=actor.home_id
        ),
    )
    output = result.output

    item.name = output.name
    item.category = output.category
    item.subcategory = output.subcategory or None
    item.estimated_size = output.size_class
    item.is_sensitive = output.is_sensitive
    item.needs_lock = output.needs_lock
    if not item.description:
        item.description = output.notes or None
    await db.commit()
    await db.refresh(item)

    return ItemVisionResponse(
        item_id=item.id,
        vision=ItemVisionView(
            name=output.name,
            category=output.category,
            subcategory=output.subcategory,
            description=output.notes,
            estimated_size=output.size_class,
            is_sensitive=output.is_sensitive,
            needs_lock=output.needs_lock,
        ),
        trace_id=result.trace_id,
    )


@router.post(
    "/infer",
    response_model=ItemInferenceResponse,
    summary="Guess an item's attributes from its name",
)
async def infer_item_attributes(
    payload: InferItemRequest,
    actor: Annotated[Actor, Depends(get_actor)],
    db: Annotated[AsyncSession, Depends(get_db)],
    provider: Annotated[AIProvider, Depends(_get_ai_provider)],
) -> ItemInferenceResponse:
    """Fill in category / subcategory / size / description / sensitivity from a
    name, so the user only has to confirm the form.

    The prompt carries the caller's real category vocabulary — an invented
    category matches no storage slot, and the recommendation pipeline would
    then return no candidates at all.

    Read-only apart from one ``AgentTrace`` row: no ``Item`` is created. The
    Web app turns the result into a prefilled form and posts to ``POST /items``
    once the user confirms.
    """
    result = await item_inference_service.infer_attributes(
        db,
        provider=provider,
        home_id=actor.home_id,
        user_id=actor.user_id,
        name=payload.name,
        description=payload.description,
    )
    await db.commit()

    output = result.output
    return ItemInferenceResponse(
        vision=ItemVisionView(
            name=output.name,
            category=output.category or "",
            subcategory=output.subcategory or "",
            description=output.description or "",
            estimated_size=output.estimated_size,
            is_sensitive=output.is_sensitive,
            needs_lock=output.needs_lock,
        ),
        trace_id=result.trace_id,
    )


# ------------------------------------------------------------------- read routes


@router.get("", response_model=PaginatedItemsView, summary="List / search items")
async def list_items(
    actor: Annotated[Actor, Depends(get_actor)],
    db: Annotated[AsyncSession, Depends(get_db)],
    q: Annotated[str | None, Query(max_length=200, description="Name substring")] = None,
    category: Annotated[str | None, Query(max_length=100)] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 20,
) -> PaginatedItemsView:
    """Newest-first page of the caller's items, with each item's placement."""
    stmt = select(Item).where(Item.home_id == actor.home_id)
    if q:
        stmt = stmt.where(Item.name.ilike(f"%{q}%"))
    if category:
        stmt = stmt.where(Item.category == category)
    total = int(
        (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    )
    rows = (
        await db.execute(
            stmt.order_by(Item.created_at.desc(), Item.name)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars().all()
    return PaginatedItemsView(
        items=await _item_views(db, home_id=actor.home_id, items=list(rows)),
        page=page,
        page_size=page_size,
        total=total,
    )


@router.get("/{item_id}", response_model=ItemView, summary="Fetch one item")
async def get_item(
    item_id: uuid.UUID,
    actor: Annotated[Actor, Depends(get_actor)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ItemView:
    """Single item with its current placement. Cross-home / unknown → 404."""
    item = await _load_item(db, item_id=item_id, home_id=actor.home_id)
    return (await _item_views(db, home_id=actor.home_id, items=[item]))[0]


@router.get(
    "/{item_id}/placements",
    response_model=list[ItemPlacementView],
    summary="Placement history for an item",
)
async def list_item_placements(
    item_id: uuid.UUID,
    actor: Annotated[Actor, Depends(get_actor)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[ItemPlacementView]:
    """Every placement ever recorded for the item, newest first.

    An ``ai_recommendation`` placement carries the reason the model gave at the
    time (P0.4's "为什么放这里"): the slot's entry in the originating
    recommendation's candidate list. A manual placement has none, and its
    ``reason`` stays empty.
    """
    await _load_item(db, item_id=item_id, home_id=actor.home_id)
    paths = await _slot_paths(db, home_id=actor.home_id)
    rows = await get_item_placements(db=db, home_id=actor.home_id, item_id=item_id)
    reasons = await _placement_reasons(db, rows)
    return [
        ItemPlacementView(
            id=row["id"],
            item_id=row["item_id"],
            slot_id=row["slot_id"],
            slot_path=paths.get(row["slot_id"]),
            source=row["source"],
            placed_at=row["placed_at"],
            removed_at=row.get("removed_at"),
            note=row.get("note"),
            reason=reasons.get(row["id"], ""),
        )
        for row in rows
    ]


@router.get(
    "/{item_id}/candidates",
    response_model=CandidateListResponse,
    summary="Deterministic candidates for an item (no LLM call)",
)
async def list_item_candidates(
    item_id: uuid.UUID,
    actor: Annotated[Actor, Depends(get_actor)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> CandidateListResponse:
    """Run RETRIEVE → CANDIDATE_GENERATION → FILTER → RANK without DECIDE.

    Same pure functions the agent uses, so the result is exactly the ranked
    list the LLM would be shown — useful for debugging a recommendation and
    for a UI that wants candidates without paying for a model call.

    Slots the user has rejected for this item are dropped here too (P0.4), so
    this endpoint agrees with the agent's own FILTER step. Every candidate
    carries a deterministic Chinese ``reason`` built by the ranker.
    """
    item = await _load_item(db, item_id=item_id, home_id=actor.home_id)
    slots = await get_storage_slots(db=db, home_id=actor.home_id)
    rules = await get_home_rules(db=db, home_id=actor.home_id)
    preferences = await get_user_preferences(
        db=db, home_id=actor.home_id, user_id=actor.user_id
    )
    history = await get_item_placements(
        db=db, home_id=actor.home_id, item_id=item_id
    )
    excluded = await get_rejected_slot_ids(
        db=db, home_id=actor.home_id, item_id=item_id
    )

    item_dict = {
        "id": str(item.id),
        "name": item.name,
        "category": item.category,
        "estimated_size": item.estimated_size,
        "is_sensitive": item.is_sensitive,
    }
    generated = [
        c
        for c in generate_candidates(slots, item_dict)
        if uuid.UUID(str(c["id"])) not in excluded
    ]
    filtered = hard_filter(
        generated,
        item=item_dict,
        hard_rules=[r for r in rules if r.get("rule_type") == "hard"],
        active_count={uuid.UUID(str(s["id"])): int(s.get("active_count") or 0) for s in generated},
    )
    ranked = rank_slots(
        filtered,
        item=item_dict,
        preferences=preferences,
        history=history,
        soft_rules=[r for r in rules if r.get("rule_type") == "soft"],
        limit=20,
    )
    return CandidateListResponse(
        pre_filter_count=len(generated),
        post_filter_count=len(filtered),
        final_candidates=[
            recommendation_service.candidate_view_from_slot(slot) for slot in ranked
        ],
    )
