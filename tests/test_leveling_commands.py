"""
Command-level tests for cogs/leveling.py under dpytest (parse → check → DB → reply).
"""

import pytest
from discord.ext import commands
from discord.ext import test as dpytest

import utils.db as db
from tests.conftest import config, grant_perms


@pytest.mark.cogs("cogs.leveling")
async def test_rank_replies_for_self(bot):
    author = config().members[0]
    await dpytest.message("!rank", member=author)
    sent = dpytest.get_message()
    assert sent.embeds
    assert author.display_name in sent.embeds[0].title


@pytest.mark.cogs("cogs.leveling")
async def test_level_set_denied_without_manage_guild(bot):
    author, target = config().members[0], config().members[1]
    with pytest.raises(commands.MissingPermissions):
        await dpytest.message(f"!level set {target.mention} 500", member=author)


@pytest.mark.cogs("cogs.leveling")
async def test_level_set_persists_with_perms(bot):
    guild = config().guilds[0]
    author, target = config().members[0], config().members[1]
    await grant_perms(author, manage_guild=True)

    await dpytest.message(f"!level set {target.mention} 500", member=author)
    sent = dpytest.get_message()
    assert sent.embeds
    assert await db.get_xp(guild.id, target.id) == 500


@pytest.mark.cogs("cogs.leveling")
async def test_level_toggle_updates_config(bot):
    guild = config().guilds[0]
    author = config().members[0]
    await grant_perms(author, manage_guild=True)

    await dpytest.message("!level toggle on", member=author)
    dpytest.get_message()
    assert (await db.get_level_config(guild.id))["enabled"] is True


@pytest.mark.cogs("cogs.leveling")
async def test_globalannounce_needs_manage_guild(bot):
    author = config().members[0]
    with pytest.raises(commands.MissingPermissions):
        await dpytest.message("!level globalannounce off", member=author)


@pytest.mark.cogs("cogs.leveling")
async def test_globalannounce_sets_clears_and_switches_off(bot):
    """The three states a server can be in, all through one command."""
    guild = config().guilds[0]
    channel = config().channels[0]
    author = config().members[0]
    await grant_perms(author, manage_guild=True)

    await dpytest.message(f"!level globalannounce {channel.mention}", member=author)
    dpytest.get_message()
    cfg = await db.get_level_config(guild.id)
    assert cfg["global_announce_channel"] == channel.id
    assert cfg["global_announce"] is True

    # "off" isn't a channel — the Optional converter has to fall through to it.
    await dpytest.message("!level globalannounce off", member=author)
    dpytest.get_message()
    assert (await db.get_level_config(guild.id))["global_announce"] is False

    # Back on: the channel that was chosen before is still the one used.
    await dpytest.message("!level globalannounce on", member=author)
    dpytest.get_message()
    cfg = await db.get_level_config(guild.id)
    assert cfg["global_announce"] is True
    assert cfg["global_announce_channel"] == channel.id

    # Bare: clear the override and follow the server's own announce setting.
    await dpytest.message("!level globalannounce", member=author)
    dpytest.get_message()
    cfg = await db.get_level_config(guild.id)
    assert cfg["global_announce_channel"] is None
    assert cfg["global_announce"] is True


@pytest.mark.cogs("cogs.leveling")
async def test_level_config_reports_the_global_announce_setting(bot):
    guild = config().guilds[0]
    channel = config().channels[0]
    author = config().members[0]
    await grant_perms(author, manage_guild=True)
    await db.set_level_config(guild.id, global_announce_channel=channel.id)

    await dpytest.message("!level config", member=author)
    embed = dpytest.get_message().embeds[0]
    field = next(f for f in embed.fields if f.name == "Global level-ups")
    assert channel.mention in field.value


@pytest.mark.cogs("cogs.leveling")
async def test_globalxp_needs_manage_guild(bot):
    author = config().members[0]
    with pytest.raises(commands.MissingPermissions):
        await dpytest.message("!level globalxp off", member=author)


@pytest.mark.cogs("cogs.leveling")
async def test_globalxp_toggle_round_trips(bot):
    """The whole-system opt-out, distinct from `globalannounce`."""
    guild = config().guilds[0]
    author = config().members[0]
    await grant_perms(author, manage_guild=True)

    await dpytest.message("!level globalxp off", member=author)
    dpytest.get_message()
    cfg = await db.get_level_config(guild.id)
    assert cfg["global_xp_enabled"] is False
    assert cfg["global_announce"] is True  # untouched by this switch

    await dpytest.message("!level globalxp on", member=author)
    dpytest.get_message()
    assert (await db.get_level_config(guild.id))["global_xp_enabled"] is True


@pytest.mark.cogs("cogs.leveling")
async def test_globalxp_rejects_junk_state(bot):
    author = config().members[0]
    await grant_perms(author, manage_guild=True)
    await dpytest.message("!level globalxp maybe", member=author)
    assert dpytest.get_message().embeds[0].title.startswith("❌")


@pytest.mark.cogs("cogs.leveling")
async def test_level_config_reports_the_global_xp_setting(bot):
    guild = config().guilds[0]
    author = config().members[0]
    await grant_perms(author, manage_guild=True)
    await db.set_level_config(guild.id, global_xp_enabled=False)

    await dpytest.message("!level config", member=author)
    embed = dpytest.get_message().embeds[0]
    field = next(f for f in embed.fields if f.name == "Global XP system")
    assert "Off" in field.value


@pytest.mark.cogs("cogs.leveling")
async def test_a_server_cannot_set_the_level_up_coin_rate(bot):
    """The payout lands in a global wallet, so the rate is the owner's (!econ
    level_coin). /level keeps every knob that stays inside the server."""
    author = config().members[0]
    await grant_perms(author, manage_guild=True)
    cog = bot.get_cog("Leveling")

    assert cog.level.get_command("coinreward") is None
    assert cog.level.get_command("rate") is not None


@pytest.mark.cogs("cogs.leveling")
async def test_level_up_awards_coins(bot):
    """A message crossing a level boundary grants the bot-wide rate × new level."""
    guild = config().guilds[0]
    author = config().members[0]
    # Deterministic gain, no cooldown, coins on; start just below level 1.
    await db.set_reward_amount("level_coin", 10)
    await db.set_level_config(
        guild.id,
        enabled=True,
        xp_min=200,
        xp_max=200,
        cooldown=0,
    )
    await db.set_xp(guild.id, author.id, 90)

    await dpytest.message("just chatting", member=author)

    assert await db.get_xp(guild.id, author.id) == 290  # 90 + 200
    # 290 XP → level 2, so reward = 10 × 2 = 20 coins.
    assert await db.get_balance(author.id) == 20
