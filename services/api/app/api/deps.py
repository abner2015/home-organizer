"""FastAPI dependencies.

Two auth paths are exposed:

- ``get_actor``: stub auth via ``X-User-Id`` / ``X-Home-Id`` headers, used by
  the asset / items endpoints that predate Phase 2.
- ``get_current_user``: real JWT auth via ``Authorization: Bearer ...``,
  backed by ``app.services.security`` + ``app.services.auth_service``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import UnauthorizedError
from app.db.session import get_db
from app.models.user import User
from app.services import auth_service
from app.services.security import decode_token


@dataclass(slots=True)
class Actor:
    """Lightweight request principal: (user, home) pair."""

    user_id: UUID
    home_id: UUID


async def get_actor(
    x_user_id: str = Header(..., alias="X-User-Id", description="User UUID"),
    x_home_id: str = Header(..., alias="X-Home-Id", description="Home UUID"),
) -> Actor:
    """Parse the stub auth headers into an `Actor`.

    TODO: replace with the real ``get_current_user`` + a home-membership check
    once the asset / items endpoints migrate off the stub.
    """
    try:
        user_id = UUID(x_user_id)
    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="X-User-Id must be a UUID",
        ) from exc
    try:
        home_id = UUID(x_home_id)
    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="X-Home-Id must be a UUID",
        ) from exc
    return Actor(user_id=user_id, home_id=home_id)


# --------------------------------------------------------------------- JWT auth


_bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    """Resolve the caller from a ``Bearer`` JWT. Raises ``UnauthorizedError``
    on missing / invalid / expired tokens."""
    if creds is None or not creds.credentials:
        raise UnauthorizedError("Authentication required")
    try:
        user_id = decode_token(creds.credentials, expected_type="access")
    except UnauthorizedError:
        # Re-raise so the AppError handler renders the standard envelope.
        raise
    user = await auth_service.get_user_by_id(db, user_id)
    if user is None:
        raise UnauthorizedError("User no longer exists")
    return user


__all__ = ["Actor", "get_actor", "get_current_user"]
