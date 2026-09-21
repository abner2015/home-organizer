"""HTTP-level tests for POST/GET/DELETE /api/v1/assets/*."""
from __future__ import annotations

import uuid

from tests.api.conftest import make_jpeg_bytes, make_png_bytes, make_webp_bytes

# ----------------------------------------------------------------- helpers


def _upload(client, actor, body: bytes, content_type: str, filename: str = "x"):
    return client.post(
        "/api/v1/assets/upload",
        headers=actor.headers(),
        files={"file": (filename, body, content_type)},
    )


# --------------------------------------------------------- happy paths


async def test_upload_png_returns_201(api_client, seeded_actor) -> None:  # type: ignore[no-untyped-def]
    body = make_png_bytes()
    resp = _upload(api_client, seeded_actor, body, "image/png", "photo.png")
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["content_type"] == "image/png"
    assert data["size"] == len(body)
    assert data["width"] == 2
    assert data["height"] == 2
    assert data["deduplicated"] is False
    assert data["asset_id"]
    assert data["url"].startswith("http")
    # The Web app attaches the key to a new item, so it must come back here.
    assert data["object_key"].startswith(f"home/{seeded_actor.home_id}/")


async def test_upload_jpeg_returns_201(api_client, seeded_actor) -> None:  # type: ignore[no-untyped-def]
    resp = _upload(api_client, seeded_actor, make_jpeg_bytes(), "image/jpeg", "p.jpg")
    assert resp.status_code == 201
    assert resp.json()["content_type"] == "image/jpeg"


async def test_upload_webp_returns_201(api_client, seeded_actor) -> None:  # type: ignore[no-untyped-def]
    resp = _upload(api_client, seeded_actor, make_webp_bytes(), "image/webp", "p.webp")
    assert resp.status_code == 201
    assert resp.json()["content_type"] == "image/webp"


# ------------------------------------------------------------- dedup


async def test_dedup_returns_same_asset(api_client, seeded_actor) -> None:  # type: ignore[no-untyped-def]
    body = make_png_bytes()
    r1 = _upload(api_client, seeded_actor, body, "image/png", "a.png")
    r2 = _upload(api_client, seeded_actor, body, "image/png", "b.png")
    assert r1.status_code == 201
    assert r2.status_code == 201
    assert r1.json()["asset_id"] == r2.json()["asset_id"]
    assert r2.json()["deduplicated"] is True


# --------------------------------------------------------- validation errors


async def test_rejects_gif(api_client, seeded_actor) -> None:  # type: ignore[no-untyped-def]
    body = b"GIF89a" + b"\x00" * 50
    resp = _upload(api_client, seeded_actor, body, "image/gif", "x.gif")
    assert resp.status_code == 400
    body_json = resp.json()
    assert body_json["error"]["code"] == "unsupported_content_type"


async def test_rejects_magic_byte_mismatch(api_client, seeded_actor) -> None:  # type: ignore[no-untyped-def]
    # Claim PNG, send JPEG bytes.
    body = make_jpeg_bytes()
    resp = _upload(api_client, seeded_actor, body, "image/png", "x.png")
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "magic_byte_mismatch"


async def test_rejects_oversize(api_client, seeded_actor) -> None:  # type: ignore[no-untyped-def]
    head = b"\x89PNG\r\n\x1a\n" + b"\x00" * 24
    body = head + b"\x00" * (20 * 1024 * 1024)
    resp = _upload(api_client, seeded_actor, body, "image/png", "big.png")
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "file_too_large"


async def test_rejects_empty_body(api_client, seeded_actor) -> None:  # type: ignore[no-untyped-def]
    resp = _upload(api_client, seeded_actor, b"", "image/png", "empty.png")
    # FastAPI rejects empty multipart file content; either 400 or 422.
    assert resp.status_code in (400, 422)


# --------------------------------------------------------- auth headers


async def test_missing_token_is_rejected(api_client, seeded_actor) -> None:  # type: ignore[no-untyped-def]
    body = make_png_bytes()
    resp = api_client.post(
        "/api/v1/assets/upload",
        headers={"X-Home-Id": str(seeded_actor.home_id)},
        files={"file": ("x.png", body, "image/png")},
    )
    assert resp.status_code == 401


async def test_invalid_token_is_rejected(api_client, seeded_actor) -> None:  # type: ignore[no-untyped-def]
    body = make_png_bytes()
    resp = api_client.post(
        "/api/v1/assets/upload",
        headers={
            "Authorization": "Bearer not-a-jwt",
            "X-Home-Id": str(seeded_actor.home_id),
        },
        files={"file": ("x.png", body, "image/png")},
    )
    assert resp.status_code == 401


# --------------------------------------------------------- GET / DELETE


async def test_get_asset(api_client, seeded_actor) -> None:  # type: ignore[no-untyped-def]
    body = make_png_bytes()
    upload = _upload(api_client, seeded_actor, body, "image/png", "p.png")
    asset_id = upload.json()["asset_id"]
    resp = api_client.get(
        f"/api/v1/assets/{asset_id}",
        headers=seeded_actor.headers(),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["asset_id"] == asset_id
    assert data["home_id"] == str(seeded_actor.home_id)
    assert data["status"] == "ready"
    assert data["url"].startswith("http")


async def test_get_asset_wrong_home_returns_404(api_client, seeded_actor) -> None:  # type: ignore[no-untyped-def]
    body = make_png_bytes()
    upload = _upload(api_client, seeded_actor, body, "image/png", "p.png")
    asset_id = upload.json()["asset_id"]
    # A valid token, but a home this user is not a member of.
    other = {**seeded_actor.headers(), "X-Home-Id": str(uuid.uuid4())}
    resp = api_client.get(f"/api/v1/assets/{asset_id}", headers=other)
    assert resp.status_code == 404


async def test_get_missing_asset_returns_404(api_client, seeded_actor) -> None:  # type: ignore[no-untyped-def]
    resp = api_client.get(
        f"/api/v1/assets/{uuid.uuid4()}",
        headers=seeded_actor.headers(),
    )
    assert resp.status_code == 404


async def test_delete_asset(api_client, seeded_actor) -> None:  # type: ignore[no-untyped-def]
    body = make_png_bytes()
    upload = _upload(api_client, seeded_actor, body, "image/png", "p.png")
    asset_id = upload.json()["asset_id"]
    resp = api_client.delete(
        f"/api/v1/assets/{asset_id}",
        headers=seeded_actor.headers(),
    )
    assert resp.status_code == 204
    follow = api_client.get(
        f"/api/v1/assets/{asset_id}",
        headers=seeded_actor.headers(),
    )
    assert follow.status_code == 404


async def test_delete_wrong_home_returns_404(api_client, seeded_actor) -> None:  # type: ignore[no-untyped-def]
    body = make_png_bytes()
    upload = _upload(api_client, seeded_actor, body, "image/png", "p.png")
    asset_id = upload.json()["asset_id"]
    # A valid token, but a home this user is not a member of.
    other = {**seeded_actor.headers(), "X-Home-Id": str(uuid.uuid4())}
    resp = api_client.delete(f"/api/v1/assets/{asset_id}", headers=other)
    assert resp.status_code == 404


# -------------------------------------------- response never leaks credentials


async def test_response_does_not_contain_minio_credentials(  # type: ignore[no-untyped-def]
    api_client, seeded_actor
) -> None:
    body = make_png_bytes()
    upload = _upload(api_client, seeded_actor, body, "image/png", "p.png")
    text = upload.text
    assert "minio_root_user" not in text
    assert "homeorg-minio" not in text  # the default minio password
    # The internal endpoint host is allowed to appear in the presigned URL,
    # but the secret access key must never be in the response.
    assert settings_minio_secret() not in text  # type: ignore[name-defined]


def settings_minio_secret() -> str:
    from app.core.config import settings
    return settings.minio_root_password
