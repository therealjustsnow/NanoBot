"""
Command-level tests for cogs/activities/ under dpytest (parse → check → DB → reply).
"""

import time
import types

import pytest
from discord.ext import commands
from discord.ext import test as dpytest

import utils.db as db
from cogs.activities import (
    EXPLORE_COINS_SMALL,
    EXPLORE_OUTCOMES,
    PICKAXES,
    ROB_FINE,
    WORK_COOLDOWN_DEFAULT,
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
    assert await db.get_balance(author.id) == expected_pay

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
    # 0.5: no cave-in (>=0.08), no bonus key (>=0.02); deterministic ore pick.
    monkeypatch.setattr(activities.random, "random", lambda: 0.5)
    expected_key = pick_ore(0.5, 0.0)

    await dpytest.message("!mine", member=author)
    sent = dpytest.get_message()
    assert "Dig" in sent.embeds[0].title
    assert await db.get_item_qty(author.id, expected_key) == 1

    await dpytest.message("!mine dig", member=author)
    sent = dpytest.get_message()
    assert "Not Yet" in sent.embeds[0].title
    assert await db.get_item_qty(author.id, expected_key) == 1


@pytest.mark.cogs("cogs.activities")
async def test_mine_cave_in_yields_nothing(bot, monkeypatch):
    from cogs.activities import cog as activities

    guild = config().guilds[0]
    author = config().members[0]
    monkeypatch.setattr(activities.random, "random", lambda: 0.01)  # cave-in

    await dpytest.message("!mine", member=author)
    sent = dpytest.get_message()
    assert "Cave-In" in sent.embeds[0].title
    assert await db.get_inventory(author.id) == []


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
    await db.add_coins(author.id, PICKAXES[1]["price"] + 100)

    await dpytest.message("!mine upgrade", member=author)
    sent = dpytest.get_message()
    assert "Upgraded" in sent.embeds[0].title
    assert (await db.get_activity_stats(author.id))["pickaxe_level"] == 1
    assert await db.get_balance(author.id) == 100


@pytest.mark.cogs("cogs.activities")
async def test_mine_upgrade_insufficient_funds(bot):
    guild = config().guilds[0]
    author = config().members[0]
    await db.add_coins(author.id, 10)

    await dpytest.message("!mine upgrade", member=author)
    sent = dpytest.get_message()
    assert "Error" in sent.embeds[0].title
    assert (await db.get_activity_stats(author.id))["pickaxe_level"] == 0
    assert await db.get_balance(author.id) == 10


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

    await dpytest.message("!adventure hunt", member=author)
    sent = dpytest.get_message()
    assert sent.embeds
    assert await db.get_item_qty(author.id, "pelt") == 1
    assert await db.get_item_qty(author.id, "padlock") == 0
    assert await db.get_balance(author.id) == 0


@pytest.mark.cogs("cogs.activities")
async def test_hunt_injury_deducts_fine(bot, monkeypatch):
    from cogs.activities import cog as activities

    guild = config().guilds[0]
    author = config().members[0]
    await db.add_coins(author.id, 100)
    rolls = iter([0.5, 0.05, 0.4, 0.9])  # catch, injury(<.12), fine roll, no padlock
    monkeypatch.setattr(activities.random, "random", lambda: next(rolls))

    await dpytest.message("!adventure hunt", member=author)
    sent = dpytest.get_message()
    assert "tumble" in sent.embeds[0].description
    assert await db.get_balance(author.id) == 100 - round(0.4 * 50)


@pytest.mark.cogs("cogs.activities")
async def test_hunt_padlock_found(bot, monkeypatch):
    from cogs.activities import cog as activities

    guild = config().guilds[0]
    author = config().members[0]
    rolls = iter([0.5, 0.9, 0.02])  # catch, no injury (>=.12), padlock (<.06)
    monkeypatch.setattr(activities.random, "random", lambda: next(rolls))

    await dpytest.message("!adventure hunt", member=author)
    assert await db.get_item_qty(author.id, "padlock") == 1


@pytest.mark.cogs("cogs.activities")
async def test_hunt_disabled(bot):
    guild = config().guilds[0]
    author = config().members[0]
    await db.set_activities_config(guild.id, hunt_enabled=False)

    await dpytest.message("!adventure hunt", member=author)
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

    await dpytest.message("!adventure explore", member=author)
    sent = dpytest.get_message()
    assert "Explore" in sent.embeds[0].title
    assert await db.get_balance(author.id) == 0


@pytest.mark.cogs("cogs.activities")
async def test_explore_coins_small(bot, monkeypatch):
    from cogs.activities import cog as activities

    guild = config().guilds[0]
    author = config().members[0]
    outcome_roll = _roll_for_outcome("coins_small")
    rolls = iter([outcome_roll, 0.5])
    monkeypatch.setattr(activities.random, "random", lambda: next(rolls))

    await dpytest.message("!adventure explore", member=author)
    expected = round(
        EXPLORE_COINS_SMALL[0] + 0.5 * (EXPLORE_COINS_SMALL[1] - EXPLORE_COINS_SMALL[0])
    )
    assert await db.get_balance(author.id) == expected


@pytest.mark.cogs("cogs.activities")
async def test_explore_item_reward(bot, monkeypatch):
    from cogs.activities import cog as activities

    guild = config().guilds[0]
    author = config().members[0]
    monkeypatch.setattr(
        activities.random, "random", lambda: _roll_for_outcome("treasure_key")
    )

    await dpytest.message("!adventure explore", member=author)
    assert await db.get_item_qty(author.id, "treasure_key") == 1


@pytest.mark.cogs("cogs.activities")
async def test_explore_disabled(bot):
    guild = config().guilds[0]
    author = config().members[0]
    await db.set_activities_config(guild.id, explore_enabled=False)

    await dpytest.message("!adventure explore", member=author)
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
    await db.add_coins(author.id, 500)
    await db.add_coins(target.id, 1000)

    await dpytest.message(f"!rob {target.mention}", member=author)
    sent = dpytest.get_message()
    assert "disabled" in sent.embeds[0].description


@pytest.mark.cogs("cogs.activities")
async def test_rob_self_rejected(bot):
    author = config().members[0]
    await db.add_coins(author.id, 500)

    await dpytest.message(f"!rob {author.mention}", member=author)
    sent = dpytest.get_message()
    assert "yourself" in sent.embeds[0].description


@pytest.mark.cogs("cogs.activities")
async def test_rob_target_shielded(bot):
    guild = config().guilds[0]
    author, target = config().members[0], config().members[1]
    await db.add_coins(author.id, 500)
    await db.add_coins(target.id, 1000)
    await db.grant_effect(target.id, "rob_shield", 1, duration=3600)

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
    await db.add_coins(target.id, 1000)

    await dpytest.message(f"!rob {target.mention}", member=author)
    sent = dpytest.get_message()
    assert "Error" in sent.embeds[0].title
    assert "at least" in sent.embeds[0].description


@pytest.mark.cogs("cogs.activities")
async def test_rob_target_balance_too_low(bot):
    guild = config().guilds[0]
    author, target = config().members[0], config().members[1]
    await db.add_coins(author.id, 500)
    await db.add_coins(target.id, 10)

    await dpytest.message(f"!rob {target.mention}", member=author)
    sent = dpytest.get_message()
    assert "Error" in sent.embeds[0].title
    assert "worth robbing" in sent.embeds[0].description


@pytest.mark.cogs("cogs.activities")
async def test_rob_success_steals_coins(bot, monkeypatch):
    from cogs.activities import cog as activities

    guild = config().guilds[0]
    author, target = config().members[0], config().members[1]
    await db.add_coins(author.id, 500)
    await db.add_coins(target.id, 2000)
    rolls = iter([0.1, 0.5])  # success roll (<.35), steal-amount roll
    monkeypatch.setattr(activities.random, "random", lambda: next(rolls))

    await dpytest.message(f"!rob {target.mention}", member=author)
    sent = dpytest.get_message()
    assert "Heist" in sent.embeds[0].title
    stolen = rob_steal_amount(0.5, 2000)
    assert await db.get_balance(author.id) == 500 + stolen
    assert await db.get_balance(target.id) == 2000 - stolen


@pytest.mark.cogs("cogs.activities")
async def test_rob_failure_pays_fine(bot, monkeypatch):
    from cogs.activities import cog as activities

    guild = config().guilds[0]
    author, target = config().members[0], config().members[1]
    await db.add_coins(author.id, 500)
    await db.add_coins(target.id, 2000)
    monkeypatch.setattr(activities.random, "random", lambda: 0.9)  # fail (>=.35)

    await dpytest.message(f"!rob {target.mention}", member=author)
    sent = dpytest.get_message()
    assert "Caught" in sent.embeds[0].title
    assert await db.get_balance(author.id) == 500 - ROB_FINE
    assert await db.get_balance(target.id) == 2000


@pytest.mark.cogs("cogs.activities")
async def test_rob_cooldown_consumed_after_attempt(bot, monkeypatch):
    from cogs.activities import cog as activities

    guild = config().guilds[0]
    author, target = config().members[0], config().members[1]
    await db.add_coins(author.id, 500)
    await db.add_coins(target.id, 2000)
    monkeypatch.setattr(activities.random, "random", lambda: 0.9)  # fail

    await dpytest.message(f"!rob {target.mention}", member=author)
    dpytest.get_message()  # discard the first (failed) attempt's reply
    await dpytest.message(f"!rob {target.mention}", member=author)
    sent = dpytest.get_message()
    assert "Not Yet" in sent.embeds[0].title


# ══════════════════════════════════════════════════════════════════════════════
#  /adventure group (hunt/explore + admin settings)
# ══════════════════════════════════════════════════════════════════════════════
@pytest.mark.cogs("cogs.activities")
async def test_adventure_bare_shows_member_overview(bot):
    """Bare /adventure is the member landing card — every activity, what it's
    for, and whether they're off cooldown (the admin dump is /adventure config)."""
    guild = config().guilds[0]
    author = config().members[0]
    await dpytest.message("!adventure", member=author)
    sent = dpytest.get_message()
    embed = sent.embeds[0]
    assert "Adventure" in embed.title
    activity_fields = {
        "💼 /work",
        "⛏️ /mine",
        "🏹 /adventure hunt",
        "🧭 /adventure explore",
        "🥷 /rob",
    }
    names = {f.name for f in embed.fields}
    assert activity_fields <= names
    # The dashboard leads with the two progression tracks these activities feed
    # (career + pickaxe), derived from the same stats row — no extra queries.
    assert "Progression" in names
    progression = next(f.value for f in embed.fields if f.name == "Progression")
    assert "💼" in progression and "tier 1/" in progression
    # Nothing claimed yet, so everything is ready and the headline says so.
    assert all(
        "Ready now" in f.value for f in embed.fields if f.name in activity_fields
    )
    assert "5" in embed.description and "ready right now" in embed.description
    # Lifetime counts are on the card so progress is visible without /mine stats.
    assert all(
        "Done **0×**" in f.value for f in embed.fields if f.name in activity_fields
    )

    # A used activity reports its remaining cooldown; a disabled one says so.
    await db.try_claim_activity(author.id, "work", time.time(), 3600)
    await db.set_activities_config(guild.id, rob_enabled=False)
    await dpytest.message("!adventure", member=author)
    embed = dpytest.get_message().embeds[0]
    fields = {f.name: f.value for f in embed.fields}
    assert "Ready in" in fields["💼 /work"]
    assert "Disabled" in fields["🥷 /rob"]
    # The headline drops the two that are no longer available.
    assert "3" in embed.description and "ready right now" in embed.description


@pytest.mark.cogs("cogs.activities")
@pytest.mark.asyncio
async def test_adventure_dashboard_is_reachable_as_a_slash_subcommand(bot):
    """The reported gap: /adventure's landing card was prefix-only, because a
    hybrid group's own callback isn't invocable over slash without `fallback`.
    Guard the app-command child by name, and that it runs."""
    group = bot.get_command("adventure")
    assert isinstance(group, commands.HybridGroup)
    assert group.fallback == "dashboard"
    assert "dashboard" in {c.name for c in group.app_command.commands}

    # The fallback name is also a prefix alias for the same card.
    await dpytest.message("!adventure dashboard", member=config().members[0])
    assert "Adventure" in dpytest.get_message().embeds[0].title


@pytest.mark.cogs("cogs.activities")
async def test_activities_toggle_denied_without_manage_guild(bot):
    author = config().members[0]
    with pytest.raises(commands.MissingPermissions):
        await dpytest.message("!adventure toggle rob", member=author)


@pytest.mark.cogs("cogs.activities")
async def test_activities_toggle_flips_with_perms(bot):
    guild = config().guilds[0]
    author = config().members[0]
    await grant_perms(author, manage_guild=True)

    await dpytest.message("!adventure toggle rob", member=author)
    sent = dpytest.get_message()
    assert "disabled" in sent.embeds[0].description
    assert (await db.get_activities_config(guild.id))["rob_enabled"] is False

    await dpytest.message("!adventure toggle rob", member=author)
    sent = dpytest.get_message()
    assert "enabled" in sent.embeds[0].description
    assert (await db.get_activities_config(guild.id))["rob_enabled"] is True


@pytest.mark.cogs("cogs.activities")
async def test_a_server_cannot_set_a_cooldown_at_all(bot):
    """The setting is bot-wide now, so /adventure has no cooldown subcommand —
    a server admin can only enable or disable an activity."""
    author = config().members[0]
    await grant_perms(author, manage_guild=True)
    cog = bot.get_cog("Activities")

    assert cog.adventure.get_command("cooldown") is None
    assert cog.adventure.get_command("toggle") is not None


@pytest.mark.cogs("cogs.activities")
async def test_the_bot_wide_cooldown_governs_every_guild(bot):
    """One override, every server — including a guild that has its own
    activities_config row for the on/off switches."""
    guild = config().guilds[0]
    cog = bot.get_cog("Activities")

    cfg = await cog._cfg(guild.id)
    assert cog._cooldown(cfg, "work") == WORK_COOLDOWN_DEFAULT

    await db.set_activity_cooldown("work", 2400)
    await db.set_activities_config(guild.id, mine_enabled=False)
    cfg = await cog._cfg(guild.id)
    assert cog._cooldown(cfg, "work") == 2400
    assert cfg["mine_enabled"] is False
    # A second, unrelated guild reads the same length.
    assert cog._cooldown(await cog._cfg(guild.id + 1), "work") == 2400

    await db.clear_activity_cooldown("work")
    assert cog._cooldown(await cog._cfg(guild.id), "work") == WORK_COOLDOWN_DEFAULT


@pytest.mark.cogs("cogs.activities")
async def test_a_junk_cooldown_row_falls_back_to_the_default(bot):
    """Defence in depth: a hand-edited setting must not become "no cooldown"."""
    guild = config().guilds[0]
    author = config().members[0]
    cog = bot.get_cog("Activities")

    await db.set_bot_setting("cooldown:work", "nonsense")
    cfg = await cog._cfg(guild.id)
    assert cog._cooldown(cfg, "work") == WORK_COOLDOWN_DEFAULT

    await dpytest.message("!work", member=author)
    dpytest.get_message()
    stats = await db.get_activity_stats(author.id)
    stats["last_work"] = time.time() - 120
    assert cog._remaining(cfg, stats, "work") > 0


@pytest.mark.cogs("cogs.activities")
async def test_mine_stats_shows_next_pickaxe_price(bot):
    """The upgrade price used to be invisible until the purchase failed."""
    guild = config().guilds[0]
    author = config().members[0]

    await dpytest.message("!mine stats", member=author)
    desc = dpytest.get_message().embeds[0].description
    assert f"{PICKAXES[1]['price']:,}" in desc
    assert "🔒" in desc  # broke, so the price is flagged as out of reach
    assert "Ready now" in desc

    # A second member with the coins in hand sees the affordable marker
    # (separate member because /mine stats carries a 5s per-user cooldown).
    rich = config().members[1]
    await db.add_coins(rich.id, PICKAXES[1]["price"])
    await dpytest.message("!mine stats", member=rich)
    assert "✅ you can afford it" in dpytest.get_message().embeds[0].description


@pytest.mark.cogs("cogs.activities")
async def test_activity_picker_shows_live_state(bot):
    guild = config().guilds[0]
    author = config().members[0]
    cog = bot.get_cog("Activities")
    interaction = types.SimpleNamespace(guild_id=guild.id, guild=guild, user=author)

    choices = await cog._toggle_activity_ac(interaction, "")
    assert [c.value for c in choices] == ["work", "mine", "hunt", "explore", "rob"]
    assert all("✅ enabled" in c.name for c in choices)

    await db.set_activities_config(guild.id, mine_enabled=False)
    await db.set_activity_cooldown("mine", 1200)
    by_value = {c.value: c.name for c in await cog._toggle_activity_ac(interaction, "")}
    assert "❌ disabled" in by_value["mine"] and "20m" in by_value["mine"]

    # Typing narrows the list.
    assert [c.value for c in await cog._toggle_activity_ac(interaction, "ro")] == [
        "rob"
    ]


@pytest.mark.cogs("cogs.activities")
async def test_activities_config_shows_settings(bot):
    author = config().members[0]
    await grant_perms(author, manage_guild=True)

    await dpytest.message("!adventure config", member=author)
    sent = dpytest.get_message()
    field_names = {f.name for f in sent.embeds[0].fields}
    assert field_names == {"/work", "/mine", "/hunt", "/explore", "/rob"}
