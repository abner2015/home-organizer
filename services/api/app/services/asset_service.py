"""Asset upload / fetch / delete service.

This module is the single chokepoint where untrusted user bytes enter the
system. Every defensive check lives here:

1. Content-Type whitelist (JPEG / PNG / WebP).
2. Size cap (default 20 MiB).
3. Magic-byte sniffing — the file header must match the claimed content type.
4. Server-generated UUID-based object key — the original filename is never
   used as an S3 key (defense against path traversal, log injection, prefix
   squatting).
5. SHA-256 dedup — the same body produces the same hash; we surface this so
   the API can either return the existing asset or refuse to duplicate.

The DB row is created in `pending` state and flipped to `ready` after the
object is verified to exist in storage. Failures flip to `failed` with a
human-readable reason.
"""
from __future__ import annotations

import hashlib
import io
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

from PIL import Image, UnidentifiedImageError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import AppError
from app.core.logging import get_logger
from app.db.enums import AssetStatus
from app.models import Asset
from app.storage.backend import get_storage
from app.storage.errors import StorageError

logger = get_logger(__name__)


# --------------------------------------------------------------------------- constants

ALLOWED_CONTENT_TYPES: Final[frozenset[str]] = frozenset(
    {"image/jpeg", "image/png", "image/webp"}
)

# Map content-type → extension used in the object key. The extension is for
# humans / cache hints only; the real authority is the content-type header.
_EXT_BY_TYPE: Final[dict[str, str]] = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
}

MAX_UPLOAD_BYTES: Final[int] = 20 * 1024 * 1024  # 20 MiB

PRESIGNED_URL_TTL_SECONDS: Final[int] = 3600

# Direct-upload tickets are short-lived: the browser PUTs immediately after
# asking for the URL.
PRESIGNED_UPLOAD_TTL_SECONDS: Final[int] = 600


# --------------------------------------------------------------------------- errors


class UploadRejectedError(AppError):
    """Raised when an upload fails validation. Inherits from `AppError` so the
    global exception handler maps it to a JSON error response with the right
    status code. `.code` is a stable string clients can switch on; `.message`
    is human-readable.
    """

    code = "upload_rejected"
    http_status = 400
    message = "Upload rejected"

    def __init__(self, code: str, message: str, *, http_status: int = 400) -> None:
        # Set instance attributes BEFORE super().__init__ so the AppError
        # handler reads the right code/status/message.
        self.code = code
        self.http_status = http_status
        self.message = message
        self.details = {}
        super().__init__(message)


# --------------------------------------------------------------------------- value objects


@dataclass(slots=True)
class UploadResult:
    """Successful upload outcome returned to the API layer."""

    asset: Asset
    url: str
    deduplicated: bool  # True if an existing asset was reused (sha256 hit)


@dataclass(slots=True)
class PresignedUpload:
    """A direct-to-storage upload ticket (no `assets` row yet)."""

    upload_url: str
    object_key: str
    expires_in: int


# --------------------------------------------------------------------------- service


def _sniff_content_type(head: bytes, claimed: str) -> tuple[str, int, int] | None:
    """Verify the body's magic bytes match the claimed MIME type.

    Returns a (content_type, width, height) tuple on success, or None if the
    header doesn't match any allowed type. Width/height are parsed via PIL
    only when the magic bytes pass.
    """
    # Magic-byte detection only needs the first ~12 bytes, but to extract
    # width/height we need a larger head — PNG's IHDR chunk extends to byte
    # 33, JPEG's SOFn marker can be hundreds of bytes in. We grab 4 KiB
    # which covers every practical case without buffering the whole file.
    head = head[:4096]
    claimed = claimed.lower()

    # JPEG: FF D8 FF
    if head.startswith(b"\xff\xd8\xff"):
        if claimed != "image/jpeg":
            return None
        kind = "jpeg"
    # PNG: 89 50 4E 47 0D 0A 1A 0A
    elif head.startswith(b"\x89PNG\r\n\x1a\n"):
        if claimed != "image/png":
            return None
        kind = "png"
    # WebP: RIFF .... WEBP
    elif head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        if claimed != "image/webp":
            return None
        kind = "webp"
    else:
        return None

    # Try to read dimensions. PIL can fail on truncated images — that's fine,
    # we only need width/height for UX, not for security. The magic byte is
    # the gate.
    width = height = 0
    try:
        img = Image.open(io.BytesIO(head))
        width, height = img.size
    except (UnidentifiedImageError, OSError, ValueError):
        pass

    content_type_for_kind = {
        "jpeg": "image/jpeg",
        "png": "image/png",
        "webp": "image/webp",
    }
    return content_type_for_kind[kind], width, height


def _build_object_key(
    home_id: uuid.UUID, content_type: str, shard: str | None = None
) -> str:
    """Build a server-side object key.

    Format: `home/<home_uuid>/<YYYY>/<MM>/<uuid>_<shard>.<ext>`
    Never contains the user's filename. `shard` is a hash prefix used as a
    distribution hint so duplicate uploads cluster in the same MinIO prefix
    range; for the presign flow the body isn't available yet, so it falls back
    to random hex.
    """
    ext = _EXT_BY_TYPE[content_type]
    now = datetime.now(UTC)
    return (
        f"home/{home_id}/{now.year:04d}/{now.month:02d}/"
        f"{uuid.uuid4().hex}_{shard or uuid.uuid4().hex[:12]}.{ext}"
    )


def _bucket_for(content_type: str) -> str:
    """Pick the right bucket by content type. All current types go to uploads;
    a future split (e.g., 'thumbnails') would key off the type here.
    """
    return settings.minio_bucket_uploads


def presign_upload(*, home_id: uuid.UUID, content_type: str) -> PresignedUpload:
    """Issue a presigned PUT URL for a direct browser → MinIO upload.

    Only the content-type whitelist is enforced here: the body never reaches
    the API, so the magic-byte / size / dedup checks that :func:`upload_image`
    performs cannot run. That is the documented trade-off of the presign flow
    (``docs/API.md`` §6) — the bytes land in a private bucket and are only ever
    served through short-lived presigned GETs.

    Raises:
        UploadRejectedError: content type not in :data:`ALLOWED_CONTENT_TYPES`.
    """
    content_type = content_type.lower().strip()
    if content_type not in ALLOWED_CONTENT_TYPES:
        raise UploadRejectedError(
            "unsupported_content_type",
            f"Content-Type {content_type!r} not allowed. "
            f"Allowed: {sorted(ALLOWED_CONTENT_TYPES)}",
        )

    if get_storage().name != "minio":
        # There is no browser-reachable upload target under the local backend;
        # clients should POST the bytes to /assets/upload instead.
        raise UploadRejectedError(
            "presign_unsupported",
            "Direct upload is unavailable with the configured storage backend.",
            http_status=409,
        )

    object_key = _build_object_key(home_id, content_type)
    upload_url = get_storage().put_url(
        object_key, content_type, PRESIGNED_UPLOAD_TTL_SECONDS
    )
    logger.info(
        "asset.presigned_upload",
        home_id=str(home_id),
        object_key=object_key,
        content_type=content_type,
    )
    return PresignedUpload(
        upload_url=upload_url,
        object_key=object_key,
        expires_in=PRESIGNED_UPLOAD_TTL_SECONDS,
    )


async def upload_image(
    db: AsyncSession,
    *,
    home_id: uuid.UUID,
    user_id: uuid.UUID,
    filename: str | None,
    content_type: str,
    body: bytes,
) -> UploadResult:
    """Validate + upload + persist an image asset. Returns a presigned URL.

    Idempotent on SHA-256: if an existing `ready` asset for this home has the
    same hash, returns it instead of re-uploading.
    """
    content_type = content_type.lower().strip()

    # 1. Content-Type whitelist
    if content_type not in ALLOWED_CONTENT_TYPES:
        raise UploadRejectedError(
            "unsupported_content_type",
            f"Content-Type {content_type!r} not allowed. "
            f"Allowed: {sorted(ALLOWED_CONTENT_TYPES)}",
        )

    # 2. Size cap
    if len(body) == 0:
        raise UploadRejectedError("empty_body", "Empty upload.")
    if len(body) > MAX_UPLOAD_BYTES:
        raise UploadRejectedError(
            "file_too_large",
            f"Upload exceeds {MAX_UPLOAD_BYTES} bytes.",
        )

    # 3. Magic-byte sniff + dim extraction
    sniffed = _sniff_content_type(body[:4096], content_type)
    if sniffed is None:
        raise UploadRejectedError(
            "magic_byte_mismatch",
            "File contents do not match the declared content-type.",
        )
    real_content_type, width, height = sniffed

    # 4. SHA-256 for dedup
    sha256_hex = hashlib.sha256(body).hexdigest()

    # 5. Dedup check (same home + same hash + status=ready → reuse)
    existing = (
        await db.execute(
            select(Asset).where(
                Asset.home_id == home_id,
                Asset.sha256 == sha256_hex,
                Asset.status == AssetStatus.READY.value,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        logger.info(
            "asset.dedup_hit",
            asset_id=str(existing.id),
            sha256=sha256_hex,
            home_id=str(home_id),
        )
        url = get_storage().url_for(existing.object_key, PRESIGNED_URL_TTL_SECONDS)
        return UploadResult(asset=existing, url=url, deduplicated=True)

    # 6. Build key, ensure bucket, upload
    object_key = _build_object_key(home_id, real_content_type, sha256_hex)
    bucket = _bucket_for(real_content_type)

    asset = Asset(
        home_id=home_id,
        created_by=user_id,
        bucket=bucket,
        object_key=object_key,
        content_type=real_content_type,
        size_bytes=len(body),
        sha256=sha256_hex,
        width=width or None,
        height=height or None,
        original_filename=filename,
        status=AssetStatus.PENDING.value,
    )
    db.add(asset)
    await db.flush()

    storage = get_storage()
    try:
        storage.ensure_ready()
        storage.put(object_key, body, real_content_type)
    except StorageError as exc:
        asset.status = AssetStatus.FAILED.value
        asset.failure_reason = str(exc)
        await db.commit()
        logger.error(
            "asset.upload_failed",
            asset_id=str(asset.id),
            error=str(exc),
        )
        raise UploadRejectedError(
            "storage_error",
            "Could not store the uploaded file.",
            http_status=502,
        ) from exc

    # 7. Verify object landed
    if not storage.exists(object_key):
        asset.status = AssetStatus.FAILED.value
        asset.failure_reason = "Object not found after upload"
        await db.commit()
        raise UploadRejectedError(
            "storage_error",
            "Upload succeeded but object not found in storage.",
            http_status=502,
        )

    asset.status = AssetStatus.READY.value
    asset.uploaded_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(asset)

    url = storage.url_for(object_key, PRESIGNED_URL_TTL_SECONDS)
    logger.info(
        "asset.uploaded",
        asset_id=str(asset.id),
        sha256=sha256_hex,
        bytes=len(body),
        home_id=str(home_id),
    )
    return UploadResult(asset=asset, url=url, deduplicated=False)


async def get_asset(db: AsyncSession, asset_id: uuid.UUID) -> Asset | None:
    return (
        await db.execute(select(Asset).where(Asset.id == asset_id))
    ).scalar_one_or_none()


async def delete_asset(db: AsyncSession, asset: Asset) -> None:
    """Remove from storage then mark DB row as deleted.

    The row is hard-deleted (no soft-delete per project convention). If the
    storage delete fails, we still remove the DB row and log — orphaned
    objects can be cleaned up by a periodic GC.
    """
    try:
        get_storage().delete(asset.object_key)
    except StorageError as exc:
        logger.warning(
            "asset.delete_storage_failed",
            asset_id=str(asset.id),
            error=str(exc),
        )
    await db.delete(asset)
    await db.commit()


def make_presigned_url(asset: Asset, ttl_seconds: int = PRESIGNED_URL_TTL_SECONDS) -> str:
    """Generate a fresh read URL for an existing asset."""
    return get_storage().url_for(asset.object_key, ttl_seconds)


def presigned_url_for_key(
    object_key: str, ttl_seconds: int = PRESIGNED_URL_TTL_SECONDS
) -> str:
    """Generate a fresh read URL for a raw object key.

    ``item_images`` stores only the key (its ``url`` column is a snapshot), so
    every read re-signs here rather than handing out a URL that expired an hour
    after the row was written.
    """
    return get_storage().url_for(object_key, ttl_seconds)
