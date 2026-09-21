"""Direct-to-storage upload endpoints (Phase 11 web wiring).

``POST /uploads/presign`` is the flow the Web app actually uses
(``docs/API.md`` §6, ``docs/ARCHITECTURE.md`` §"上传"): the browser asks for a
presigned PUT URL, uploads straight to MinIO, and then passes the returned
``object_key`` to ``POST /items``.

The alternative — ``POST /assets/upload`` in :mod:`app.api.v1.assets` — streams
the body through the API so it can sniff magic bytes and dedup by SHA-256. That
flow is stricter but costs a round-trip through the API process; both exist
because the item images go through presign while stricter ingest can use the
upload route.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.api.deps import Actor, get_actor
from app.schemas.upload import PresignUploadRequest, PresignUploadResponse
from app.services import asset_service

router = APIRouter(prefix="/uploads", tags=["uploads"])


@router.post(
    "/presign",
    response_model=PresignUploadResponse,
    status_code=status.HTTP_200_OK,
    summary="Issue a presigned PUT URL for a direct image upload",
)
async def presign_upload(
    payload: PresignUploadRequest,
    actor: Annotated[Actor, Depends(get_actor)],
) -> PresignUploadResponse:
    """Return ``{upload_url, object_key, method, expires_in}``.

    The object key is scoped to the caller's home
    (``home/<home_uuid>/<YYYY>/<MM>/...``); ``POST /items`` rejects keys
    outside that prefix, so one home can never attach another's object.
    """
    ticket = asset_service.presign_upload(
        home_id=actor.home_id, content_type=payload.content_type
    )
    return PresignUploadResponse(
        upload_url=ticket.upload_url,
        object_key=ticket.object_key,
        expires_in=ticket.expires_in,
    )


__all__ = ["router"]
