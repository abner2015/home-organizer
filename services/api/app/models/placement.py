"""ItemPlacement — historical and current placement records.

An item may have many placements over its lifetime. At most one row may have
removed_at IS NULL at any time (partial unique index in the migration).
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, utc_now
from app.db.types import GUID


class ItemPlacement(Base):
    """A historical record of an item occupying a storage slot.

    Removed rows (removed_at IS NOT NULL) are immutable historical records.
    Active rows (removed_at IS NULL) form the current placement graph.
    """

    __tablename__ = "item_placements"
    __table_args__ = (
        Index("ix_item_placements_item_id", "item_id"),
        Index("ix_item_placements_slot_id", "slot_id"),
        # Partial unique indexes (one active placement per item, one active
        # placement per slot) are created via raw SQL in the Alembic migration.
        CheckConstraint(
            "source IN ('ai_recommendation','user_manual')",
            name="ck_item_placements_source",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        primary_key=True,
        default=uuid.uuid4,
    )
    item_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        ForeignKey("items.id", ondelete="CASCADE"),
        nullable=False,
    )
    slot_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        ForeignKey("storage_slots.id", ondelete="RESTRICT"),
        nullable=False,
        comment="RESTRICT: cannot delete a slot that has placements",
    )
    placed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        server_default=func.current_timestamp(),
    )
    removed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="NULL = currently active"
    )
    placed_by: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    source: Mapped[str] = mapped_column(Text, nullable=False)
    recommendation_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(),
        ForeignKey("recommendations.id", ondelete="SET NULL"),
        nullable=True,
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Relationships
    item: Mapped[Item] = relationship(back_populates="placements", lazy="joined")  # type: ignore[name-defined]  # noqa: F821
    slot: Mapped[StorageSlot] = relationship(lazy="joined")  # type: ignore[name-defined]  # noqa: F821

    @property
    def is_active(self) -> bool:
        return self.removed_at is None

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<ItemPlacement item={self.item_id} slot={self.slot_id} "
            f"active={self.is_active} source={self.source}>"
        )
