"""Home + HomeMembership models."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Text, func, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, utc_now
from app.db.enums import HomeRole
from app.db.types import GUID

if TYPE_CHECKING:
    from app.models.room import Room
    from app.models.user import User


class Home(Base):
    """A user's home — the top-level organizational unit.

    All rooms / units / sections / slots / items / rules / traces belong to
    exactly one home.
    """

    __tablename__ = "homes"
    __table_args__ = (Index("ix_homes_owner_id", "owner_id"),)

    id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        primary_key=True,
        default=uuid.uuid4,
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    timezone: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default=text("'Asia/Shanghai'")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        server_default=func.current_timestamp(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
        server_default=func.current_timestamp(),
    )

    # Relationships
    memberships: Mapped[list[HomeMembership]] = relationship(
        back_populates="home",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    rooms: Mapped[list[Room]] = relationship(
        back_populates="home",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Home id={self.id} name={self.name!r}>"


class HomeMembership(Base):
    """User ↔ Home many-to-many with a role."""

    __tablename__ = "home_memberships"
    __table_args__ = (
        Index("ix_home_memberships_home_id", "home_id"),
        Index("uq_home_memberships_user_home", "user_id", "home_id", unique=True),
        CheckConstraint(
            "role IN ('owner','member')",
            name="ck_home_memberships_role",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        primary_key=True,
        default=uuid.uuid4,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    home_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        ForeignKey("homes.id", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(Text, nullable=False)
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        server_default=func.current_timestamp(),
    )

    # Relationships
    user: Mapped[User] = relationship(back_populates="memberships", lazy="joined")
    home: Mapped[Home] = relationship(back_populates="memberships", lazy="joined")

    @property
    def role_enum(self) -> HomeRole:
        return HomeRole(self.role)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<HomeMembership user={self.user_id} home={self.home_id} role={self.role}>"


# Sentinel to silence unused import warning for PG_UUID (kept for raw SQL escape hatches).
_ = PG_UUID
