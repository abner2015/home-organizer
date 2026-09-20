"""Context tools — user preferences + home rules for the agent.

These return plain JSON-safe dicts; no cross-home access is possible because
both tables are FK'd to ``homes.id``.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.preference import UserPreference
from app.models.rule import HomeRule


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt is not None else None


def _preference_dict(p: UserPreference) -> dict[str, Any]:
    return {
        "id": str(p.id),
        "user_id": str(p.user_id),
        "home_id": str(p.home_id),
        "key": p.key,
        "value": p.value,
        "created_at": _iso(p.created_at),
    }


def _rule_dict(r: HomeRule) -> dict[str, Any]:
    return {
        "id": str(r.id),
        "home_id": str(r.home_id),
        "name": r.name,
        "description": r.description,
        "rule_type": r.rule_type,
        "scope": r.scope,
        "enabled": r.enabled,
        "created_at": _iso(r.created_at),
    }


async def get_user_preferences(
    *,
    db: AsyncSession,
    home_id: uuid.UUID,
    user_id: uuid.UUID | None = None,
    key: str | None = None,
    **_: Any,
) -> list[dict[str, Any]]:
    """User preferences for a home (optionally scoped to one user / one key).

    An empty value list is a legitimate result — means the user hasn't set any
    preferences yet (which the agent must treat as "no constraints", not as an
    error).
    """
    stmt = select(UserPreference).where(UserPreference.home_id == home_id)
    if user_id is not None:
        stmt = stmt.where(UserPreference.user_id == user_id)
    if key is not None:
        stmt = stmt.where(UserPreference.key == key)
    stmt = stmt.order_by(UserPreference.created_at.desc())
    result = await db.execute(stmt)
    return [_preference_dict(p) for p in result.scalars().all()]


async def get_home_rules(
    *,
    db: AsyncSession,
    home_id: uuid.UUID,
    enabled_only: bool = True,
    **_: Any,
) -> list[dict[str, Any]]:
    """All home rules (hard + soft); default returns enabled-only.

    Returns dicts with ``rule_type`` already set so the agent can pick out
    the hard ones without re-querying.
    """
    stmt = select(HomeRule).where(HomeRule.home_id == home_id)
    if enabled_only:
        stmt = stmt.where(HomeRule.enabled.is_(True))
    stmt = stmt.order_by(HomeRule.created_at.asc())
    result = await db.execute(stmt)
    return [_rule_dict(r) for r in result.scalars().all()]


__all__ = ["get_home_rules", "get_user_preferences"]
