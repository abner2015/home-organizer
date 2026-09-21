"""Tests for ``GET /api/v1/files/{key}`` — the local-storage read endpoint.

The route exists only when ``storage_backend == 'local'``: the signature in the
query string is the credential, so there is deliberately no ``get_actor``
dependency (the browser fetches these URLs directly, exactly like a presigned
MinIO GET).
"""
from __future__ import annotations

from urllib.parse import urlparse

from fastapi.testclient import TestClient

from app.storage.backend import get_storage, local_storage_signature
from tests.api.conftest import (
    make_jpeg_bytes,
    make_png_bytes,
    make_webp_bytes,
)

_KEY = "home/deadbeef/2026/09/photo.png"


def _signed_url(key: str, *, exp: int) -> str:
    """A link whose signature matches its expiry — the caller's own construction."""
    return f"/api/v1/files/{key}?exp={exp}&sig={local_storage_signature(key, exp)}"


def test_valid_signature_serves_the_bytes(
    api_client: TestClient, local_storage
) -> None:
    body = make_png_bytes(4, 4)
    get_storage().put(_KEY, body, "image/png")

    resp = api_client.get(get_storage().url_for(_KEY))
    assert resp.status_code == 200, resp.text
    assert resp.content == body
    assert resp.headers["content-type"] == "image/png"
    assert "private" in resp.headers["cache-control"]


def test_content_type_follows_the_extension(
    api_client: TestClient, local_storage
) -> None:
    cases = [
        ("home/x/a.jpg", make_jpeg_bytes(), "image/jpeg"),
        ("home/x/b.png", make_png_bytes(), "image/png"),
        ("home/x/c.webp", make_webp_bytes(), "image/webp"),
    ]
    for key, body, expected in cases:
        get_storage().put(key, body, expected)
        resp = api_client.get(get_storage().url_for(key))
        assert resp.status_code == 200
        assert resp.headers["content-type"] == expected


def test_wrong_signature_is_403(api_client: TestClient, local_storage) -> None:
    get_storage().put(_KEY, make_png_bytes(), "image/png")
    resp = api_client.get(f"/api/v1/files/{_KEY}?exp=2000000000&sig={'0' * 32}")
    assert resp.status_code == 403


def test_expired_signature_is_403(api_client: TestClient, local_storage) -> None:
    get_storage().put(_KEY, make_png_bytes(), "image/png")
    # Correctly signed, but for a moment that has already passed.
    resp = api_client.get(_signed_url(_KEY, exp=1_000_000_000))
    assert resp.status_code == 403


def test_signature_for_a_different_key_is_403(
    api_client: TestClient, local_storage
) -> None:
    get_storage().put(_KEY, make_png_bytes(), "image/png")
    exp = 2_000_000_000
    other = local_storage_signature("home/x/somebody-else.png", exp)
    resp = api_client.get(f"/api/v1/files/{_KEY}?exp={exp}&sig={other}")
    assert resp.status_code == 403


def test_missing_params_are_403(api_client: TestClient, local_storage) -> None:
    get_storage().put(_KEY, make_png_bytes(), "image/png")
    resp = api_client.get(urlparse(get_storage().url_for(_KEY)).path)
    assert resp.status_code == 403


def test_unknown_key_is_404(api_client: TestClient, local_storage) -> None:
    """Signed correctly, but nothing was ever written there."""
    resp = api_client.get(get_storage().url_for("home/x/absent.png"))
    assert resp.status_code == 404


def test_route_is_absent_under_the_minio_backend(api_client: TestClient) -> None:
    """A MinIO deployment must not expose the filesystem route at all."""
    assert api_client.get(f"/api/v1/files/{_KEY}?exp=1&sig=x").status_code == 404
