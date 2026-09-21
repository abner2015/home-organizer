"""Tests for the switchable storage backend (``app.storage.backend``)."""
from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest

from app.core.config import settings
from app.storage.backend import (
    LocalBackend,
    local_storage_signature,
    reset_storage,
)
from app.storage.errors import StorageError

_KEY = "home/abc/2026/09/thing.png"


@pytest.fixture
def backend(tmp_path) -> LocalBackend:
    b = LocalBackend(str(tmp_path / "storage"))
    b.ensure_ready()
    return b


# ------------------------------------------------------------------ round trip


def test_put_get_exists_delete_round_trip(backend: LocalBackend) -> None:
    backend.put(_KEY, b"\x89PNG\r\n\x1a\npayload", "image/png")
    assert backend.exists(_KEY) is True
    assert backend.get(_KEY) == b"\x89PNG\r\n\x1a\npayload"

    backend.delete(_KEY)
    assert backend.exists(_KEY) is False
    with pytest.raises(StorageError):
        backend.get(_KEY)


def test_put_creates_nested_directories(backend: LocalBackend, tmp_path) -> None:
    backend.put("a/b/c/d.bin", b"x", "application/octet-stream")
    assert (tmp_path / "storage" / "a" / "b" / "c" / "d.bin").is_file()


def test_delete_is_idempotent(backend: LocalBackend) -> None:
    backend.delete("never/existed.png")  # no raise


def test_read_returns_the_exact_bytes(backend: LocalBackend) -> None:
    """JPEG/PNG payloads must survive the round trip byte-for-byte."""
    body = bytes(range(256)) * 4
    backend.put("blob.bin", body, "application/octet-stream")
    assert backend.get("blob.bin") == body


# ------------------------------------------------------------------ path safety


@pytest.mark.parametrize(
    "key",
    [
        "../escape.png",
        "a/../../escape.png",
        "/etc/passwd",
        "..",
    ],
)
def test_resolve_rejects_traversal_and_absolute_keys(
    backend: LocalBackend, key: str
) -> None:
    with pytest.raises(StorageError):
        backend.get(key)


def test_resolve_rejects_empty_key(backend: LocalBackend) -> None:
    with pytest.raises(StorageError):
        backend.get("")


def test_traversal_cannot_write_outside_the_root(
    backend: LocalBackend, tmp_path
) -> None:
    outside = tmp_path / "escaped.png"
    with pytest.raises(StorageError):
        backend.put("../escaped.png", b"nope", "image/png")
    assert not outside.exists()


# ------------------------------------------------------------------ signed urls


def test_url_for_signature_is_recomputable(backend: LocalBackend) -> None:
    url = backend.url_for(_KEY, expires_seconds=60)
    parsed = urlparse(url)
    assert parsed.path == f"/api/v1/files/{_KEY}"
    qs = parse_qs(parsed.query)
    exp = int(qs["exp"][0])
    # The verifier recomputes the HMAC from `key` + `exp` alone.
    assert qs["sig"][0] == local_storage_signature(_KEY, exp)


def test_url_for_is_stable_for_the_same_expiry(backend: LocalBackend) -> None:
    a = backend.url_for(_KEY, expires_seconds=60)
    b = backend.url_for(_KEY, expires_seconds=60)
    assert parse_qs(urlparse(a).query)["sig"] == parse_qs(urlparse(b).query)["sig"]


def test_url_for_differs_per_key(backend: LocalBackend) -> None:
    a = parse_qs(urlparse(backend.url_for(_KEY)).query)
    b = parse_qs(urlparse(backend.url_for("other.png")).query)
    assert a["sig"] != b["sig"]


def test_url_for_honours_api_public_base_url(
    backend: LocalBackend, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "api_public_base_url", "https://api.example.com/")
    assert backend.url_for("a.png").startswith("https://api.example.com/api/v1/files/")


def test_signature_changes_with_expiry(backend: LocalBackend) -> None:
    url = backend.url_for(_KEY, expires_seconds=60)
    exp = int(parse_qs(urlparse(url).query)["exp"][0])
    assert local_storage_signature(_KEY, exp) != local_storage_signature(_KEY, exp + 1)


# ------------------------------------------------------------------ uploads


def test_put_url_is_unsupported_locally(backend: LocalBackend) -> None:
    """There is no browser-reachable PUT target for a filesystem backend."""
    with pytest.raises(StorageError):
        backend.put_url(_KEY, "image/png")


# ------------------------------------------------------------------ factory


def test_get_storage_follows_the_setting(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    from app.storage.backend import get_storage

    monkeypatch.setattr(settings, "storage_backend", "local")
    monkeypatch.setattr(settings, "storage_local_dir", str(tmp_path))
    reset_storage()
    assert get_storage().name == "local"

    monkeypatch.setattr(settings, "storage_backend", "minio")
    reset_storage()
    assert get_storage().name == "minio"
