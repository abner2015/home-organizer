"""Recognition service — orchestrates a single item recognition call.

The endpoint ``POST /api/v1/items/recognize`` accepts an ``asset_id``
and optional ``description``. This service:

1. Loads the asset and verifies it belongs to the calling home.
2. Inlines the stored bytes as a `data:` URI for the AI provider, so the
   model never has to fetch a URL it cannot reach.
3. Calls :func:`vision_service.recognize_image` to run the Vision LLM.
4. Persists the recognition into an ``AgentTrace`` row so the result is
   linkable from logs and from the future ``items`` table.

Per the project prompt this service does NOT create an ``Item`` row or
attempt any placement. That's Phase 9 work.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.errors import AIProviderError
from app.ai.provider import AIProvider
from app.core.exceptions import NotFoundError, ValidationFailedError
from app.core.logging import get_logger
from app.models import Asset
from app.services import vision_service
from app.services.image_payload import to_data_uri
from app.storage.backend import get_storage

logger = get_logger(__name__)


class RecognitionError(AIProviderError):
    """Re-raise as a domain-level error after vision_service fails.

    vision_service already raises typed :class:`AIProviderError`
    subclasses with the right HTTP semantics — this class only exists
    so the API layer can ``except RecognitionError`` as a single
    namespace if it wants to.
    """

    code = "recognition_error"
    http_status = 503
    message = "Item recognition failed"


async def recognize_from_asset(
    db: AsyncSession,
    *,
    provider: AIProvider,
    home_id: uuid.UUID,
    user_id: uuid.UUID,
    asset_id: uuid.UUID,
    description: str | None = None,
    context: str = "",
    timeout_s: float = 30.0,
) -> vision_service.VisionResult:
    """Run vision recognition on an existing asset.

    Returns a :class:`VisionResult` whose ``output`` field is the
    :class:`VisionOutput` parsed from the LLM.

    Raises:
        NotFoundError: asset doesn't exist or doesn't belong to ``home_id``.
        AIProviderError subclasses: bubbled up from :mod:`app.ai.errors`.
    """
    asset = await _load_asset(db, asset_id, home_id)
    image_uri = to_data_uri(get_storage().get(asset.object_key))

    return await vision_service.recognize_image(
        db,
        provider=provider,
        image_url=image_uri,
        asset_id=asset.id,
        home_id=home_id,
        user_id=user_id,
        hint=description,
        context=context,
        timeout_s=timeout_s,
    )


# --------------------------------------------------------------- helpers


async def _load_asset(
    db: AsyncSession, asset_id: uuid.UUID, home_id: uuid.UUID
) -> Asset:
    asset = (
        await db.execute(select(Asset).where(Asset.id == asset_id))
    ).scalar_one_or_none()
    if asset is None or asset.home_id != home_id:
        raise NotFoundError("Asset not found")
    if asset.status != "ready":
        raise ValidationFailedError(
            "Asset is not ready for recognition",
            details={"status": asset.status},
        )
    return asset


__all__ = ["RecognitionError", "recognize_from_asset"]
