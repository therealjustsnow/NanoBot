"""
Command-level tests for cogs/casino/ under dpytest (parse → check → DB → reply).
"""

import pytest
from discord.ext import commands
from discord.ext import test as dpytest

import utils.db as db
from tests.conftest import config, grant_perms


@pytest.mark.cogs("cogs.casino")
async def test_flip_win_credits_payout_and_streak(bot, monkeypatch):
    from cogs.casino import cog as casino

    guild = config().guilds[0]
    author = config().members[0]
    await db.add_coins(guild.id, author.id, 500)
    # roll 0.0 -> "heads" always wins.
    monkeypatch.setattr(casino.random, "random", lambda: 0.0)

    await dpytest.message("!casino flip 100 heads", member=author)
    sent = dpytest.get_message()
    assert "Won" in sent.embeds[0].title
    # 500 - 100 (debit) + round(100*1.92) (payout) = 592
    assert await db.get_balance(guild.id, author.id) == 592
    stats = await db.get_casino_stats(guild.id, author.id)
    assert stats["games"] == 1
    assert stats["streak"] == 1


@pytest.mark.cogs("cogs.casino")
async def test_flip_loss_debits_and_resets_streak(bot, monkeypatch):
    from cogs.casino import cog as casino

    guild = config().guilds[0]
    author = config().members[0]
    await db.add_coins(guild.id, author.id, 500)
    # roll near 1.0 -> "tails"; betting on heads loses.
    monkeypatch.setattr(casino.random, "random", lambda: 0.999999)

    await dpytest.message("!casino flip 100 heads", member=author)
    sent = dpytest.get_message()
    assert "Lost" in sent.embeds[0].title
    assert await db.get_balance(guild.id, author.id) == 400
    stats = await db.get_casino_stats(guild.id, author.id)
    assert stats["streak"] == 0


@pytest.mark.cogs("cogs.casino")
async def test_flip_insufficient_funds(bot):
    guild = config().guilds[0]
    author = config().members[0]
    await db.add_coins(guild.id, author.id, 10)

    await dpytest.message("!casino flip 100 heads", member=author)
    sent = dpytest.get_message()
    assert "Error" in sent.embeds[0].title
    assert await db.get_balance(guild.id, author.id) == 10
    assert (await db.get_casino_stats(guild.id, author.id))["games"] == 0


@pytest.mark.cogs("cogs.casino")
async def test_flip_invalid_side(bot):
    guild = config().guilds[0]
    author = config().members[0]
    await db.add_coins(guild.id, author.id, 500)

    await dpytest.message("!casino flip 100 sideways", member=author)
    sent = dpytest.get_message()
    assert "Error" in sent.embeds[0].title
    assert await db.get_balance(guild.id, author.id) == 500


@pytest.mark.cogs("cogs.casino")
async def test_bet_below_minimum_rejected(bot):
    guild = config().guilds[0]
    author = config().members[0]
    await db.add_coins(guild.id, author.id, 500)

    await dpytest.message("!casino flip 1 heads", member=author)
    sent = dpytest.get_message()
    assert "Error" in sent.embeds[0].title
    assert "between" in sent.embeds[0].description
    assert await db.get_balance(guild.id, author.id) == 500


@pytest.mark.cogs("cogs.casino")
async def test_bet_zero_or_negative_rejected(bot):
    guild = config().guilds[0]
    author = config().members[0]
    await db.add_coins(guild.id, author.id, 500)

    await dpytest.message("!casino dice 0", member=author)
    sent = dpytest.get_message()
    assert "Error" in sent.embeds[0].title
    assert await db.get_balance(guild.id, author.id) == 500


@pytest.mark.cogs("cogs.casino")
async def test_dice_push_refunds_bet(bot, monkeypatch):
    from cogs.casino import cog as casino

    guild = config().guilds[0]
    author = config().members[0]
    await db.add_coins(guild.id, author.id, 500)
    # Both player and dealer roll a total of 7 (die(0.0)=1, die(0.999)=6).
    rolls = iter([0.0, 0.999999, 0.999999, 0.0])
    monkeypatch.setattr(casino.random, "random", lambda: next(rolls))

    await dpytest.message("!casino dice 100", member=author)
    sent = dpytest.get_message()
    assert "Push" in sent.embeds[0].title
    assert await db.get_balance(guild.id, author.id) == 500  # bet refunded


@pytest.mark.cogs("cogs.casino")
async def test_slots_triple_seven_awards_jackpot(bot, monkeypatch):
    from cogs.casino import cog as casino

    guild = config().guilds[0]
    author = config().members[0]
    await db.add_coins(guild.id, author.id, 1000)
    await db.add_to_jackpot(guild.id, 777)
    monkeypatch.setattr(casino.random, "random", lambda: 0.999999)  # all reels -> 7️⃣

    await dpytest.message("!casino slots 100", member=author)
    sent = dpytest.get_message()
    assert "Jackpot" in sent.embeds[0].title
    assert any("JACKPOT" in (f.name or "") for f in sent.embeds[0].fields)
    assert (await db.get_casino_config(guild.id))["jackpot_pool"] == 0
    # 1000 - 100 (debit) + 4500 (triple-7 payout) + 777 (jackpot) = 6177
    assert await db.get_balance(guild.id, author.id) == 6177


@pytest.mark.cogs("cogs.casino")
async def test_roulette_unknown_space_rejected(bot):
    guild = config().guilds[0]
    author = config().members[0]
    await db.add_coins(guild.id, author.id, 500)

    await dpytest.message("!casino roulette 100 purple", member=author)
    sent = dpytest.get_message()
    assert "Error" in sent.embeds[0].title
    assert await db.get_balance(guild.id, author.id) == 500


@pytest.mark.cogs("cogs.casino")
async def test_roulette_number_bet_wins(bot, monkeypatch):
    from cogs.casino import cog as casino

    guild = config().guilds[0]
    author = config().members[0]
    await db.add_coins(guild.id, author.id, 500)
    monkeypatch.setattr(casino.random, "random", lambda: 0.0)  # spins to pocket 0

    await dpytest.message("!casino roulette 100 0", member=author)
    sent = dpytest.get_message()
    assert "Won" in sent.embeds[0].title
    # 500 - 100 + 100*35 = 3900
    assert await db.get_balance(guild.id, author.id) == 3900


@pytest.mark.cogs("cogs.casino")
async def test_blackjack_deals_and_debits(bot, monkeypatch):
    from cogs.casino import cog as casino

    guild = config().guilds[0]
    author = config().members[0]
    await db.add_coins(guild.id, author.id, 500)

    # A fixed, non-shuffled shoe (shoe.pop() takes from the end): dealt order
    # is player 2♠ 7♥ (9, no natural), dealer 3♦ 4♣ (7, no natural).
    fixed_shoe = [("4", "♣"), ("3", "♦"), ("7", "♥"), ("2", "♠")]
    monkeypatch.setattr(casino, "new_shoe", lambda decks: list(fixed_shoe))
    monkeypatch.setattr(casino.random, "shuffle", lambda seq: None)

    await dpytest.message("!casino blackjack 100", member=author)
    sent = dpytest.get_message()
    assert sent.embeds
    assert sent.embeds[0].title == "🃏 Blackjack"
    assert await db.get_balance(guild.id, author.id) == 400  # bet debited, hand open


@pytest.mark.cogs("cogs.casino")
async def test_stats_and_jackpot_and_overview_reply(bot):
    guild = config().guilds[0]
    author = config().members[0]

    await dpytest.message("!casino", member=author)
    sent = dpytest.get_message()
    assert "Casino" in sent.embeds[0].title

    await dpytest.message("!casino jackpot", member=author)
    sent = dpytest.get_message()
    assert "Jackpot" in sent.embeds[0].title

    await dpytest.message("!casino stats", member=author)
    sent = dpytest.get_message()
    assert author.display_name in sent.embeds[0].title


@pytest.mark.cogs("cogs.casino")
async def test_toggle_denied_without_manage_guild(bot):
    author = config().members[0]
    with pytest.raises(commands.MissingPermissions):
        await dpytest.message("!casino toggle", member=author)


@pytest.mark.cogs("cogs.casino")
async def test_toggle_flips_config_and_blocks_play(bot):
    guild = config().guilds[0]
    author = config().members[0]
    await grant_perms(author, manage_guild=True)
    await db.add_coins(guild.id, author.id, 500)

    await dpytest.message("!casino toggle", member=author)
    sent = dpytest.get_message()
    assert "disabled" in sent.embeds[0].description
    assert (await db.get_casino_config(guild.id))["enabled"] is False

    await dpytest.message("!casino flip 100 heads", member=author)
    sent = dpytest.get_message()
    assert "disabled" in sent.embeds[0].description
    assert await db.get_balance(guild.id, author.id) == 500

    await dpytest.message("!casino toggle", member=author)
    sent = dpytest.get_message()
    assert "enabled" in sent.embeds[0].description
    assert (await db.get_casino_config(guild.id))["enabled"] is True


@pytest.mark.cogs("cogs.casino")
async def test_limit_denied_without_manage_guild(bot):
    author = config().members[0]
    with pytest.raises(commands.MissingPermissions):
        await dpytest.message("!casino limit 5 50", member=author)


@pytest.mark.cogs("cogs.casino")
async def test_limit_updates_bounds_and_enforces_them(bot):
    guild = config().guilds[0]
    author = config().members[0]
    await grant_perms(author, manage_guild=True)
    await db.add_coins(guild.id, author.id, 500)

    await dpytest.message("!casino limit 5 50", member=author)
    sent = dpytest.get_message()
    assert "Bet limits set" in sent.embeds[0].description
    cfg = await db.get_casino_config(guild.id)
    assert cfg["min_bet"] == 5
    assert cfg["max_bet"] == 50

    # Now a bet of 100 is above the new max.
    await dpytest.message("!casino flip 100 heads", member=author)
    sent = dpytest.get_message()
    assert "between" in sent.embeds[0].description
    assert await db.get_balance(guild.id, author.id) == 500


@pytest.mark.cogs("cogs.casino")
async def test_limit_rejects_max_below_min(bot):
    author = config().members[0]
    await grant_perms(author, manage_guild=True)

    await dpytest.message("!casino limit 100 10", member=author)
    sent = dpytest.get_message()
    assert "Error" in sent.embeds[0].title


@pytest.mark.cogs("cogs.casino")
async def test_config_requires_manage_guild(bot):
    author = config().members[0]
    with pytest.raises(commands.MissingPermissions):
        await dpytest.message("!casino config", member=author)


@pytest.mark.cogs("cogs.casino")
async def test_config_shows_settings_with_perms(bot):
    author = config().members[0]
    await grant_perms(author, manage_guild=True)

    await dpytest.message("!casino config", member=author)
    sent = dpytest.get_message()
    assert "Casino Settings" in sent.embeds[0].title
