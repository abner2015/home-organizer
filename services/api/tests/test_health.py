"""Tests for / and /health endpoints."""
from unittest.mock import patch

from fastapi.testclient import TestClient


def test_root_returns_service_info(client: TestClient) -> None:
    resp = client.get("/")
    assert resp.status_code == 200
    data = resp.json()
    assert "name" in data
    assert "version" in data
    assert data["docs"] == "/docs"
    assert data["health"] == "/health"


def test_health_ok_when_all_deps_up(client: TestClient) -> None:
    with (
        patch("app.main.check_database", return_value=(True, None)),
        patch("app.main.check_redis", return_value=(True, None)),
    ):
        resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["checks"]["database"] == {"ok": True, "error": None}
    assert data["checks"]["redis"] == {"ok": True, "error": None}


def test_health_degraded_when_db_down(client: TestClient) -> None:
    with (
        patch("app.main.check_database", return_value=(False, "OperationalError")),
        patch("app.main.check_redis", return_value=(True, None)),
    ):
        resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "degraded"
    assert data["checks"]["database"] == {"ok": False, "error": "OperationalError"}
    assert data["checks"]["redis"] == {"ok": True, "error": None}


def test_health_degraded_when_redis_down(client: TestClient) -> None:
    with (
        patch("app.main.check_database", return_value=(True, None)),
        patch("app.main.check_redis", return_value=(False, "ConnectionError")),
    ):
        resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "degraded"
    assert data["checks"]["redis"] == {"ok": False, "error": "ConnectionError"}


def test_validation_error_format(client: TestClient) -> None:
    """Sanity: error envelope shape is consistent.

    Trigger validation via a non-existent route with a malformed request —
    Pydantic validation will not fire here, so we just assert the app
    responds in a sane way. Real validation tests are added with API
    routes in Phase 2+.
    """
    resp = client.get("/this-route-does-not-exist")
    assert resp.status_code == 404
    body = resp.json()
    assert "error" in body
    assert "code" in body["error"]
    assert "message" in body["error"]
