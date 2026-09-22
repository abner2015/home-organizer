"""Deterministic Chinese "why this spot" reasons (P0.4).

Two jobs:

1. :func:`build_reason` composes a readable reason for *any* ranked slot from
   the ranker's own score terms. It runs on every candidate, so the acceptance
   rule "every candidate carries a non-empty Chinese reason" holds even on the
   no-LLM ``GET /items/{id}/candidates`` path, which never sees a model.

2. :func:`is_acceptable_llm_reason` gates the model's own reason. The
   no-code/no-English rule has lived only in ``recommend.v2.md`` since Phase 12;
   this is the runtime enforcement. A reason that fails the gate is dropped and
   the deterministic one is used instead, so a chatty model can never push
   ``L1S1`` or ``category=books`` to the UI.

Deliberately free of the two things that would break determinism: no UUIDs and
no timestamps. ``test_candidates_are_deterministic_and_ranked`` asserts two
back-to-back responses are byte-identical.

Also deliberately imports nothing from ``app.agents`` — ``ranking`` imports
*this* module, and the reverse would be an import cycle. The small Chinese
vocabularies below are duplicated rather than shared for that reason.
"""
from __future__ import annotations

import re
from typing import Any

_CJK = re.compile(r"[\u4e00-\u9fff]")
_ASCII_LETTER = re.compile(r"[A-Za-z]")
_MAX_LEN = 512

# Item category → Chinese noun. Keys cover both the domain vocabulary
# (docs/DOMAIN.md) and the English slugs the vision layer emits. An unknown
# category is simply omitted — never echoed raw, or an English slug would leak
# straight into the reason.
_CATEGORY_ZH: dict[str, str] = {
    "厨具": "厨具",
    "餐具": "餐具",
    "utensil": "餐具",
    "食品": "食品",
    "food": "食品",
    "衣物": "衣物",
    "clothes": "衣物",
    "clothing": "衣物",
    "书籍": "书籍",
    "book": "书籍",
    "books": "书籍",
    "药品": "药品",
    "medicine": "药品",
    "保健品": "保健品",
    "电子产品": "电子产品",
    "electronic": "电子产品",
    "electronics": "电子产品",
    "工具": "工具",
    "tool": "工具",
    "五金": "五金",
    "hardware": "五金",
    "清洁用品": "清洁用品",
    "cleaning": "清洁用品",
    "chemical": "清洁用品",
    "装饰品": "装饰品",
    "decor": "装饰品",
    "玻璃器皿": "玻璃器皿",
    "glass": "玻璃器皿",
    "陶瓷器皿": "陶瓷器皿",
    "ceramic": "陶瓷器皿",
    "小家电": "小家电",
    "appliance": "小家电",
    "玩具": "玩具",
    "toy": "玩具",
    "toys": "玩具",
    "儿童用品": "儿童用品",
    "kids": "儿童用品",
    "文件": "文件",
    "document": "文件",
    "documents": "文件",
    "证件": "证件",
    "文档": "文档",
    "misc": "杂物",
    "daily": "日用品",
}

# Slot room_type → Chinese noun (the seed/domain vocabulary).
_ROOM_TYPE_ZH: dict[str, str] = {
    "kitchen": "厨房",
    "bedroom": "卧室",
    "living": "客厅",
    "study": "书房",
    "bathroom": "浴室",
    "storage": "储物间",
    "other": "其它空间",
}

_LOCK_MARKERS = ("锁", "locked", "lockable")


def is_acceptable_llm_reason(text: str | None) -> bool:
    """True if the model's reason may be shown to the user verbatim.

    Requires a non-empty, reasonably sized string that contains Chinese and
    **no ASCII letters at all** — which subsumes slot codes (``L1S1``, ``S-1``),
    field names (``category=books``) and English prose in one test.
    """
    if not text:
        return False
    stripped = text.strip()
    if not (2 <= len(stripped) <= _MAX_LEN):
        return False
    if _ASCII_LETTER.search(stripped):
        return False
    return bool(_CJK.search(stripped))


def _has_ascii_letter(text: str) -> bool:
    return bool(_ASCII_LETTER.search(text))


def _clean_segments(text: str | None) -> list[str]:
    """Split a path on ``/``, dropping empty and ASCII-bearing segments.

    ``full_path`` falls back to ``<section>/<slot.code>`` when a slot has no
    ``label``, so it can contain ``L1S1``. Dropping just that segment keeps the
    readable part of the path instead of discarding the whole thing.
    """
    if not text:
        return []
    return [seg.strip() for seg in text.split("/") if seg.strip() and not _has_ascii_letter(seg)]


def _location_text(slot: dict[str, Any]) -> str:
    """The most specific ASCII-free description of the slot."""
    segments = _clean_segments(slot.get("full_path"))
    if segments:
        return "/".join(segments)
    named = [
        str(slot.get(key)).strip()
        for key in ("room_name", "unit_name", "section_name")
        if slot.get(key) and not _has_ascii_letter(str(slot[key]))
    ]
    if named:
        return "/".join(named)
    label = str(slot.get("label") or "").strip()
    return label if label and not _has_ascii_letter(label) else ""


def _slot_is_locked(slot: dict[str, Any]) -> bool:
    """Mirror of the verifier's lock heuristic, kept local to avoid importing
    ``app.verification`` from here (that would invert the dependency)."""
    if str(slot.get("unit_type") or "").strip().lower() in {"drawer_cabinet", "box"}:
        return True
    haystack = " ".join(
        str(slot.get(key) or "")
        for key in ("section_name", "label", "code")
    ).lower()
    return any(marker in haystack for marker in _LOCK_MARKERS)


def _evidence_clause(slot: dict[str, Any], item: dict[str, Any], terms: dict[str, int]) -> str:
    """The strongest applicable "why", in descending weight order."""
    if terms.get("category"):
        return "该位置可以存放此类物品"
    if item.get("is_sensitive") and _slot_is_locked(slot):
        return "带锁的收纳位置适合存放敏感物品"
    if terms.get("capacity"):
        return "目前是空的，容量充足"
    if terms.get("history"):
        return "你之前就把同类物品放在这里"
    if terms.get("preference"):
        return "符合你以往的收纳习惯"
    if terms.get("room"):
        room_zh = _ROOM_TYPE_ZH.get(str(slot.get("room_type") or "").strip().lower(), "")
        if room_zh:
            return f"{room_zh}通常是收纳这类物品的地方"
    return ""


def build_reason(
    slot: dict[str, Any],
    item: dict[str, Any],
    *,
    score_terms: dict[str, int] | None = None,
) -> str:
    """Compose a Chinese reason for one candidate slot.

    Always mentions the item's name when one is available, which is also what
    keeps ``check_reason_consistent`` satisfied — a slot reason can therefore
    never trigger a verifier retry.
    """
    parts: list[str] = []

    name = str(item.get("name") or "").strip()
    category = str(item.get("category") or "").strip().lower()
    category_zh = _CATEGORY_ZH.get(category)
    if name and category_zh:
        parts.append(f"{name}是{category_zh}类物品")
    elif name:
        parts.append(name)

    location = _location_text(slot)
    if location:
        parts.append(f"建议放在{location}")

    evidence = _evidence_clause(slot, item, score_terms or {})
    if evidence:
        parts.append(evidence)

    if not parts:
        return "这是一个合适的收纳位置"

    reason = "，".join(parts)
    if len(reason) > _MAX_LEN:
        reason = reason[:_MAX_LEN]
    return reason


__all__ = ["build_reason", "is_acceptable_llm_reason"]
