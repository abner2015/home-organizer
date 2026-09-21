"""Seed script — populate a development database with example data.

Idempotent: if a user/home with the seed email/name already exists, the
existing rows are reused. Run via:

    python -m app.db.seed

Creates:
  * 1 demo user (email: demo@home.local, password: demo1234 — DO NOT USE IN PROD)
    with fixed id 00000000-0000-0000-0000-000000000001 (matches the web
    app's stub-auth default)
  * 1 home ("我的家") with fixed id ...-0002
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
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.core.logging import configure_logging, get_logger
from app.db.base import utc_now
from app.db.enums import (
    HomeRole,
    HomeRuleType,
    PlacementSource,
    RoomType,
    StorageSectionType,
    StorageUnitType,
)
from app.models import (
    Home,
    HomeMembership,
    HomeRule,
    Item,
    ItemPlacement,
    Room,
    StorageSection,
    StorageSlot,
    StorageUnit,
    User,
)
from app.services.security import hash_password

logger = get_logger(__name__)


SEED_USER_EMAIL = "demo@home.local"
SEED_USER_NAME = "演示用户"
SEED_HOME_NAME = "我的家"
SEED_USER_PASSWORD = "demo1234"

# Stable ids so the demo web app (which hardcodes these in
# apps/web/src/lib/session.ts and apps/web/.env.example) can talk to the
# seeded data without a login round-trip. 0001/0002 are the values the
# frontend's stub-auth headers use.
SEED_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
SEED_HOME_ID = uuid.UUID("00000000-0000-0000-0000-000000000002")


async def _get_or_create_user(session: AsyncSession) -> User:
    result = await session.execute(select(User).where(User.email == SEED_USER_EMAIL))
    user = result.scalar_one_or_none()
    if user:
        logger.info("seed.user_exists", email=user.email, id=str(user.id))
        return user
    user = User(
        id=SEED_USER_ID,
        email=SEED_USER_EMAIL,
        password_hash=hash_password(SEED_USER_PASSWORD),
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
    home = Home(id=SEED_HOME_ID, name=SEED_HOME_NAME, owner_id=owner.id)
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
            allowed_categories=["misc", "electronic"],
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
                allowed_categories=["food", "utensil", "appliance"],
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


# --------------------------------------------------------------------- items


@dataclass(slots=True)
class _DemoItem:
    """One seeded item plus where it should end up."""

    name: str
    category: str
    subcategory: str
    size: str
    room: str
    section: str
    code: str
    is_sensitive: bool = False
    needs_lock: bool = False
    description: str | None = None


# Item → (room, section, slot code). Room/section/code are resolved to real
# slot ids at seed time, so this table stays readable as the schema evolves.
# The last two have no placement on purpose: the dashboard's "未放置" counter
# and the item list both need un-placed rows to render.
DEMO_ITEMS: tuple[_DemoItem, ...] = (
    _DemoItem(
        name="马克杯",
        category="utensil",
        subcategory="杯具",
        size="small",
        room="厨房",
        section="第1层",
        code="L1S1",
        description="每天早晨用的陶瓷马克杯",
    ),
    _DemoItem(
        name="玻璃花瓶",
        category="decor",
        subcategory="摆件",
        size="medium",
        room="客厅",
        section="左玻璃柜",
        code="L1",
        description="朋友送的手工玻璃花瓶",
    ),
    _DemoItem(
        name="纸巾收纳箱",
        category="misc",
        subcategory="杂物",
        size="medium",
        room="客厅",
        section="下柜",
        code="C1",
    ),
    _DemoItem(
        name="处方药",
        category="medicine",
        subcategory="处方药",
        size="small",
        room="主卧",
        section="带锁抽屉",
        code="LK1",
        is_sensitive=True,
        needs_lock=True,
        description="降压药，必须上锁并放在儿童接触不到的地方",  # noqa: RUF001
    ),
    _DemoItem(
        name="儿童退烧药",
        category="medicine",
        subcategory="儿童用药",
        size="small",
        room="儿童房",
        section="药品带锁抽屉",
        code="MLK1",
        is_sensitive=True,
        needs_lock=True,
    ),
    _DemoItem(
        name="儿童绘本",
        category="books",
        subcategory="绘本",
        size="medium",
        room="儿童房",
        section="绘本架",
        code="B1",
    ),
    _DemoItem(
        name="羽绒服",
        category="clothes",
        subcategory="外套",
        size="large",
        room="主卧",
        section="大衣区",
        code="H1",
    ),
    _DemoItem(
        name="数据线",
        category="electronic",
        subcategory="线材",
        size="small",
        room="",
        section="",
        code="",
        description="还没有找到合适的收纳位置",
    ),
    _DemoItem(
        name="空气炸锅",
        category="appliance",
        subcategory="厨电",
        size="large",
        room="",
        section="",
        code="",
        description="体积较大，等待 AI 推荐位置",  # noqa: RUF001
    ),
)


async def _ensure_item(
    session: AsyncSession, *, home_id: uuid.UUID, created_by: uuid.UUID, demo: _DemoItem, created_at: datetime
) -> Item:
    result = await session.execute(
        select(Item).where(Item.home_id == home_id, Item.name == demo.name)
    )
    item = result.scalar_one_or_none()
    if item:
        return item
    item = Item(
        home_id=home_id,
        name=demo.name,
        description=demo.description,
        category=demo.category,
        subcategory=demo.subcategory,
        estimated_size=demo.size,
        is_sensitive=demo.is_sensitive,
        needs_lock=demo.needs_lock,
        created_by=created_by,
        created_at=created_at,
    )
    session.add(item)
    await session.flush()
    logger.info("seed.item_created", name=demo.name, category=demo.category)
    return item


async def _find_slot(
    session: AsyncSession, *, home_id: uuid.UUID, room: str, section: str, code: str
) -> StorageSlot | None:
    """Resolve a (room, section, code) triple to a slot row."""
    result = await session.execute(
        select(StorageSlot)
        .join(StorageSection, StorageSection.id == StorageSlot.section_id)
        .join(StorageUnit, StorageUnit.id == StorageSection.unit_id)
        .join(Room, Room.id == StorageUnit.room_id)
        .where(
            Room.home_id == home_id,
            Room.name == room,
            StorageSection.name == section,
            StorageSlot.code == code,
        )
    )
    return result.scalar_one_or_none()


async def _ensure_placement(
    session: AsyncSession,
    *,
    item: Item,
    slot: StorageSlot,
    user_id: uuid.UUID,
    source: PlacementSource,
    removed: bool = False,
) -> None:
    result = await session.execute(
        select(ItemPlacement).where(
            ItemPlacement.item_id == item.id, ItemPlacement.slot_id == slot.id
        )
    )
    if result.scalars().first() is not None:
        return
    now = utc_now()
    session.add(
        ItemPlacement(
            item_id=item.id,
            slot_id=slot.id,
            placed_by=user_id,
            source=source.value,
            removed_at=now if removed else None,
        )
    )
    await session.flush()


async def _seed_demo_items(
    session: AsyncSession, *, home_id: uuid.UUID, user_id: uuid.UUID
) -> None:
    """Create the demo item list + placements (idempotent)."""
    # Staggered timestamps so "最近添加" has a deterministic order.
    base = datetime(2026, 9, 1, 9, 0, tzinfo=UTC)
    for index, demo in enumerate(DEMO_ITEMS):
        item = await _ensure_item(
            session,
            home_id=home_id,
            created_by=user_id,
            demo=demo,
            created_at=base + timedelta(hours=index),
        )
        if not demo.room:
            continue
        slot = await _find_slot(
            session, home_id=home_id, room=demo.room, section=demo.section, code=demo.code
        )
        if slot is None:
            logger.warning(
                "seed.item_slot_missing",
                item=demo.name,
                room=demo.room,
                section=demo.section,
                code=demo.code,
            )
            continue
        await _ensure_placement(
            session,
            item=item,
            slot=slot,
            user_id=user_id,
            source=PlacementSource.AI_RECOMMENDATION,
        )

    # One historical (removed) placement so the item detail page has a
    # non-trivial history to render.
    mug = (
        await session.execute(
            select(Item).where(Item.home_id == home_id, Item.name == "马克杯")
        )
    ).scalar_one_or_none()
    if mug is not None:
        old_slot = await _find_slot(
            session, home_id=home_id, room="客厅", section="下柜", code="C2"
        )
        if old_slot is not None:
            await _ensure_placement(
                session,
                item=mug,
                slot=old_slot,
                user_id=user_id,
                source=PlacementSource.USER_MANUAL,
                removed=True,
            )


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
        await _seed_demo_items(session, home_id=home.id, user_id=user.id)

    logger.info("seed.done")


def main() -> None:
    asyncio.run(seed())


if __name__ == "__main__":
    main()
