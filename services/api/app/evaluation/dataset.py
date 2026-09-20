"""Dataset loader for AI evaluation cases.

Each case declares:

- ``id``             — unique identifier
- ``item``           — the input item dict (mirrors the Item schema)
- ``expected_slots`` — slot-tag ids that should be returned
- ``forbidden_slots``— slot-tag ids that must NOT be returned (hard violations)
- ``reason``         — human-readable explanation (for the Markdown report)

``slot_tag`` is a logical name (e.g. ``kitchen_cabinet``) rather than a UUID,
so golden cases remain stable as the seed schema evolves. The runner maps
logical tags to real slot ids via the seeded home's space tree.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Default dataset location: ``services/api/evaluation/dataset/*.json``.
# Resolved relative to the *package* directory so it works whether the
# caller runs from the repo root or from inside ``services/api``.
_PACKAGE_DIR = Path(__file__).resolve().parents[2]
DEFAULT_DATASET_DIR = _PACKAGE_DIR / "evaluation" / "dataset"


@dataclass(slots=True)
class EvalCase:
    """One golden case from the dataset."""

    id: str
    category: str
    item: dict[str, Any]
    expected_slots: list[str] = field(default_factory=list)
    forbidden_slots: list[str] = field(default_factory=list)
    reason: str = ""

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "EvalCase":
        return cls(
            id=str(payload["id"]),
            category=str(payload.get("category", "")),
            item=dict(payload.get("item", {})),
            expected_slots=list(payload.get("expected_slots", [])),
            forbidden_slots=list(payload.get("forbidden_slots", [])),
            reason=str(payload.get("reason", "")),
        )


def load_dataset(dataset_dir: Path | str | None = None) -> list[EvalCase]:
    """Load every ``*.json`` file in ``dataset_dir`` and return all cases.

    Skips non-case files (e.g. ``README.json``) — anything with no ``cases``
    key is treated as metadata and ignored.
    """
    base = Path(dataset_dir) if dataset_dir is not None else DEFAULT_DATASET_DIR
    if not base.exists():
        raise FileNotFoundError(f"Dataset directory not found: {base}")

    cases: list[EvalCase] = []
    for path in sorted(base.glob("*.json")):
        with path.open("r", encoding="utf-8") as fh:
            payload = json.load(fh)
        for entry in payload.get("cases", []) or []:
            cases.append(EvalCase.from_dict(entry))
    return cases


__all__ = ["EvalCase", "load_dataset", "DEFAULT_DATASET_DIR"]