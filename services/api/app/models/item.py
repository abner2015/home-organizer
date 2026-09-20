"""Item + ItemImage models.

An Item is a physical object the user wants to store. It has zero or more
ItemImages (primary + gallery) and zero or more historical ItemPlacements.

items.primary_image_id references item_images.id and is deferrable (set after
both rows exist). The FK is declared with use_alter=True so SQLAlchemy creates
the constraint via ALTER TABLE after both tables exist; in the Alembic
migration we additionally mark it DEFERRABLE INITIALLY DEFERRED so the
application can INSERT the item and its image in any order within a tx.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
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

from app.db.base import Base, TimestampMixin, utc_now
from app.db.types import GUID


class Item(Base, TimestampMixin):
    """A physical object the user wants to find a storage spot for."""

    __tablename__ = "items"
    __table_args__ = (
        Index("ix_items_home_id", "home_id"),
        Index("ix_items_home_category", "home_id", "category"),
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
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    subcategory: Mapped[str | None] = mapped_column(String(100), nullable=True)
    brand: Mapped[str | None] = mapped_column(String(100), nullable=True)
    estimated_size: Mapped[str | None] = mapped_column(String(100), nullable=True)
    is_sensitive: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    needs_lock: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    primary_image_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(),
        ForeignKey("item_images.id", ondelete="SET NULL", use_alter=True),
        nullable=True,
        comment="Deferrable FK to item_images.id (see Alembic migration)",
    )
    created_by: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )

    # Relationships
    images: Mapped[list[ItemImage]] = relationship(
        back_populates="item",
        cascade="all, delete-orphan",
        foreign_keys="ItemImage.item_id",
        lazy="selectin",
    )
    placements: Mapped[list[ItemPlacement]] = relationship(  # type: ignore[name-defined]  # noqa: F821
        back_populates="item",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    primary_image: Mapped[ItemImage | None] = relationship(
        foreign_keys=[primary_image_id],
        post_update=True,
        lazy="joined",
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Item id={self.id} name={self.name!r}>"


class ItemImage(Base):
    """A photo of an item, stored in MinIO.

    Each item has at most one image with is_primary=true (partial unique index
    enforced in the migration).
    """

    __tablename__ = "item_images"
    __table_args__ = (
        Index("ix_item_images_item_id", "item_id"),
        # Partial unique: enforced via raw SQL in Alembic (see migration).
        CheckConstraint(
            "(width IS NULL OR width > 0) AND (height IS NULL OR height > 0)",
            name="ck_item_images_dimensions_positive",
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
    object_key: Mapped[str] = mapped_column(
        Text, nullable=False, comment="MinIO object key"
    )
    url: Mapped[str] = mapped_column(Text, nullable=False, comment="Public/presigned URL")
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_primary: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        server_default=func.current_timestamp(),
    )

    # Relationships
    item: Mapped[Item] = relationship(
        back_populates="images",
        foreign_keys=[item_id],
        lazy="joined",
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<ItemImage id={self.id} item={self.item_id} primary={self.is_primary}>"
