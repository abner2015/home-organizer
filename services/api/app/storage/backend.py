"""Switchable object-storage backends.

Business code calls `get_storage()` and never imports a concrete backend. Two
implementations exist:

- `MinioBackend` — delegates to `app.storage.minio_client` (S3-compatible).
- `LocalBackend` — writes files under `settings.storage_local_dir` and serves
  them back through `GET /api/v1/files/{key}` using a signed, expiring URL.

`storage_backend` selects one at startup. Both expose the same surface:
`put` / `get` / `delete` / `exists` / `url_for`.
"""
from __future__ import annotations

import hashlib
import hmac
import time
from functools import lru_cache
from pathlib import Path
from typing import Protocol

from app.core.config import settings
from app.core.logging import get_logger
from app.storage import minio_client
from app.storage.errors import StorageError

logger = get_logger(__name__)

# Domain separator so a local-storage signature can never be confused with any
# other HMAC this app computes over the same secret.
_SIG_CONTEXT = "local-storage"


class StorageBackend(Protocol):
    """Minimal object-storage surface used by the asset layer."""

    name: str

    def ensure_ready(self) -> None:
        """Make the backend usable (create bucket / root directory)."""
        ...

    def put(self, key: str, body: bytes, content_type: str) -> None: ...

    def get(self, key: str) -> bytes: ...

    def delete(self, key: str) -> None: ...

    def exists(self, key: str) -> bool: ...

    def url_for(self, key: str, expires_seconds: int = 3600) -> str: ...

    def put_url(
        self, key: str, content_type: str, expires_seconds: int = 600
    ) -> str:
        """A URL the *browser* can PUT bytes to directly.

        Only meaningful when the backend is independently reachable from the
        client. Backends without that property raise `StorageError`, and
        `asset_service.presign_upload` rejects the request before calling here.
        """
        ...


# --------------------------------------------------------------------------- minio


class MinioBackend:
    """S3-compatible backend. Thin pass-through to `minio_client`."""

    name = "minio"

    def __init__(self, bucket: str) -> None:
        self._bucket = bucket

    def ensure_ready(self) -> None:
        minio_client.ensure_bucket(self._bucket)

    def put(self, key: str, body: bytes, content_type: str) -> None:
        minio_client.put_object(self._bucket, key, body, content_type)

    def get(self, key: str) -> bytes:
        return minio_client.get_object(self._bucket, key)

    def delete(self, key: str) -> None:
        minio_client.delete_object(self._bucket, key)

    def exists(self, key: str) -> bool:
        return minio_client.object_exists(self._bucket, key)

    def url_for(self, key: str, expires_seconds: int = 3600) -> str:
        return minio_client.presigned_get_url(self._bucket, key, expires_seconds)

    def put_url(self, key: str, content_type: str, expires_seconds: int = 600) -> str:
        return minio_client.presigned_put_url(
            self._bucket, key, content_type, expires_seconds
        )


# --------------------------------------------------------------------------- local


def local_storage_secret() -> str:
    """Signing key for local read URLs."""
    return settings.storage_local_secret or settings.jwt_secret


def local_storage_signature(key: str, expires_at: int) -> str:
    """HMAC over `(key, expires_at)`. Shared by the writer and the verifier."""
    payload = f"{_SIG_CONTEXT}|{key}|{expires_at}".encode()
    digest = hmac.new(local_storage_secret().encode(), payload, hashlib.sha256)
    return digest.hexdigest()[:32]


class LocalBackend:
    """Filesystem backend for deployments with no object store.

    Keys are the same server-generated strings the MinIO path uses
    (`home/<uuid>/<YYYY>/<MM>/<uuid>_<shard>.<ext>`), so the two backends are
    swappable without touching the `assets` table.
    """

    name = "local"

    def __init__(self, root: str) -> None:
        self._root = Path(root)

    # -- path safety ------------------------------------------------------

    def _resolve(self, key: str) -> Path:
        """Map an object key to a path under the root.

        The key is attacker-influenced only in theory (the API mints it), but
        an absolute or `..`-bearing key would escape the root entirely, so both
        are rejected before any filesystem call.
        """
        if not key:
            raise StorageError("Empty object key")
        candidate = Path(key)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise StorageError(f"Refusing unsafe object key: {key!r}")

        root = self._root.resolve()
        resolved = (root / candidate).resolve()
        if not resolved.is_relative_to(root):
            raise StorageError(f"Object key escapes storage root: {key!r}")
        return resolved

    # -- StorageBackend ---------------------------------------------------

    def ensure_ready(self) -> None:
        self._root.mkdir(parents=True, exist_ok=True)

    def put(self, key: str, body: bytes, content_type: str) -> None:
        # content_type is unused on disk: readers derive it from the extension
        # (the key always carries one), so there is no sidecar metadata file.
        del content_type
        path = self._resolve(key)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(body)
        except OSError as exc:
            raise StorageError(f"Write failed for {key}: {exc}") from exc
        logger.info("storage.object_put", backend="local", key=key, size=len(body))

    def get(self, key: str) -> bytes:
        path = self._resolve(key)
        try:
            return path.read_bytes()
        except OSError as exc:
            raise StorageError(f"Read failed for {key}: {exc}") from exc

    def delete(self, key: str) -> None:
        path = self._resolve(key)
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            raise StorageError(f"Delete failed for {key}: {exc}") from exc

    def exists(self, key: str) -> bool:
        return self._resolve(key).is_file()

    def url_for(self, key: str, expires_seconds: int = 3600) -> str:
        expires_at = int(time.time()) + expires_seconds
        sig = local_storage_signature(key, expires_at)
        base = settings.api_public_base_url.rstrip("/")
        return f"{base}/api/v1/files/{key}?exp={expires_at}&sig={sig}"

    def put_url(self, key: str, content_type: str, expires_seconds: int = 600) -> str:
        raise StorageError("Local storage backend cannot accept direct uploads")


# --------------------------------------------------------------------------- factory


@lru_cache
def get_storage() -> StorageBackend:
    """Return the configured backend (process-wide singleton)."""
    if settings.storage_backend == "local":
        return LocalBackend(settings.storage_local_dir)
    return MinioBackend(settings.minio_bucket_uploads)


def reset_storage() -> None:
    """Drop the cached backend. Tests flip `storage_backend` and call this."""
    get_storage.cache_clear()


__all__ = [
    "LocalBackend",
    "MinioBackend",
    "StorageBackend",
    "get_storage",
    "local_storage_secret",
    "local_storage_signature",
    "reset_storage",
]
