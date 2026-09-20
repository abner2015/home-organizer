"""Seed script — populate a development database with example data.

Idempotent: if a user/home with the seed email/name already exists, the
existing rows are reused. Run via:

    python -m app.db.seed

Creates:
  * 1 demo user (email: demo@home.local, password: demo-pass — DO NOT USE IN PROD)
  * 1 home ("我的家")
  * 4 rooms: 客厅 / 厨房 / 主卧 / 儿童房
  * Storage hierarchy:
      - 客厅: 1 complex cabinet "客厅装饰柜":
          * 左玻璃柜 (3 layers, 1 slot each)
          * 中间开放区 (1 compartment, 1 slot)
          * 右玻璃柜 (3 layers, 1 slot each)
          * 下柜 (3 compartments)
      - 厨房: 1 simple cabinet "厨房吊柜" (2 layers, 2 slots each)
      - 主卧: 1 drawer cabinet "主卧衣柜" (大衣区 / 抽屉 / 带锁抽屉)
      - 儿童房: 1 cabinet "儿童房收纳柜" (玩具抽屉 / 绘本架 / 药品带锁抽屉)
  * A few HomeRules (厨房不放过期食品, 药品上锁, ...)
"""
from __future__ import annotations

import asyncio
import contextlib
import uuid
from collections.abc import AsyncIterator, Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.core.logging import configure_logging, get_logger
from app.db.enums import (
    HomeRole,
    HomeRuleType,
    RoomType,
    StorageSectionType,
    StorageUnitType,
)
from app.models import (
    Home,
    HomeMembership,
    HomeRule,
    Room,
    StorageSection,
    StorageSlot,
    StorageUnit,
    User,
)

logger = get_logger(__name__)


SEED_USER_EMAIL = "demo@home.local"
SEED_USER_NAME = "演示用户"
SEED_HOME_NAME = "我的家"


async def _get_or_create_user(session: AsyncSession) -> User:
    result = await session.execute(select(User).where(User.email == SEED_USER_EMAIL))
    user = result.scalar_one_or_none()
    if user:
        logger.info("seed.user_exists", email=user.email, id=str(user.id))
        return user
    # bcrypt-style placeholder hash; the dev login flow (Phase 4+) will replace it.
    user = User(
        email=SEED_USER_EMAIL,
        password_hash="seed-placeholder-not-a-real-hash",
        display_name=SEED_USER_NAME,
    )
    session.add(user)
    await session.flush()
    logger.info("seed.user_created", email=user.email, id=str(user.id))
    return user


async def _get_or_create_home(session: AsyncSession, owner: User) -> Home:
    result = await session.execute(select(Home).where(Home.name == SEED_HOME_NAME, Home.owner_id == owner.id))
    home = result.scalar_one_or_none()
    if home:
        logger.info("seed.home_exists", name=home.name, id=str(home.id))
        return home
    home = Home(name=SEED_HOME_NAME, owner_id=owner.id)
    session.add(home)
    await session.flush()
    # Membership: owner
    membership = HomeMembership(
        user_id=owner.id,
        home_id=home.id,
        role=HomeRole.OWNER.value,
    )
    session.add(membership)
    await session.flush()
    logger.info("seed.home_created", name=home.name, id=str(home.id))
    return home


async def _ensure_room(
    session: AsyncSession, home_id: uuid.UUID, name: str, room_type: RoomType, sort: int
) -> Room:
    result = await session.execute(select(Room).where(Room.home_id == home_id, Room.name == name))
    room = result.scalar_one_or_none()
    if room:
        return room
    room = Room(home_id=home_id, name=name, room_type=room_type.value, sort_order=sort)
    session.add(room)
    await session.flush()
    logger.info("seed.room_created", name=name, room_type=room_type.value)
    return room


async def _ensure_unit(
    session: AsyncSession,
    room_id: uuid.UUID,
    name: str,
    unit_type: StorageUnitType,
    sort: int,
    description: str | None = None,
) -> StorageUnit:
    result = await session.execute(
        select(StorageUnit).where(StorageUnit.room_id == room_id, StorageUnit.name == name)
    )
    unit = result.scalar_one_or_none()
    if unit:
        return unit
    unit = StorageUnit(
        room_id=room_id,
        name=name,
        unit_type=unit_type.value,
        description=description,
        sort_order=sort,
    )
    session.add(unit)
    await session.flush()
    logger.info("seed.unit_created", room_id=str(room_id), name=name)
    return unit


async def _ensure_section(
    session: AsyncSession,
    unit_id: uuid.UUID,
    name: str,
    section_type: StorageSectionType,
    sort: int,
) -> StorageSection:
    result = await session.execute(
        select(StorageSection).where(
            StorageSection.unit_id == unit_id, StorageSection.name == name
        )
    )
    section = result.scalar_one_or_none()
    if section:
        return section
    section = StorageSection(
        unit_id=unit_id, name=name, section_type=section_type.value, sort_order=sort
    )
    session.add(section)
    await session.flush()
    return section


async def _ensure_slot(
    session: AsyncSession,
    section_id: uuid.UUID,
    code: str,
    *,
    label: str | None = None,
    allowed_categories: Iterable[str] = (),
    sort: int = 0,
) -> StorageSlot:
    result = await session.execute(
        select(StorageSlot).where(
            StorageSlot.section_id == section_id, StorageSlot.code == code
        )
    )
    slot = result.scalar_one_or_none()
    if slot:
        return slot
    slot = StorageSlot(
        section_id=section_id,
        code=code,
        label=label,
        allowed_categories=list(allowed_categories),
        sort_order=sort,
    )
    session.add(slot)
    await session.flush()
    return slot


async def _build_complex_cabinet(session: AsyncSession, room_id: uuid.UUID) -> StorageUnit:
    """客厅装饰柜 — exercises the full hierarchy."""
    cabinet = await _ensure_unit(
        session,
        room_id,
        name="客厅装饰柜",
        unit_type=StorageUnitType.CABINET,
        sort=1,
        description="包含左玻璃柜、中间开放区、右玻璃柜、下柜四部分",
    )

    # 左玻璃柜 — 3 layers
    left = await _ensure_section(
        session, cabinet.id, "左玻璃柜", StorageSectionType.LAYER, sort=1
    )
    for i in range(1, 4):
        await _ensure_slot(
            session,
            left.id,
            code=f"L{i}",
            label=f"左玻璃柜第{i}层",
            allowed_categories=["decor", "books"],
            sort=i,
        )

    # 中间开放区 — 1 compartment
    middle = await _ensure_section(
        session, cabinet.id, "中间开放区", StorageSectionType.COMPARTMENT, sort=2
    )
    await _ensure_slot(
        session,
        middle.id,
        code="M1",
        label="中间开放区",
        allowed_categories=["decor"],
        sort=1,
    )

    # 右玻璃柜 — 3 layers
    right = await _ensure_section(
        session, cabinet.id, "右玻璃柜", StorageSectionType.LAYER, sort=3
    )
    for i in range(1, 4):
        await _ensure_slot(
            session,
            right.id,
            code=f"R{i}",
            label=f"右玻璃柜第{i}层",
            allowed_categories=["decor", "books"],
            sort=i,
        )

    # 下柜 — 3 compartments (note: section_type='compartment' but parent is cabinet;
    # the schema allows a section to be any type regardless of unit_type)
    lower = await _ensure_section(
        session, cabinet.id, "下柜", StorageSectionType.COMPARTMENT, sort=4
    )
    for i, code in enumerate(["C1", "C2", "C3"], start=1):
        await _ensure_slot(
            session,
            lower.id,
            code=code,
            label=f"下柜第{i}格",
            allowed_categories=["misc"],
            sort=i,
        )

    return cabinet


async def _build_kitchen_cabinet(session: AsyncSession, room_id: uuid.UUID) -> StorageUnit:
    """厨房吊柜 — 2 layers, 2 slots each."""
    cabinet = await _ensure_unit(
        session,
        room_id,
        name="厨房吊柜",
        unit_type=StorageUnitType.CABINET,
        sort=1,
        description="吊挂式两层柜子",
    )
    for layer_num in range(1, 3):
        layer = await _ensure_section(
            session,
            cabinet.id,
            f"第{layer_num}层",
            StorageSectionType.LAYER,
            sort=layer_num,
        )
        for slot_num in range(1, 3):
            code = f"L{layer_num}S{slot_num}"
            await _ensure_slot(
                session,
                layer.id,
                code=code,
                label=f"第{layer_num}层第{slot_num}格",
                allowed_categories=["food", "utensil"],
                sort=slot_num,
            )
    return cabinet


async def _build_bedroom_wardrobe(session: AsyncSession, room_id: uuid.UUID) -> StorageUnit:
    """主卧衣柜 — the locked drawer here is the legal target for sensitive items."""
    wardrobe = await _ensure_unit(
        session,
        room_id,
        name="主卧衣柜",
        unit_type=StorageUnitType.DRAWER_CABINET,
        sort=1,
        description="含大衣区、普通抽屉与带锁抽屉",
    )
    hanging = await _ensure_section(
        session, wardrobe.id, "大衣区", StorageSectionType.COMPARTMENT, sort=1
    )
    await _ensure_slot(
        session,
        hanging.id,
        code="H1",
        label="大衣区",
        allowed_categories=["clothes"],
        sort=1,
    )

    drawer = await _ensure_section(
        session, wardrobe.id, "抽屉", StorageSectionType.DRAWER, sort=2
    )
    await _ensure_slot(
        session,
        drawer.id,
        code="D1",
        label="普通抽屉",
        allowed_categories=["misc", "medicine", "clothes"],
        sort=1,
    )

    # Section name carries the "锁" marker that check_hard_safety keys on.
    locked = await _ensure_section(
        session, wardrobe.id, "带锁抽屉", StorageSectionType.DRAWER, sort=3
    )
    await _ensure_slot(
        session,
        locked.id,
        code="LK1",
        label="带锁抽屉",
        allowed_categories=["medicine", "misc"],
        sort=1,
    )
    return wardrobe


async def _build_kids_cabinet(session: AsyncSession, room_id: uuid.UUID) -> StorageUnit:
    """儿童房收纳柜 — plain cabinet with one locked medicine drawer."""
    cabinet = await _ensure_unit(
        session,
        room_id,
        name="儿童房收纳柜",
        unit_type=StorageUnitType.CABINET,
        sort=1,
        description="含玩具抽屉、绘本架与药品带锁抽屉",
    )
    toys = await _ensure_section(
        session, cabinet.id, "玩具抽屉", StorageSectionType.DRAWER, sort=1
    )
    await _ensure_slot(
        session, toys.id, code="T1", label="玩具抽屉", allowed_categories=["misc"], sort=1
    )

    shelf = await _ensure_section(
        session, cabinet.id, "绘本架", StorageSectionType.LAYER, sort=2
    )
    await _ensure_slot(
        session, shelf.id, code="B1", label="绘本架", allowed_categories=["books"], sort=1
    )

    locked = await _ensure_section(
        session, cabinet.id, "药品带锁抽屉", StorageSectionType.DRAWER, sort=3
    )
    await _ensure_slot(
        session,
        locked.id,
        code="MLK1",
        label="药品带锁抽屉",
        allowed_categories=["medicine"],
        sort=1,
    )
    return cabinet


async def _ensure_rules(session: AsyncSession, home_id: uuid.UUID) -> None:
    rules = [
        (
            "厨房不放过期食品",
            "生鲜、调味料等有时效性的食品不允许长期存放在厨房以外的柜子中。",
            HomeRuleType.HARD,
            {"room_types": ["kitchen"], "categories": ["food"]},
        ),
        (
            "药品上锁",
            "处方药、有毒化学品必须存放在带锁的储物中，且只能由成年人操作。",  # noqa: RUF001
            HomeRuleType.HARD,
            {"categories": ["medicine"], "needs_lock": True},
        ),
        (
            "儿童房不放易碎品",
            "玻璃器皿、陶瓷等易碎物品不应放入儿童房。",
            HomeRuleType.SOFT,
            {"room_types": ["bedroom"], "categories": ["glass", "ceramic"]},
        ),
        (
            "常用物品靠近使用区",
            "每天使用的物品应放在与使用场景最近的柜子，例如杯子靠近餐桌。",  # noqa: RUF001
            HomeRuleType.SOFT,
            {},
        ),
    ]
    existing = {
        r.name
        for r in (await session.execute(select(HomeRule).where(HomeRule.home_id == home_id)))
        .scalars()
        .all()
    }
    for name, description, rule_type, scope in rules:
        if name in existing:
            continue
        session.add(
            HomeRule(
                home_id=home_id,
                name=name,
                description=description,
                rule_type=rule_type.value,
                scope=scope,
                enabled=True,
            )
        )
    await session.flush()


@contextlib.asynccontextmanager
async def _session_scope() -> AsyncIterator[AsyncSession]:
    """Open a session with commit/rollback semantics."""
    engine = create_async_engine(settings.database_url, echo=False)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise
    finally:
        await engine.dispose()


async def seed() -> None:
    """Run the full seed."""
    configure_logging("INFO")
    logger.info("seed.start")
    async with _session_scope() as session:
        user = await _get_or_create_user(session)
        home = await _get_or_create_home(session, user)

        living = await _ensure_room(session, home.id, "客厅", RoomType.LIVING, sort=1)
        kitchen = await _ensure_room(session, home.id, "厨房", RoomType.KITCHEN, sort=2)
        master = await _ensure_room(session, home.id, "主卧", RoomType.BEDROOM, sort=3)
        kids = await _ensure_room(session, home.id, "儿童房", RoomType.BEDROOM, sort=4)

        await _build_complex_cabinet(session, living.id)
        await _build_kitchen_cabinet(session, kitchen.id)
        # Bedrooms hold the locked containers required for sensitive items
        # (medicine). Without them the Verifier can never approve a
        # recommendation for an item with is_sensitive=True.
        await _build_bedroom_wardrobe(session, master.id)
        await _build_kids_cabinet(session, kids.id)

        await _ensure_rules(session, home.id)

    logger.info("seed.done")


def main() -> None:
    asyncio.run(seed())


if __name__ == "__main__":
    main()
