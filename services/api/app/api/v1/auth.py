"""Authentication routes — signup / login / refresh / me.

Per API.md §2. JWTs are issued with separate ``type`` claims so a refresh
token cannot be used as an access token and vice versa. Refresh is rotated
on each call.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.user import User
from app.schemas.auth import (
    LoginRequest,
    RefreshRequest,
    SignupRequest,
    SignupResponse,
    TokenResponse,
    UserResponse,
)
from app.services import auth_service

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/signup",
    response_model=SignupResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new user account",
)
async def signup(
    body: SignupRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> SignupResponse:
    user = await auth_service.signup(db, body)
    return SignupResponse(
        user=UserResponse(
            id=user.id, email=user.email, display_name=user.display_name
        )
    )


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Exchange email + password for access + refresh tokens",
)
async def login(
    body: LoginRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TokenResponse:
    user = await auth_service.authenticate(db, body)
    return auth_service._issue_tokens(user.id)


@router.post(
    "/refresh",
    response_model=TokenResponse,
    summary="Rotate a refresh token for a new access + refresh pair",
)
async def refresh(
    body: RefreshRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TokenResponse:
    return await auth_service.refresh(db, body)


@router.get(
    "/me",
    response_model=UserResponse,
    summary="Return the currently authenticated user",
)
async def me(
    current_user: Annotated[User, Depends(get_current_user)],
) -> UserResponse:
    return UserResponse(
        id=current_user.id,
        email=current_user.email,
        display_name=current_user.display_name,
    )


__all__ = ["router"]
