"""Shared fixtures for the unit-test package.

Re-exports ``seeded_actor`` from the root conftest so individual test files
don't need to know where it lives, and provides a small ``storage_hierarchy``
fixture for tools that need real Room/Unit/Section/Slot/Item rows.
"""
from __future__ import annotations

import base64
import uuid
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import settings
from app.db.enums import (
    RoomType,
    StorageSectionType,
    StorageUnitType,
)
from app.models.item import Item
from app.models.room import Room
from app.models.storage import StorageSection, StorageSlot, StorageUnit
from app.storage import minio_client
from app.storage.backend import reset_storage

# Re-export for convenience.
from tests.conftest import SeededActor, seeded_actor

# A 1x1 PNG. Services that inline an image (`to_data_uri`) only need *decodable*
# bytes, and a real one keeps the happy path on the same code path as production.
_PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


@pytest.fixture(autouse=True)
def _unit_storage(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Keep unit tests off the real disk and off the network.

    ``settings`` reads ``services/api/.env``, so without pinning the backend a
    developer whose ``.env`` selects local storage would get unit tests that
    read from their working directory. Pinning to MinIO and stubbing the byte
    fetch makes the suite independent of both the filesystem and credentials.
    """
    monkeypatch.setattr(settings, "storage_backend", "minio")
    monkeypatch.setattr(
        minio_client, "get_object", lambda bucket, key: _PNG_1PX
    )
    reset_storage()
    yield
    reset_storage()


@dataclass(slots=True)
class StorageHierarchy:
    """A tiny but full storage hierarchy built for one test.

    Layout (all inside the seeded home):

        客厅 (living)
          └── 客厅装饰柜 (cabinet)
                ├── 左玻璃柜 (layer, 3 slots L1/L2/L3, allowed=decor)
                ├── 右玻璃柜 (layer, 2 slots R1/R2, allowed=books)
                └── 中间开放区 (compartment, 1 slot M1, allowed=decor)
        厨房 (kitchen)
          └── 厨房吊柜 (cabinet)
                ├── 第1层 (layer, 2 slots L1S1/L1S2, allowed=utensil)
                └── 第2层 (layer, 2 slots L2S1/L2S2, allowed=food)
        主卧 (bedroom)
          └── 床头柜 (drawer_cabinet)
                └── 抽屉 (drawer, 1 slot D1, allowed=misc, needs_lock=False)

        Item: 马克杯 (kitchen/living usage, is_sensitive=False)
        Item: 处方药 (is_sensitive=True, needs_lock=True)
    """

    living_room_id: uuid.UUID
    kitchen_room_id: uuid.UUID
    bedroom_room_id: uuid.UUID
    cabinet_id: uuid.UUID  # 客厅装饰柜
    kitchen_cabinet_id: uuid.UUID
    bedside_id: uuid.UUID  # 床头柜
    slots: dict[str, uuid.UUID]  # code → id
    items: dict[str, uuid.UUID]  # name → id


@pytest_asyncio.fixture
async def storage_hierarchy(
    seeded_actor: SeededActor, db_engine
) -> AsyncIterator[StorageHierarchy]:
    """Build a small but representative storage hierarchy + 2 items.

    Persists everything in the same engine ``seeded_actor`` uses, then yields
    the result. Teardown happens implicitly because the engine is dropped
    after the test (see ``tests/conftest.py:db_engine``).
    """
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    slots: dict[str, uuid.UUID] = {}
    items: dict[str, uuid.UUID] = {}

    async with factory() as session:
        living = Room(
            id=uuid.uuid4(),
            home_id=seeded_actor.home_id,
            name="客厅",
            room_type=RoomType.LIVING.value,
            sort_order=1,
        )
        kitchen = Room(
            id=uuid.uuid4(),
            home_id=seeded_actor.home_id,
            name="厨房",
            room_type=RoomType.KITCHEN.value,
            sort_order=2,
        )
        bedroom = Room(
            id=uuid.uuid4(),
            home_id=seeded_actor.home_id,
            name="主卧",
            room_type=RoomType.BEDROOM.value,
            sort_order=3,
        )
        session.add_all([living, kitchen, bedroom])
        await session.flush()

        # 客厅装饰柜
        cabinet = StorageUnit(
            id=uuid.uuid4(),
            room_id=living.id,
            name="客厅装饰柜",
            unit_type=StorageUnitType.CABINET.value,
            sort_order=1,
        )
        # 厨房吊柜
        kcab = StorageUnit(
            id=uuid.uuid4(),
            room_id=kitchen.id,
            name="厨房吊柜",
            unit_type=StorageUnitType.CABINET.value,
            sort_order=1,
        )
        # 床头柜 (drawer_cabinet)
        bedside = StorageUnit(
            id=uuid.uuid4(),
            room_id=bedroom.id,
            name="床头柜",
            unit_type=StorageUnitType.DRAWER_CABINET.value,
            sort_order=1,
        )
        session.add_all([cabinet, kcab, bedside])
        await session.flush()

        # Sections + slots
        async def _add_slots(
            unit_id: uuid.UUID,
            name: str,
            section_type: StorageSectionType,
            slot_specs: list[tuple[str, str | None, list[str]]],
            sort: int,
        ) -> None:
            section = StorageSection(
                id=uuid.uuid4(),
                unit_id=unit_id,
                name=name,
                section_type=section_type.value,
                sort_order=sort,
            )
            session.add(section)
            await session.flush()
            # Labels mirror `app/db/seed.py`: a human-readable Chinese name,
            # never the ASCII `code`. `get_storage_slots` builds `full_path`
            # from the label, and that path is what the UI and the prompts show.
            for idx, (slot_code, slot_label, allowed) in enumerate(slot_specs, start=1):
                slot = StorageSlot(
                    id=uuid.uuid4(),
                    section_id=section.id,
                    code=slot_code,
                    label=slot_label or name,
                    allowed_categories=allowed,
                    sort_order=idx,
                )
                session.add(slot)
                slots[slot_code] = slot.id

        await _add_slots(
            cabinet.id,
            "左玻璃柜",
            StorageSectionType.LAYER,
            [
                ("L1", "左玻璃柜第1层", ["decor"]),
                ("L2", "左玻璃柜第2层", ["decor"]),
                ("L3", "左玻璃柜第3层", ["decor"]),
            ],
            sort=1,
        )
        await _add_slots(
            cabinet.id,
            "右玻璃柜",
            StorageSectionType.LAYER,
            [("R1", "右玻璃柜第1层", ["books"]), ("R2", "右玻璃柜第2层", ["books"])],
            sort=2,
        )
        await _add_slots(
            cabinet.id,
            "中间开放区",
            StorageSectionType.COMPARTMENT,
            [("M1", "中间开放区", ["decor"])],
            sort=3,
        )
        await _add_slots(
            kcab.id,
            "第1层",
            StorageSectionType.LAYER,
            [
                ("L1S1", "第1层第1格", ["utensil"]),
                ("L1S2", "第1层第2格", ["utensil"]),
            ],
            sort=1,
        )
        await _add_slots(
            kcab.id,
            "第2层",
            StorageSectionType.LAYER,
            [("L2S1", "第2层第1格", ["food"]), ("L2S2", "第2层第2格", ["food"])],
            sort=2,
        )
        await _add_slots(
            bedside.id,
            "抽屉",
            StorageSectionType.DRAWER,
            [("D1", "抽屉", ["medicine", "misc"])],
            sort=1,
        )

        # Items
        cup = Item(
            id=uuid.uuid4(),
            home_id=seeded_actor.home_id,
            name="马克杯",
            category="utensil",
            subcategory="cup",
            estimated_size="small",
            is_sensitive=False,
            needs_lock=False,
            created_by=seeded_actor.user_id,
        )
        med = Item(
            id=uuid.uuid4(),
            home_id=seeded_actor.home_id,
            name="处方药",
            category="medicine",
            estimated_size="small",
            is_sensitive=True,
            needs_lock=True,
            created_by=seeded_actor.user_id,
        )
        session.add_all([cup, med])
        await session.flush()
        items["马克杯"] = cup.id
        items["处方药"] = med.id
        await session.commit()

    yield StorageHierarchy(
        living_room_id=living.id,
        kitchen_room_id=kitchen.id,
        bedroom_room_id=bedroom.id,
        cabinet_id=cabinet.id,
        kitchen_cabinet_id=kcab.id,
        bedside_id=bedside.id,
        slots=slots,
        items=items,
    )


def slot_dicts_by_code(dicts: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Index ``get_storage_slots`` results by their slot code for easy asserts."""
    return {d["code"]: d for d in dicts}


__all__ = ["StorageHierarchy", "seeded_actor", "slot_dicts_by_code"]
