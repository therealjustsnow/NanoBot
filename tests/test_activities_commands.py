"""
Command-level tests for cogs/activities/ under dpytest (parse → check → DB → reply).
"""

import pytest
from discord.ext import commands
from discord.ext import test as dpytest

import utils.db as db
from cogs.activities import (
    EXPLORE_COINS_SMALL,
    EXPLORE_OUTCOMES,
    PICKAXES,
    ROB_FINE,
    pick_ore,
    rob_steal_amount,
    roll_work_pay,
)
from tests.conftest import config, grant_perms


def _roll_for_outcome(target: str) -> float:
    """A roll in [0, 1) that lands pick_explore_outcome on `target`."""
    acc = 0.0
    for key, p in EXPLORE_OUTCOMES:
        if key == target:
            return acc + p / 2
        acc += p
    raise ValueError(target)


# ══════════════════════════════════════════════════════════════════════════════
#  /work
# ══════════════════════════════════════════════════════════════════════════════
@pytest.mark.cogs("cogs.activities")
async def test_work_pays_and_blocks_second_shift(bot, monkeypatch):
    from cogs.activities import cog as activities

    guild = config().guilds[0]
    author = config().members[0]
    monkeypatch.setattr(activities.random, "random", lambda: 0.5)

    await dpytest.message("!work", member=author)
    sent = dpytest.get_message()
    assert sent.embeds
    expected_pay = roll_work_pay(0.5, 0)
    assert await db.get_balance(guild.id, author.id) == expected_pay

    await dpytest.message("!work", member=author)
    sent = dpytest.get_message()
    assert "Not Yet" in sent.embeds[0].title


@pytest.mark.cogs("cogs.activities")
async def test_work_disabled(bot):
    guild = config().guilds[0]
    author = config().members[0]
    await db.set_activities_config(guild.id, work_enabled=False)

    await dpytest.message("!work", member=author)
    sent = dpytest.get_message()
    assert "disabled" in sent.embeds[0].description


# ══════════════════════════════════════════════════════════════════════════════
#  /mine
# ══════════════════════════════════════════════════════════════════════════════
@pytest.mark.cogs("cogs.activities")
async def test_mine_dig_adds_ore_and_blocks_second_dig(bot, monkeypatch):
    from cogs.activities import cog as activities

    guild = config().guilds[0]
    author = config().members[0]
    # 0.5: no cave-in (>=0.08), no bonus key (>=0.05); deterministic ore pick.
    monkeypatch.setattr(activities.random, "random", lambda: 0.5)
    expected_key = pick_ore(0.5, 0.0)

    await dpytest.message("!mine", member=author)
    sent = dpytest.get_message()
    assert "Dig" in sent.embeds[0].title
    assert await db.get_item_qty(guild.id, author.id, expected_key) == 1

    await dpytest.message("!mine dig", member=author)
    sent = dpytest.get_message()
    assert "Not Yet" in sent.embeds[0].title
    assert await db.get_item_qty(guild.id, author.id, expected_key) == 1


@pytest.mark.cogs("cogs.activities")
async def test_mine_cave_in_yields_nothing(bot, monkeypatch):
    from cogs.activities import cog as activities

    guild = config().guilds[0]
    author = config().members[0]
    monkeypatch.setattr(activities.random, "random", lambda: 0.01)  # cave-in

    await dpytest.message("!mine", member=author)
    sent = dpytest.get_message()
    assert "Cave-In" in sent.embeds[0].title
    assert await db.get_inventory(guild.id, author.id) == []


@pytest.mark.cogs("cogs.activities")
async def test_mine_disabled(bot):
    guild = config().guilds[0]
    author = config().members[0]
    await db.set_activities_config(guild.id, mine_enabled=False)

    await dpytest.message("!mine", member=author)
    sent = dpytest.get_message()
    assert "disabled" in sent.embeds[0].description


@pytest.mark.cogs("cogs.activities")
async def test_mine_upgrade_charges_and_advances(bot):
    guild = config().guilds[0]
    author = config().members[0]
    await db.add_coins(guild.id, author.id, 600)

    await dpytest.message("!mine upgrade", member=author)
    sent = dpytest.get_message()
    assert "Upgraded" in sent.embeds[0].title
    assert (await db.get_activity_stats(guild.id, author.id))["pickaxe_level"] == 1
    assert await db.get_balance(guild.id, author.id) == 600 - PICKAXES[1]["price"]


@pytest.mark.cogs("cogs.activities")
async def test_mine_upgrade_insufficient_funds(bot):
    guild = config().guilds[0]
    author = config().members[0]
    await db.add_coins(guild.id, author.id, 10)

    await dpytest.message("!mine upgrade", member=author)
    sent = dpytest.get_message()
    assert "Error" in sent.embeds[0].title
    assert (await db.get_activity_stats(guild.id, author.id))["pickaxe_level"] == 0
    assert await db.get_balance(guild.id, author.id) == 10


# ══════════════════════════════════════════════════════════════════════════════
#  /hunt
# ══════════════════════════════════════════════════════════════════════════════
@pytest.mark.cogs("cogs.activities")
async def test_hunt_catch_only(bot, monkeypatch):
    from cogs.activities import cog as activities

    guild = config().guilds[0]
    author = config().members[0]
    # 0.5 for every roll: pelt (< .57 cumulative), no injury (>= .12), no padlock (>= .06).
    monkeypatch.setattr(activities.random, "random", lambda: 0.5)

    await dpytest.message("!hunt", member=author)
    sent = dpytest.get_message()
    assert sent.embeds
    assert await db.get_item_qty(guild.id, author.id, "pelt") == 1
    assert await db.get_item_qty(guild.id, author.id, "padlock") == 0
    assert await db.get_balance(guild.id, author.id) == 0


@pytest.mark.cogs("cogs.activities")
async def test_hunt_injury_deducts_fine(bot, monkeypatch):
    from cogs.activities import cog as activities

    guild = config().guilds[0]
    author = config().members[0]
    await db.add_coins(guild.id, author.id, 100)
    rolls = iter([0.5, 0.05, 0.4, 0.9])  # catch, injury(<.12), fine roll, no padlock
    monkeypatch.setattr(activities.random, "random", lambda: next(rolls))

    await dpytest.message("!hunt", member=author)
    sent = dpytest.get_message()
    assert "tumble" in sent.embeds[0].description
    assert await db.get_balance(guild.id, author.id) == 100 - round(0.4 * 50)


@pytest.mark.cogs("cogs.activities")
async def test_hunt_padlock_found(bot, monkeypatch):
    from cogs.activities import cog as activities

    guild = config().guilds[0]
    author = config().members[0]
    rolls = iter([0.5, 0.9, 0.02])  # catch, no injury (>=.12), padlock (<.06)
    monkeypatch.setattr(activities.random, "random", lambda: next(rolls))

    await dpytest.message("!hunt", member=author)
    assert await db.get_item_qty(guild.id, author.id, "padlock") == 1


@pytest.mark.cogs("cogs.activities")
async def test_hunt_disabled(bot):
    guild = config().guilds[0]
    author = config().members[0]
    await db.set_activities_config(guild.id, hunt_enabled=False)

    await dpytest.message("!hunt", member=author)
    sent = dpytest.get_message()
    assert "disabled" in sent.embeds[0].description


# ══════════════════════════════════════════════════════════════════════════════
#  /explore
# ══════════════════════════════════════════════════════════════════════════════
@pytest.mark.cogs("cogs.activities")
async def test_explore_nothing(bot, monkeypatch):
    from cogs.activities import cog as activities

    guild = config().guilds[0]
    author = config().members[0]
    monkeypatch.setattr(
        activities.random, "random", lambda: _roll_for_outcome("nothing")
    )

    await dpytest.message("!explore", member=author)
    sent = dpytest.get_message()
    assert "Explore" in sent.embeds[0].title
    assert await db.get_balance(guild.id, author.id) == 0


@pytest.mark.cogs("cogs.activities")
async def test_explore_coins_small(bot, monkeypatch):
    from cogs.activities import cog as activities

    guild = config().guilds[0]
    author = config().members[0]
    outcome_roll = _roll_for_outcome("coins_small")
    rolls = iter([outcome_roll, 0.5])
    monkeypatch.setattr(activities.random, "random", lambda: next(rolls))

    await dpytest.message("!explore", member=author)
    expected = round(
        EXPLORE_COINS_SMALL[0] + 0.5 * (EXPLORE_COINS_SMALL[1] - EXPLORE_COINS_SMALL[0])
    )
    assert await db.get_balance(guild.id, author.id) == expected


@pytest.mark.cogs("cogs.activities")
async def test_explore_item_reward(bot, monkeypatch):
    from cogs.activities import cog as activities

    guild = config().guilds[0]
    author = config().members[0]
    monkeypatch.setattr(
        activities.random, "random", lambda: _roll_for_outcome("treasure_key")
    )

    await dpytest.message("!explore", member=author)
    assert await db.get_item_qty(guild.id, author.id, "treasure_key") == 1


@pytest.mark.cogs("cogs.activities")
async def test_explore_disabled(bot):
    guild = config().guilds[0]
    author = config().members[0]
    await db.set_activities_config(guild.id, explore_enabled=False)

    await dpytest.message("!explore", member=author)
    sent = dpytest.get_message()
    assert "disabled" in sent.embeds[0].description


# ══════════════════════════════════════════════════════════════════════════════
#  /rob
# ══════════════════════════════════════════════════════════════════════════════
@pytest.mark.cogs("cogs.activities")
async def test_rob_disabled(bot):
    guild = config().guilds[0]
    author, target = config().members[0], config().members[1]
    await db.set_activities_config(guild.id, rob_enabled=False)
    await db.add_coins(guild.id, author.id, 500)
    await db.add_coins(guild.id, target.id, 1000)

    await dpytest.message(f"!rob {target.mention}", member=author)
    sent = dpytest.get_message()
    assert "disabled" in sent.embeds[0].description


@pytest.mark.cogs("cogs.activities")
async def test_rob_self_rejected(bot):
    author = config().members[0]
    await db.add_coins(config().guilds[0].id, author.id, 500)

    await dpytest.message(f"!rob {author.mention}", member=author)
    sent = dpytest.get_message()
    assert "yourself" in sent.embeds[0].description


@pytest.mark.cogs("cogs.activities")
async def test_rob_target_shielded(bot):
    guild = config().guilds[0]
    author, target = config().members[0], config().members[1]
    await db.add_coins(guild.id, author.id, 500)
    await db.add_coins(guild.id, target.id, 1000)
    await db.grant_effect(guild.id, target.id, "rob_shield", 1, duration=3600)

    await dpytest.message(f"!rob {target.mention}", member=author)
    sent = dpytest.get_message()
    assert "Shielded" in sent.embeds[0].title
    # A guard failure doesn't burn the cooldown — the same attempt is refused
    # again immediately with the same reason, not a "Not Yet" cooldown message.
    await dpytest.message(f"!rob {target.mention}", member=author)
    sent = dpytest.get_message()
    assert "Shielded" in sent.embeds[0].title


@pytest.mark.cogs("cogs.activities")
async def test_rob_robber_balance_too_low(bot):
    guild = config().guilds[0]
    author, target = config().members[0], config().members[1]
    await db.add_coins(guild.id, target.id, 1000)

    await dpytest.message(f"!rob {target.mention}", member=author)
    sent = dpytest.get_message()
    assert "Error" in sent.embeds[0].title
    assert "at least" in sent.embeds[0].description


@pytest.mark.cogs("cogs.activities")
async def test_rob_target_balance_too_low(bot):
    guild = config().guilds[0]
    author, target = config().members[0], config().members[1]
    await db.add_coins(guild.id, author.id, 500)
    await db.add_coins(guild.id, target.id, 10)

    await dpytest.message(f"!rob {target.mention}", member=author)
    sent = dpytest.get_message()
    assert "Error" in sent.embeds[0].title
    assert "worth robbing" in sent.embeds[0].description


@pytest.mark.cogs("cogs.activities")
async def test_rob_success_steals_coins(bot, monkeypatch):
    from cogs.activities import cog as activities

    guild = config().guilds[0]
    author, target = config().members[0], config().members[1]
    await db.add_coins(guild.id, author.id, 500)
    await db.add_coins(guild.id, target.id, 2000)
    rolls = iter([0.1, 0.5])  # success roll (<.35), steal-amount roll
    monkeypatch.setattr(activities.random, "random", lambda: next(rolls))

    await dpytest.message(f"!rob {target.mention}", member=author)
    sent = dpytest.get_message()
    assert "Heist" in sent.embeds[0].title
    stolen = rob_steal_amount(0.5, 2000)
    assert await db.get_balance(guild.id, author.id) == 500 + stolen
    assert await db.get_balance(guild.id, target.id) == 2000 - stolen


@pytest.mark.cogs("cogs.activities")
async def test_rob_failure_pays_fine(bot, monkeypatch):
    from cogs.activities import cog as activities

    guild = config().guilds[0]
    author, target = config().members[0], config().members[1]
    await db.add_coins(guild.id, author.id, 500)
    await db.add_coins(guild.id, target.id, 2000)
    monkeypatch.setattr(activities.random, "random", lambda: 0.9)  # fail (>=.35)

    await dpytest.message(f"!rob {target.mention}", member=author)
    sent = dpytest.get_message()
    assert "Caught" in sent.embeds[0].title
    assert await db.get_balance(guild.id, author.id) == 500 - ROB_FINE
    assert await db.get_balance(guild.id, target.id) == 2000


@pytest.mark.cogs("cogs.activities")
async def test_rob_cooldown_consumed_after_attempt(bot, monkeypatch):
    from cogs.activities import cog as activities

    guild = config().guilds[0]
    author, target = config().members[0], config().members[1]
    await db.add_coins(guild.id, author.id, 500)
    await db.add_coins(guild.id, target.id, 2000)
    monkeypatch.setattr(activities.random, "random", lambda: 0.9)  # fail

    await dpytest.message(f"!rob {target.mention}", member=author)
    dpytest.get_message()  # discard the first (failed) attempt's reply
    await dpytest.message(f"!rob {target.mention}", member=author)
    sent = dpytest.get_message()
    assert "Not Yet" in sent.embeds[0].title


# ══════════════════════════════════════════════════════════════════════════════
#  /activities admin group
# ══════════════════════════════════════════════════════════════════════════════
@pytest.mark.cogs("cogs.activities")
async def test_activities_bare_denied_without_manage_guild(bot):
    author = config().members[0]
    with pytest.raises(commands.MissingPermissions):
        await dpytest.message("!activities", member=author)


@pytest.mark.cogs("cogs.activities")
async def test_activities_toggle_denied_without_manage_guild(bot):
    author = config().members[0]
    with pytest.raises(commands.MissingPermissions):
        await dpytest.message("!activities toggle rob", member=author)


@pytest.mark.cogs("cogs.activities")
async def test_activities_toggle_flips_with_perms(bot):
    guild = config().guilds[0]
    author = config().members[0]
    await grant_perms(author, manage_guild=True)

    await dpytest.message("!activities toggle rob", member=author)
    sent = dpytest.get_message()
    assert "disabled" in sent.embeds[0].description
    assert (await db.get_activities_config(guild.id))["rob_enabled"] is False

    await dpytest.message("!activities toggle rob", member=author)
    sent = dpytest.get_message()
    assert "enabled" in sent.embeds[0].description
    assert (await db.get_activities_config(guild.id))["rob_enabled"] is True


@pytest.mark.cogs("cogs.activities")
async def test_activities_cooldown_out_of_bounds(bot):
    guild = config().guilds[0]
    author = config().members[0]
    await grant_perms(author, manage_guild=True)

    await dpytest.message("!activities cooldown work 1", member=author)
    sent = dpytest.get_message()
    assert "Error" in sent.embeds[0].title
    assert (await db.get_activities_config(guild.id))["work_cooldown"] == 3600


@pytest.mark.cogs("cogs.activities")
async def test_activities_cooldown_sets_value(bot):
    guild = config().guilds[0]
    author = config().members[0]
    await grant_perms(author, manage_guild=True)

    await dpytest.message("!activities cooldown work 120", member=author)
    sent = dpytest.get_message()
    assert "Done" in sent.embeds[0].title or "set" in sent.embeds[0].description
    assert (await db.get_activities_config(guild.id))["work_cooldown"] == 120


@pytest.mark.cogs("cogs.activities")
async def test_activities_config_shows_settings(bot):
    author = config().members[0]
    await grant_perms(author, manage_guild=True)

    await dpytest.message("!activities config", member=author)
    sent = dpytest.get_message()
    field_names = {f.name for f in sent.embeds[0].fields}
    assert field_names == {"/work", "/mine", "/hunt", "/explore", "/rob"}
