"""HTTP-level tests for /api/v1/auth/{signup,login,refresh,me}.

Exercises the full stack against the in-memory SQLite engine + mocked S3
(from tests/api/conftest.py). Validates status codes, envelope shape, and
the contract that ``password_hash`` is NEVER exposed.
"""
from __future__ import annotations

import uuid
from typing import Any


def _signup_payload(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "email": "alice@example.com",
        "password": "supersecret1",
        "display_name": "Alice",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------- signup


async def test_signup_returns_201_and_user_shape(api_client) -> None:  # type: ignore[no-untyped-def]
    resp = api_client.post("/api/v1/auth/signup", json=_signup_payload())
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["user"]["email"] == "alice@example.com"
    assert body["user"]["display_name"] == "Alice"
    uuid.UUID(body["user"]["id"])  # parses
    assert "password_hash" not in body["user"]
    assert "password" not in body["user"]


async def test_signup_normalizes_email(api_client) -> None:  # type: ignore[no-untyped-def]
    resp = api_client.post(
        "/api/v1/auth/signup",
        json=_signup_payload(email="  Alice@Example.COM  "),
    )
    assert resp.status_code == 201
    assert resp.json()["user"]["email"] == "alice@example.com"


async def test_signup_strips_display_name_whitespace(api_client) -> None:  # type: ignore[no-untyped-def]
    resp = api_client.post(
        "/api/v1/auth/signup", json=_signup_payload(display_name="  Alice  ")
    )
    assert resp.status_code == 201
    assert resp.json()["user"]["display_name"] == "Alice"


async def test_signup_duplicate_email_returns_409(api_client) -> None:  # type: ignore[no-untyped-def]
    first = api_client.post("/api/v1/auth/signup", json=_signup_payload())
    assert first.status_code == 201
    second = api_client.post(
        "/api/v1/auth/signup",
        json=_signup_payload(email="alice@example.com"),
    )
    assert second.status_code == 409
    body = second.json()
    assert body["error"]["code"] == "conflict"
    assert body["error"]["message"] == "Email already registered"


async def test_signup_short_password_returns_422(api_client) -> None:  # type: ignore[no-untyped-def]
    resp = api_client.post(
        "/api/v1/auth/signup", json=_signup_payload(password="short")
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"


async def test_signup_bad_email_returns_422(api_client) -> None:  # type: ignore[no-untyped-def]
    resp = api_client.post(
        "/api/v1/auth/signup", json=_signup_payload(email="not-an-email")
    )
    assert resp.status_code == 422


async def test_signup_extra_field_is_forbidden(api_client) -> None:  # type: ignore[no-untyped-def]
    resp = api_client.post(
        "/api/v1/auth/signup",
        json={**_signup_payload(), "is_admin": True},
    )
    assert resp.status_code == 422


# ----------------------------------------------------------------- login


async def test_login_returns_tokens_and_correct_ttl(api_client) -> None:  # type: ignore[no-untyped-def]
    api_client.post("/api/v1/auth/signup", json=_signup_payload())
    resp = api_client.post(
        "/api/v1/auth/login",
        json={"email": "alice@example.com", "password": "supersecret1"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] > 0
    assert isinstance(body["access_token"], str) and body["access_token"]
    assert isinstance(body["refresh_token"], str) and body["refresh_token"]
    assert body["access_token"] != body["refresh_token"]


async def test_login_wrong_password_returns_401(api_client) -> None:  # type: ignore[no-untyped-def]
    api_client.post("/api/v1/auth/signup", json=_signup_payload())
    resp = api_client.post(
        "/api/v1/auth/login",
        json={"email": "alice@example.com", "password": "WRONG"},
    )
    assert resp.status_code == 401
    body = resp.json()
    assert body["error"]["code"] == "unauthenticated"
    assert body["error"]["message"] == "Invalid email or password"


async def test_login_unknown_email_returns_same_401(api_client) -> None:  # type: ignore[no-untyped-def]
    resp = api_client.post(
        "/api/v1/auth/login",
        json={"email": "ghost@example.com", "password": "anything"},
    )
    assert resp.status_code == 401
    assert resp.json()["error"]["message"] == "Invalid email or password"


# ------------------------------------------------------------------- /me


async def test_me_without_token_returns_401(api_client) -> None:  # type: ignore[no-untyped-def]
    resp = api_client.get("/api/v1/auth/me")
    assert resp.status_code == 401


async def test_me_with_malformed_token_returns_401(api_client) -> None:  # type: ignore[no-untyped-def]
    resp = api_client.get(
        "/api/v1/auth/me", headers={"Authorization": "Bearer not-a-jwt"}
    )
    assert resp.status_code == 401


async def test_me_with_valid_token_returns_user(api_client) -> None:  # type: ignore[no-untyped-def]
    api_client.post("/api/v1/auth/signup", json=_signup_payload())
    login = api_client.post(
        "/api/v1/auth/login",
        json={"email": "alice@example.com", "password": "supersecret1"},
    )
    access = login.json()["access_token"]
    resp = api_client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {access}"}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["email"] == "alice@example.com"
    assert body["display_name"] == "Alice"
    assert "password_hash" not in body
    assert "password" not in body


async def test_me_with_refresh_token_returns_401(api_client) -> None:  # type: ignore[no-untyped-def]
    api_client.post("/api/v1/auth/signup", json=_signup_payload())
    login = api_client.post(
        "/api/v1/auth/login",
        json={"email": "alice@example.com", "password": "supersecret1"},
    )
    refresh = login.json()["refresh_token"]
    resp = api_client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {refresh}"}
    )
    assert resp.status_code == 401


# ----------------------------------------------------------------- refresh


async def test_refresh_rotates_tokens(api_client) -> None:  # type: ignore[no-untyped-def]
    api_client.post("/api/v1/auth/signup", json=_signup_payload())
    first = api_client.post(
        "/api/v1/auth/login",
        json={"email": "alice@example.com", "password": "supersecret1"},
    )
    first_refresh = first.json()["refresh_token"]
    resp = api_client.post(
        "/api/v1/auth/refresh", json={"refresh_token": first_refresh}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["refresh_token"] != first_refresh
    assert body["access_token"]
    # New access token works on /me.
    me = api_client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"}
    )
    assert me.status_code == 200


async def test_refresh_with_access_token_returns_401(api_client) -> None:  # type: ignore[no-untyped-def]
    api_client.post("/api/v1/auth/signup", json=_signup_payload())
    login = api_client.post(
        "/api/v1/auth/login",
        json={"email": "alice@example.com", "password": "supersecret1"},
    )
    access = login.json()["access_token"]
    resp = api_client.post(
        "/api/v1/auth/refresh", json={"refresh_token": access}
    )
    assert resp.status_code == 401


# ----------------------------------------------------------------- envelope


async def test_error_envelope_has_request_id(api_client) -> None:  # type: ignore[no-untyped-def]
    # Any 4xx surfaces the standard envelope. Login with unknown email is the
    # simplest path.
    resp = api_client.post(
        "/api/v1/auth/login",
        json={"email": "ghost@example.com", "password": "x"},
    )
    assert resp.status_code == 401
    body = resp.json()
    assert "request_id" in body["error"]
    assert isinstance(body["error"]["request_id"], str)
    # Response also echoes the header so clients can log it.
    assert resp.headers["x-request-id"] == body["error"]["request_id"]


async def test_request_id_header_is_echoed(api_client) -> None:  # type: ignore[no-untyped-def]
    # A successful request should also carry the header (generated UUID).
    api_client.post("/api/v1/auth/signup", json=_signup_payload())
    resp = api_client.post(
        "/api/v1/auth/login",
        json={"email": "alice@example.com", "password": "supersecret1"},
    )
    assert resp.status_code == 200
    rid = resp.headers.get("x-request-id")
    assert rid is not None
    uuid.UUID(rid)  # parses


async def test_caller_supplied_request_id_is_honored(api_client) -> None:  # type: ignore[no-untyped-def]
    api_client.post("/api/v1/auth/signup", json=_signup_payload())
    provided = "test-req-123"
    resp = api_client.post(
        "/api/v1/auth/login",
        json={"email": "alice@example.com", "password": "supersecret1"},
        headers={"X-Request-ID": provided},
    )
    assert resp.status_code == 200
    assert resp.headers["x-request-id"] == provided
