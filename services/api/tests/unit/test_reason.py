"""Deterministic Chinese reasons + the LLM-reason gate (P0.4).

Two properties matter most:

- ``build_reason`` is **deterministic** (no UUIDs / timestamps) and never emits
  an ASCII letter — not even when ``full_path`` carries a slot code like
  ``L1S1`` (which happens whenever a slot has no Chinese ``label``).
- ``is_acceptable_llm_reason`` is the runtime enforcement of the rule that
  lived only in ``recommend.v2.md`` since Phase 12.
"""
from __future__ import annotations

from app.agents.reason import build_reason, is_acceptable_llm_reason


def _slot(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "id": "11111111-1111-1111-1111-111111111111",
        "code": "L1S1",
        "full_path": "厨房/吊柜/第1层/第1层第1格",
        "room_name": "厨房",
        "room_type": "kitchen",
        "unit_name": "厨房吊柜",
        "section_name": "第1层",
        "label": "第1层第1格",
        "allowed_categories": ["utensil"],
        "active_count": 0,
    }
    base.update(overrides)
    return base


def _item(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {"name": "马克杯", "category": "utensil"}
    base.update(overrides)
    return base


def _has_ascii_letter(text: str) -> bool:
    return any("A" <= ch <= "z" for ch in text)


# ------------------------------------------------------------ is_acceptable


def test_accepts_a_plain_chinese_reason() -> None:
    assert is_acceptable_llm_reason("厨房吊柜离你常待的地方很近")
    assert is_acceptable_llm_reason("可以放在厨房吊柜。")


def test_rejects_empty_and_whitespace_and_too_short() -> None:
    assert not is_acceptable_llm_reason("")
    assert not is_acceptable_llm_reason("   ")
    assert not is_acceptable_llm_reason(None)
    assert not is_acceptable_llm_reason("好")


def test_rejects_any_reason_containing_ascii_letters() -> None:
    """Slot codes and English prose are the two things the gate exists for."""
    assert not is_acceptable_llm_reason("可以放在 L1S1。")
    assert not is_acceptable_llm_reason("slot CP1-19 is a good fit")
    assert not is_acceptable_llm_reason("category=books")


def test_rejects_a_reason_with_no_chinese_at_all() -> None:
    assert not is_acceptable_llm_reason("123456")
    assert not is_acceptable_llm_reason("......")


def test_rejects_reasons_longer_than_the_cap() -> None:
    assert not is_acceptable_llm_reason("好" * 513)


# --------------------------------------------------------------- build_reason


def test_reason_mentions_the_item_name() -> None:
    reason = build_reason(_slot(), _item())
    assert "马克杯" in reason
    assert reason


def test_reason_never_contains_an_ascii_letter() -> None:
    reason = build_reason(_slot(), _item())
    assert not _has_ascii_letter(reason)


def test_reason_drops_ascii_path_segments_but_keeps_the_readable_rest() -> None:
    """`full_path` falls back to `<section>/<slot.code>` when there's no label."""
    slot = _slot(full_path="厨房/吊柜/第1层/L1S1")
    reason = build_reason(slot, _item())
    assert "L1S1" not in reason
    assert "厨房" in reason
    assert not _has_ascii_letter(reason)


def test_reason_falls_back_to_named_fields_when_the_path_is_all_ascii() -> None:
    slot = _slot(full_path="L1S1", room_name="厨房", unit_name="厨房吊柜")
    reason = build_reason(slot, _item())
    assert "厨房吊柜" in reason
    assert not _has_ascii_letter(reason)


def test_unknown_category_is_never_echoed_raw() -> None:
    reason = build_reason(_slot(), _item(category="widget"))
    assert "widget" not in reason
    assert not _has_ascii_letter(reason)


def test_category_match_is_named_as_the_evidence() -> None:
    reason = build_reason(_slot(), _item(), score_terms={"category": 1})
    assert "该位置可以存放此类物品" in reason


def test_preference_is_narrated_alongside_a_stronger_clause() -> None:
    """`category` (+25) fires for nearly every proposed slot, so a first-wins
    clause list would make the preference sentence unreachable — and the user
    could not see their own accept reflected in the reason (P0.4 acceptance)."""
    reason = build_reason(_slot(), _item(), score_terms={"category": 1, "preference": 1})
    assert "该位置可以存放此类物品" in reason
    assert "符合你以往的收纳习惯" in reason


def test_preference_is_not_mentioned_when_the_term_did_not_fire() -> None:
    reason = build_reason(_slot(), _item(), score_terms={"category": 1})
    assert "符合你以往的收纳习惯" not in reason


def test_preference_is_not_repeated_when_it_is_the_strongest_clause() -> None:
    reason = build_reason(_slot(), _item(), score_terms={"preference": 1})
    assert reason.count("符合你以往的收纳习惯") == 1


def test_blank_item_and_blank_location_still_yields_a_reason() -> None:
    reason = build_reason({}, {})
    assert reason == "这是一个合适的收纳位置"


def test_sensitive_item_in_a_locked_slot_says_so() -> None:
    slot = _slot(unit_type="drawer_cabinet", section_name="带锁抽屉")
    reason = build_reason(slot, _item(name="处方药", category="medicine", is_sensitive=True))
    assert "带锁" in reason


def test_reason_is_deterministic() -> None:
    """No UUIDs, no timestamps — two calls are byte-identical."""
    slot = _slot()
    item = _item()
    assert build_reason(slot, item) == build_reason(slot, item)


def test_reason_is_capped_at_512_chars() -> None:
    reason = build_reason(_slot(), _item(name="超" * 600))
    assert len(reason) <= 512
