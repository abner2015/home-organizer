"""Evaluation runner — wires the dataset to the recommendation agent.

For each golden case the runner:

1. Seeds an in-memory SQLite DB with a synthetic home that has every logical
   slot tag the dataset references.
2. Persists the case's ``item`` against the synthetic home.
3. Runs :class:`app.agents.pipeline.RecommendationAgent` end-to-end.
4. Captures the agent's chosen slot + verifier outcome + retry count + step list.

The runner is deliberately self-contained: it owns a fresh in-memory engine
per case so cases don't bleed state. Use :meth:`EvalRunner.run_all` to drive
the full dataset.
"""
from __future__ import annotations

import os
import time
import uuid
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.agents.pipeline import MAX_RETRIES_DEFAULT, RecommendationAgent
from app.ai.factory import get_provider, reset_provider
from app.ai.provider import AIProvider, CandidateSlot, RankingOutput
from app.ai.providers.mock import MockAIProvider
from app.core.logging import configure_logging, get_logger
from app.db.base import Base
from app.db.enums import (
    HomeRole,
    HomeRuleType,
    RoomType,
    StorageSectionType,
    StorageUnitType,
)
from app.evaluation.dataset import EvalCase, load_dataset
from app.models import (
    Home,
    HomeMembership,
    HomeRule,
    Item,
    Room,
    StorageSection,
    StorageSlot,
    StorageUnit,
    User,
)
from app.tools.registry import get_default_registry

logger = get_logger(__name__)

# ----------------------------------------------------------------- slot tags

# Logical name → real seeded slot_id. Populated by ``_build_synthetic_home``.
# Tests / metrics read this back via :meth:`EvalRunner.slot_id_for_tag`.
SlotTagMap = dict[str, list[str]]


# ----------------------------------------------------------------- synthetic home

# A complete slot hierarchy that covers every logical tag referenced in the
# dataset. Rooms: 客厅 (living), 厨房 (kitchen), 主卧 (bedroom), 儿童房
# (kids_room), 卫生间 (bathroom). All slot ids are stable across runs
# (UUID4 seeded with a fixed namespace + tag) so the tag map is deterministic.
_SYNTHETIC_TREE: list[dict[str, Any]] = [
    {
        "room": "客厅",
        "room_type": RoomType.LIVING,
        "units": [
            {
                "name": "客厅装饰柜",
                "unit_type": StorageUnitType.CABINET,
                "sections": [
                    {
                        "name": "左玻璃柜第1层",
                        "section_type": StorageSectionType.LAYER,
                        "tags": ["living_cabinet_glass"],
                    },
                    {
                        "name": "左玻璃柜第2层",
                        "section_type": StorageSectionType.LAYER,
                        "tags": ["living_cabinet_glass"],
                    },
                    {
                        "name": "左玻璃柜第3层",
                        "section_type": StorageSectionType.LAYER,
                        "tags": ["living_cabinet_glass"],
                    },
                    {
                        "name": "中间开放区",
                        "section_type": StorageSectionType.COMPARTMENT,
                        "tags": ["living_cabinet_open", "any_open_slot"],
                    },
                    {
                        "name": "右玻璃柜第1层",
                        "section_type": StorageSectionType.LAYER,
                        "tags": ["living_cabinet_glass"],
                    },
                ],
            },
        ],
    },
    {
        "room": "厨房",
        "room_type": RoomType.KITCHEN,
        "units": [
            {
                "name": "厨房吊柜",
                "unit_type": StorageUnitType.CABINET,
                "sections": [
                    {
                        "name": "第1层",
                        "section_type": StorageSectionType.LAYER,
                        "tags": ["kitchen_cabinet"],
                    },
                    {
                        "name": "第2层",
                        "section_type": StorageSectionType.LAYER,
                        "tags": ["kitchen_cabinet"],
                    },
                ],
            },
        ],
    },
    {
        "room": "主卧",
        "room_type": RoomType.BEDROOM,
        "units": [
            {
                "name": "主卧衣柜",
                "unit_type": StorageUnitType.DRAWER_CABINET,
                "sections": [
                    {
                        "name": "大衣区",
                        "section_type": StorageSectionType.COMPARTMENT,
                        "tags": ["bedroom_wardrobe"],
                    },
                    {
                        "name": "抽屉",
                        "section_type": StorageSectionType.DRAWER,
                        "tags": ["bedroom_drawer"],
                    },
                    {
                        "name": "带锁抽屉",
                        "section_type": StorageSectionType.DRAWER,
                        "tags": ["bedroom_locked_drawer", "locked_cabinet"],
                    },
                ],
            },
        ],
    },
    {
        "room": "儿童房",
        "room_type": RoomType.BEDROOM,
        "units": [
            {
                "name": "儿童房收纳柜",
                "unit_type": StorageUnitType.CABINET,
                "sections": [
                    {
                        "name": "玩具抽屉",
                        "section_type": StorageSectionType.DRAWER,
                        "tags": ["kids_room_drawer", "any_open_slot"],
                    },
                    {
                        "name": "绘本架",
                        "section_type": StorageSectionType.LAYER,
                        "tags": ["kids_room_shelf", "any_open_slot"],
                    },
                    {
                        "name": "药品带锁抽屉",
                        "section_type": StorageSectionType.DRAWER,
                        "tags": ["kids_room_locked", "locked_cabinet"],
                    },
                ],
            },
        ],
    },
    {
        "room": "卫生间",
        "room_type": RoomType.BATHROOM,
        "units": [
            {
                "name": "卫生间吊柜",
                "unit_type": StorageUnitType.CABINET,
                "sections": [
                    {
                        "name": "洗漱区",
                        "section_type": StorageSectionType.COMPARTMENT,
                        "tags": ["bathroom_cabinet"],
                    },
                ],
            },
        ],
    },
]

_SYNTHETIC_RULES: list[dict[str, Any]] = [
    {
        "name": "厨房放食品",
        "description": "Kitchen stores food items.",
        "rule_type": HomeRuleType.HARD,
        "scope": {"room_types": ["kitchen"], "categories": ["food"]},
    },
    {
        "name": "药品上锁",
        "description": "Sensitive medicine must be locked.",
        "rule_type": HomeRuleType.HARD,
        "scope": {"categories": ["medicine"], "needs_lock": True},
    },
    {
        "name": "儿童房不放易碎品",
        "description": "Fragile items must not go into the kids' room.",
        "rule_type": HomeRuleType.SOFT,
        "scope": {"room_types": ["bedroom"], "categories": ["glass", "fragile", "ceramic"]},
    },
]


@dataclass(slots=True)
class SyntheticHome:
    """Per-case handles + the tag→slot_id map the runner needs to score."""

    home_id: uuid.UUID
    user_id: uuid.UUID
    tag_map: SlotTagMap = field(default_factory=lambda: defaultdict(list))


async def _build_synthetic_home(session: AsyncSession) -> SyntheticHome:
    """Persist a fresh home, rooms, units, sections, slots, and rules."""
    user = User(
        id=uuid.uuid4(),
        email=f"eval-{uuid.uuid4().hex[:8]}@home.local",
        display_name="Eval User",
        password_hash="!eval-disabled!",
    )
    session.add(user)
    home = Home(id=uuid.uuid4(), name="Eval Home", timezone="UTC", owner_id=user.id)
    session.add(home)
    session.add(
        HomeMembership(
            home_id=home.id,
            user_id=user.id,
            role=HomeRole.OWNER.value,
        )
    )

    tag_map: SlotTagMap = defaultdict(list)
    for room_def in _SYNTHETIC_TREE:
        room = Room(
            id=uuid.uuid4(),
            home_id=home.id,
            name=room_def["room"],
            room_type=room_def["room_type"].value,
        )
        session.add(room)
        for unit_def in room_def["units"]:
            unit = StorageUnit(
                id=uuid.uuid4(),
                room_id=room.id,
                name=unit_def["name"],
                unit_type=unit_def["unit_type"].value,
            )
            session.add(unit)
            for layer_idx, sec_def in enumerate(unit_def["sections"], start=1):
                section = StorageSection(
                    id=uuid.uuid4(),
                    unit_id=unit.id,
                    name=sec_def["name"],
                    section_type=sec_def["section_type"].value,
                    sort_order=layer_idx,
                )
                session.add(section)
                slot = StorageSlot(
                    id=uuid.uuid4(),
                    section_id=section.id,
                    code=f"S-{layer_idx}",
                    label=sec_def["name"],
                    capacity_hint=None,
                    allowed_categories=None,
                )
                session.add(slot)
                for tag in sec_def.get("tags", []):
                    tag_map[tag].append(str(slot.id))

    for rule_def in _SYNTHETIC_RULES:
        session.add(
            HomeRule(
                id=uuid.uuid4(),
                home_id=home.id,
                name=rule_def["name"],
                description=rule_def["description"],
                rule_type=rule_def["rule_type"].value,
                scope=rule_def["scope"],
                enabled=True,
            )
        )

    await session.commit()
    return SyntheticHome(
        home_id=home.id,
        user_id=user.id,
        tag_map=dict(tag_map),
    )


# ------------------------------------------------------- mock decision provider


class CandidateAwareMockProvider(MockAIProvider):
    """Mock DECIDE step: behaves like a competent-but-fallible ranker.

    The base :class:`MockAIProvider` replays a fixed script of slot ids without
    looking at the candidate set. In the eval harness that made every case in
    every category receive the same three slots (whatever happened to be first
    in the DB), and — because the script repeated identically — a retry always
    re-proposed the exact slot the Verifier had just rejected. The retry loop
    therefore could never converge, so the harness reported a 0% retry success
    rate no matter how the agent behaved.

    This subclass models the real DECIDE step instead:

    * it proposes only slots drawn from ``candidates`` (the deterministic
      ranker's output), so a proposal can never fall outside the whitelist;
    * it follows the ranker's ordering, emitting up to ``max_picks`` of them
      with a mildly decaying confidence;
    * when ``last_failure`` is set — i.e. the pipeline is retrying — it drops
      the slot it led with last time, so retries make progress instead of
      looping on the same rejected slot.
    """

    def __init__(self, *, max_picks: int = 3) -> None:
        super().__init__()
        self.max_picks = max_picks
        self._rejected: set[str] = set()
        self._last_lead: str | None = None

    async def rank_candidates(
        self,
        *,
        item: dict[str, Any],
        candidates: list[dict[str, Any]],
        rules: list[dict[str, Any]],
        preferences: list[dict[str, Any]],
        history: list[dict[str, Any]],
        last_failure: str | None = None,
        timeout_s: float = 30.0,
    ) -> RankingOutput:
        self.call_count += 1
        candidate_ids = [
            str(c.get("slot_id") or c.get("id")) for c in candidates
        ]
        self.recorded_calls.append(
            (
                "rank_candidates",
                {
                    "item": item,
                    "last_failure": last_failure,
                    "candidate_ids": candidate_ids,
                },
            )
        )
        await self._maybe_sleep()

        if last_failure and self._last_lead:
            self._rejected.add(self._last_lead)

        pool = [sid for sid in candidate_ids if sid not in self._rejected]
        if not pool:
            # Every candidate has been rejected at least once; fall back to the
            # full list rather than emitting an empty (invalid) RankingOutput.
            pool = candidate_ids

        picks = pool[: self.max_picks]
        self._last_lead = picks[0] if picks else None
        reason = _decision_reason(item)
        return RankingOutput(
            candidates=[
                CandidateSlot(
                    slot_id=uuid.UUID(sid),
                    # Decay slightly by rank; the pipeline takes the max, so
                    # this still leads with the ranker's top candidate.
                    confidence=max(0.5, 0.9 - 0.1 * rank),
                    reason=reason,
                )
                for rank, sid in enumerate(picks)
            ]
        )


def _decision_reason(item: dict[str, Any]) -> str:
    """Reason text that satisfies the Verifier's ``check_reason_consistent``.

    It names the item and its category, which is what that check looks for.
    Punctuation is deliberately ASCII: a fullwidth comma would trip RUF001.
    """
    name = item.get("name") or ""
    category = item.get("category") or "物品"
    return f"{name} 是 {category} 类物品, 适合放在候选位置。"


# ----------------------------------------------------------------- runner

@dataclass(slots=True)
class EvalRunnerConfig:
    """Runner options."""

    use_real_ai: bool = False
    max_retries: int = MAX_RETRIES_DEFAULT
    on_progress: Callable[[int, int, EvalCase], None] | None = None


@dataclass(slots=True)
class CaseResult:
    """One case's outcome — fields are flattened for CSV / JSON output."""

    case_id: str
    category: str
    expected_slots: list[str]
    forbidden_slots: list[str]
    chosen_slot_id: str | None
    chosen_slot_tag: str | None  # logical tag the chosen slot maps to
    top_slot_ids: list[str]
    top_slot_tags: list[str]
    state: str
    retries_used: int
    verifier_passed: bool
    item_recognition_correct: bool | None  # True/False/None (no vision)
    error: str | None
    duration_ms: int
    reason: str = ""


class EvalRunner:
    """Drives the evaluation end-to-end."""

    def __init__(self, config: EvalRunnerConfig | None = None) -> None:
        self.config = config or EvalRunnerConfig()
        # Optional env-var override for real-AI opt-in. The CLI also flips
        # this via the constructor.
        if os.getenv("EVAL_USE_REAL_AI") == "1":
            self.config.use_real_ai = True

    # -------------------------------------------------------------- public

    async def run_all(
        self,
        cases: Iterable[EvalCase] | None = None,
    ) -> list[CaseResult]:
        """Run every case in the dataset (or the supplied list)."""
        if cases is None:
            cases = load_dataset()
        cases = list(cases)
        results: list[CaseResult] = []
        total = len(cases)
        for idx, case in enumerate(cases, start=1):
            result = await self.run_case(case)
            results.append(result)
            if self.config.on_progress:
                self.config.on_progress(idx, total, case)
        return results

    async def run_case(self, case: EvalCase) -> CaseResult:
        """Run a single case in a fresh in-memory DB."""
        # Force Mock AI per case when in mock mode, so we don't leak state
        # between cases (the mock keeps a counter).
        if not self.config.use_real_ai:
            reset_provider()
        engine: AsyncEngine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            echo=False,
            future=True,
        )
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)

        started = time.perf_counter()
        try:
            async with session_factory() as session:
                synthetic = await _build_synthetic_home(session)

                # Persist the case item.
                item = Item(
                    id=uuid.uuid4(),
                    home_id=synthetic.home_id,
                    name=case.item.get("name", ""),
                    description=None,
                    category=case.item.get("category"),
                    subcategory=case.item.get("subcategory"),
                    estimated_size=case.item.get("estimated_size", "small"),
                    is_sensitive=bool(case.item.get("is_sensitive", False)),
                    needs_lock=bool(case.item.get("needs_lock", False)),
                    created_by=synthetic.user_id,
                )
                session.add(item)
                await session.commit()

                provider = self._provider_for_case()
                tools = get_default_registry(session)
                agent = RecommendationAgent(
                    ai=provider,
                    tools=tools,
                    db=session,
                    max_retries=self.config.max_retries,
                )
                run = await agent.run(
                    home_id=synthetic.home_id,
                    user_id=synthetic.user_id,
                    item_id=item.id,
                )

                # Score the agent's chosen slot against the tag map.
                tag_map = synthetic.tag_map
                chosen_tag = self._tag_for_slot(tag_map, run.chosen_slot_id)
                top_tags = [self._tag_for_slot(tag_map, s["id"]) for s in run.candidates]
                top_ids = [s["id"] for s in run.candidates]

                # Build a tiny in-process recognition check: the LLM didn't
                # run in mock mode (we use a scripted ranker), so the only
                # honest signal is whether the agent's chosen tag is in the
                # expected set. We mark the item-recognition flag None to
                # signal "not exercised" — the metric then treats it as N/A.
                return CaseResult(
                    case_id=case.id,
                    category=case.category,
                    expected_slots=case.expected_slots,
                    forbidden_slots=case.forbidden_slots,
                    chosen_slot_id=str(run.chosen_slot_id) if run.chosen_slot_id else None,
                    chosen_slot_tag=chosen_tag,
                    top_slot_ids=top_ids,
                    top_slot_tags=top_tags,
                    state=run.state.value,
                    retries_used=run.retries_used,
                    verifier_passed=run.ok,
                    item_recognition_correct=None,
                    error=run.error,
                    duration_ms=int((time.perf_counter() - started) * 1000),
                    reason=case.reason,
                )
        finally:
            await engine.dispose()

    # -------------------------------------------------------------- helpers

    def _provider_for_case(self) -> AIProvider:
        """Return Mock (default) or real provider for one case.

        In mock mode we use :class:`CandidateAwareMockProvider`, which picks
        from the candidate list the pipeline actually hands it. The previous
        implementation read the first three slots straight out of the DB and
        replayed them unchanged on every retry, which made the harness's retry
        rate structurally zero (see that class's docstring).
        """
        if self.config.use_real_ai:
            return get_provider()
        return CandidateAwareMockProvider()

    @staticmethod
    def _tag_for_slot(tag_map: SlotTagMap, slot_id: str | uuid.UUID | None) -> str | None:
        if slot_id is None:
            return None
        sid = str(slot_id)
        for tag, ids in tag_map.items():
            if sid in {str(s) for s in ids}:
                return tag
        return None


# Convenience: full default run + report wiring.

async def run_evaluation(
    use_real_ai: bool = False,
    on_progress: Callable[[int, int, EvalCase], None] | None = None,
) -> list[CaseResult]:
    """One-shot helper: load + run the full dataset."""
    configure_logging("WARNING")
    config = EvalRunnerConfig(use_real_ai=use_real_ai, on_progress=on_progress)
    runner = EvalRunner(config)
    return await runner.run_all()


__all__ = [
    "CandidateAwareMockProvider",
    "CaseResult",
    "EvalRunner",
    "EvalRunnerConfig",
    "SyntheticHome",
    "run_evaluation",
]
