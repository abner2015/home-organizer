"""Request bodies for the storage-structure write API.

Companion to :mod:`app.schemas.home`, which holds the response views. The read
views describe rows the server built; these describe rows a client asks for,
so the validation is stricter where it is cheap.

Two deliberate choices:

* Enum-typed fields. ``room_type`` is ``RoomType``, not ``str``, which turns
  ``"room_type": "厨房"`` into a 422 at the edge instead of an ``IntegrityError``
  from the database CHECK constraint (a 500). ``app.db.enums`` is the single
  source of truth for those values on both sides.
* ``extra="forbid"`` but never ``strict=True``. Strict mode rejects UUIDs and
  enum values arriving as JSON strings, which is all JSON ever delivers.
"""
from __future__ import annotations

import uuid
from typing import Annotated, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.agents.structure.validate import ProposalWarning
from app.ai.provider import StructureProposalOutput
from app.core.exceptions import ValidationFailedError
from app.db.enums import RoomType, StorageSectionType, StorageUnitType

Name = Annotated[str, StringConstraints(min_length=1, max_length=100)]
Code = Annotated[str, StringConstraints(min_length=1, max_length=50)]
Category = Annotated[str, StringConstraints(min_length=1, max_length=64)]


class _SparseUpdate(BaseModel):
    """Base for PATCH bodies: every field optional, omitted means untouched."""

    model_config = ConfigDict(extra="forbid")

    # Only these may be explicitly nulled; a null ``name`` is a client bug.
    nullable: ClassVar[frozenset[str]] = frozenset()

    def changes(self) -> dict[str, object]:
        """The fields the client actually sent.

        Explicit ``null`` is meaningful only on nullable columns. Sending it
        for a NOT NULL one would otherwise reach the database and come back as
        a 500, so it is refused here where the message can name the field.
        """
        sent = self.model_dump(exclude_unset=True)
        for field, value in sent.items():
            if value is None and field not in self.nullable:
                raise ValidationFailedError(
                    f"字段 {field} 不能为 null",
                    details={"field": field},
                )
        return sent


class HomeUpdateRequest(BaseModel):
    """Rename a home. Owner only."""

    model_config = ConfigDict(extra="forbid")

    name: Name


class RoomCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Name
    room_type: RoomType
    sort_order: int | None = Field(default=None, ge=0)


class RoomUpdateRequest(_SparseUpdate):
    nullable: ClassVar[frozenset[str]] = frozenset()

    name: Name | None = None
    room_type: RoomType | None = None
    sort_order: int | None = Field(default=None, ge=0)


class UnitCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Name
    unit_type: StorageUnitType
    description: str | None = None
    sort_order: int | None = Field(default=None, ge=0)


class UnitUpdateRequest(_SparseUpdate):
    nullable: ClassVar[frozenset[str]] = frozenset({"description"})

    name: Name | None = None
    unit_type: StorageUnitType | None = None
    description: str | None = None
    sort_order: int | None = Field(default=None, ge=0)


class SectionCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Name
    section_type: StorageSectionType
    sort_order: int | None = Field(default=None, ge=0)


class SectionUpdateRequest(_SparseUpdate):
    nullable: ClassVar[frozenset[str]] = frozenset()

    name: Name | None = None
    section_type: StorageSectionType | None = None
    sort_order: int | None = Field(default=None, ge=0)


class SlotCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: Code
    label: str | None = Field(default=None, max_length=200)
    # Free text on purpose. ``candidate_gen`` / ``checks`` also understand the
    # Chinese size words and a bare number ("6" means six things), so narrowing
    # this to a three-value literal would make the API unable to express
    # something the engine already supports. The *model's* output is narrowed
    # instead, in ``ProposedSlot``.
    capacity_hint: str | None = Field(default=None, max_length=100)
    allowed_categories: list[Category] = Field(default_factory=list, max_length=20)
    sort_order: int | None = Field(default=None, ge=0)


class SlotUpdateRequest(_SparseUpdate):
    nullable: ClassVar[frozenset[str]] = frozenset({"label", "capacity_hint"})

    code: Code | None = None
    label: str | None = Field(default=None, max_length=200)
    capacity_hint: str | None = Field(default=None, max_length=100)
    allowed_categories: list[Category] | None = Field(default=None, max_length=20)
    sort_order: int | None = Field(default=None, ge=0)


class StructureProposalRequest(BaseModel):
    """Ask the AI (or the template) for a structure.

    Both fields absent is not an error — it selects the server-side template,
    which is the "I'd rather not describe anything" path. Sending both is a
    photo with a caption.
    """

    model_config = ConfigDict(extra="forbid")

    asset_id: uuid.UUID | None = None
    description: str | None = Field(default=None, max_length=2000)


class StructureProposalResponse(BaseModel):
    """A proposal, plus everything the confirm screen needs to be honest.

    ``warnings`` is not decoration. Step 4 rewrites the model's answer
    (trimming, dropping duplicate codes, clearing ungrounded categories), and
    a user who is not told about that would be confirming something the model
    never proposed.
    """

    model_config = ConfigDict(extra="forbid")

    proposal: StructureProposalOutput
    warnings: list[ProposalWarning] = Field(default_factory=list)
    source: Literal["photo", "text", "template"]
    trace_id: uuid.UUID | None = None


__all__ = [
    "HomeUpdateRequest",
    "RoomCreateRequest",
    "RoomUpdateRequest",
    "SectionCreateRequest",
    "SectionUpdateRequest",
    "SlotCreateRequest",
    "SlotUpdateRequest",
    "StructureProposalRequest",
    "StructureProposalResponse",
    "UnitCreateRequest",
    "UnitUpdateRequest",
]
