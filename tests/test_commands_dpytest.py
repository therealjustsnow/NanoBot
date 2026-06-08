"""
tests/test_commands_dpytest.py
Command-level tests that need a "semblance of Discord" — dpytest provides a fake
guild, members, and message dispatch so we can verify permission enforcement and
full command round-trips (parse → check → DB → reply) without a live gateway.

These complement the pure-logic suites; they're the only place command wiring
(decorators, checks, argument parsing) is actually executed.
"""

import pytest
from discord.ext import commands
from discord.ext import test as dpytest

import utils.db as db
from tests.conftest import config, grant_perms


# ── Permission boundaries ───────────────────────────────────────────────────────
@pytest.mark.cogs("cogs.moderation")
async def test_note_denied_without_mod_perms(bot):
    """A member lacking Manage Messages cannot use !note."""
    author, target = config().members[0], config().members[1]
    # dpytest re-raises the command's exception; the check must reject the call.
    with pytest.raises(commands.MissingPermissions):
        await dpytest.message(f"!note {target.mention} spamming", member=author)


@pytest.mark.cogs("cogs.moderation")
async def test_note_allowed_with_mod_perms(bot):
    """With Manage Messages on both the user and the bot, !note succeeds."""
    author, target = config().members[0], config().members[1]
    await grant_perms(author, manage_messages=True)
    await grant_perms(config().guilds[0].me, manage_messages=True)

    await dpytest.message(f"!note {target.mention} spamming in general", member=author)
    sent = dpytest.get_message()
    assert sent.embeds, "expected a reply embed"
    assert "Note Saved" in sent.embeds[0].title


# ── Full round-trip: write then read back ───────────────────────────────────────
@pytest.mark.cogs("cogs.moderation")
async def test_note_roundtrip_persists_and_lists(bot):
    guild = config().guilds[0]
    author, target = config().members[0], config().members[1]
    await grant_perms(author, manage_messages=True)
    await grant_perms(guild.me, manage_messages=True)

    await dpytest.message(f"!note {target.mention} first note", member=author)
    dpytest.get_message()

    # It is now in the DB...
    notes = await db.get_notes(guild.id, target.id)
    assert len(notes) == 1
    assert notes[0]["note"] == "first note"

    # ...and !notes surfaces it.
    await dpytest.message(f"!notes {target.mention}", member=author)
    listing = dpytest.get_message()
    assert listing.embeds
    assert "first note" in (listing.embeds[0].description or "")
