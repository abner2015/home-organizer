"""Auth business logic: signup, authenticate, refresh, fetch-by-id.

Pure async functions taking an AsyncSession; routes should depend on this module
rather than touching the ORM directly.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import ConflictError, UnauthorizedError
from app.models.user import User
from app.schemas.auth import (
    LoginRequest,
    RefreshRequest,
    SignupRequest,
    TokenResponse,
)
from app.services.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)


def _normalize_email(email: str) -> str:
    return email.strip().lower()


def _issue_tokens(user_id: uuid.UUID) -> TokenResponse:
    return TokenResponse(
        access_token=create_access_token(user_id),
        refresh_token=create_refresh_token(user_id),
        token_type="bearer",
        expires_in=settings.jwt_access_ttl,
    )


async def signup(db: AsyncSession, req: SignupRequest) -> User:
    """Create a new user.

    Email uniqueness is enforced at the DB level; we explicitly ``flush()`` to
    surface IntegrityError as ``ConflictError`` rather than waiting until the
    session commits (which would surface a 500 instead).
    """
    user = User(
        id=uuid.uuid4(),
        email=_normalize_email(req.email),
        display_name=req.display_name.strip(),
        password_hash=hash_password(req.password),
    )
    db.add(user)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise ConflictError("Email already registered") from exc
    return user


async def authenticate(db: AsyncSession, req: LoginRequest) -> User:
    """Verify email + password. Same error for unknown-email and wrong-password
    (no user enumeration)."""
    email = _normalize_email(req.email)
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()
    if user is None or not verify_password(req.password, user.password_hash):
        raise UnauthorizedError("Invalid email or password")
    return user


async def refresh(db: AsyncSession, req: RefreshRequest) -> TokenResponse:
    """Validate a refresh token, re-issue a fresh access+refresh pair."""
    user_id = decode_token(req.refresh_token, expected_type="refresh")
    user = await get_user_by_id(db, user_id)
    if user is None:
        # Token was valid but the user has been deleted.
        raise UnauthorizedError("User no longer exists")
    return _issue_tokens(user.id)


async def get_user_by_id(db: AsyncSession, user_id: uuid.UUID) -> User | None:
    result = await db.execute(select(User).where(User.id == user_id))
    return result.scalar_one_or_none()


__all__ = [
    "authenticate",
    "get_user_by_id",
    "refresh",
    "signup",
]
