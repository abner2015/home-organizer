"""Shared pytest fixtures for the API test package.

Provides:
- `mock_s3` (auto): wraps each test in `moto.mock_aws()`, creates the uploads
  bucket, and clears the boto3 client cache so the storage wrapper talks to
  the mock.
- `api_client`: FastAPI TestClient with the DB and storage layers mocked.

``seeded_actor`` and ``SeededActor`` live in the root ``conftest.py`` so
both unit and API tests can use them.
"""
from __future__ import annotations

import io
from collections.abc import AsyncIterator, Iterator

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from moto import mock_aws
from PIL import Image
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import settings
from app.db.enums import AssetStatus
from app.storage import minio_client
from app.storage.backend import get_storage, reset_storage

# Re-export for convenience; the canonical definition is in tests/conftest.py.
from tests.conftest import SeededActor, seeded_actor  # noqa: F401

# Re-export the StorageHierarchy fixture from tests/unit/conftest.py so API
# tests can request it alongside the seeded_actor.
from tests.unit.conftest import StorageHierarchy, storage_hierarchy  # noqa: F401

# ----------------------------------------------------------------- S3 mocking


@pytest.fixture(autouse=True)
def mock_s3(monkeypatch) -> Iterator[None]:
    """Wrap the test in a moto-mocked AWS environment.

    moto 5 intercepts boto3 calls only when the endpoint URL matches an
    `*.amazonaws.com` regex. We override `minio_endpoint` for the duration of
    the test so the storage wrapper talks to a host moto can stub. The
    boto3 client is cached via `lru_cache`; we clear it before AND after so
    cached connections don't leak across tests.

    `storage_backend` is pinned to `minio` for the same reason: `Settings`
    reads `services/api/.env`, so a developer pointing their local `.env` at
    the `local` backend would otherwise silently redirect the whole API suite
    at the filesystem. Tests that want `local` opt in explicitly.
    """
    monkeypatch.setattr(settings, "minio_endpoint", "s3.amazonaws.com")
    monkeypatch.setattr(settings, "minio_use_ssl", True)
    monkeypatch.setattr(settings, "storage_backend", "minio")
    minio_client.get_s3_client.cache_clear()
    reset_storage()
    with mock_aws():
        minio_client.ensure_bucket(settings.minio_bucket_uploads)
        yield
    minio_client.get_s3_client.cache_clear()
    reset_storage()


@pytest.fixture
def local_storage(monkeypatch, tmp_path) -> Iterator[None]:
    """Run a test against the `local` storage backend rooted at `tmp_path`."""
    monkeypatch.setattr(settings, "storage_backend", "local")
    monkeypatch.setattr(settings, "storage_local_dir", str(tmp_path / "storage"))
    monkeypatch.setattr(settings, "api_public_base_url", "")
    reset_storage()
    get_storage().ensure_ready()
    yield
    reset_storage()


# --------------------------------------------------------------- image helpers


def make_png_bytes(width: int = 2, height: int = 2) -> bytes:
    """Encode a tiny in-memory PNG."""
    img = Image.new("RGBA", (width, height), (255, 0, 0, 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def make_jpeg_bytes(width: int = 2, height: int = 2) -> bytes:
    img = Image.new("RGB", (width, height), (0, 255, 0))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def make_webp_bytes(width: int = 2, height: int = 2) -> bytes:
    img = Image.new("RGB", (width, height), (0, 0, 255))
    buf = io.BytesIO()
    img.save(buf, format="WEBP")
    return buf.getvalue()


# ----------------------------------------------------------- API client fixture


@pytest_asyncio.fixture
async def api_client(db_engine) -> AsyncIterator[TestClient]:
    """FastAPI TestClient with the DB dependency overridden to use SQLite."""
    from app.db.session import get_db
    from app.main import app

    factory = async_sessionmaker(db_engine, expire_on_commit=False)

    async def _override_get_db() -> AsyncIterator:
        async with factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


# Silence unused-import warning while keeping the symbol handy for future
# tests that want to assert storage internals.
_ = AssetStatus
