"""Asset upload / fetch / delete endpoints (Phase 3).

Security notes (matching docs/PHASE3.md):

- The endpoint NEVER returns the MinIO endpoint, Access Key, or secret. All
  access URLs are presigned with a bounded TTL.
- The original filename is taken for display only; it is never used as the
  object key.
- File body is fully buffered and re-validated server-side (content-type
  whitelist + magic-byte sniff + size cap) before any storage call.
"""
from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Actor, get_actor
from app.db.session import get_db
from app.schemas.asset import AssetResponse, AssetUploadResponse
from app.services import asset_service

router = APIRouter(prefix="/assets", tags=["assets"])


@router.post(
    "/upload",
    response_model=AssetUploadResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload an image asset",
)
async def upload_asset(
    actor: Annotated[Actor, Depends(get_actor)],
    db: Annotated[AsyncSession, Depends(get_db)],
    file: Annotated[UploadFile, File(description="JPEG/PNG/WebP image, max 20 MiB")],
) -> AssetUploadResponse:
    """Validate, store, and persist a new image asset.

    On a SHA-256 hit (same home, same body) the existing `ready` asset is
    reused and `deduplicated=true` is returned. The response `url` is a fresh
    presigned URL valid for ~1 hour.
    """
    if file.content_type is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing Content-Type on uploaded file.",
        )
    body = await file.read()
    if not body:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file is empty.",
        )

    result = await asset_service.upload_image(
        db,
        home_id=actor.home_id,
        user_id=actor.user_id,
        filename=file.filename,
        content_type=file.content_type,
        body=body,
    )
    return AssetUploadResponse(
        asset_id=result.asset.id,
        url=result.url,
        content_type=result.asset.content_type,
        size=result.asset.size_bytes,
        width=result.asset.width,
        height=result.asset.height,
        sha256=result.asset.sha256,
        deduplicated=result.deduplicated,
    )


@router.get(
    "/{asset_id}",
    response_model=AssetResponse,
    summary="Fetch an asset by id",
)
async def get_asset(
    asset_id: uuid.UUID,
    actor: Annotated[Actor, Depends(get_actor)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> AssetResponse:
    asset = await asset_service.get_asset(db, asset_id)
    if asset is None or asset.home_id != actor.home_id:
        # Treat cross-home lookups as not-found to avoid leaking existence.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Asset not found",
        )
    url = asset_service.make_presigned_url(asset)
    return AssetResponse(
        asset_id=asset.id,
        home_id=asset.home_id,
        created_by=asset.created_by,
        content_type=asset.content_type,
        size=asset.size_bytes,
        width=asset.width,
        height=asset.height,
        sha256=asset.sha256,
        original_filename=asset.original_filename,
        status=asset.status,
        failure_reason=asset.failure_reason,
        uploaded_at=asset.uploaded_at,
        created_at=asset.created_at,
        url=url,
    )


@router.delete(
    "/{asset_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an asset",
)
async def delete_asset(
    asset_id: uuid.UUID,
    actor: Annotated[Actor, Depends(get_actor)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    asset = await asset_service.get_asset(db, asset_id)
    if asset is None or asset.home_id != actor.home_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Asset not found",
        )
    await asset_service.delete_asset(db, asset)
