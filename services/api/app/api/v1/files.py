"""Read endpoint for the `local` storage backend.

When `storage_backend == "local"` there is no object store to hand out
presigned URLs, so `LocalBackend.url_for` mints a signed, expiring link back to
this route. The signature is the credential — the caller is typically an
`<img src>` in the browser, which cannot attach an `Authorization` header, so
this route deliberately does not depend on `get_actor`.

Under any other backend the route 404s: a MinIO deployment has no local files,
and leaving it reachable would only offer a probing surface.
"""
from __future__ import annotations

import hmac
import time

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import Response

from app.core.config import settings
from app.core.logging import get_logger
from app.storage.backend import get_storage, local_storage_signature
from app.storage.errors import StorageError

logger = get_logger(__name__)

router = APIRouter(prefix="/files", tags=["files"])


# Extension → media type. The key is always minted by `asset_service` with one
# of these extensions (`_EXT_BY_TYPE`), so lookup is total in practice; the
# fallback keeps an unexpected key from breaking the response.
_CONTENT_TYPE_BY_EXT: dict[str, str] = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "webp": "image/webp",
}


def _media_type_for(key: str) -> str:
    ext = key.rsplit(".", 1)[-1].lower() if "." in key else ""
    return _CONTENT_TYPE_BY_EXT.get(ext, "application/octet-stream")


@router.get(
    "/{key:path}",
    summary="Fetch a locally-stored object (local storage backend only)",
    response_class=Response,
)
async def get_local_file(key: str, exp: int = 0, sig: str = "") -> Response:
    """Serve a stored file after verifying its signed, expiring URL."""
    if settings.storage_backend != "local":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    if exp <= 0 or exp < int(time.time()):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Link expired"
        )
    # Constant-time compare so a bad signature can't be recovered byte-by-byte.
    if not sig or not hmac.compare_digest(sig, local_storage_signature(key, exp)):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Invalid signature"
        )

    try:
        body = get_storage().get(key)
    except StorageError as exc:
        logger.warning("files.get_failed", key=key, error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Not found"
        ) from exc

    # `exp` bounds how long the URL stays valid, so the response may be cached
    # for the remainder of that window.
    max_age = max(0, exp - int(time.time()))
    return Response(
        content=body,
        media_type=_media_type_for(key),
        headers={"Cache-Control": f"private, max-age={max_age}"},
    )
