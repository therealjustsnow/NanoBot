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
from discord.ext import commands

from tests.conftest import config, grant_perms


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


# ── /clean + /snailpurge → /purge only:… / mode:… ──────────────────────────
def test_purge_options_are_order_and_label_tolerant():
    """Slash passes `only` and `mode` by name; the prefix assigns positionally,
    so `!purge 300 slow` puts "slow" in the `only` slot. Reading it as the mode
    is the difference between the obvious thing working and six positional
    placeholders."""
    from cogs.moderation.cog import _INVALID, _resolve_purge_options

    # The obvious prefix shapes.
    assert _resolve_purge_options(None, None) == (None, "fast")
    assert _resolve_purge_options("slow", None) == (None, "slow")
    assert _resolve_purge_options("bots", None) == ("bots", "fast")
    assert _resolve_purge_options("bots", "slow") == ("bots", "slow")

    # Someone who learned the command on slash types the labels too.
    assert _resolve_purge_options("mode:slow", None) == (None, "slow")
    assert _resolve_purge_options("mode: slow", None) == (None, "slow")
    assert _resolve_purge_options("only:bots", None) == ("bots", "fast")
    assert _resolve_purge_options("only: nanobot", "mode: slow") == ("nanobot", "slow")

    # An explicit mode still wins over a mode word in the only slot.
    assert _resolve_purge_options("fast", "slow") == (_INVALID, "slow")

    # Genuinely unknown values are still errors, one field at a time.
    assert _resolve_purge_options("mods", None) == (_INVALID, "fast")
    assert _resolve_purge_options(None, "medium") == (None, _INVALID)


def test_purge_only_filter_normalises_its_input():
    """`only` replaced a bare `bots: bool`, so the old prefix shape must keep
    working — and an unrecognised value has to be rejected out loud. A filter
    that silently does nothing on a delete command is how a channel gets wiped
    by accident."""
    from cogs.moderation.cog import _INVALID, _normalise_purge_only

    # No filter at all.
    for value in (None, "", "   ", "anyone", "any", "all", "everyone", "false", "no"):
        assert _normalise_purge_only(value) is None, value

    # The three real filters, plus the legacy `!purge 50 true` shape.
    assert _normalise_purge_only("bots") == "bots"
    assert _normalise_purge_only("BOTS") == "bots"
    assert _normalise_purge_only("true") == "bots"
    assert _normalise_purge_only("bot") == "bots"
    assert _normalise_purge_only("humans") == "humans"
    assert _normalise_purge_only("nanobot") == "nanobot"
    assert _normalise_purge_only("self") == "nanobot"

    # Anything else is an error, never a silently-ignored filter.
    assert _normalise_purge_only("everybody") is _INVALID
    assert _normalise_purge_only("mods") is _INVALID


@pytest.mark.cogs("cogs.moderation")
async def test_purge_rejects_an_unknown_filter_and_names_both_option_sets(bot):
    """`only` and `mode` share one positional slot on the prefix, so the error
    has to list both — naming whichever one the parser happened to fill would
    send a mod hunting in the wrong place."""
    author = config().members[0]
    await grant_perms(author, manage_messages=True)
    await grant_perms(config().guilds[0].me, manage_messages=True)

    await dpytest.message("!purge 10 medium", member=author)
    reply = dpytest.get_message()
    body = reply.embeds[0].description or ""
    assert "nanobot" in body and "slow" in body


@pytest.mark.cogs("cogs.moderation")
async def test_slow_mode_allows_an_amount_fast_mode_would_reject(bot):
    """The two modes have different caps — 100 for a bulk delete, 500 for the
    one-by-one path. Picking slow must apply slow's cap, not fast's."""
    author = config().members[0]
    await grant_perms(author, manage_messages=True)
    await grant_perms(config().guilds[0].me, manage_messages=True)

    # Fast mode stops at 100 and says how to go higher.
    await dpytest.message("!purge 300", member=author)
    reply = dpytest.get_message()
    assert reply.embeds and "slow" in (reply.embeds[0].description or "").lower()

    # Slow mode accepts it — and asks for a confirmation code rather than
    # deleting anything.
    await dpytest.message("!purge 300 slow", member=author)
    confirm = dpytest.get_message()
    assert confirm.embeds and "Confirm" in (confirm.embeds[0].title or "")


@pytest.mark.cogs("cogs.moderation")
async def test_slow_purge_still_bounds_its_amount(bot):
    author = config().members[0]
    await grant_perms(author, manage_messages=True)
    await grant_perms(config().guilds[0].me, manage_messages=True)

    await dpytest.message("!purge 900 slow", member=author)
    reply = dpytest.get_message()
    assert reply.embeds and "500" in (reply.embeds[0].description or "")


@pytest.mark.cogs("cogs.moderation")
async def test_snailpurge_prefix_shorthand_still_asks_to_confirm(bot):
    """n!snailpurge is a shim onto the shared slow body now — it must still
    reach the confirmation step rather than deleting straight away."""
    author = config().members[0]
    await grant_perms(author, manage_messages=True)
    await grant_perms(config().guilds[0].me, manage_messages=True)

    await dpytest.message("!snailpurge 200", member=author)
    reply = dpytest.get_message()
    assert reply.embeds and "Confirm" in (reply.embeds[0].title or "")


@pytest.mark.cogs("cogs.moderation")
async def test_clean_prefix_shorthand_still_works(bot):
    author = config().members[0]
    await grant_perms(author, manage_messages=True)
    await grant_perms(config().guilds[0].me, manage_messages=True)

    await dpytest.message("!clean 20", member=author)
    reply = dpytest.get_message()
    assert reply.embeds and "Cleaned" in (reply.embeds[0].title or "")


@pytest.mark.cogs("cogs.moderation")
async def test_purge_and_clean_still_need_manage_messages(bot):
    """Reshaping a destructive command must not loosen its gate."""
    author = config().members[0]
    # Give the bot the permission but not the caller, so the check that fires is
    # the one about the *user*.
    await grant_perms(config().guilds[0].me, manage_messages=True)
    for content in ("!purge 10", "!clean", "!snailpurge 10"):
        with pytest.raises(commands.MissingPermissions):
            await dpytest.message(content, member=author)


# ── /clear /remove /move /jump → under /music ──────────────────────────────
@pytest.mark.cogs("cogs.music")
async def test_music_queue_editing_keeps_its_prefix_names(bot):
    """The four queue commands moved under /music on slash only. With no
    player connected they should all answer 'the queue is empty' — which is
    enough to prove the prefix name still resolves to the right callback."""
    author = config().members[0]

    for content in ("!clear", "!remove 1", "!move 1 2", "!jump 1"):
        await dpytest.message(content, member=author)
        reply = dpytest.get_message()
        assert reply.embeds, f"{content} produced no embed"
        assert "empty" in (reply.embeds[0].description or "").lower(), content
