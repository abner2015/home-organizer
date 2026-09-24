"""Recommendation endpoints (Phase 5 + P0.6 + P0.B).

Six endpoints:

- ``POST /api/v1/recommendations/items/{item_id}/recommend`` — run the 9-step
  pipeline, return top-3 candidates + chosen slot.
- ``GET /api/v1/recommendations/{rec_id}`` — re-read a persisted
  recommendation (the Web app's recommendation detail page).
- ``POST /api/v1/recommendations/{rec_id}/accept`` — mark recommendation
  ``accepted`` and create an ItemPlacement row.
- ``POST /api/v1/recommendations/{rec_id}/reject`` — mark recommendation
  ``rejected`` (no placement).
- ``POST /api/v1/recommendations/{rec_id}/revoke`` — mark a previously
  ``rejected`` recommendation ``revoked`` (the slot becomes recommendable
  again for this item; P0.6).
- ``POST /api/v1/recommendations/bulk-revoke`` — bulk un-do many rejections
  in one round-trip; with ``auto_rerun=true`` the route re-runs the
  recommendation pipeline for every affected item synchronously and returns
  the new candidates (P0.B).
- ``PATCH /api/v1/recommendations/{rec_id}`` — user moves chosen_slot_id
  before accept (status stays ``pending``).

All endpoints require ``X-User-Id`` + ``X-Home-Id`` headers (stub auth; real
JWT lands with the user-facing frontend).

Cross-home lookups return 404. Accept / reject / revoke on a recommendation
in the wrong status return 409. ``bulk-revoke`` always returns 200; per-entry
failures are collected inside ``errors[]``.
"""
from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.placement_service import (
    accept_recommendation,
    bulk_revoke,
    patch_recommendation,
    reject_recommendation,
    revoke_recommendation,
)
from app.ai.factory import get_provider
from app.ai.provider import AIProvider
from app.api.deps import Actor, get_actor
from app.db.session import get_db
from app.schemas.recommendation import (
    AcceptRequest,
    AcceptResponse,
    BulkRevokeError,
    BulkRevokeRequest,
    BulkRevokeRerunItem,
    BulkRevokeResponse,
    BulkRevokeRevokedItem,
    PatchRequest,
    PatchResponse,
    PlacementView,
    RecommendRequest,
    RecommendResponse,
    RejectRequest,
    RejectResponse,
    RevokeRequest,
    RevokeResponse,
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


# -------------------------------------------------------------------- get one


@router.get(
    "/{rec_id}",
    response_model=RecommendResponse,
    status_code=status.HTTP_200_OK,
    summary="Fetch a persisted recommendation",
)
async def get_recommendation(
    rec_id: uuid.UUID,
    actor: Annotated[Actor, Depends(get_actor)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> RecommendResponse:
    """Re-read a recommendation persisted by a previous ``POST /recommend``.

    Candidate location metadata is refreshed from the live slot rows (a slot
    may have been renamed) and ``is_recommended`` is computed against the
    current ``chosen_slot_id``, so a PATCHed recommendation renders correctly.

    Failures:
    - ``404 not_found`` — unknown id, or the item belongs to another home.
    """
    view = await recommendation_service.get_recommendation_view(
        db, home_id=actor.home_id, recommendation_id=rec_id
    )
    return RecommendResponse(**view)


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


# ---------------------------------------------------------------------- revoke


@router.post(
    "/{rec_id}/revoke",
    response_model=RevokeResponse,
    status_code=status.HTTP_200_OK,
    summary="Revoke a rejection (slot becomes recommendable again)",
)
async def revoke(
    rec_id: uuid.UUID,
    _payload: RevokeRequest,
    actor: Annotated[Actor, Depends(get_actor)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> RevokeResponse:
    """Flip a ``rejected`` recommendation to ``revoked`` (P0.6). No placement
    is created. The excluded slot is back in the candidate pool for the next
    ``POST /recommend`` — the read-side filter
    (:func:`get_rejected_slot_ids`) keys off ``status='rejected'`` and so
    transparently drops the revoked row.

    The original reject's reason is preserved on
    ``candidates[0].audit_note`` for the audit trail.

    Failures:
    - ``404 not_found`` — recommendation doesn't exist or wrong home.
    - ``409 conflict`` — recommendation is not in ``rejected`` status.
    """
    outcome = await revoke_recommendation(
        db,
        recommendation_id=rec_id,
        home_id=actor.home_id,
    )
    await db.commit()
    return RevokeResponse(
        recommendation_id=outcome.recommendation.id,
        status=outcome.recommendation.status,
    )


# -------------------------------------------------------------------- bulk-revoke


@router.post(
    "/bulk-revoke",
    response_model=BulkRevokeResponse,
    status_code=status.HTTP_200_OK,
    summary="Bulk-revoke rejections and (optionally) auto-rerun recommend",
)
async def bulk_revoke_endpoint(
    payload: BulkRevokeRequest,
    actor: Annotated[Actor, Depends(get_actor)],
    db: Annotated[AsyncSession, Depends(get_db)],
    provider: Annotated[AIProvider, Depends(_get_ai_provider)],
) -> BulkRevokeResponse:
    """Bulk-un-do a batch of ``rejected`` recommendations (P0.B).

    Always returns 200; per-entry failures are reported inside ``errors[]``
    (one of ``not_found`` / ``conflict`` / ``ai_error``). The response has
    three sections so the front-end can render each independently:

    - ``revoked[]`` — successfully un-done recommendations.
    - ``rerun_results[]`` — one entry per affected item, only when
      ``auto_rerun=true`` and at least one revoke succeeded. Each entry
      carries the same shape as a single ``POST /recommend`` response so the
      UI can swap the new candidates in without a second round-trip.
    - ``errors[]`` — per-entry failures with a stable ``code``.

    Top-level failures:
    - ``422`` — ``recommendation_ids`` empty / over 50 / unknown field
      (Pydantic ``extra='forbid'``).
    - ``401`` — missing / bad credentials (handled by ``get_actor``).
    """
    outcome = await bulk_revoke(
        db,
        home_id=actor.home_id,
        user_id=actor.user_id,
        recommendation_ids=payload.recommendation_ids,
        auto_rerun=payload.auto_rerun,
        provider=provider,
    )
    await db.commit()

    revoked_views = [
        BulkRevokeRevokedItem(
            recommendation_id=rec.id,
            status=rec.status,
        )
        for rec in outcome.revoked
    ]

    rerun_views: list[BulkRevokeRerunItem] = []
    for item_id, rerun_outcome, _err_code in outcome.rerun_results:
        if rerun_outcome is None:
            # Rerun failed — surface an empty failed entry so the UI can show
            # "still no candidates" instead of a missing row.
            rerun_views.append(
                BulkRevokeRerunItem(
                    item_id=item_id,
                    new_recommendation_id=None,
                    state="failed",
                    chosen_slot_id=None,
                    candidates=[],
                )
            )
            continue
        rd = rerun_outcome.to_dict()
        candidates = [
            recommendation_service.candidate_view_from_slot(
                c,
                is_recommended=(
                    str(c.get("slot_id") or "") == (rd.get("chosen_slot_id") or "")
                ),
            )
            for c in (rerun_outcome.result.candidates or [])[:3]
        ]
        rerun_views.append(
            BulkRevokeRerunItem(
                item_id=item_id,
                new_recommendation_id=rd["recommendation_id"],
                state=("success" if rerun_outcome.ok else "failed"),
                chosen_slot_id=(
                    uuid.UUID(rd["chosen_slot_id"])
                    if rd.get("chosen_slot_id")
                    else None
                ),
                candidates=candidates,
            )
        )

    error_views = [
        BulkRevokeError(
            recommendation_id=(
                uuid.UUID(e["recommendation_id"])
                if e.get("recommendation_id")
                else None
            ),
            item_id=uuid.UUID(e["item_id"]) if e.get("item_id") else None,
            code=e["code"],
            message=e["message"],
        )
        for e in outcome.errors
    ]

    return BulkRevokeResponse(
        revoked=revoked_views,
        rerun_results=rerun_views,
        errors=error_views,
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
    # Re-build the candidate view list so the patched slot surfaces first and
    # carries the `is_recommended` flag.
    patched_str = str(payload.chosen_slot_id)
    candidates = [
        c for c in (outcome.recommendation.candidates or []) if isinstance(c, dict)
    ]
    front = [c for c in candidates if str(c.get("slot_id")) == patched_str]
    rest = [c for c in candidates if str(c.get("slot_id")) != patched_str]
    view_candidates = [
        recommendation_service.candidate_view_from_slot(
            c, is_recommended=str(c.get("slot_id")) == patched_str
        )
        for c in front + rest
    ]
    return PatchResponse(
        recommendation_id=outcome.recommendation.id,
        status=outcome.recommendation.status,
        chosen_slot_id=outcome.recommendation.chosen_slot_id,
        candidates=view_candidates,
    )


__all__ = ["router"]
