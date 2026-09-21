"""Pure tests for the location-hint matcher (``app/agents/search/location.py``).

Regression origin: the assistant answered 「客厅装饰柜 L1里没有放置任何物品。」
for a slot that really held 玻璃花瓶, because the old matcher tested
``hint in full_path`` and the LLM's hint ("客厅装饰柜 L1", with a space) is not a
substring of the path ("客厅/客厅装饰柜/左玻璃柜/L1", with slashes).
"""
from __future__ import annotations

import pytest

from app.agents.search.location import match_slots_by_hint, split_location_hint

# ---------------------------------------------------------------------- fixtures


def _slot(code: str, room: str, unit: str, section: str, suffix: str = "") -> dict:
    return {
        "id": f"id-{code}{suffix}",
        "code": code,
        "label": f"{section}{code}",
        "room_name": room,
        "unit_name": unit,
        "section_name": section,
        "full_path": f"{room}/{unit}/{section}/{code}",
    }


@pytest.fixture
def slots() -> list[dict]:
    """The demo home's shape: L1 (living) and L1S1 (kitchen) collide on prefix."""
    return [
        _slot("L1", "客厅", "客厅装饰柜", "左玻璃柜"),
        _slot("L2", "客厅", "客厅装饰柜", "左玻璃柜"),
        _slot("R1", "客厅", "客厅装饰柜", "右玻璃柜"),
        _slot("M1", "客厅", "客厅装饰柜", "中间开放区"),
        _slot("L1S1", "厨房", "厨房吊柜", "第1层"),
        _slot("L1S2", "厨房", "厨房吊柜", "第1层"),
    ]


# ---------------------------------------------------------------------- split


@pytest.mark.parametrize(
    ("hint", "expected"),
    [
        ("客厅装饰柜 L1", ("L1", "客厅装饰柜")),
        ("客厅装饰柜L1", ("L1", "客厅装饰柜")),
        ("L1", ("L1", "")),
        ("l1s1", ("l1s1", "")),
        ("第1层", ("", "第1层")),
        ("L1 第1层", ("L1", "第1层")),
        ("厨房", ("", "厨房")),
        ("", ("", "")),
        ("   ", ("", "")),
        # A bare digit run is a name fragment, not a slot code.
        ("1", ("", "1")),
    ],
)
def test_split_location_hint(hint: str, expected: tuple[str, str]) -> None:
    assert split_location_hint(hint) == expected


# ---------------------------------------------------------------------- match


def test_empty_hint_matches_nothing(slots: list[dict]) -> None:
    assert match_slots_by_hint(slots, "") == []
    assert match_slots_by_hint(slots, "   ") == []


def test_exact_code_outranks_prefix_family(slots: list[dict]) -> None:
    """“L1” is a prefix of L1S1/L1S2 but the exact slot must come first."""
    matched = match_slots_by_hint(slots, "L1")
    assert matched[0]["code"] == "L1"
    assert {s["code"] for s in matched} == {"L1", "L1S1", "L1S2"}


def test_room_plus_code_excludes_the_other_rooms_same_prefix(
    slots: list[dict],
) -> None:
    """The Bug-2 regression: 客厅装饰柜 L1 must not reach the kitchen's L1S1."""
    matched = match_slots_by_hint(slots, "客厅装饰柜 L1")
    assert [s["code"] for s in matched] == ["L1"]


def test_unit_name_returns_every_slot_under_it(slots: list[dict]) -> None:
    matched = match_slots_by_hint(slots, "客厅装饰柜")
    assert {s["code"] for s in matched} == {"L1", "L2", "R1", "M1"}


def test_room_name_matches_all_its_slots(slots: list[dict]) -> None:
    matched = match_slots_by_hint(slots, "厨房")
    assert {s["code"] for s in matched} == {"L1S1", "L1S2"}


def test_section_name_matches_its_slots(slots: list[dict]) -> None:
    matched = match_slots_by_hint(slots, "第1层")
    assert {s["code"] for s in matched} == {"L1S1", "L1S2"}


def test_full_path_hint_matches_itself(slots: list[dict]) -> None:
    """The grounded prompt now returns the exact ``full_path``; keep it working."""
    matched = match_slots_by_hint(slots, "客厅/客厅装饰柜/左玻璃柜/L1")
    assert [s["code"] for s in matched] == ["L1"]


def test_unknown_hint_matches_nothing(slots: list[dict]) -> None:
    assert match_slots_by_hint(slots, "车库") == []
    assert match_slots_by_hint(slots, "Z9") == []


def test_ordering_is_deterministic(slots: list[dict]) -> None:
    first = [s["id"] for s in match_slots_by_hint(slots, "客厅装饰柜")]
    second = [s["id"] for s in match_slots_by_hint(list(reversed(slots)), "客厅装饰柜")]
    assert first == second
