"""
Command-level tests for the owner-only `!cooldown` in cogs/admin/ under dpytest.

Activity cooldown *claims* are keyed by user, not by (guild, user), so their
length was never a server's to set: whichever of a member's servers picked the
shortest one silently set the pace everywhere, and the coins it minted spend
everywhere. The setting therefore moved off `/adventure` (Manage Server) and
onto this owner-only prefix command.
"""

import pytest
from discord.ext import test as dpytest

import utils.db as db
from cogs.activities import ACTIVITY_DEFAULT_COOLDOWNS
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
