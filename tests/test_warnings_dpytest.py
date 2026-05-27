"""
tests/test_warnings_dpytest.py
Warning issuance through dpytest: permission enforcement and the per-user count
increment (auto-kick/ban thresholds default to off, so two warns stay safe).
"""

import pytest
from discord.ext import commands
from discord.ext import test as dpytest

from tests.conftest import config, grant_perms


def _drain_embed_text() -> str:
    """Pop every queued message and concatenate its embed titles + descriptions."""
    chunks = []
    while True:
        try:
            msg = dpytest.get_message()
        except Exception:
            break
        for e in msg.embeds:
            chunks.append((e.title or "") + " " + (e.description or ""))
    return "\n".join(chunks)


@pytest.mark.cogs("cogs.warnings")
async def test_warn_denied_without_mod_perms(bot):
    author, target = config().members[0], config().members[1]
    with pytest.raises(commands.MissingPermissions):
        await dpytest.message(f"!warn {target.mention} spam", member=author)


@pytest.mark.cogs("cogs.warnings")
async def test_warn_increments_count(bot):
    guild, author, target = config().guilds[0], config().members[0], config().members[1]
    await grant_perms(author, manage_messages=True)
    await grant_perms(guild.me, manage_messages=True)

    # warn replies, posts a public embed, and may DM the target — the count
    # appears across those, so search all queued messages rather than one.
    await dpytest.message(f"!warn {target.mention} first", member=author)
    assert "#1" in _drain_embed_text()

    await dpytest.message(f"!warn {target.mention} second", member=author)
    assert "#2" in _drain_embed_text()
