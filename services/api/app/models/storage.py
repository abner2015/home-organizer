"""Storage hierarchy: StorageUnit → StorageSection → StorageSlot.

Models the full "柜子 → 层/抽屉/收纳盒 → 格" chain so the agent can recommend
specific leaf slots. The complex cabinet in seed data exercises the full tree.
"""
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
from app.db.types import GUID, TextArray

if TYPE_CHECKING:
    from app.models.room import Room


class StorageUnit(Base):
    """A piece of furniture / container — cabinet, shelf, drawer cabinet, box, etc."""

    __tablename__ = "storage_units"
    __table_args__ = (
        Index("ix_storage_units_room_id_sort", "room_id", "sort_order"),
        CheckConstraint(
            "unit_type IN ('cabinet','shelf','drawer_cabinet','box','other')",
            name="ck_storage_units_unit_type",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        primary_key=True,
        default=uuid.uuid4,
    )
    room_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        ForeignKey("rooms.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    unit_type: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
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
    room: Mapped[Room] = relationship(back_populates="storage_units", lazy="joined")
    sections: Mapped[list[StorageSection]] = relationship(
        back_populates="unit",
        cascade="all, delete-orphan",
        order_by="StorageSection.sort_order",
        lazy="selectin",
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<StorageUnit id={self.id} name={self.name!r} type={self.unit_type}>"


class StorageSection(Base):
    """A layer / drawer / box / compartment inside a StorageUnit."""

    __tablename__ = "storage_sections"
    __table_args__ = (
        Index("ix_storage_sections_unit_id_sort", "unit_id", "sort_order"),
        CheckConstraint(
            "section_type IN ('layer','drawer','box','compartment','other')",
            name="ck_storage_sections_section_type",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        primary_key=True,
        default=uuid.uuid4,
    )
    unit_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        ForeignKey("storage_units.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    section_type: Mapped[str] = mapped_column(Text, nullable=False)
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
    unit: Mapped[StorageUnit] = relationship(back_populates="sections", lazy="joined")
    slots: Mapped[list[StorageSlot]] = relationship(
        back_populates="section",
        cascade="all, delete-orphan",
        order_by="StorageSlot.sort_order",
        lazy="selectin",
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<StorageSection id={self.id} name={self.name!r} type={self.section_type}>"


class StorageSlot(Base):
    """A leaf storage position — the unit of recommendation.

    Every item must ultimately be placed in a StorageSlot. The agent pipeline
    (docs/AGENT.md) must always reference real slot ids; Verifier enforces.
    """

    __tablename__ = "storage_slots"
    __table_args__ = (
        Index("uq_storage_slots_section_code", "section_id", "code", unique=True),
        Index("ix_storage_slots_section_id_sort", "section_id", "sort_order"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        primary_key=True,
        default=uuid.uuid4,
    )
    section_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        ForeignKey("storage_sections.id", ondelete="CASCADE"),
        nullable=False,
    )
    code: Mapped[str] = mapped_column(
        String(50), nullable=False, comment="Unique code within the section, e.g. 'A1'"
    )
    label: Mapped[str | None] = mapped_column(String(200), nullable=True)
    capacity_hint: Mapped[str | None] = mapped_column(Text, nullable=True)
    allowed_categories: Mapped[list[str]] = mapped_column(
        TextArray,
        nullable=False,
        server_default=text("'{}'"),
        default=list,
        comment="Whitelist of item categories that may occupy this slot",
    )
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
    section: Mapped[StorageSection] = relationship(back_populates="slots", lazy="joined")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<StorageSlot id={self.id} code={self.code!r} section={self.section_id}>"

    @property
    def full_path(self) -> str:
        """Human-readable path: 客厅/左玻璃柜/第一层/A1.

        Useful for UI display and verifier human-readable errors.
        """
        unit = self.section.unit
        room = unit.room
        return f"{room.name}/{unit.name}/{self.section.name}/{self.code}"
