"""
Command-level tests for the two social features under dpytest.

  * `/profile rep` — the once-a-day "this person is cool" counter, shown on the
    profile card.
  * `/fun cookie`  — give a cookie, or render the cookie-jar dashboard.

Both are global (an account's tally, not a server's) and worth no coins, so
these check the guard rails that actually matter: you can't rep yourself or a
bot, you can't rep twice in a day, and the dashboard renders on a brand-new
account as well as a busy one.
"""

import time

import pytest
from discord.ext import test as dpytest

import utils.db as db
from utils import cookie_card
from utils.db.social import REP_COOLDOWN
from tests.conftest import config


def _reply():
    return dpytest.get_message()


def _clear_cooldowns(bot):
    """Drop every per-user command cooldown.

    Both commands carry a short anti-spam cooldown, which is right in a server
    and useless in a test that has to invoke twice in a row to prove anything.
    """
    for command in bot.walk_commands():
        command._buckets._cache.clear()


# ── /profile rep ──────────────────────────────────────────────────────────────
@pytest.mark.cogs("cogs.identity")
async def test_rep_awards_the_target_once_a_day(bot):
    giver, target = config().members[0], config().members[1]

    await dpytest.message(f"!profile rep <@{target.id}>", member=giver)
    assert "1" in _reply().embeds[0].description
    assert (await db.get_social(target.id))["rep"] == 1
    assert (await db.get_social(giver.id))["rep"] == 0  # the giver gains none

    # Same day, second attempt: refused, and the target doesn't move.
    _clear_cooldowns(bot)
    await dpytest.message(f"!profile rep <@{target.id}>", member=giver)
    assert "Once a day" in _reply().embeds[0].title
    assert (await db.get_social(target.id))["rep"] == 1


@pytest.mark.cogs("cogs.identity")
async def test_a_day_later_the_rep_is_ready_again(bot):
    giver, target = config().members[0], config().members[1]

    # Pretend they gave one just over a day ago.
    await db.try_claim_rep(giver.id, time.time() - REP_COOLDOWN - 5)
    await dpytest.message(f"!profile rep <@{target.id}>", member=giver)
    _reply()
    assert (await db.get_social(target.id))["rep"] == 1


@pytest.mark.cogs("cogs.identity")
async def test_cannot_rep_yourself_or_a_bot(bot):
    giver = config().members[0]

    await dpytest.message(f"!profile rep <@{giver.id}>", member=giver)
    assert "Nice try" in _reply().embeds[0].description
    _clear_cooldowns(bot)
    await dpytest.message(f"!profile rep <@{bot.user.id}>", member=giver)
    assert "Bots" in _reply().embeds[0].description
    # Neither attempt spent the day.
    assert await db.try_claim_rep(giver.id, time.time()) == 0


@pytest.mark.cogs("cogs.identity")
async def test_bare_rep_reports_your_own_standing(bot):
    author, other = config().members[0], config().members[1]

    await dpytest.message("!profile rep", member=author)
    fresh = _reply().embeds[0].description
    assert "0" in fresh and "now" in fresh  # nothing yet, and ready to give

    await db.add_rep(author.id, 7)
    await db.add_rep(other.id, 3)
    await db.try_claim_rep(author.id, time.time())
    _clear_cooldowns(bot)
    await dpytest.message("!profile rep", member=author)
    used = _reply().embeds[0].description
    assert "7" in used and "#1" in used  # total, and the global position
    assert "ready in" in used  # today's rep is spent


@pytest.mark.cogs("cogs.identity")
async def test_rep_shows_on_the_profile_card(bot):
    author, other = config().members[0], config().members[1]
    await db.add_rep(author.id, 12)
    cog = bot.get_cog("Identity")

    class _Ctx:
        guild = config().guilds[0]

        def __init__(self, user):
            self.author = user

    data, _notes = await cog._collect(_Ctx(author), author)
    assert data["rep"] == 12
    # Someone else's card carries their number, not the viewer's.
    data, _notes = await cog._collect(_Ctx(author), other)
    assert data["rep"] == 0


# ── /fun cookie ───────────────────────────────────────────────────────────────
@pytest.mark.cogs("cogs.fun")
async def test_cookie_counts_both_halves(bot):
    giver, target = config().members[0], config().members[1]

    await dpytest.message(f"!cookie <@{target.id}>", member=giver)
    body = _reply().embeds[0].description
    assert "1" in body

    assert (await db.get_social(giver.id))["cookies_sent"] == 1
    assert (await db.get_social(giver.id))["cookies_received"] == 0
    assert (await db.get_social(target.id))["cookies_received"] == 1


@pytest.mark.cogs("cogs.fun")
async def test_cookie_refuses_yourself_and_bots(bot):
    giver = config().members[0]

    await dpytest.message(f"!cookie <@{giver.id}>", member=giver)
    assert "doesn't count" in _reply().embeds[0].description
    _clear_cooldowns(bot)
    await dpytest.message(f"!cookie <@{bot.user.id}>", member=giver)
    assert "can't eat" in _reply().embeds[0].description
    assert (await db.get_social(giver.id))["cookies_sent"] == 0


@pytest.mark.cogs("cogs.fun")
async def test_bare_cookie_renders_the_dashboard(bot):
    author, other = config().members[0], config().members[1]

    # A brand-new account still gets a card rather than an error.
    await dpytest.message("!cookie", member=author)
    sent = _reply()
    assert sent.attachments and sent.attachments[0].filename.endswith(".png")

    for _ in range(3):
        await db.give_cookie(author.id, other.id)
    await db.give_cookie(other.id, author.id)
    _clear_cooldowns(bot)
    await dpytest.message("!cookie", member=author)
    assert _reply().attachments


# ── The renderer itself ───────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "data",
    [
        {"name": "Fresh", "sent": 0, "received": 0, "rank": None},
        {"name": "Busy", "sent": 4_321, "received": 98_765, "rank": 1},
        {"name": "A" * 60, "sent": 1, "received": 2, "rank": 12},
        {"name": "Broken avatar", "avatar": b"not an image", "sent": 1},
    ],
)
def test_cookie_card_renders_a_png(data):
    """Every degenerate case still produces an image — the card is the reply,
    so a failure here is a command with nothing to say."""
    png = cookie_card.render_cookie_card(data)
    assert png.startswith(b"\x89PNG")
    assert len(png) > 1000


def test_cookie_art_is_deterministic():
    """The chips are placed from a fixed table, so two renders match — that's
    what makes the card safe to compare in a snapshot test later."""
    assert cookie_card.render_cookie_card(
        {"name": "Same", "sent": 3, "received": 3}
    ) == cookie_card.render_cookie_card({"name": "Same", "sent": 3, "received": 3})
