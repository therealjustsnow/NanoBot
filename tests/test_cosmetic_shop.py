"""
tests/test_cosmetic_shop.py
The cosmetic shop: the catalogue rules that make it a real sink, and the
/shop unlock purchase path end to end.

The load-bearing rule is that a `purchase` cosmetic is *never* unlocked by
playing. If one ever became auto-unlockable it would stop costing anything and
the sink would quietly become a giveaway, so that is asserted from both ends —
the rule evaluator and the lazy-unlock helper the profile card uses.
"""

import os
import types

import pytest
from discord.ext import test as dpytest
from PIL import ImageFont

import utils.db as db
from cogs.identity.helpers import interleave_by_slot, newly_unlocked, unlock_context
from tests.conftest import config
from utils import cosmetics, profile_card


# ── Catalogue ─────────────────────────────────────────────────────────────────
def test_every_purchasable_cosmetic_has_a_price_and_a_real_slot():
    stock = cosmetics.purchasable()
    assert stock, "the shop should not be empty"
    for d in stock:
        assert d.price > 0, f"{d.key} is for sale for nothing"
        assert d.slot in cosmetics.SLOTS
        assert d.category in cosmetics.CATEGORIES
        assert d.description, f"{d.key} has no shop description"


def test_purchasable_is_sorted_cheapest_first():
    prices = [d.price for d in cosmetics.purchasable()]
    assert prices == sorted(prices)


def test_both_aisles_have_stock():
    for category in cosmetics.CATEGORIES:
        assert cosmetics.purchasable(category), f"{category} aisle is empty"


def test_a_shop_cosmetic_is_never_unlocked_by_playing():
    """The whole sink rests on this: no amount of levels, prestige, stats or
    achievements may hand over something the shop sells."""
    maxed = unlock_context(
        global_level=999,
        prestige=10,
        achievements={d.key for d in cosmetics.COSMETICS.values()},
        stats={key: 10**12 for key in ("balance", "fish_caught", "casino_games")},
    )
    for d in cosmetics.purchasable():
        assert not cosmetics.is_unlocked(d, maxed), f"{d.key} unlocked itself"
    assert not [d for d in newly_unlocked(set(), maxed) if d.price]
    assert not [d for d in cosmetics.auto_unlockable() if d.price]


def test_coin_glyphs_render_in_the_bundled_font():
    """A missing glyph draws as a tofu box, and the coin is the middle of the
    wallet card — catch it here rather than on someone's balance."""
    path = next((p for p in profile_card._FONT_CANDIDATES if os.path.exists(p)), None)
    if path is None:  # pragma: no cover - bare container without fonts
        pytest.skip("no TrueType font available")
    font = ImageFont.truetype(path, 40)
    missing = bytes(font.getmask("￿"))
    bad = [
        d.key
        for d in cosmetics.in_slot("coin")
        if bytes(font.getmask(d.glyph)) == missing
    ]
    assert not bad, f"coin glyphs not covered by the font: {bad}"


def test_every_declared_texture_pattern_and_style_is_implemented():
    """A typo in a def's `texture`/`pattern` would silently fall back to the
    default look rather than failing, so registry membership is checked."""
    for d in cosmetics.COSMETICS.values():
        assert d.texture in profile_card._TEXTURES, (d.key, d.texture)
        assert d.pattern in profile_card._BANNER_PATTERNS, (d.key, d.pattern)
        assert d.style in profile_card._BORDER_STYLES, (d.key, d.style)


def test_interleave_by_slot_gives_every_slot_a_turn():
    pairs = [("badge", f"b{i}") for i in range(5)] + [("banner", "n1"), ("coin", "c1")]
    out = interleave_by_slot(pairs)
    assert out[:3] == ["b0", "n1", "c1"]
    assert len(out) == len(pairs)
    assert interleave_by_slot([]) == []


# ── /shop unlock ──────────────────────────────────────────────────────────────
_ITEM = "banner_sunset"  # 12,000 coins


def _clear_cooldown(bot, qualified_name: str):
    """dpytest replays messages faster than a human can; the per-user command
    cooldown is not what any of these are testing."""
    bot.get_command(qualified_name)._buckets._cache.clear()


@pytest.mark.cogs("cogs.economy")
async def test_unlock_charges_once_and_grants_the_cosmetic(bot):
    author = config().members[0]
    price = cosmetics.get(_ITEM).price
    await db.add_coins(author.id, price + 500)

    await dpytest.message(f"!shop unlock {_ITEM}", member=author)
    assert dpytest.get_message().embeds
    assert _ITEM in await db.get_unlocked_cosmetics(author.id)
    assert await db.get_balance(author.id) == 500

    # Buying it again is refused before any coins move.
    _clear_cooldown(bot, "shop unlock")
    await dpytest.message(f"!shop unlock {_ITEM}", member=author)
    dpytest.get_message()
    assert await db.get_balance(author.id) == 500


@pytest.mark.cogs("cogs.economy")
async def test_unlock_refuses_when_you_cannot_afford_it(bot):
    author = config().members[1]
    await db.add_coins(author.id, 10)

    await dpytest.message(f"!shop unlock {_ITEM}", member=author)
    embed = dpytest.get_message().embeds[0]
    assert "short" in embed.description
    assert await db.get_balance(author.id) == 10  # nothing was taken
    assert _ITEM not in await db.get_unlocked_cosmetics(author.id)


@pytest.mark.cogs("cogs.economy")
async def test_unlock_refuses_something_that_is_not_for_sale(bot):
    """An earned cosmetic can't be bought — that would undo the achievement."""
    author = config().members[0]
    await db.add_coins(author.id, 10_000_000)

    await dpytest.message("!shop unlock banner_ember", member=author)
    embed = dpytest.get_message().embeds[0]
    assert "isn't for sale" in embed.description
    assert "banner_ember" not in await db.get_unlocked_cosmetics(author.id)
    assert await db.get_balance(author.id) == 10_000_000


@pytest.mark.cogs("cogs.economy")
async def test_unlock_rejects_an_unknown_name(bot):
    author = config().members[0]
    await dpytest.message("!shop unlock definitely not a cosmetic", member=author)
    assert dpytest.get_message().embeds


# ── The aisles ────────────────────────────────────────────────────────────────
@pytest.mark.cogs("cogs.economy")
async def test_the_hub_lists_every_aisle(bot):
    author = config().members[0]
    await dpytest.message("!shop", member=author)
    embed = dpytest.get_message().embeds[0]
    names = " ".join(f.name for f in embed.fields)
    for expected in ("Profile", "Wallet", "Server"):
        assert expected in names, names


@pytest.mark.cogs("cogs.economy")
async def test_each_cosmetic_aisle_marks_what_you_can_afford(bot):
    author = config().members[0]
    await db.add_coins(author.id, 6_000)  # enough for the cheapest, not the rest

    for aisle in ("profile", "wallet"):
        _clear_cooldown(bot, f"shop {aisle}")
        await dpytest.message(f"!shop {aisle}", member=author)
        embed = dpytest.get_message().embeds[0]
        marks = " ".join(f.name for f in embed.fields)
        assert "🟢" in marks or "🔒" in marks, marks
        assert "every server" in embed.footer.text


@pytest.mark.cogs("cogs.economy")
async def test_the_unlock_picker_leads_with_what_you_can_afford(bot):
    """Nobody types `banner_sakura` on a phone; the picker also has to say
    which rows are actually buyable right now."""
    guild = bot.guilds[0]
    user = guild.members[0]
    cog = bot.get_cog("Economy")
    interaction = types.SimpleNamespace(guild_id=guild.id, guild=guild, user=user)

    broke = await cog._shop_unlock_ac(interaction, "")
    assert broke, "the picker must never come back empty"
    assert all(c.name.startswith("🔒") for c in broke)
    assert all(len(c.name) <= 100 for c in broke)

    cheapest = cosmetics.purchasable()[0]
    await db.add_coins(user.id, cheapest.price)
    choices = await cog._shop_unlock_ac(interaction, "")
    assert choices[0].value == cheapest.key
    assert choices[0].name.startswith("🟢")

    # Owned ones sink below everything buyable (past the 25-row cut, on a big
    # catalogue — so this looks it up by name rather than in the top slice).
    await db.unlock_cosmetic(user.id, cheapest.key, at=0)
    rows = await cog._shop_unlock_ac(interaction, cheapest.name.lower())
    owned_row = next(c for c in rows if c.value == cheapest.key)
    assert owned_row.name.startswith("✅")
    assert rows.index(owned_row) == len(rows) - 1

    # And typing filters by name, key, slot or aisle.
    assert all(
        cosmetics.get(c.value).category == "wallet"
        for c in await cog._shop_unlock_ac(interaction, "wallet")
    )


@pytest.mark.cogs("cogs.economy")
async def test_the_server_aisle_still_answers_to_its_old_name(bot):
    """`/shop list` was the guild shop for a long time; the prefix alias keeps
    working after the rename to `/shop server`."""
    guild, author = config().guilds[0], config().members[0]
    await db.add_shop_item(guild.id, "VIP", 5_000, "custom", payload="a perk")

    for invocation in ("!shop server", "!shop list"):
        _clear_cooldown(bot, "shop server")
        await dpytest.message(invocation, member=author)
        embed = dpytest.get_message().embeds[0]
        assert any("VIP" in f.name for f in embed.fields)
