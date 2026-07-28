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
async def test_balance_replies_with_a_wallet_card(bot):
    """/balance is a rendered card now, not an embed."""
    author = config().members[0]
    await db.add_coins(author.id, 4_200)

    await dpytest.message("!balance", member=author)
    sent = dpytest.get_message()
    assert sent.attachments, "the wallet should come back as an image"
    attachment = sent.attachments[0]
    assert attachment.filename.endswith(".png")
    assert attachment.size > 1000  # a real render, not an empty file


@pytest.mark.cogs("cogs.economy")
async def test_balance_works_for_a_brand_new_account(bot):
    """No coins, no streak, nothing equipped — still a card, never an error."""
    author = config().members[1]
    await dpytest.message("!balance", member=author)
    assert dpytest.get_message().attachments


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


# ── Co-op payouts are rate-limited per member ─────────────────────────────────
# dpytest can't press a button, so these drive the payout path the views call
# (Economy._pay_party) directly — the SellConfirmView precedent.
@pytest.mark.cogs("cogs.economy")
async def test_a_coop_payout_can_only_be_claimed_once_per_cooldown(bot):
    """The gap this closes: the reward is a faucet and had no rate limit at all
    — only the invoker's few-second command cooldown — so two members could
    confirm a squad every half-minute forever."""
    cog = bot.get_cog("Economy")
    a, b = config().members[0].id, config().members[1].id

    paid, skipped = await cog._pay_party([a, b], "coop", 50)
    assert paid == [a, b] and skipped == []
    assert await db.get_balance(a) == 50

    # Straight back for another go: both are on cooldown, nobody is paid twice.
    paid, skipped = await cog._pay_party([a, b], "coop", 50)
    assert paid == [] and skipped == [a, b]
    assert await db.get_balance(a) == 50


@pytest.mark.cogs("cogs.economy")
async def test_one_member_on_cooldown_does_not_cost_the_party(bot):
    cog = bot.get_cog("Economy")
    a, b = config().members[0].id, config().members[1].id

    await cog._pay_party([a], "coop", 50)  # `a` alone claims first
    paid, skipped = await cog._pay_party([a, b], "coop", 50)
    assert paid == [b] and skipped == [a]
    assert await db.get_balance(a) == 50 and await db.get_balance(b) == 50


@pytest.mark.cogs("cogs.economy")
async def test_squad_and_raid_claims_are_independent(bot):
    cog = bot.get_cog("Economy")
    a = config().members[0].id

    assert (await cog._pay_party([a], "coop", 50))[0] == [a]
    # A spent /squad must not block /raid — they're separate faucets.
    assert (await cog._pay_party([a], "raid", 100))[0] == [a]
    assert await db.get_balance(a) == 150
    assert (await cog._pay_party([a], "raid", 100))[0] == []


@pytest.mark.cogs("cogs.economy")
async def test_the_coop_cooldown_is_bot_wide_and_owner_set(bot):
    """Same rule as every other cooldown: the claim is per user, so the length
    can't be a server's — it comes from bot_settings via !cooldown."""
    cog = bot.get_cog("Economy")
    from cogs.activities.constants import COOP_COOLDOWN_DEFAULT

    assert await cog._coop_cooldown("coop") == COOP_COOLDOWN_DEFAULT
    await db.set_activity_cooldown("coop", 60)
    assert await cog._coop_cooldown("coop") == 60
    # Junk in the row falls back to the default rather than to no cooldown.
    await db.set_bot_setting("cooldown:coop", "soon")
    assert await cog._coop_cooldown("coop") == COOP_COOLDOWN_DEFAULT


@pytest.mark.cogs("cogs.economy")
async def test_squad_refuses_to_open_while_the_host_is_on_cooldown(bot):
    """The real guard is the per-member claim; this just tells the host now
    rather than after five people have pressed Confirm."""
    author, mate = config().members[0], config().members[1]
    cog = bot.get_cog("Economy")
    await cog._pay_party([author.id], "coop", 50)

    await dpytest.message(f"!squad {mate.mention}", member=author)
    sent = dpytest.get_message()
    assert "cooldown" in sent.embeds[0].title.lower()
