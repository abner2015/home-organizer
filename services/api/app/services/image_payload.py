"""Turn stored image bytes into an inline `data:` URI for the vision call.

The vision providers take an `image_url` and hand it to the model verbatim.
Passing a *URL* only works when the model can reach it — which is false for a
MinIO host on the local network (and for the `local` backend's signed links,
which point at the API itself). Inlining the bytes removes that dependency
entirely: the model never fetches anything.

Images are downscaled and re-encoded before base64 so a multi-megabyte phone
photo doesn't turn into a multi-megabyte prompt.
"""
from __future__ import annotations

import base64
import io

from PIL import Image, UnidentifiedImageError

from app.core.logging import get_logger
from app.storage.errors import StorageError

logger = get_logger(__name__)

# Longest edge of the re-encoded image. The model only needs enough detail to
# name the object and judge its size class; 1024px is comfortably above that.
MAX_EDGE: int = 1024

JPEG_QUALITY: int = 85

# If PIL can't decode the bytes we fall back to inlining them untouched — but
# only under this size, so a malformed header can't smuggle a huge payload
# into the prompt.
MAX_FALLBACK_BYTES: int = 4 * 1024 * 1024

# Magic-byte sniffing for the fallback path only (the happy path re-encodes to
# JPEG regardless of the input format).
_MAGIC_TYPES: tuple[tuple[bytes, str], ...] = (
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
)


def _sniff_mime(body: bytes) -> str | None:
    for magic, mime in _MAGIC_TYPES:
        if body.startswith(magic):
            return mime
    if body[:4] == b"RIFF" and body[8:12] == b"WEBP":
        return "image/webp"
    return None


def to_data_uri(body: bytes, *, max_edge: int = MAX_EDGE) -> str:
    """Return a `data:image/jpeg;base64,...` URI for `body`.

    Raises:
        StorageError: the bytes aren't a decodable image and are too large to
            inline safely.
    """
    if not body:
        raise StorageError("Cannot build an image payload from an empty body")

    try:
        with Image.open(io.BytesIO(body)) as img:
            rgb = img.convert("RGB")
            rgb.thumbnail((max_edge, max_edge))
            buf = io.BytesIO()
            rgb.save(buf, format="JPEG", quality=JPEG_QUALITY, optimize=True)
        encoded = base64.b64encode(buf.getvalue()).decode("ascii")
        return f"data:image/jpeg;base64,{encoded}"
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        # Fall through to the raw-inline path below.
        logger.warning("image_payload.decode_failed", error=str(exc), size=len(body))

    mime = _sniff_mime(body)
    if mime is None or len(body) > MAX_FALLBACK_BYTES:
        raise StorageError(
            "Unsupported image payload: cannot decode, and too large to inline raw"
        )
    encoded = base64.b64encode(body).decode("ascii")
    return f"data:{mime};base64,{encoded}"


__all__ = ["MAX_EDGE", "to_data_uri"]
