"""
tests/test_regrouped_commands_dpytest.py
The slash-surface audit moved commands off the top level. Each one kept a flat
prefix name, and this is where those prefix names are actually *run* — through
the real parse → check → callback path — rather than only checked for
existence.

`tests/test_slash_surface.py` proves the names are still registered and that
the slash side landed under a parent. Registration is not behaviour: a
regrouped command whose shared helper was wired up wrong is still registered,
still fails CI nowhere, and still 500s the first time someone types it.
"""

import pytest
from discord.ext import test as dpytest

import utils.db as db
from tests.conftest import config


# ── /remind → /reminders user (prefix name: !remind) ────────────────────────
@pytest.mark.cogs("cogs.reminders")
async def test_prefix_remind_still_sets_a_reminder_for_someone_else(bot):
    """!remind is the prefix twin of /reminders user, routed through the
    shared _remind_other helper. It must still store a reminder whose target
    is the *other* member, not the caller."""
    author, target = config().members[0], config().members[1]

    await dpytest.message(f"!remind {target.mention} check that PR 2h", member=author)
    reply = dpytest.get_message()
    assert reply.embeds, "expected a confirmation embed"

    stored = await db.get_all_reminders()
    assert len(stored) == 1
    info = next(iter(stored.values()))
    assert info["target_id"] == str(target.id)
    assert info["set_by_id"] == str(author.id)
    assert "check that PR" in info["message"]


@pytest.mark.cogs("cogs.reminders")
async def test_prefix_remind_refuses_bots(bot):
    """The guard lives in the shared helper now — it has to still fire from
    the prefix side."""
    author = config().members[0]

    await dpytest.message(f"!remind {bot.user.mention} beep 1h", member=author)
    reply = dpytest.get_message()
    assert reply.embeds and "bots" in (reply.embeds[0].description or "").lower()
    assert await db.get_all_reminders() == {}


@pytest.mark.cogs("cogs.reminders")
async def test_slash_side_reminders_user_shares_the_same_body(bot):
    """/reminders user is reachable as a prefix subcommand too (it is hybrid),
    which is the cheapest way to exercise the same callback dpytest can't
    dispatch as an interaction."""
    author, target = config().members[0], config().members[1]

    await dpytest.message(
        f"!reminders user {target.mention} water the plants 3h", member=author
    )
    reply = dpytest.get_message()
    assert reply.embeds

    stored = await db.get_all_reminders()
    assert len(stored) == 1
    assert next(iter(stored.values()))["target_id"] == str(target.id)


# ── /every → /recurring every (prefix name: !every) ─────────────────────────
@pytest.mark.cogs("cogs.recurring")
async def test_prefix_every_still_creates_a_recurring_reminder(bot):
    author = config().members[0]

    await dpytest.message("!every daily stand up meeting", member=author)
    reply = dpytest.get_message()
    assert reply.embeds and "Recurring Reminder Set" in reply.embeds[0].title

    stored = await db.get_all_recurring()
    assert len(stored) == 1
    info = next(iter(stored.values()))
    assert info["interval"] == 86_400
    assert "stand up meeting" in info["message"]


@pytest.mark.cogs("cogs.recurring")
async def test_prefix_every_still_rejects_a_sub_hour_interval(bot):
    """The 1-hour floor moved into the shared _create_recurring body."""
    author = config().members[0]

    await dpytest.message("!every 5m too fast", member=author)
    reply = dpytest.get_message()
    assert reply.embeds and "1 hour" in (reply.embeds[0].description or "")
    assert await db.get_all_recurring() == {}


@pytest.mark.cogs("cogs.recurring")
async def test_recurring_every_subcommand_shares_the_same_body(bot):
    """/recurring every is hybrid, so the prefix form of the subcommand runs
    the same callback the interaction would. (Its `label`/`dm` options are
    slash-only in practice — discord.py's prefix parser only transforms the
    first keyword-only parameter, which `message` already is.)"""
    author = config().members[0]

    await dpytest.message("!recurring every weekly pay the rent", member=author)
    reply = dpytest.get_message()
    assert reply.embeds and "Recurring Reminder Set" in reply.embeds[0].title

    info = next(iter((await db.get_all_recurring()).values()))
    assert info["interval"] == 7 * 86_400
    assert "pay the rent" in info["message"]


# ── /info … (prefix names: !id, !mc, !roleinfo, !channelinfo, !firstmsg) ────
@pytest.mark.cogs("cogs.utility")
async def test_prefix_info_lookups_still_reply(bot):
    """Prefix commands whose slash entry point moved under /info. Each should
    still answer with an embed."""
    author = config().members[0]

    # !firstmsg is left out: it walks channel history, which dpytest's fake
    # channel doesn't implement.
    for content, expect_in_title in (
        ("!id", "Discord ID"),
        ("!mc", ""),
        ("!channelinfo", ""),
    ):
        await dpytest.message(content, member=author)
        reply = dpytest.get_message()
        assert reply.embeds, f"{content} produced no embed"
        if expect_in_title:
            assert expect_in_title in (reply.embeds[0].title or "")


@pytest.mark.cogs("cogs.utility")
async def test_prefix_id_resolves_a_mentioned_member(bot):
    author, target = config().members[0], config().members[1]

    await dpytest.message(f"!id {target.mention}", member=author)
    reply = dpytest.get_message()
    assert reply.embeds
    assert str(target.id) in (reply.embeds[0].description or "")


@pytest.mark.cogs("cogs.utility")
async def test_prefix_roleinfo_still_reads_a_role(bot):
    guild, author = config().guilds[0], config().members[0]
    role = guild.default_role

    await dpytest.message(f"!roleinfo {role.name}", member=author)
    reply = dpytest.get_message()
    assert reply.embeds and role.name in (reply.embeds[0].title or "")


# ── Owner tools that left the slash tree (prefix must still gate them) ──────
@pytest.mark.cogs("cogs.economy")
async def test_prefix_coin_grant_is_still_owner_only(bot):
    """Moving a command off slash must not move its permission check with it.

    Non-owner: discord.py's is_owner raises NotOwner, which the bot's error
    handler swallows into a reply — either way, no coins may be minted.
    """
    author, target = config().members[0], config().members[1]
    bot.owner_id = author.id + 1_000

    try:
        await dpytest.message(f"!coin grant 500 {target.mention}", member=author)
    except Exception:
        pass  # NotOwner propagates out of dpytest; the assertion below is the point.

    assert await db.get_balance(target.id) == 0


@pytest.mark.cogs("cogs.economy")
async def test_prefix_coin_grant_works_for_the_owner(bot):
    author, target = config().members[0], config().members[1]
    bot.owner_id = author.id

    await dpytest.message(f"!coin grant 500 {target.mention}", member=author)
    reply = dpytest.get_message()
    assert reply.embeds
    assert await db.get_balance(target.id) == 500
