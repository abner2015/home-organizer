"""Tests for ``app.services.image_payload.to_data_uri``."""
from __future__ import annotations

import base64
import io

import pytest
from PIL import Image

from app.services.image_payload import MAX_EDGE, MAX_FALLBACK_BYTES, to_data_uri
from app.storage.errors import StorageError

_PREFIX = "data:image/jpeg;base64,"


def _encode(fmt: str, size: tuple[int, int], color=(200, 30, 30)) -> bytes:
    mode = "RGB" if fmt == "JPEG" else "RGBA"
    buf = io.BytesIO()
    Image.new(mode, size, color).save(buf, format=fmt)
    return buf.getvalue()


def _decode(uri: str) -> bytes:
    assert uri.startswith(_PREFIX)
    return base64.b64decode(uri[len(_PREFIX) :])


@pytest.mark.parametrize("fmt", ["JPEG", "PNG", "WEBP"])
def test_any_supported_format_becomes_a_jpeg_data_uri(fmt: str) -> None:
    """Everything is re-encoded to JPEG, so the prefix never varies."""
    uri = to_data_uri(_encode(fmt, (8, 8)))
    with Image.open(io.BytesIO(_decode(uri))) as img:
        assert img.format == "JPEG"


def test_large_images_are_downscaled_to_max_edge() -> None:
    uri = to_data_uri(_encode("PNG", (2400, 1200)))
    with Image.open(io.BytesIO(_decode(uri))) as img:
        assert max(img.size) == MAX_EDGE
        # Aspect ratio is preserved by thumbnail().
        assert img.size == (MAX_EDGE, MAX_EDGE // 2)


def test_small_images_are_not_upscaled() -> None:
    uri = to_data_uri(_encode("PNG", (32, 16)))
    with Image.open(io.BytesIO(_decode(uri))) as img:
        assert img.size == (32, 16)


def test_custom_max_edge_is_respected() -> None:
    uri = to_data_uri(_encode("PNG", (400, 400)), max_edge=64)
    with Image.open(io.BytesIO(_decode(uri))) as img:
        assert max(img.size) == 64


def test_downscaling_shrinks_the_payload() -> None:
    raw = _encode("PNG", (2000, 2000))
    assert len(_decode(to_data_uri(raw))) < len(raw)


# ------------------------------------------------------------------ fallbacks


def test_transparency_survives_the_rgb_conversion() -> None:
    """RGBA input must not blow up when flattened onto RGB."""
    buf = io.BytesIO()
    Image.new("RGBA", (16, 16), (0, 0, 255, 128)).save(buf, format="PNG")
    uri = to_data_uri(buf.getvalue())
    with Image.open(io.BytesIO(_decode(uri))) as img:
        assert img.mode == "RGB"


def test_undecodable_small_body_is_inlined_raw() -> None:
    """A truncated JPEG header can't be decoded, so the bytes pass through."""
    body = b"\xff\xd8\xff\xe0" + b"\x00" * 60
    uri = to_data_uri(body)
    assert uri.startswith("data:image/jpeg;base64,")
    assert base64.b64decode(uri.split(",", 1)[1]) == body


def test_undecodable_png_magic_is_inlined_raw() -> None:
    body = b"\x89PNG\r\n\x1a\n" + b"\x00" * 40
    uri = to_data_uri(body)
    assert uri.startswith("data:image/png;base64,")


def test_undecodable_and_oversized_body_raises() -> None:
    body = b"\x89PNG\r\n\x1a\n" + b"\x00" * (MAX_FALLBACK_BYTES + 1)
    with pytest.raises(StorageError):
        to_data_uri(body)


def test_unrecognised_bytes_raise() -> None:
    with pytest.raises(StorageError):
        to_data_uri(b"this is a text file, not an image")


def test_empty_body_raises() -> None:
    with pytest.raises(StorageError):
        to_data_uri(b"")
