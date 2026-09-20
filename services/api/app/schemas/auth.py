"""Pydantic schemas for /api/v1/auth/* endpoints."""
from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class _Strict(BaseModel):
    """Base: forbid extras + strict types (per AGENTS.md §3.9)."""

    model_config = ConfigDict(extra="forbid", strict=True)


class SignupRequest(_Strict):
    """POST /auth/signup body."""

    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    display_name: str = Field(min_length=1, max_length=100)

    @field_validator("email", mode="before")
    @classmethod
    def _normalize_email(cls, v: object) -> object:
        if isinstance(v, str):
            return v.strip().lower()
        return v

    @field_validator("display_name", mode="before")
    @classmethod
    def _strip_display_name(cls, v: object) -> object:
        if isinstance(v, str):
            return v.strip()
        return v


class LoginRequest(_Strict):
    """POST /auth/login body."""

    email: EmailStr
    password: str = Field(min_length=1, max_length=128)

    @field_validator("email", mode="before")
    @classmethod
    def _normalize_email(cls, v: object) -> object:
        if isinstance(v, str):
            return v.strip().lower()
        return v


class RefreshRequest(_Strict):
    """POST /auth/refresh body."""

    refresh_token: str = Field(min_length=1, max_length=4096)


class TokenResponse(_Strict):
    """Tokens returned by login / refresh."""

    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int = Field(ge=1)


class UserResponse(_Strict):
    """Public user representation — never includes password_hash."""

    id: UUID
    email: EmailStr
    display_name: str


class SignupResponse(_Strict):
    """POST /auth/signup response: 201."""

    user: UserResponse


__all__ = [
    "LoginRequest",
    "RefreshRequest",
    "SignupRequest",
    "SignupResponse",
    "TokenResponse",
    "UserResponse",
]
