"""Password hashing + JWT issue / decode helpers.

Single source of truth for credential + token primitives. Used by
``auth_service`` and FastAPI dependencies; never import this from request
handlers directly.

Note: we talk to ``bcrypt`` directly rather than via ``passlib`` because
passlib's bcrypt backend self-test crashes on modern bcrypt releases (≥ 4.x)
which strictly enforce the 72-byte limit. Bypassing passlib is simpler than
fighting its backend detection.
"""
from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

import bcrypt
from jose import JWTError, jwt

from app.core.config import settings
from app.core.exceptions import UnauthorizedError

# bcrypt has a 72-byte input limit; anything beyond raises ValueError on
# modern bcrypt releases. Truncate explicitly so the behavior is documented
# and unit-testable.
_BCRYPT_MAX_BYTES = 72


def _truncate_password(plain: str) -> bytes:
    return plain.encode("utf-8")[:_BCRYPT_MAX_BYTES]


def hash_password(plain: str) -> str:
    """Hash a password with bcrypt. Truncates input to 72 bytes per bcrypt spec."""
    return bcrypt.hashpw(_truncate_password(plain), bcrypt.gensalt()).decode("ascii")


def verify_password(plain: str, hashed: str) -> bool:
    """Constant-time bcrypt verify; truncated input must match the original."""
    try:
        return bcrypt.checkpw(_truncate_password(plain), hashed.encode("ascii"))
    except (ValueError, TypeError):
        # Malformed hash or wrong algorithm — treat as a no-match.
        return False


# ---------------------------------------------------------------- truncate: JWT


TokenType = Literal["access", "refresh"]


def _encode(payload: dict[str, object]) -> str:
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def _decode(token: str) -> dict[str, object]:
    try:
        return jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
        )
    except JWTError as exc:
        raise UnauthorizedError("Invalid or expired token") from exc


def create_access_token(user_id: UUID) -> str:
    """Issue a short-lived access token (default TTL: ``settings.jwt_access_ttl``)."""
    now = datetime.now(UTC)
    payload: dict[str, object] = {
        "sub": str(user_id),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=settings.jwt_access_ttl)).timestamp()),
        "type": "access",
        "jti": secrets.token_urlsafe(16),
    }
    return _encode(payload)


def create_refresh_token(user_id: UUID) -> str:
    """Issue a long-lived refresh token. NEVER accepted as an access token."""
    now = datetime.now(UTC)
    payload: dict[str, object] = {
        "sub": str(user_id),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=settings.jwt_refresh_ttl)).timestamp()),
        "type": "refresh",
        "jti": secrets.token_urlsafe(16),
    }
    return _encode(payload)


def decode_token(token: str, expected_type: TokenType) -> UUID:
    """Decode a JWT, validate its ``type`` claim, and return the user id.

    Raises ``UnauthorizedError`` on any failure (bad signature, expired,
    wrong type, malformed UUID). Callers should not leak the underlying reason
    to the client.
    """
    payload = _decode(token)
    token_type = payload.get("type")
    if token_type != expected_type:
        raise UnauthorizedError("Invalid token type")
    sub = payload.get("sub")
    if not isinstance(sub, str):
        raise UnauthorizedError("Invalid token subject")
    try:
        return UUID(sub)
    except (ValueError, TypeError) as exc:
        raise UnauthorizedError("Invalid token subject") from exc


__all__ = [
    "TokenType",
    "create_access_token",
    "create_refresh_token",
    "decode_token",
    "hash_password",
    "verify_password",
]
