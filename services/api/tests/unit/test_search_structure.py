"""Pure tests for the home-storage blueprint.

Regression origin: asked 「我家有几个柜子？」 the agent had no structure-centric
intent, so the model filled ``query="柜子"`` and the answer came back as
「目前只查到1个柜子」 — the name of an *item* that happens to contain 柜子.
「我家一共有几个房间？」 did worse: ``intent=unknown`` plus a flat refusal to
count rooms, although ``get_rooms`` answers it in one query.

These tests pin the artifact that makes those questions answerable.
"""
from __future__ import annotations

from app.agents.search.structure import (
    ROOM_TYPE_LABEL,
    UNIT_TYPE_LABEL,
    build_home_blueprint,
)


def _room(rid: str, name: str, room_type: str) -> dict:
    return {"id": rid, "name": name, "room_type": room_type}


def _unit(uid: str, room_id: str, name: str, unit_type: str) -> dict:
    return {"id": uid, "room_id": room_id, "name": name, "unit_type": unit_type}


def _section(sid: str, unit_id: str, name: str) -> dict:
    return {"id": sid, "unit_id": unit_id, "name": name}


def _slot(
    slot_id: str, section_id: str, label: str, *, count: int = 0,
    room: str = "", unit: str = "", unit_type: str = "cabinet",
) -> dict:
    return {
        "id": slot_id,
        "section_id": section_id,
        "code": "X1",
        "label": label,
        "room_name": room,
        "unit_name": unit,
        "unit_type": unit_type,
        "active_count": count,
    }


def _home() -> dict:
    """Two rooms: 客厅 holds a 3-position cabinet, 主卧 a single-position drawer
    cabinet — the smallest hierarchy that exercises every branch."""
    return {
        "rooms": [
            _room("r1", "客厅", "living"),
            _room("r2", "主卧", "bedroom"),
        ],
        "units": [
            _unit("u1", "r1", "客厅装饰柜", "cabinet"),
            _unit("u2", "r2", "主卧衣柜", "drawer_cabinet"),
        ],
        "sections": [
            _section("s1", "u1", "左玻璃柜"),
            _section("s2", "u1", "下柜"),
            _section("s3", "u2", "大衣区"),
        ],
        "slots": [
            _slot("p1", "s1", "左玻璃柜第1层", count=1, room="客厅", unit="客厅装饰柜"),
            _slot("p2", "s1", "左玻璃柜第2层", room="客厅", unit="客厅装饰柜"),
            _slot("p3", "s2", "下柜第1格", count=2, room="客厅", unit="客厅装饰柜"),
            _slot(
                "p4", "s3", "大衣区", room="主卧", unit="主卧衣柜",
                unit_type="drawer_cabinet",
            ),
        ],
    }


def _render(**overrides: object) -> str:
    return build_home_blueprint(**{**_home(), **overrides}).text  # type: ignore[arg-type]


# ---------------------------------------------------------------- headline counts


def test_overview_counts_rooms_furniture_and_positions_by_type() -> None:
    """「我家有几个柜子？」 must be answerable from the totals line alone."""
    text = _render()
    assert "房间 2 个" in text
    assert "收纳家具 2 件" in text
    assert f"{UNIT_TYPE_LABEL['cabinet']} 1 件" in text
    assert f"{UNIT_TYPE_LABEL['drawer_cabinet']} 1 件" in text
    assert "收纳分区 3 处" in text
    assert "收纳位 4 个" in text


def test_occupancy_is_the_sum_of_active_counts_plus_what_is_left() -> None:
    """「我家收纳空间够用吗？」 needs both the used and the free count."""
    text = _render()
    assert "已放入物品 3 件" in text
    assert "空余 1 个" in text


def test_never_free_is_reported_as_zero_not_negative() -> None:
    """A slot can hold several placements, so placed may exceed the slot count."""
    home = _home()
    home["slots"] = [dict(s, active_count=5) for s in home["slots"]]
    text = build_home_blueprint(**home).text
    assert "空余 0 个" in text
    assert "空余 -" not in text


# ---------------------------------------------------------------- labels


def test_type_words_are_chinese_never_the_ascii_enum() -> None:
    """`cabinet`/`bedroom` leaking into an answer is the English bug again."""
    text = _render()
    assert "cabinet" not in text
    assert "drawer_cabinet" not in text
    assert "bedroom" not in text and "living" not in text


def test_room_type_is_annotated_only_when_the_name_does_not_already_say_it() -> None:
    text = _render()
    # 主卧 doesn't contain 卧室 → annotated; 客厅 does → plain.
    assert f"主卧（{ROOM_TYPE_LABEL['bedroom']}）" in text
    assert "客厅（客厅）" not in text


def test_slot_labels_are_used_never_the_ascii_code() -> None:
    """Slot `code` is machine identity (`X1` in this fixture) — never shown."""
    text = _render()
    assert "左玻璃柜第1层" in text
    assert "X1" not in text


def test_a_section_named_after_its_only_slot_is_not_repeated() -> None:
    """「大衣区（大衣区）」 is noise; 「下柜（下柜第1格）」 is information."""
    text = _render()
    assert "大衣区（" not in text
    assert "下柜（下柜第1格）" in text


# ---------------------------------------------------------------- shape


def test_every_room_and_furniture_item_appears() -> None:
    text = _render()
    for name in ("客厅", "主卧", "客厅装饰柜", "主卧衣柜"):
        assert name in text


def test_a_room_with_no_furniture_is_announced_not_silently_dropped() -> None:
    """「客厅有哪些收纳？」 is legitimately answered by "none"."""
    home = _home()
    home["rooms"] = [*home["rooms"], _room("r3", "厨房", "kitchen")]
    text = build_home_blueprint(**home).text
    assert "厨房" in text
    assert "（该房间还没有收纳家具）" in text


# ---------------------------------------------------------------- empty home


def test_empty_home_yields_empty_text_and_zero_counts() -> None:
    bp = build_home_blueprint(rooms=[], units=[], sections=[], slots=[])
    assert bp.text == ""
    assert (bp.room_count, bp.unit_count) == (0, 0)


def test_units_without_rooms_are_still_described() -> None:
    """Defensive: a unit whose room row is missing must not blank the answer."""
    bp = build_home_blueprint(
        rooms=[],
        units=[_unit("u1", "r1", "孤柜", "cabinet")],
        sections=[_section("s1", "u1", "层")],
        slots=[_slot("p1", "s1", "第1层")],
    )
    assert bp.text != ""
    assert "（暂无房间记录）" in bp.text


# ---------------------------------------------------------------- caps


def test_overview_truncates_with_the_true_total_named() -> None:
    home = _home()
    home["rooms"] = [_room(f"r{i}", f"房间{i}", "other") for i in range(10)]
    text = build_home_blueprint(**home, max_rooms=2).text
    assert "共 10 个，此处仅列前 2 个" in text
    assert "其余 8 个房间未列出" in text


def test_sections_are_capped_per_unit() -> None:
    home = _home()
    home["sections"] = [_section(f"s{i}", "u1", f"分区{i}") for i in range(20)]
    home["slots"] = [_slot(f"p{i}", f"s{i}", f"格子{i}") for i in range(20)]
    text = build_home_blueprint(**home, max_sections_per_unit=3).text
    assert "等共 20 个分区" in text


def test_slots_are_capped_per_section() -> None:
    home = _home()
    home["sections"] = [_section("s1", "u1", "层")]
    home["slots"] = [_slot(f"p{i}", "s1", f"第{i}格") for i in range(10)]
    text = build_home_blueprint(**home, max_slots_per_section=2).text
    assert "第0格、第1格" in text
    assert "等 10 个" in text


# ---------------------------------------------------------------- determinism


def test_deterministic_for_the_same_input() -> None:
    """The recorded `prompt_hash` must stay comparable across runs."""
    assert _render() == _render()
