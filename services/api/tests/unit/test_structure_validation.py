"""Pure tests for the Step 4 proposal validator.

The API test exercises this through one wiring (an HTTP call); what is only
visible here is the *policy* — which edits are made, which are merely reported,
and the fact that an oversized or ungrounded proposal is rewritten rather than
refused.
"""
from __future__ import annotations

from app.agents.structure.validate import (
    KIND_CATEGORY_CLEARED,
    KIND_DUPLICATE_CODE,
    KIND_EMPTY_VOCABULARY,
    KIND_POSSIBLE_DUPLICATE,
    KIND_TRUNCATED,
    MAX_ROOMS,
    MAX_SLOTS_PER_SECTION,
    validate_proposal,
)
from app.ai.provider import (
    ProposedRoom,
    ProposedSection,
    ProposedSlot,
    ProposedUnit,
    StructureProposalOutput,
)


def _slot(code: str = "A1", *, label: str = "左侧", categories: list[str] | None = None):
    return ProposedSlot(
        code=code, label=label, allowed_categories=categories or [], capacity_hint="medium"
    )


def _proposal(slots: list[ProposedSlot] | None = None, *, room_name: str = "厨房"):
    return StructureProposalOutput(
        rooms=[
            ProposedRoom(
                name=room_name,
                room_type="kitchen",
                units=[
                    ProposedUnit(
                        name="吊柜",
                        unit_type="cabinet",
                        sections=[
                            ProposedSection(
                                name="上层", section_type="layer", slots=slots or [_slot()]
                            )
                        ],
                    )
                ],
            )
        ]
    )


def _kinds(warnings) -> list[str]:
    return [warning.kind for warning in warnings]


def test_a_clean_proposal_passes_through_untouched() -> None:
    proposal, warnings = validate_proposal(
        _proposal([_slot(categories=["utensil"])]),
        vocabulary=["utensil"],
        room_names=[],
        unit_names=[],
    )
    assert warnings == []
    assert proposal.rooms[0].units[0].sections[0].slots[0].allowed_categories == ["utensil"]


def test_trimming_keeps_the_first_n_and_says_so() -> None:
    slots = [_slot(f"S{i}") for i in range(MAX_SLOTS_PER_SECTION + 3)]
    proposal, warnings = validate_proposal(
        _proposal(slots), vocabulary=[], room_names=[], unit_names=[]
    )

    kept = proposal.rooms[0].units[0].sections[0].slots
    assert len(kept) == MAX_SLOTS_PER_SECTION
    assert kept[0].code == "S0"
    # The empty vocabulary also clears categories, so look for the one
    # truncation warning rather than asserting the list is exactly one long.
    assert KIND_TRUNCATED in _kinds(warnings)


def test_too_many_rooms_is_trimmed_at_the_top_level() -> None:
    proposal = StructureProposalOutput(
        rooms=[
            ProposedRoom(name=f"房间{i}", room_type="other", units=[])
            for i in range(MAX_ROOMS + 2)
        ]
    )
    trimmed, warnings = validate_proposal(
        proposal, vocabulary=[], room_names=[], unit_names=[]
    )
    assert len(trimmed.rooms) == MAX_ROOMS
    assert [w.kind for w in warnings].count(KIND_TRUNCATED) == 1


def test_a_repeated_code_in_one_section_drops_the_later_slot() -> None:
    """``(section_id, code)`` is unique in the DB; the write API would 409."""
    proposal, warnings = validate_proposal(
        _proposal([_slot("A1", label="第一个"), _slot("A1", label="第二个")]),
        vocabulary=[],
        room_names=[],
        unit_names=[],
    )
    kept = proposal.rooms[0].units[0].sections[0].slots
    assert [slot.label for slot in kept] == ["第一个"]
    assert KIND_DUPLICATE_CODE in _kinds(warnings)


def test_the_same_code_in_two_sections_is_left_alone() -> None:
    """The unique index is per section — this must not be "fixed"."""
    proposal = StructureProposalOutput(
        rooms=[
            ProposedRoom(
                name="厨房",
                room_type="kitchen",
                units=[
                    ProposedUnit(
                        name="吊柜",
                        unit_type="cabinet",
                        sections=[
                            ProposedSection(name="上层", section_type="layer", slots=[_slot("A1")]),
                            ProposedSection(name="下层", section_type="layer", slots=[_slot("A1")]),
                        ],
                    )
                ],
            )
        ]
    )
    out, warnings = validate_proposal(
        proposal, vocabulary=[], room_names=[], unit_names=[]
    )
    assert len(out.rooms[0].units[0].sections[0].slots) == 1
    assert len(out.rooms[0].units[0].sections[1].slots) == 1
    assert KIND_DUPLICATE_CODE not in _kinds(warnings)


def test_only_the_ungrounded_categories_are_dropped() -> None:
    """Not the whole slot — a partly-wrong list keeps its valid half."""
    proposal, warnings = validate_proposal(
        _proposal([_slot(categories=["utensil", "spaceship"])]),
        vocabulary=["utensil"],
        room_names=[],
        unit_names=[],
    )
    slot = proposal.rooms[0].units[0].sections[0].slots[0]
    assert slot.allowed_categories == ["utensil"]
    cleared = [w for w in warnings if w.kind == KIND_CATEGORY_CLEARED]
    assert len(cleared) == 1
    assert "spaceship" in cleared[0].message


def test_clearing_every_category_is_legal() -> None:
    """The empty list means "no restriction" downstream — never an error."""
    proposal, _ = validate_proposal(
        _proposal([_slot(categories=["spaceship"])]),
        vocabulary=[],
        room_names=[],
        unit_names=[],
    )
    assert proposal.rooms[0].units[0].sections[0].slots[0].allowed_categories == []


def test_an_empty_vocabulary_warns_once() -> None:
    """A brand-new home has no categories at all; that is the first-run answer.

    Emitting this note is what keeps the cleared categories from reading like a
    bug — and the test is here so that "helpfully" seeding a default vocabulary
    has to be a deliberate act rather than a silent fix.
    """
    _, warnings = validate_proposal(
        _proposal([_slot(categories=["utensil"])]),
        vocabulary=[],
        room_names=[],
        unit_names=[],
    )
    assert _kinds(warnings).count(KIND_EMPTY_VOCABULARY) == 1


def test_a_known_vocabulary_does_not_warn_about_emptiness() -> None:
    _, warnings = validate_proposal(
        _proposal([]), vocabulary=["utensil"], room_names=[], unit_names=[]
    )
    assert KIND_EMPTY_VOCABULARY not in _kinds(warnings)


def test_a_name_the_home_already_has_is_flagged_but_kept() -> None:
    """A second 「储物柜」 is a real thing — a note, not a rejection."""
    proposal, warnings = validate_proposal(
        _proposal(room_name="厨房"),
        vocabulary=[],
        room_names=["厨房"],
        unit_names=["吊柜"],
    )
    assert [room.name for room in proposal.rooms] == ["厨房"]
    assert _kinds(warnings).count(KIND_POSSIBLE_DUPLICATE) == 2


def test_warnings_are_ordered_top_down() -> None:
    """The confirm UI renders them in order; non-determinism would be visible."""
    slots = [_slot("A1"), _slot("A1")]
    first = validate_proposal(
        _proposal(slots), vocabulary=["utensil"], room_names=["厨房"], unit_names=["吊柜"]
    )
    second = validate_proposal(
        _proposal(slots), vocabulary=["utensil"], room_names=["厨房"], unit_names=["吊柜"]
    )
    assert _kinds(first[1]) == _kinds(second[1])
    assert _kinds(first[1])[0] == KIND_POSSIBLE_DUPLICATE
