"""Room model — a room within a home."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, utc_now
from app.db.types import GUID

if TYPE_CHECKING:
    from app.models.home import Home
    from app.models.storage import StorageUnit


class Room(Base):
    """A room inside a home. Room type drives UI defaults and agent hints."""

    __tablename__ = "rooms"
    __table_args__ = (
        Index("ix_rooms_home_id_sort", "home_id", "sort_order"),
        CheckConstraint(
            "room_type IN ('bedroom','kitchen','bathroom','study','living','storage','other')",
            name="ck_rooms_room_type",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        primary_key=True,
        default=uuid.uuid4,
    )
    home_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        ForeignKey("homes.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    room_type: Mapped[str] = mapped_column(Text, nullable=False)
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        server_default=func.current_timestamp(),
    )

    # Relationships
    home: Mapped[Home] = relationship(back_populates="rooms", lazy="joined")
    storage_units: Mapped[list[StorageUnit]] = relationship(
        back_populates="room",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Room id={self.id} name={self.name!r} type={self.room_type}>"
