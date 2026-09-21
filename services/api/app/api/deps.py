"""FastAPI dependencies.

Auth is a single path since the JWT migration:

- ``get_current_user`` — the caller's ``User``, from ``Authorization: Bearer``.
- ``get_actor`` — ``(user, home)`` for every route that acts *inside* a home.
  The token answers "who are you"; the ``X-Home-Id`` header answers "which of
  your homes are you acting in". That header is a *selector*, not a credential:
  ``ensure_member`` re-verifies the pairing against ``HomeMembership`` on every
  request, so naming a home you do not belong to is indistinguishable from
  naming one that does not exist.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError, UnauthorizedError
from app.db.session import get_db
from app.models import HomeMembership
from app.models.user import User
from app.services import auth_service
from app.services.security import decode_token


@dataclass(slots=True)
class Actor:
    """Lightweight request principal: (user, home) pair."""

    user_id: UUID
    home_id: UUID


# ``auto_error=False`` so a missing header surfaces as our own
# ``UnauthorizedError`` (rendered by the AppError handler) rather than
# FastAPI's bare 403.
_bearer_scheme = HTTPBearer(auto_error=False)


async def ensure_member(db: AsyncSession, *, home_id: UUID, user_id: UUID) -> None:
    """Raise ``NotFoundError`` unless ``user_id`` is a member of ``home_id``.

    Deliberately 404 rather than 403: telling a caller "this home exists but
    isn't yours" leaks the existence of another household's data.
    """
    result = await db.execute(
        select(HomeMembership.id).where(
            HomeMembership.home_id == home_id,
            HomeMembership.user_id == user_id,
        )
    )
    if result.scalar_one_or_none() is None:
        raise NotFoundError("Home not found")


async def get_actor(
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)],
    db: Annotated[AsyncSession, Depends(get_db)],
    x_home_id: Annotated[str, Header(alias="X-Home-Id", description="Home UUID")],
) -> Actor:
    """Resolve the caller from a Bearer JWT plus the home they claim to act in.

    Returns the same ``Actor(user_id, home_id)`` shape the header-only stub
    returned, so the ~40 ``Depends(get_actor)`` call sites needed no change
    when identity moved from a trusted ``X-User-Id`` header to a signed token.
    """
    if creds is None or not creds.credentials:
        raise UnauthorizedError("Authentication required")
    user_id = decode_token(creds.credentials, expected_type="access")
    try:
        home_id = UUID(x_home_id)
    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="X-Home-Id must be a UUID",
        ) from exc
    await ensure_member(db, home_id=home_id, user_id=user_id)
    return Actor(user_id=user_id, home_id=home_id)


# --------------------------------------------------------------------- JWT auth


async def get_current_user(
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    """Resolve the caller from a ``Bearer`` JWT. Raises ``UnauthorizedError``
    on missing / invalid / expired tokens."""
    if creds is None or not creds.credentials:
        raise UnauthorizedError("Authentication required")
    user_id = decode_token(creds.credentials, expected_type="access")
    user = await auth_service.get_user_by_id(db, user_id)
    if user is None:
        raise UnauthorizedError("User no longer exists")
    return user


__all__ = ["Actor", "ensure_member", "get_actor", "get_current_user"]
