"""
Command-level tests for cogs/economy/ under dpytest (parse → check → DB → reply).
"""

import pytest
from discord.ext import commands
from discord.ext import test as dpytest

import utils.db as db
from cogs.economy import _DEFAULT_SHOP_ITEMS
from cogs.economy.helpers import seconds_to_afford
from tests.conftest import config, grant_perms


@pytest.mark.cogs("cogs.economy")
async def test_balance_replies(bot):
    author = config().members[0]
    await dpytest.message("!balance", member=author)
    sent = dpytest.get_message()
    assert sent.embeds
    assert author.display_name in sent.embeds[0].title


@pytest.mark.cogs("cogs.economy")
async def test_daily_grants_then_blocks(bot):
    guild = config().guilds[0]
    author = config().members[0]

    await dpytest.message("!daily", member=author)
    dpytest.get_message()
    assert await db.get_balance(author.id) == 100  # default daily

    # An immediate second claim is the double-fire case (one user action
    # delivered twice). Balance is unchanged AND the contradictory "already
    # claimed" reply is swallowed, so the user sees exactly one message.
    await dpytest.message("!daily", member=author)
    assert dpytest.verify().message().nothing()
    assert await db.get_balance(author.id) == 100


@pytest.mark.cogs("cogs.economy")
async def test_pay_transfers_funds(bot):
    guild = config().guilds[0]
    author, target = config().members[0], config().members[1]
    await db.add_coins(author.id, 100)

    await dpytest.message(f"!pay {target.mention} 30", member=author)
    sent = dpytest.get_message()
    assert sent.embeds
    assert await db.get_balance(author.id) == 70
    assert await db.get_balance(target.id) == 30


@pytest.mark.cogs("cogs.economy")
async def test_pay_insufficient_funds(bot):
    guild = config().guilds[0]
    author, target = config().members[0], config().members[1]
    await db.add_coins(author.id, 10)

    await dpytest.message(f"!pay {target.mention} 50", member=author)
    sent = dpytest.get_message()
    assert "Error" in sent.embeds[0].title
    assert await db.get_balance(author.id) == 10
    assert await db.get_balance(target.id) == 0


@pytest.mark.cogs("cogs.economy")
async def test_grant_and_take_are_owner_only(bot):
    """Granting mints straight into a global wallet and taking destroys coins
    earned in servers this one has never seen — the same objection that already
    made /coin reset owner-only, at a smaller scale."""
    author, target = config().members[0], config().members[1]
    await grant_perms(author, manage_guild=True)  # not enough any more
    bot.owner_id = author.id + 1

    for command in (
        f"!coin grant 500 {target.mention}",
        f"!coin take 5 {target.mention}",
    ):
        with pytest.raises(commands.NotOwner):
            await dpytest.message(command, member=author)
    assert await db.get_balance(target.id) == 0


@pytest.mark.cogs("cogs.economy")
async def test_grant_credits_with_perms(bot):
    guild = config().guilds[0]
    author, target = config().members[0], config().members[1]
    bot.owner_id = author.id

    await dpytest.message(f"!coin grant 500 {target.mention}", member=author)
    sent = dpytest.get_message()
    assert sent.embeds
    assert await db.get_balance(target.id) == 500


@pytest.mark.cogs("cogs.economy")
async def test_grant_credits_multiple_tagged_members(bot):
    guild = config().guilds[0]
    author = config().members[0]
    t1, t2 = config().members[1], config().members[2]
    bot.owner_id = author.id

    await dpytest.message(f"!coin grant 500 {t1.mention} {t2.mention}", member=author)
    sent = dpytest.get_message()
    assert sent.embeds
    assert await db.get_balance(t1.id) == 500
    assert await db.get_balance(t2.id) == 500


@pytest.mark.cogs("cogs.economy")
async def test_take_debits_multiple_tagged_members(bot):
    guild = config().guilds[0]
    author = config().members[0]
    t1, t2 = config().members[1], config().members[2]
    bot.owner_id = author.id
    await db.add_coins(t1.id, 500)
    await db.add_coins(t2.id, 500)

    await dpytest.message(f"!coin take 200 {t1.mention} {t2.mention}", member=author)
    sent = dpytest.get_message()
    assert sent.embeds
    assert await db.get_balance(t1.id) == 300
    assert await db.get_balance(t2.id) == 300


@pytest.mark.cogs("cogs.economy")
async def test_gamble_win(bot, monkeypatch):
    from cogs.economy import cog as economy

    guild = config().guilds[0]
    author = config().members[0]
    await db.add_coins(author.id, 100)
    monkeypatch.setattr(economy.random, "random", lambda: 0.0)  # force a win

    await dpytest.message("!coin gamble 50", member=author)
    sent = dpytest.get_message()
    assert "Winner" in sent.embeds[0].title
    assert await db.get_balance(author.id) == 150


@pytest.mark.cogs("cogs.economy")
async def test_gamble_loss(bot, monkeypatch):
    from cogs.economy import cog as economy

    guild = config().guilds[0]
    author = config().members[0]
    await db.add_coins(author.id, 100)
    monkeypatch.setattr(economy.random, "random", lambda: 0.99)  # force a loss

    await dpytest.message("!coin gamble 50", member=author)
    sent = dpytest.get_message()
    assert "Bust" in sent.embeds[0].title
    assert await db.get_balance(author.id) == 50


@pytest.mark.cogs("cogs.economy")
async def test_shop_seed_denied_without_manage_guild(bot):
    author = config().members[0]
    with pytest.raises(commands.MissingPermissions):
        await dpytest.message("!shop seed", member=author)


@pytest.mark.cogs("cogs.economy")
async def test_shop_seed_populates_then_idempotent(bot):
    guild = config().guilds[0]
    author = config().members[0]
    await grant_perms(author, manage_guild=True)

    # First seed fills the empty shop with the full starter set.
    await dpytest.message("!shop seed", member=author)
    sent = dpytest.get_message()
    assert "Seeded" in sent.embeds[0].title
    assert await db.count_shop_items(guild.id) == len(_DEFAULT_SHOP_ITEMS)

    # Re-seeding adds nothing (idempotent by name) and says so.
    await dpytest.message("!shop seed", member=author)
    sent = dpytest.get_message()
    assert "already exist" in sent.embeds[0].description
    assert await db.count_shop_items(guild.id) == len(_DEFAULT_SHOP_ITEMS)


@pytest.mark.cogs("cogs.economy")
async def test_gamble_insufficient_funds(bot):
    guild = config().guilds[0]
    author = config().members[0]
    await db.add_coins(author.id, 10)

    await dpytest.message("!coin gamble 50", member=author)
    sent = dpytest.get_message()
    assert "Error" in sent.embeds[0].title
    assert await db.get_balance(author.id) == 10


@pytest.mark.cogs("cogs.economy")
async def test_pay_rejects_negative_amount(bot):
    """Audit regression: a negative /pay must not become a reverse-transfer."""
    guild = config().guilds[0]
    author, other = config().members[0], config().members[1]
    await db.set_coins(author.id, 100)
    await db.set_coins(other.id, 100)

    await dpytest.message(f"!pay {other.mention} -50", member=author)
    sent = dpytest.get_message()
    assert "Error" in sent.embeds[0].title
    assert await db.get_balance(author.id) == 100
    assert await db.get_balance(other.id) == 100


# ── Global economy: scoped leaderboards + owner-gated wipe ────────────────────
@pytest.mark.cogs("cogs.economy")
async def test_coin_top_server_scope_lists_only_this_server(bot):
    """Wallets are global; the default board filters them to this guild's
    members, and `global` shows everyone the bot knows."""
    author = config().members[0]
    outsider_id = 999_000_111  # has coins, but isn't in this test guild
    await db.add_coins(author.id, 500)
    await db.add_coins(outsider_id, 5_000)

    await dpytest.message("!coin top", member=author)
    server_board = dpytest.get_message().embeds[0]
    assert author.display_name in server_board.description
    assert str(outsider_id) not in server_board.description

    # /coin top carries a 5s per-user cooldown, so the global view is checked
    # from a second member.
    other = config().members[1]
    await dpytest.message("!coin top 1 global", member=other)
    global_board = dpytest.get_message().embeds[0]
    assert "Global" in global_board.title
    assert str(outsider_id) in global_board.description  # uncached user shown by id


@pytest.mark.cogs("cogs.economy")
async def test_coin_reset_is_owner_only(bot):
    """A guild admin can no longer wipe balances — the wallet they'd be
    deleting is the member's in every server, so it's bot-owner-only now."""
    author = config().members[0]
    await grant_perms(author, manage_guild=True)
    await db.add_coins(author.id, 400)

    with pytest.raises(commands.NotOwner):
        await dpytest.message("!coin reset", member=author)
    assert await db.get_balance(author.id) == 400


# ── Shop pricing stays the guild's, and now comes with a yardstick ────────────
@pytest.mark.cogs("cogs.economy")
async def test_a_server_still_sets_its_own_shop_prices(bot):
    """The faucets went bot-wide; the shop deliberately did not. A purchase is a
    sink — it destroys coins for this guild's own reward — so its price can't
    affect anyone outside the server, and its mods keep it."""
    guild = config().guilds[0]
    author = config().members[0]
    await grant_perms(author, manage_guild=True)

    cog = bot.get_cog("Economy")
    for kept in ("add", "edit", "remove", "seed"):
        assert cog.shop.get_command(kept) is not None

    # A mod's price is stored as given and nothing rewrites it — /shop seed's
    # scaling only ever applies to items it creates itself.
    await db.add_shop_item(guild.id, "VIP", 7500, "custom", payload="a perk")
    await dpytest.message("!shop seed", member=author)
    dpytest.get_message()
    priced = {i["name"]: i["price"] for i in await db.list_shop_items(guild.id)}
    assert priced["VIP"] == 7500


@pytest.mark.cogs("cogs.economy")
async def test_shop_quotes_each_price_as_a_time_to_earn(bot):
    """The number a mod was missing: what a price costs in play time."""
    guild = config().guilds[0]
    author = config().members[0]
    await grant_perms(author, manage_guild=True)

    await db.add_shop_item(guild.id, "VIP", 5000, "custom", payload="a perk")
    await dpytest.message("!shop list", member=author)
    embed = dpytest.get_message().embeds[0]

    from utils import helpers as h

    expected = h.fmt_duration(seconds_to_afford(5000))
    assert any(expected in field.value for field in embed.fields), embed.fields
    # And the footer is honest that the figure is a floor.
    assert "non-stop" in embed.footer.text


@pytest.mark.cogs("cogs.economy")
async def test_a_server_cannot_set_a_reward_amount(bot):
    """Every faucet subcommand is gone from /coin; the sinks and cosmetics stay."""
    author = config().members[0]
    await grant_perms(author, manage_guild=True)
    cog = bot.get_cog("Economy")

    for gone in ("daily", "streakbonus", "coop", "raid"):
        assert cog.coin.get_command(gone) is None
    for kept in ("name", "emoji", "raidsize", "config"):
        assert cog.coin.get_command(kept) is not None
