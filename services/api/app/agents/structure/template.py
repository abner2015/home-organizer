"""The no-LLM starter structure.

Reached when the user taps 「用模板搭」 instead of describing their home. It
exists so that building a storage tree never *requires* a working model — if
the AI provider is down, misconfigured, or simply not what the user wants, the
acceptance path (structure exists → recommendations stop failing) still works.

Server-side rather than a constant in the web client for two reasons: the
client then has one call and one response shape regardless of source, and the
template goes through the same ``validate_proposal`` pass as an LLM proposal,
so it cannot become the one path that ships an ungrounded category.

Deliberately small — three rooms, one piece of furniture each. The user is
meant to extend it, and a thirty-node wall is harder to confirm than a
ten-node one.
"""
from __future__ import annotations

from app.ai.provider import (
    ProposedRoom,
    ProposedSection,
    ProposedSlot,
    ProposedUnit,
    StructureProposalOutput,
)
from app.db.enums import RoomType, StorageSectionType, StorageUnitType


def build_template_proposal(vocabulary: list[str]) -> StructureProposalOutput:
    """A conservative 卧室 / 厨房 / 客厅 skeleton.

    Categories are drawn only from ``vocabulary``, so on any home this template
    survives :func:`validate_proposal` without a single warning — including a
    brand-new home, where the vocabulary is empty and every slot is left
    unrestricted.
    """
    known = {c.strip().lower() for c in vocabulary if c.strip()}

    def categories(*candidates: str) -> list[str]:
        return [name for name in candidates if name in known]

    rooms = [
        ProposedRoom(
            name="卧室",
            room_type=RoomType.BEDROOM,
            units=[
                ProposedUnit(
                    name="衣柜",
                    unit_type=StorageUnitType.CABINET,
                    sections=[
                        ProposedSection(
                            name="挂衣区",
                            section_type=StorageSectionType.LAYER,
                            slots=[
                                ProposedSlot(
                                    code="W1",
                                    label="长衣区",
                                    allowed_categories=categories("clothes"),
                                    capacity_hint="medium",
                                ),
                                ProposedSlot(
                                    code="W2",
                                    label="叠放区",
                                    allowed_categories=categories("clothes"),
                                    capacity_hint="small",
                                ),
                            ],
                        ),
                        ProposedSection(
                            name="抽屉",
                            section_type=StorageSectionType.DRAWER,
                            slots=[
                                ProposedSlot(
                                    code="W3",
                                    label="内衣抽屉",
                                    allowed_categories=categories("clothes"),
                                    capacity_hint="small",
                                )
                            ],
                        ),
                    ],
                )
            ],
        ),
        ProposedRoom(
            name="厨房",
            room_type=RoomType.KITCHEN,
            units=[
                ProposedUnit(
                    name="吊柜",
                    unit_type=StorageUnitType.CABINET,
                    sections=[
                        ProposedSection(
                            name="上层",
                            section_type=StorageSectionType.LAYER,
                            slots=[
                                ProposedSlot(
                                    code="K1",
                                    label="左侧",
                                    allowed_categories=categories("utensil", "appliance"),
                                    capacity_hint="medium",
                                ),
                                ProposedSlot(
                                    code="K2",
                                    label="右侧",
                                    allowed_categories=categories("utensil", "appliance"),
                                    capacity_hint="medium",
                                ),
                            ],
                        ),
                        ProposedSection(
                            name="下层",
                            section_type=StorageSectionType.LAYER,
                            slots=[
                                ProposedSlot(
                                    code="K3",
                                    label="碗盘区",
                                    allowed_categories=categories("utensil"),
                                    capacity_hint="large",
                                ),
                                ProposedSlot(
                                    code="K4",
                                    label="食品区",
                                    allowed_categories=categories("food"),
                                    capacity_hint="medium",
                                ),
                            ],
                        ),
                    ],
                )
            ],
        ),
        ProposedRoom(
            name="客厅",
            room_type=RoomType.LIVING,
            units=[
                ProposedUnit(
                    name="电视柜",
                    unit_type=StorageUnitType.DRAWER_CABINET,
                    sections=[
                        ProposedSection(
                            name="抽屉",
                            section_type=StorageSectionType.DRAWER,
                            slots=[
                                ProposedSlot(
                                    code="L1",
                                    label="上层抽屉",
                                    allowed_categories=categories("misc", "electronic"),
                                    capacity_hint="small",
                                ),
                                ProposedSlot(
                                    code="L2",
                                    label="下层抽屉",
                                    allowed_categories=categories("misc", "electronic"),
                                    capacity_hint="small",
                                ),
                            ],
                        ),
                        ProposedSection(
                            name="开放格",
                            section_type=StorageSectionType.COMPARTMENT,
                            slots=[
                                ProposedSlot(
                                    code="L3",
                                    label="左侧开放格",
                                    allowed_categories=categories("decor", "books"),
                                    capacity_hint="medium",
                                ),
                                ProposedSlot(
                                    code="L4",
                                    label="右侧开放格",
                                    allowed_categories=categories("decor", "books"),
                                    capacity_hint="medium",
                                ),
                            ],
                        ),
                    ],
                )
            ],
        ),
    ]

    return StructureProposalOutput(
        rooms=rooms,
        rationale=(
            "这是一套通用骨架：卧室衣柜、厨房吊柜、客厅电视柜。"
            "请按自家情况增删房间、家具和格子——所有条目都可以改名或去掉。"
        ),
        # Low on purpose: this is a guess about a home nobody has described.
        confidence=0.3,
    )


__all__ = ["build_template_proposal"]
