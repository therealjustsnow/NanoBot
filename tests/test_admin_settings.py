"""
Command-level tests for the owner-only `!cooldown` and `!econ` in cogs/admin/.

Both settings govern something that crosses server boundaries, so neither could
ever have been a server's to set. A cooldown *claim* is keyed by user, so
whichever of a member's servers picked the shortest length silently set the
pace everywhere. A reward *amount* pays into one global wallet, so a server
raising its daily was minting coins that spend in every other server. They
moved off `/adventure` and `/coin` (Manage Server) onto these owner-only prefix
commands. What each server keeps is its shop, which is a sink.
"""

import pytest
from discord.ext import test as dpytest

import utils.db as db
from cogs.activities import ACTIVITY_DEFAULT_COOLDOWNS
from cogs.economy.constants import REWARD_DEFAULTS
from tests.conftest import config


def _reply():
    return dpytest.get_message().embeds[0]


@pytest.mark.cogs("cogs.admin")
async def test_a_non_owner_cannot_touch_cooldowns(bot):
    """The whole point of the move: a server admin has no way in."""
    author = config().members[0]
    bot.owner_id = author.id + 1

    with pytest.raises(Exception):
        await dpytest.message("!cooldown mine 60", member=author)
    assert await db.get_activity_cooldowns() == {}


@pytest.mark.cogs("cogs.admin")
async def test_owner_sets_and_clears_a_cooldown(bot):
    author = config().members[0]
    bot.owner_id = author.id

    await dpytest.message("!cooldown mine 45m", member=author)
    assert "45m" in _reply().description
    assert await db.get_activity_cooldowns() == {"mine": 2700}

    # Bare invocation lists every activity with its live length.
    await dpytest.message("!cooldown", member=author)
    listed = _reply().description
    assert "45m" in listed and "/work" in listed and "default" in listed

    # One activity, no length: just report it.
    await dpytest.message("!cooldown mine", member=author)
    assert "45m" in _reply().description

    await dpytest.message("!cooldown mine default", member=author)
    assert await db.get_activity_cooldowns() == {}
    assert "30m" in _reply().description  # back to MINE_COOLDOWN_DEFAULT


@pytest.mark.cogs("cogs.admin")
async def test_plain_seconds_and_unparseable_input(bot):
    author = config().members[0]
    bot.owner_id = author.id

    await dpytest.message("!cooldown work 2400", member=author)
    assert "40m" in _reply().description
    assert await db.get_activity_cooldowns() == {"work": 2400}

    await dpytest.message("!cooldown work banana", member=author)
    assert "Error" in _reply().title
    assert await db.get_activity_cooldowns() == {"work": 2400}  # unchanged


@pytest.mark.cogs("cogs.admin")
async def test_out_of_range_and_unknown_activity_are_refused(bot):
    """The bounds are a typo guard, not a balance rule — the owner may go far
    below the old per-guild floor, just not to nothing."""
    author = config().members[0]
    bot.owner_id = author.id

    await dpytest.message("!cooldown work 1", member=author)
    assert "Error" in _reply().title
    await dpytest.message("!cooldown work 999d", member=author)
    assert "Error" in _reply().title
    await dpytest.message("!cooldown farming 1h", member=author)
    assert "Unknown activity" in _reply().description
    assert await db.get_activity_cooldowns() == {}

    # Well below the old floor (half the default) is now perfectly legal.
    await dpytest.message("!cooldown work 30s", member=author)
    assert await db.get_activity_cooldowns() == {"work": 30}
    assert 30 < ACTIVITY_DEFAULT_COOLDOWNS["work"] // 2


@pytest.mark.cogs("cogs.admin")
async def test_cooldown_also_covers_the_coop_payouts(bot):
    """/squad and /raid aren't /adventure activities, but their payout is the
    same per-user claim on a global wallet, so !cooldown owns their length
    too."""
    author = config().members[0]
    bot.owner_id = author.id

    await dpytest.message("!cooldown", member=author)
    listed = _reply().description
    assert "/coop" in listed and "/raid" in listed

    await dpytest.message("!cooldown coop 20m", member=author)
    assert "20m" in _reply().description
    assert await db.get_activity_cooldowns() == {"coop": 1200}


# ── !econ (bot-wide coin faucets) ─────────────────────────────────────────────
@pytest.mark.cogs("cogs.admin")
async def test_a_non_owner_cannot_touch_reward_amounts(bot):
    author = config().members[0]
    bot.owner_id = author.id + 1

    with pytest.raises(Exception):
        await dpytest.message("!econ daily 100000", member=author)
    assert await db.get_reward_amounts() == {}


@pytest.mark.cogs("cogs.admin")
async def test_owner_sets_lists_and_clears_a_reward(bot):
    author = config().members[0]
    bot.owner_id = author.id

    await dpytest.message("!econ daily 150", member=author)
    assert "150" in _reply().description
    assert await db.get_reward_amounts() == {"daily": 150}

    await dpytest.message("!econ", member=author)
    listed = _reply().description
    assert "150" in listed and "streak_bonus" in listed and "default" in listed

    await dpytest.message("!econ daily", member=author)
    assert "150" in _reply().description

    await dpytest.message("!econ daily default", member=author)
    assert str(REWARD_DEFAULTS["daily"]) in _reply().description
    assert await db.get_reward_amounts() == {}


@pytest.mark.cogs("cogs.admin")
async def test_zero_survives_because_it_is_how_a_faucet_is_turned_off(bot):
    """Unlike a cooldown, 0 is a real amount here — it disables /squad."""
    author = config().members[0]
    bot.owner_id = author.id

    await dpytest.message("!econ coop 0", member=author)
    assert "/squad" in _reply().description
    assert await db.get_reward_amounts() == {"coop": 0}


@pytest.mark.cogs("cogs.admin")
async def test_unknown_reward_and_unparseable_amount_are_refused(bot):
    author = config().members[0]
    bot.owner_id = author.id

    await dpytest.message("!econ pocketmoney 50", member=author)
    assert "Unknown reward" in _reply().description
    await dpytest.message("!econ daily heaps", member=author)
    assert "Error" in _reply().title
    await dpytest.message("!econ daily 999999999999", member=author)
    assert "Error" in _reply().title
    assert await db.get_reward_amounts() == {}
