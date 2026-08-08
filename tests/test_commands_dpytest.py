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


# ── /unban picker ban-list cache ────────────────────────────────────────────
# The cache is keyed by guild and holds up to _BAN_FETCH_LIMIT user tuples.
# Entries were only ever added: every guild where anyone had once opened the
# picker kept its ban list alive for the life of the process, long after the
# few-second TTL made it unservable.


def _mod_cog():
    """A Moderation instance without a bot — the prune is pure dict work."""
    from cogs.moderation.cog import Moderation

    cog = Moderation.__new__(Moderation)
    cog._ban_cache = {}
    return cog


def test_ban_cache_prune_evicts_entries_past_their_usefulness():
    cog = _mod_cog()
    now = 1_000_000.0
    cog._ban_cache[1] = (now - cog._BAN_CACHE_PRUNE_AFTER - 1, [(5, "old")])
    cog._ban_cache[2] = (now - 1, [(6, "fresh")])
    cog._prune_ban_cache(now)
    assert 1 not in cog._ban_cache
    assert 2 in cog._ban_cache


def test_ban_cache_prune_keeps_anything_still_within_the_ttl():
    """A prune that dropped a servable entry would turn every keystroke of the
    autocomplete back into an HTTP fetch — the thing the cache exists to stop."""
    cog = _mod_cog()
    now = 1_000_000.0
    cog._ban_cache[9] = (now - cog._BAN_CACHE_TTL / 2, [(1, "a")])
    cog._prune_ban_cache(now)
    assert 9 in cog._ban_cache


def test_ban_cache_prune_handles_an_empty_cache():
    cog = _mod_cog()
    cog._prune_ban_cache(1_000_000.0)
    assert cog._ban_cache == {}


def test_ban_cache_prune_after_exceeds_the_ttl():
    """Sweeping sooner than the TTL would evict entries that could still be
    served, so the two constants have to stay ordered."""
    cog = _mod_cog()
    assert cog._BAN_CACHE_PRUNE_AFTER > cog._BAN_CACHE_TTL
