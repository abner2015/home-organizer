"""Item endpoints (Phase 4+).

Phase 4 scope: only ``POST /items/recognize``. Phase 9 will add
recommendation / placement endpoints; Phase 10 will add search.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.factory import get_provider
from app.ai.provider import AIProvider
from app.api.deps import Actor, get_actor
from app.db.session import get_db
from app.schemas.recognition import RecognizeRequest, RecognizeResponse
from app.services import recognition_service

router = APIRouter(prefix="/items", tags=["items"])


def _get_ai_provider() -> AIProvider:
    """FastAPI dependency. Overridden in tests to inject a mock."""
    return get_provider()


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
    )
    await db.commit()
    return RecognizeResponse(
        result=result.output.model_dump(),  # type: ignore[arg-type]
        trace_id=result.trace_id,
        attempts=result.attempts,
        duration_ms=result.total_duration_ms,
        provider=provider.name,
    )
