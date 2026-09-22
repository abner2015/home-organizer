"""Step 4 — clean the model's proposal up before the user ever sees it.

Pure: no DB, no LLM, no I/O, so the whole policy is testable in-process.

Why a rewrite pass exists at all: the LLM's output schema is intentionally
loose (see ``app.ai.provider`` — its ``max_length`` values are runaway guards).
A proposal that overshoots by a few nodes should be *trimmed and explained*,
not rejected wholesale; rejecting costs a retry and then a hard failure over
something a human would simply delete.

The non-negotiable part is that **every change is reported**. The user is shown
a proposal and asked to confirm it. If the server silently dropped a slot or
cleared a category, they would be confirming something the model never said.

What this does *not* do is judge taste:

- A proposed room or furniture name that already exists in the home is legal
  (a second 「储物柜」 is a real thing) — it earns a note, not a rejection.
- The only edits are the mechanical ones: trim over-long lists, drop a
  duplicate ``code`` within one section, and drop categories the caller's own
  data never mentions.

Enum values are not checked here. ``room_type`` / ``unit_type`` /
``section_type`` are ``StrEnum`` fields on the schema, so a bad value is
already an ``AIOutputParseError`` and therefore a retry — which is the right
answer, because unlike the above it is not something a rule can repair.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from app.ai.provider import (
    ProposedRoom,
    ProposedSection,
    ProposedSlot,
    ProposedUnit,
    StructureProposalOutput,
)

# The sizes the product actually wants. Deliberately below the schema's
# runaway guards so an overshoot is trimmed rather than refused.
MAX_ROOMS = 6
MAX_UNITS_PER_ROOM = 12
MAX_SECTIONS_PER_UNIT = 12
MAX_SLOTS_PER_SECTION = 20
MAX_CATEGORIES_PER_SLOT = 20

# ``kind`` values, as a closed set the UI can switch on. Kept as plain strings
# rather than an enum so the persisted/round-tripped JSON stays readable.
KIND_TRUNCATED = "truncated"
KIND_DUPLICATE_CODE = "duplicate_code"
KIND_CATEGORY_CLEARED = "category_cleared"
KIND_POSSIBLE_DUPLICATE = "possible_duplicate"
KIND_EMPTY_VOCABULARY = "empty_vocabulary"


class ProposalWarning(BaseModel):
    """One mechanical edit the validator made to the model's proposal."""

    model_config = ConfigDict(extra="forbid")

    kind: str
    #: Where in the proposal, e.g. ``rooms[0].units[1].sections[0].slots[3]``.
    path: str
    #: User-facing Chinese — it is rendered in the confirm step.
    message: str


def validate_proposal(
    proposal: StructureProposalOutput,
    *,
    vocabulary: list[str],
    room_names: list[str],
    unit_names: list[str],
) -> tuple[StructureProposalOutput, list[ProposalWarning]]:
    """Trim ``proposal`` to what the write API can hold, reporting every edit.

    Returns the (possibly rewritten) proposal and the warnings, in tree order.
    """
    known = {c.strip().lower() for c in vocabulary if c.strip()}
    existing_rooms = {name.strip() for name in room_names if name.strip()}
    existing_units = {name.strip() for name in unit_names if name.strip()}

    warnings: list[ProposalWarning] = []

    if not known:
        # A home with no items and no slots has no category vocabulary. Every
        # proposed category is therefore ungrounded and gets cleared below.
        # That is the *correct* first-run result — an empty category list means
        # "no restriction" to the candidate generator — but it reads like a bug
        # without this note, and the test suite pins it.
        warnings.append(
            ProposalWarning(
                kind=KIND_EMPTY_VOCABULARY,
                path="$",
                message=(
                    "这个家目前还没有任何物品类别，因此提议里填写的「适用类别」已全部清空。"
                    "清空表示该位置不限制物品类别，之后添加物品时会自动补充。"
                ),
            )
        )

    rooms_in = proposal.rooms
    if len(rooms_in) > MAX_ROOMS:
        warnings.append(
            ProposalWarning(
                kind=KIND_TRUNCATED,
                path="$",
                message=f"提议的房间过多（{len(rooms_in)} 个），只保留前 {MAX_ROOMS} 个。",
            )
        )
        rooms_in = rooms_in[:MAX_ROOMS]

    rooms_out: list[ProposedRoom] = []
    for r_index, room in enumerate(rooms_in):
        r_path = f"rooms[{r_index}]"
        if room.name.strip() in existing_rooms:
            warnings.append(
                ProposalWarning(
                    kind=KIND_POSSIBLE_DUPLICATE,
                    path=r_path,
                    message=f"家里已经有一个叫「{room.name}」的房间，这一条可能是重复的。",
                )
            )

        units_in = room.units
        if len(units_in) > MAX_UNITS_PER_ROOM:
            warnings.append(
                ProposalWarning(
                    kind=KIND_TRUNCATED,
                    path=r_path,
                    message=(
                        f"「{room.name}」下的收纳家具过多（{len(units_in)} 件），"
                        f"只保留前 {MAX_UNITS_PER_ROOM} 件。"
                    ),
                )
            )
            units_in = units_in[:MAX_UNITS_PER_ROOM]

        units_out: list[ProposedUnit] = []
        for u_index, unit in enumerate(units_in):
            u_path = f"{r_path}.units[{u_index}]"
            if unit.name.strip() in existing_units:
                warnings.append(
                    ProposalWarning(
                        kind=KIND_POSSIBLE_DUPLICATE,
                        path=u_path,
                        message=f"家里已经有一件叫「{unit.name}」的收纳家具，这一条可能是重复的。",
                    )
                )

            sections_in = unit.sections
            if len(sections_in) > MAX_SECTIONS_PER_UNIT:
                warnings.append(
                    ProposalWarning(
                        kind=KIND_TRUNCATED,
                        path=u_path,
                        message=(
                            f"「{unit.name}」下的分区过多（{len(sections_in)} 个），"
                            f"只保留前 {MAX_SECTIONS_PER_UNIT} 个。"
                        ),
                    )
                )
                sections_in = sections_in[:MAX_SECTIONS_PER_UNIT]

            sections_out: list[ProposedSection] = []
            for s_index, section in enumerate(sections_in):
                s_path = f"{u_path}.sections[{s_index}]"

                slots_in = section.slots
                if len(slots_in) > MAX_SLOTS_PER_SECTION:
                    warnings.append(
                        ProposalWarning(
                            kind=KIND_TRUNCATED,
                            path=s_path,
                            message=(
                                f"「{section.name}」下的收纳位过多（{len(slots_in)} 个），"
                                f"只保留前 {MAX_SLOTS_PER_SECTION} 个。"
                            ),
                        )
                    )
                    slots_in = slots_in[:MAX_SLOTS_PER_SECTION]

                slots_out: list[ProposedSlot] = []
                seen_codes: set[str] = set()
                for o_index, slot in enumerate(slots_in):
                    o_path = f"{s_path}.slots[{o_index}]"

                    # ``(section_id, code)`` is the one unique index on the
                    # storage tables, so a repeat here would 409 at persist
                    # time. Drop the later one: the first is what the model
                    # meant, and the confirm UI renumbers nothing.
                    if slot.code in seen_codes:
                        warnings.append(
                            ProposalWarning(
                                kind=KIND_DUPLICATE_CODE,
                                path=o_path,
                                message=(
                                    f"「{section.name}」里有两个编码为「{slot.code}」的收纳位，"
                                    "已去掉后一个。"
                                ),
                            )
                        )
                        continue
                    seen_codes.add(slot.code)

                    categories = [
                        c for c in slot.allowed_categories if c.strip().lower() in known
                    ]
                    if len(categories) != len(slot.allowed_categories):
                        dropped = [
                            c
                            for c in slot.allowed_categories
                            if c.strip().lower() not in known
                        ]
                        warnings.append(
                            ProposalWarning(
                                kind=KIND_CATEGORY_CLEARED,
                                path=o_path,
                                message=(
                                    f"「{slot.label or slot.code}」的适用类别里有家里不存在的"
                                    f"类别（{'、'.join(dropped)}），已去掉。"
                                ),
                            )
                        )
                    if len(categories) > MAX_CATEGORIES_PER_SLOT:
                        warnings.append(
                            ProposalWarning(
                                kind=KIND_TRUNCATED,
                                path=o_path,
                                message=(
                                    f"「{slot.label or slot.code}」的适用类别过多"
                                    f"（{len(categories)} 个），只保留前 "
                                    f"{MAX_CATEGORIES_PER_SLOT} 个。"
                                ),
                            )
                        )
                        categories = categories[:MAX_CATEGORIES_PER_SLOT]

                    slots_out.append(
                        slot.model_copy(update={"allowed_categories": categories})
                    )

                sections_out.append(section.model_copy(update={"slots": slots_out}))

            units_out.append(unit.model_copy(update={"sections": sections_out}))

        rooms_out.append(room.model_copy(update={"units": units_out}))

    return proposal.model_copy(update={"rooms": rooms_out}), warnings


__all__ = [
    "KIND_CATEGORY_CLEARED",
    "KIND_DUPLICATE_CODE",
    "KIND_EMPTY_VOCABULARY",
    "KIND_POSSIBLE_DUPLICATE",
    "KIND_TRUNCATED",
    "MAX_ROOMS",
    "MAX_SECTIONS_PER_UNIT",
    "MAX_SLOTS_PER_SECTION",
    "MAX_UNITS_PER_ROOM",
    "ProposalWarning",
    "validate_proposal",
]
