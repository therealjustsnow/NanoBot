"""
Command-level tests for cogs/inventory/ under dpytest (parse → check → DB →
reply) — plus the effect-stacking cap regression from the release audit.
"""

import pytest
from discord.ext import test as dpytest

import utils.db as db
from cogs.inventory.constants import EFFECT_MAX_DURATION, EFFECT_MAX_USES
from tests.conftest import config


@pytest.mark.cogs("cogs.inventory")
async def test_bare_inventory_empty_message(bot):
    author = config().members[0]
    await dpytest.message("!inventory", member=author)
    sent = dpytest.get_message()
    assert "empty" in sent.embeds[0].description.lower()


@pytest.mark.cogs("cogs.inventory")
async def test_use_timed_effect_grants_and_consumes(bot):
    guild = config().guilds[0]
    author = config().members[0]
    await db.add_item(author.id, "lucky_charm", 2)

    await dpytest.message("!inventory use lucky_charm", member=author)
    sent = dpytest.get_message()
    assert "luck" in sent.embeds[0].description
    assert await db.get_item_qty(author.id, "lucky_charm") == 1
    effs = await db.get_active_effects(author.id)
    assert effs["luck"]["magnitude"] == 0.5


@pytest.mark.cogs("cogs.inventory")
async def test_use_bulk_timed_effect_capped_at_duration_ceiling(bot):
    """Audit regression: qty × duration must not exceed EFFECT_MAX_DURATION,
    and only the clamped qty may be consumed (no items eaten for nothing)."""
    guild = config().guilds[0]
    author = config().members[0]
    await db.add_item(author.id, "lucky_charm", 1000)

    await dpytest.message("!inventory use lucky_charm 1000", member=author)
    dpytest.get_message()
    effs = await db.get_active_effects(author.id)
    granted = effs["luck"]["expires_at"] - __import__("time").time()
    assert granted <= EFFECT_MAX_DURATION + 5
    # lucky_charm is 1800s a use → cap allows 48; the other 952 stay owned.
    expected_used = EFFECT_MAX_DURATION // 1800
    assert await db.get_item_qty(author.id, "lucky_charm") == 1000 - expected_used


@pytest.mark.cogs("cogs.inventory")
async def test_effect_uses_cap_constant_sane():
    # Charge cap guards the same stacking hole for charge-based items.
    assert 0 < EFFECT_MAX_USES <= 100


@pytest.mark.cogs("cogs.inventory")
async def test_give_rejects_self(bot):
    guild = config().guilds[0]
    author = config().members[0]
    await db.add_item(author.id, "lucky_charm", 1)
    await dpytest.message(
        f"!inventory give {author.mention} lucky_charm", member=author
    )
    sent = dpytest.get_message()
    assert "Error" in sent.embeds[0].title
    assert await db.get_item_qty(author.id, "lucky_charm") == 1


@pytest.mark.cogs("cogs.inventory")
async def test_give_transfers_between_members(bot):
    guild = config().guilds[0]
    author, other = config().members[0], config().members[1]
    await db.add_item(author.id, "lucky_charm", 3)
    await dpytest.message(
        f"!inventory give {other.mention} lucky_charm 2", member=author
    )
    dpytest.get_message()
    assert await db.get_item_qty(author.id, "lucky_charm") == 1
    assert await db.get_item_qty(other.id, "lucky_charm") == 2


@pytest.mark.cogs("cogs.inventory")
async def test_sell_unsellable_item_refused(bot):
    guild = config().guilds[0]
    author = config().members[0]
    await db.add_item(author.id, "treasure_key", 1)
    await dpytest.message("!inventory sell treasure_key", member=author)
    sent = dpytest.get_message()
    assert "can't be sold" in sent.embeds[0].description
    assert await db.get_item_qty(author.id, "treasure_key") == 1


@pytest.mark.cogs("cogs.inventory")
async def test_chest_requires_key(bot):
    guild = config().guilds[0]
    author = config().members[0]
    await db.add_item(author.id, "treasure_chest", 1)
    await dpytest.message("!inventory use treasure_chest", member=author)
    sent = dpytest.get_message()
    assert "Key" in sent.embeds[0].description
    assert await db.get_item_qty(author.id, "treasure_chest") == 1


@pytest.mark.cogs("cogs.inventory")
async def test_chest_opens_with_key_and_pays(bot):
    guild = config().guilds[0]
    author = config().members[0]
    await db.add_item(author.id, "treasure_chest", 1)
    await db.add_item(author.id, "treasure_key", 1)
    await dpytest.message("!inventory use treasure_chest", member=author)
    sent = dpytest.get_message()
    assert "Opened" in sent.embeds[0].title
    assert await db.get_item_qty(author.id, "treasure_chest") == 0
    assert await db.get_item_qty(author.id, "treasure_key") == 0
    assert await db.get_balance(author.id) >= 250
