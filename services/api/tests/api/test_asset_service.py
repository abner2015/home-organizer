"""Unit tests for app.services.asset_service.

These tests exercise the service layer directly (no FastAPI) against an
in-memory SQLite DB and a moto-mocked S3 endpoint.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.db.enums import AssetStatus
from app.models import Asset
from app.services import asset_service
from app.services.asset_service import (
    ALLOWED_CONTENT_TYPES,
    MAX_UPLOAD_BYTES,
    PRESIGNED_URL_TTL_SECONDS,
    UploadRejectedError,
)
from tests.api.conftest import make_jpeg_bytes, make_png_bytes, make_webp_bytes

# ----------------------------------------------------------------- constants


def test_allowed_content_types_is_frozen() -> None:
    assert "image/jpeg" in ALLOWED_CONTENT_TYPES
    assert "image/png" in ALLOWED_CONTENT_TYPES
    assert "image/webp" in ALLOWED_CONTENT_TYPES
    assert "image/gif" not in ALLOWED_CONTENT_TYPES


def test_size_cap_is_20_mib() -> None:
    assert MAX_UPLOAD_BYTES == 20 * 1024 * 1024


def test_presigned_ttl_is_one_hour() -> None:
    assert PRESIGNED_URL_TTL_SECONDS == 3600


# -------------------------------------------------------- magic-byte sniffing


def test_sniff_png_magic() -> None:
    head = b"\x89PNG\r\n\x1a\n" + b"\x00" * 24
    assert asset_service._sniff_content_type(head, "image/png") == ("image/png", 0, 0)


def test_sniff_jpeg_magic() -> None:
    head = b"\xff\xd8\xff\xe0" + b"\x00" * 28
    assert asset_service._sniff_content_type(head, "image/jpeg") == ("image/jpeg", 0, 0)


def test_sniff_webp_magic() -> None:
    head = b"RIFF" + b"\x00" * 4 + b"WEBP" + b"\x00" * 20
    assert asset_service._sniff_content_type(head, "image/webp") == ("image/webp", 0, 0)


def test_sniff_rejects_mismatch() -> None:
    png_head = b"\x89PNG\r\n\x1a\n" + b"\x00" * 24
    # Claiming JPEG while bytes are PNG must fail.
    assert asset_service._sniff_content_type(png_head, "image/jpeg") is None


def test_sniff_rejects_unknown() -> None:
    assert asset_service._sniff_content_type(b"random garbage bytes here", "image/png") is None


# ---------------------------------------------------------------- object keys


def test_build_object_key_uses_uuid_and_sha() -> None:
    home_id = uuid.uuid4()
    sha = "a" * 64
    key = asset_service._build_object_key(home_id, "image/png", sha)
    assert key.startswith(f"home/{home_id}/")
    assert key.endswith(".png")
    assert sha[:12] in key
    # No user-supplied filename anywhere.
    assert "../../" not in key
    assert "/" not in key.split("/")[-1].replace(".png", "").replace("_", "")


# ------------------------------------------------------------- happy paths


async def test_upload_png_happy_path(db_session, seeded_actor) -> None:  # type: ignore[no-untyped-def]
    body = make_png_bytes()
    result = await asset_service.upload_image(
        db_session,
        home_id=seeded_actor.home_id,
        user_id=seeded_actor.user_id,
        filename="photo.png",
        content_type="image/png",
        body=body,
    )
    assert result.deduplicated is False
    assert result.asset.content_type == "image/png"
    assert result.asset.size_bytes == len(body)
    assert result.asset.status == AssetStatus.READY.value
    assert result.asset.width == 2
    assert result.asset.height == 2
    assert result.url.startswith("http")
    assert "X-Amz-Signature" in result.url or "Signature" in result.url


async def test_upload_jpeg_happy_path(db_session, seeded_actor) -> None:  # type: ignore[no-untyped-def]
    body = make_jpeg_bytes()
    result = await asset_service.upload_image(
        db_session,
        home_id=seeded_actor.home_id,
        user_id=seeded_actor.user_id,
        filename=None,
        content_type="image/jpeg",
        body=body,
    )
    assert result.asset.content_type == "image/jpeg"
    assert result.asset.original_filename is None


async def test_upload_webp_happy_path(db_session, seeded_actor) -> None:  # type: ignore[no-untyped-def]
    body = make_webp_bytes()
    result = await asset_service.upload_image(
        db_session,
        home_id=seeded_actor.home_id,
        user_id=seeded_actor.user_id,
        filename="x.webp",
        content_type="image/webp",
        body=body,
    )
    assert result.asset.content_type == "image/webp"


# ------------------------------------------------------------- dedup path


async def test_upload_dedup_returns_existing(db_session, seeded_actor) -> None:  # type: ignore[no-untyped-def]
    body = make_png_bytes()
    first = await asset_service.upload_image(
        db_session,
        home_id=seeded_actor.home_id,
        user_id=seeded_actor.user_id,
        filename="a.png",
        content_type="image/png",
        body=body,
    )
    second = await asset_service.upload_image(
        db_session,
        home_id=seeded_actor.home_id,
        user_id=seeded_actor.user_id,
        filename="b.png",
        content_type="image/png",
        body=body,
    )
    assert first.asset.id == second.asset.id
    assert second.deduplicated is True
    # Only one DB row exists.
    rows = (
        await db_session.execute(
            select(Asset).where(Asset.home_id == seeded_actor.home_id)
        )
    ).scalars().all()
    assert len(rows) == 1


# --------------------------------------------------------- rejection paths


async def test_rejects_unsupported_content_type(db_session, seeded_actor) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(UploadRejectedError) as exc_info:
        await asset_service.upload_image(
            db_session,
            home_id=seeded_actor.home_id,
            user_id=seeded_actor.user_id,
            filename="x.gif",
            content_type="image/gif",
            body=b"GIF89a" + b"\x00" * 100,
        )
    assert exc_info.value.code == "unsupported_content_type"
    assert exc_info.value.http_status == 400


async def test_rejects_empty_body(db_session, seeded_actor) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(UploadRejectedError) as exc_info:
        await asset_service.upload_image(
            db_session,
            home_id=seeded_actor.home_id,
            user_id=seeded_actor.user_id,
            filename="x.png",
            content_type="image/png",
            body=b"",
        )
    assert exc_info.value.code == "empty_body"


async def test_rejects_oversize(db_session, seeded_actor) -> None:  # type: ignore[no-untyped-def]
    # Build a body that exceeds the cap; magic bytes must still pass so the
    # size cap is what trips first. We construct a PNG with random trailing
    # data so it stays above the cap.
    head = b"\x89PNG\r\n\x1a\n" + b"\x00" * 24
    body = head + b"\x00" * (MAX_UPLOAD_BYTES + 1)
    with pytest.raises(UploadRejectedError) as exc_info:
        await asset_service.upload_image(
            db_session,
            home_id=seeded_actor.home_id,
            user_id=seeded_actor.user_id,
            filename="x.png",
            content_type="image/png",
            body=body,
        )
    assert exc_info.value.code == "file_too_large"


async def test_rejects_magic_byte_mismatch(db_session, seeded_actor) -> None:  # type: ignore[no-untyped-def]
    # Claim PNG, send JPEG bytes.
    body = make_jpeg_bytes()
    with pytest.raises(UploadRejectedError) as exc_info:
        await asset_service.upload_image(
            db_session,
            home_id=seeded_actor.home_id,
            user_id=seeded_actor.user_id,
            filename="x.png",
            content_type="image/png",
            body=body,
        )
    assert exc_info.value.code == "magic_byte_mismatch"


async def test_filename_not_used_as_object_key(db_session, seeded_actor) -> None:  # type: ignore[no-untyped-def]
    """Even with a malicious-looking filename, the object key is server-side."""
    body = make_png_bytes()
    evil = "../../../etc/passwd.png"
    result = await asset_service.upload_image(
        db_session,
        home_id=seeded_actor.home_id,
        user_id=seeded_actor.user_id,
        filename=evil,
        content_type="image/png",
        body=body,
    )
    assert ".." not in result.asset.object_key
    assert "passwd" not in result.asset.object_key
    # original_filename is preserved for audit but not used as key.
    assert result.asset.original_filename == evil


# ----------------------------------------------------------- get / delete


async def test_get_asset_returns_row(db_session, seeded_actor) -> None:  # type: ignore[no-untyped-def]
    body = make_png_bytes()
    created = await asset_service.upload_image(
        db_session,
        home_id=seeded_actor.home_id,
        user_id=seeded_actor.user_id,
        filename="a.png",
        content_type="image/png",
        body=body,
    )
    fetched = await asset_service.get_asset(db_session, created.asset.id)
    assert fetched is not None
    assert fetched.id == created.asset.id


async def test_get_asset_missing_returns_none(db_session) -> None:  # type: ignore[no-untyped-def]
    fetched = await asset_service.get_asset(db_session, uuid.uuid4())
    assert fetched is None


async def test_delete_asset_removes_row(db_session, seeded_actor) -> None:  # type: ignore[no-untyped-def]
    body = make_png_bytes()
    created = await asset_service.upload_image(
        db_session,
        home_id=seeded_actor.home_id,
        user_id=seeded_actor.user_id,
        filename="a.png",
        content_type="image/png",
        body=body,
    )
    await asset_service.delete_asset(db_session, created.asset)
    assert await asset_service.get_asset(db_session, created.asset.id) is None


async def test_make_presigned_url_returns_valid_url(db_session, seeded_actor) -> None:  # type: ignore[no-untyped-def]
    body = make_png_bytes()
    created = await asset_service.upload_image(
        db_session,
        home_id=seeded_actor.home_id,
        user_id=seeded_actor.user_id,
        filename="a.png",
        content_type="image/png",
        body=body,
    )
    url = asset_service.make_presigned_url(created.asset)
    assert url.startswith("http")
    # Presigned URL must contain the bucket in the path (path-style addressing).
    assert created.asset.bucket in url
    assert created.asset.object_key in url


async def test_public_endpoint_rewrites_host(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """When `minio_public_endpoint` is set, presigned URLs should swap the
    internal host for the public one so the browser can resolve them.
    """
    from app.core.config import settings
    from app.storage import minio_client

    # The mock_s3 fixture sets minio_endpoint to s3.amazonaws.com. We set
    # the public endpoint to a different value and assert the rewrite.
    monkeypatch.setattr(settings, "minio_public_endpoint", "public.example.com")
    url = minio_client.presigned_get_url("any-bucket", "any/key.png", 60)
    assert "public.example.com" in url
    # The internal endpoint must NOT leak to the browser.
    assert settings.minio_endpoint not in url


def test_no_public_endpoint_returns_internal_url(monkeypatch) -> None:
    """When `minio_public_endpoint` is unset, presigned URLs use the same
    host as the internal endpoint (single-host / single-network deploys).
    """
    from app.core.config import settings
    from app.storage import minio_client

    monkeypatch.setattr(settings, "minio_public_endpoint", None)
    url = minio_client.presigned_get_url("any-bucket", "any/key.png", 60)
    assert settings.minio_endpoint in url
