"""Recommendation endpoints (Phase 5).

Four endpoints:

- ``POST /api/v1/recommendations/items/{item_id}/recommend`` — run the 9-step
  pipeline, return top-3 candidates + chosen slot.
- ``POST /api/v1/recommendations/{rec_id}/accept`` — mark recommendation
  ``accepted`` and create an ItemPlacement row.
- ``POST /api/v1/recommendations/{rec_id}/reject`` — mark recommendation
  ``rejected`` (no placement).
- ``PATCH /api/v1/recommendations/{rec_id}`` — user moves chosen_slot_id
  before accept (status stays ``pending``).

All endpoints require ``X-User-Id`` + ``X-Home-Id`` headers (stub auth; real
JWT lands with the user-facing frontend).

Cross-home lookups return 404. Accept / reject on a non-pending recommendation
return 409.
"""
from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.placement_service import (
    accept_recommendation,
    patch_recommendation,
    reject_recommendation,
)
from app.ai.factory import get_provider
from app.ai.provider import AIProvider
from app.api.deps import Actor, get_actor
from app.db.session import get_db
from app.schemas.recommendation import (
    AcceptRequest,
    AcceptResponse,
    PatchRequest,
    PatchResponse,
    PlacementView,
    RecommendRequest,
    RecommendResponse,
    RejectRequest,
    RejectResponse,
)
from app.services import recommendation_service

router = APIRouter(prefix="/recommendations", tags=["recommendations"])


def _get_ai_provider() -> AIProvider:
    """FastAPI dependency. Overridden in tests to inject a mock."""
    return get_provider()


def _placement_view(p) -> PlacementView:  # type: ignore[no-untyped-def]
    return PlacementView(
        id=p.id,
        item_id=p.item_id,
        slot_id=p.slot_id,
        source=p.source,
        is_active=p.is_active,
        note=p.note,
    )


# ---------------------------------------------------------------------- recommend


@router.post(
    "/items/{item_id}/recommend",
    response_model=RecommendResponse,
    status_code=status.HTTP_200_OK,
    summary="Run the 9-step recommendation pipeline for an item",
)
async def recommend(
    item_id: uuid.UUID,
    _payload: RecommendRequest,
    actor: Annotated[Actor, Depends(get_actor)],
    db: Annotated[AsyncSession, Depends(get_db)],
    provider: Annotated[AIProvider, Depends(_get_ai_provider)],
) -> RecommendResponse:
    """Run the agent and return the top-3 candidates.

    Always persists an ``AgentTrace`` row (for observability) and a
    ``Recommendation`` row (status='pending') even when the pipeline fails.
    Returns 200 in both cases; ``state`` tells the caller which path was
    taken.

    Failures:
    - ``404 not_found`` — item missing or belongs to a different home.
    - ``503 ai_*`` — provider unavailable / parse failure / timeout.
    """
    outcome = await recommendation_service.run_recommendation(
        db,
        provider=provider,
        home_id=actor.home_id,
        user_id=actor.user_id,
        item_id=item_id,
    )
    await db.commit()
    return RecommendResponse(**outcome.to_dict())


# ---------------------------------------------------------------------- accept


@router.post(
    "/{rec_id}/accept",
    response_model=AcceptResponse,
    status_code=status.HTTP_200_OK,
    summary="Accept a pending recommendation",
)
async def accept(
    rec_id: uuid.UUID,
    payload: AcceptRequest,
    actor: Annotated[Actor, Depends(get_actor)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> AcceptResponse:
    """Mark the recommendation accepted and create an ItemPlacement.

    Failures:
    - ``404 not_found`` — recommendation doesn't exist or wrong home.
    - ``409 conflict`` — recommendation isn't in ``pending`` status.
    """
    outcome = await accept_recommendation(
        db,
        recommendation_id=rec_id,
        home_id=actor.home_id,
        user_id=actor.user_id,
        note=payload.note,
    )
    await db.commit()
    return AcceptResponse(
        recommendation_id=outcome.recommendation.id,
        status=outcome.recommendation.status,
        placement=_placement_view(outcome.placement),
    )


# ---------------------------------------------------------------------- reject


@router.post(
    "/{rec_id}/reject",
    response_model=RejectResponse,
    status_code=status.HTTP_200_OK,
    summary="Reject a pending recommendation",
)
async def reject(
    rec_id: uuid.UUID,
    payload: RejectRequest,
    actor: Annotated[Actor, Depends(get_actor)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> RejectResponse:
    """Mark the recommendation rejected. No placement is created.

    Failures:
    - ``404 not_found`` — recommendation doesn't exist or wrong home.
    - ``409 conflict`` — recommendation isn't in ``pending`` status.
    """
    outcome = await reject_recommendation(
        db,
        recommendation_id=rec_id,
        home_id=actor.home_id,
        note=payload.note,
    )
    await db.commit()
    return RejectResponse(
        recommendation_id=outcome.recommendation.id,
        status=outcome.recommendation.status,
        note=payload.note,
    )


# ---------------------------------------------------------------------- patch


@router.patch(
    "/{rec_id}",
    response_model=PatchResponse,
    status_code=status.HTTP_200_OK,
    summary="User edits the chosen slot before accepting",
)
async def patch(
    rec_id: uuid.UUID,
    payload: PatchRequest,
    actor: Annotated[Actor, Depends(get_actor)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> PatchResponse:
    """Override the agent's chosen slot. Status stays ``pending``.

    Failures:
    - ``404 not_found`` — recommendation or new slot doesn't exist or wrong home.
    - ``409 conflict`` — recommendation isn't in ``pending`` status.
    """
    outcome = await patch_recommendation(
        db,
        recommendation_id=rec_id,
        home_id=actor.home_id,
        chosen_slot_id=payload.chosen_slot_id,
        reason=payload.reason,
    )
    await db.commit()
    # Re-build the candidate view list so the patched slot surfaces first.
    candidates = list(outcome.recommendation.candidates or [])
    view_candidates: list[dict[str, object]] = []
    patched_str = str(payload.chosen_slot_id)
    front = [c for c in candidates if str(c.get("slot_id")) == patched_str]
    rest = [c for c in candidates if str(c.get("slot_id")) != patched_str]
    for c in front + rest:
        view_candidates.append(
            {
                "slot_id": str(c.get("slot_id")),
                "code": c.get("code") or "",
                "label": c.get("label") or "",
                "full_path": c.get("full_path") or "",
                "room_name": c.get("room_name") or "",
                "unit_name": c.get("unit_name") or "",
                "score": int(c.get("det_score") or 0),
                "confidence": float(c.get("confidence") or 0.0),
                "reason": c.get("reason") or "",
            }
        )
    return PatchResponse(
        recommendation_id=outcome.recommendation.id,
        status=outcome.recommendation.status,
        chosen_slot_id=outcome.recommendation.chosen_slot_id,  # type: ignore[arg-type]
        candidates=view_candidates,  # type: ignore[arg-type]
    )


__all__ = ["router"]
