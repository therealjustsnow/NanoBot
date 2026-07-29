"""
Tests for utils/consumables.py — the shared "arm an item" layer — and for
`helpers.split_targets`, the one-option-many-picks parser both the inventory and
the tackle shop read their targets through.

The db half runs against in-memory SQLite (the tests/test_items_db.py pattern);
everything else is pure.
"""

import aiosqlite
import pytest

import utils.db as db
from utils import consumables
from utils import helpers as h
from utils import items as item_catalog

# Loading these registers the bait/consumable/ore defs the tests use.
import cogs.fishing.items  # noqa: F401
import cogs.activities.items  # noqa: F401


@pytest.fixture(autouse=True)
async def database(monkeypatch):
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA journal_mode=WAL")
    monkeypatch.setattr(db, "_db", conn)
    await db._ensure_items_tables()
    yield conn
    await conn.close()


A = 4001


# ── the stacking ceiling ─────────────────────────────────────────────────────
def test_a_single_item_is_always_usable_however_long_it_lasts():
    """The cap limits stacking, not the item: an item whose own duration already
    exceeds the ceiling must still be usable once."""
    long_one = item_catalog.ItemDef(
        key="_test_long",
        name="Long One",
        emoji="⏳",
        category="consumable",
        effect={
            "key": "luck",
            "magnitude": 0.5,
            "duration": consumables.EFFECT_MAX_DURATION * 3,
        },
    )
    assert consumables.clamp_qty(long_one, 1) == 1
    assert consumables.clamp_qty(long_one, 50) == 1


def test_bulk_use_is_clamped_to_the_duration_and_charge_ceilings():
    charm = item_catalog.get("lucky_charm")
    assert charm is not None
    duration = charm.effect["duration"]
    assert (
        consumables.clamp_qty(charm, 10_000)
        == consumables.EFFECT_MAX_DURATION // duration
    )

    bait = item_catalog.get("bait_worm")
    per_use = bait.effect["uses"]
    assert consumables.clamp_qty(bait, 10_000) == consumables.EFFECT_MAX_USES // per_use


def test_effect_grant_multiplies_only_the_half_the_item_has():
    charm = item_catalog.get("lucky_charm")
    duration, uses = consumables.effect_grant(charm, 3)
    assert duration == charm.effect["duration"] * 3 and uses == 0

    bait = item_catalog.get("bait_worm")
    duration, uses = consumables.effect_grant(bait, 2)
    assert uses == bait.effect["uses"] * 2 and duration == 0


def test_usable_covers_chests_and_rejects_materials():
    assert consumables.usable(item_catalog.get("treasure_chest")) is True
    assert consumables.usable(item_catalog.get("lucky_charm")) is True
    assert consumables.usable(item_catalog.get("iron_ore")) is False
    assert consumables.usable(None) is False


# ── arming ───────────────────────────────────────────────────────────────────
async def test_use_item_consumes_the_stack_and_writes_the_effect():
    await db.add_item(A, "lucky_charm", 2)

    result = await consumables.use_item(A, "lucky_charm", 1)

    assert result.ok and result.effect_key == "luck"
    assert await db.get_item_qty(A, "lucky_charm") == 1
    assert (await db.get_active_effects(A))["luck"]["magnitude"] == 0.5


async def test_use_item_reports_short_without_granting_anything():
    """The consume is the atomic one, so asking for more than you own must not
    leave a buff nobody paid for."""
    await db.add_item(A, "lucky_charm", 1)

    result = await consumables.use_item(A, "lucky_charm", 2)

    assert result.reason == "short" and result.have == 1
    assert await db.get_active_effects(A) == {}
    assert await db.get_item_qty(A, "lucky_charm") == 1


async def test_use_item_refuses_an_unknown_key_and_an_unusable_item():
    assert (await consumables.use_item(A, "not_a_real_item")).reason == "unknown"
    await db.add_item(A, "iron_ore", 1)
    assert (await consumables.use_item(A, "iron_ore")).reason == "not_usable"
    assert await db.get_item_qty(A, "iron_ore") == 1


async def test_owned_usable_lists_only_gear_in_catalogue_order():
    await db.add_item(A, "iron_ore", 5)  # sellable, not usable
    await db.add_item(A, "lucky_charm", 1)
    await db.add_item(A, "bait_worm", 3)

    gear = await consumables.owned_usable(A)

    keys = [row["item"].key for row in gear]
    assert "iron_ore" not in keys
    assert set(keys) == {"lucky_charm", "bait_worm"}
    # Bait sorts before consumables, as it does everywhere else in the bot.
    assert keys[0] == "bait_worm"
    assert dict((r["item"].key, r["qty"]) for r in gear)["bait_worm"] == 3


# ── one option, several picks ─────────────────────────────────────────────────
def test_split_targets_reads_a_list_and_leaves_a_single_name_alone():
    assert h.split_targets("lucky charm") == ["lucky charm"]
    assert h.split_targets("glowgrub, lucky charm") == ["glowgrub", "lucky charm"]
    # A phone keyboard offers a semicolon first, so it counts too.
    assert h.split_targets("a; b ,c") == ["a", "b", "c"]


def test_split_targets_drops_blanks_and_caps_the_list():
    assert h.split_targets("") == []
    assert h.split_targets("  ,  ,  ") == []
    assert h.split_targets(",".join(str(n) for n in range(50)), 4) == [
        "0",
        "1",
        "2",
        "3",
    ]
