"""Pure unit tests for ``app.services.security``.

No DB, no FastAPI — exercise password hashing (incl. bcrypt 72-byte
truncation) and JWT issue/decode in isolation.
"""
from __future__ import annotations

import time
from uuid import UUID, uuid4

import pytest

from app.core.config import settings
from app.core.exceptions import UnauthorizedError
from app.services.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)

# --------------------------------------------------------------- passwords


def test_hash_then_verify_round_trip() -> None:
    hashed = hash_password("correct horse battery staple")
    assert hashed != "correct horse battery staple"
    assert hashed.startswith("$2")
    assert verify_password("correct horse battery staple", hashed) is True


def test_verify_rejects_wrong_password() -> None:
    hashed = hash_password("hunter2")
    assert verify_password("hunter3", hashed) is False


def test_verify_rejects_malformed_hash() -> None:
    # passlib should not raise; we return False instead.
    assert verify_password("anything", "not-a-bcrypt-hash") is False


def test_password_is_truncated_to_72_bytes() -> None:
    """bcrypt silently truncates past 72 bytes. We truncate explicitly to make
    the behavior testable + consistent across implementations."""
    short = "a" * 72
    long = "a" * 73 + "DIFFERENT-TAIL"
    short_hash = hash_password(short)
    long_hash = hash_password(long)
    # 72-byte prefix is identical, so both hashes must verify either input.
    assert verify_password(short, short_hash) is True
    assert verify_password(short, long_hash) is True
    assert verify_password(long, short_hash) is True
    assert verify_password(long, long_hash) is True


# ---------------------------------------------------------------- JWT shape


def test_access_and_refresh_tokens_differ() -> None:
    user_id = uuid4()
    a = create_access_token(user_id)
    r = create_refresh_token(user_id)
    assert a != r


def test_decode_access_returns_user_id() -> None:
    user_id = uuid4()
    token = create_access_token(user_id)
    assert decode_token(token, expected_type="access") == user_id
    assert isinstance(decode_token(token, expected_type="access"), UUID)


def test_decode_rejects_access_token_when_refresh_expected() -> None:
    token = create_access_token(uuid4())
    with pytest.raises(UnauthorizedError):
        decode_token(token, expected_type="refresh")


def test_decode_rejects_refresh_token_when_access_expected() -> None:
    token = create_refresh_token(uuid4())
    with pytest.raises(UnauthorizedError):
        decode_token(token, expected_type="access")


def test_decode_rejects_garbage() -> None:
    with pytest.raises(UnauthorizedError):
        decode_token("not-a-jwt", expected_type="access")


def test_decode_rejects_tampered_signature() -> None:
    token = create_access_token(uuid4())
    tampered = token[:-2] + ("AA" if token[-2:] != "AA" else "BB")
    with pytest.raises(UnauthorizedError):
        decode_token(tampered, expected_type="access")


def test_decoded_exp_is_within_configured_ttl() -> None:
    """The token's ``exp`` must be ~``settings.jwt_access_ttl`` seconds in the
    future (slop for clock drift)."""
    from jose import jwt

    user_id = uuid4()
    before = int(time.time())
    token = create_access_token(user_id)
    payload = jwt.decode(
        token, settings.jwt_secret, algorithms=[settings.jwt_algorithm]
    )
    delta = payload["exp"] - before
    assert settings.jwt_access_ttl - 5 <= delta <= settings.jwt_access_ttl + 5
    assert payload["type"] == "access"
    assert payload["sub"] == str(user_id)
