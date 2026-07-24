"""
tests/test_autocomplete.py
Guards for the economy's tap-to-pick option lists.

Mobile-first means never asking someone to type a recipe key, a fish name, or
a shop item id from memory. Two kinds of check live here:

  1. A registration guard — every option that used to be free text still has
     an autocomplete (or static choices) attached to its slash option, so a
     refactor can't silently drop the picker.
  2. Behaviour tests — the callbacks are called directly with a stub
     interaction (they only ever read guild_id/user/guild), so they're
     exercised against a real DB without a live gateway.
"""

import types

import pytest

from utils import db
from utils import items as item_catalog

pytestmark = pytest.mark.asyncio


def _stub_interaction(guild, user):
    """The autocomplete callbacks only touch these three attributes."""
    return types.SimpleNamespace(guild_id=guild.id, guild=guild, user=user)


def _option(bot, qualified_name: str, param: str):
    """The slash option named `param` on the app command `qualified_name`."""
    for cmd in bot.tree.walk_commands():
        if cmd.qualified_name == qualified_name:
            return cmd._params[param]
    raise AssertionError(f"no app command {qualified_name!r} in the tree")


# ── registration guard ────────────────────────────────────────────────────────
_PICKER_OPTIONS = [
    ("craft make", "recipe"),
    ("craft info", "recipe"),
    ("fish sell", "fish"),
    ("fish buy", "item"),
    ("inventory use", "item"),
    ("inventory sell", "item"),
    ("inventory give", "item"),
    ("inventory info", "item"),
    ("shop buy", "item"),
    ("shop edit", "item"),
    ("shop remove", "item"),
    ("shop fulfill", "purchase_id"),
    ("progress title", "name"),
    ("casino roulette", "space"),
    ("casino flip", "side"),
]


@pytest.mark.cogs(
    "cogs.economy",
    "cogs.fishing",
    "cogs.inventory",
    "cogs.crafting",
    "cogs.casino",
    "cogs.progression",
)
async def test_every_picker_option_offers_suggestions(bot):
    """No economy option should leave a member guessing at free text."""
    missing = []
    for qualified_name, param in _PICKER_OPTIONS:
        option = _option(bot, qualified_name, param)
        if not (option.autocomplete or option.choices):
            missing.append(f"/{qualified_name} <{param}>")
    assert not missing, "Options with no picker: " + ", ".join(missing)


# ── /craft ────────────────────────────────────────────────────────────────────
@pytest.mark.cogs("cogs.crafting")
async def test_craft_recipe_autocomplete_marks_craftable_first(bot):
    guild = bot.guilds[0]
    user = guild.members[0]
    cog = bot.get_cog("Crafting")
    interaction = _stub_interaction(guild, user)

    # Nothing owned: every recipe still listed, all locked.
    choices = await cog._craft_make_ac(interaction, "")
    assert choices, "recipes should be suggested even with an empty inventory"
    assert all(c.name.startswith("🔒") for c in choices)

    # Stock up on one recipe's inputs — it jumps to the top, marked craftable.
    from cogs.crafting.recipes import RECIPES

    key = sorted(RECIPES)[0]
    for item_key, need in RECIPES[key].inputs.items():
        await db.add_item(guild.id, user.id, item_key, need)
    choices = await cog._craft_make_ac(interaction, "")
    assert choices[0].value == key
    assert choices[0].name.startswith("✅")


@pytest.mark.cogs("cogs.crafting")
async def test_craft_recipe_autocomplete_filters_on_current(bot):
    guild = bot.guilds[0]
    cog = bot.get_cog("Crafting")
    interaction = _stub_interaction(guild, guild.members[0])
    from cogs.crafting.recipes import RECIPES

    key = sorted(RECIPES)[0]
    choices = await cog._craft_info_ac(interaction, key)
    assert [c.value for c in choices] == [key]


# ── /fish ─────────────────────────────────────────────────────────────────────
@pytest.mark.cogs("cogs.fishing")
async def test_fish_sell_autocomplete_lists_your_bag(bot):
    guild = bot.guilds[0]
    user = guild.members[0]
    cog = bot.get_cog("Fishing")
    interaction = _stub_interaction(guild, user)

    assert await cog._fish_sell_ac(interaction, "") == []

    await db.record_catch(guild.id, user.id, "mackerel", 1.5, 11)
    await db.record_catch(guild.id, user.id, "mackerel", 1.2, 9)
    choices = await cog._fish_sell_ac(interaction, "")
    assert choices[0].value == "all"  # sell-everything option comes first
    assert [c.value for c in choices[1:]] == ["mackerel"]
    assert "×2" in choices[1].name


@pytest.mark.cogs("cogs.fishing")
async def test_fish_buy_autocomplete_marks_affordability(bot):
    guild = bot.guilds[0]
    user = guild.members[0]
    cog = bot.get_cog("Fishing")
    interaction = _stub_interaction(guild, user)

    choices = await cog._fish_buy_ac(interaction, "")
    assert choices, "the bait shop should list its stock"
    assert all(c.name.startswith("🔒") for c in choices)  # broke: everything locked
    cheapest = item_catalog.get(choices[0].value)

    await db.add_coins(guild.id, user.id, cheapest.price)
    choices = await cog._fish_buy_ac(interaction, "")
    assert choices[0].name.startswith("✅")


@pytest.mark.cogs("cogs.fishing")
async def test_fish_sell_accepts_the_everything_choice(bot):
    """The 'all' value the picker hands back must sell the whole bag."""
    from discord.ext import test as dpytest

    guild = dpytest.get_config().guilds[0]
    author = dpytest.get_config().members[0]
    await db.record_catch(guild.id, author.id, "mackerel", 1.5, 11)
    await db.record_catch(guild.id, author.id, "cod", 2.0, 20)

    await dpytest.message("!fish sell all", member=author)
    sent = dpytest.get_message()
    assert "Sold" in sent.embeds[0].title
    assert await db.get_bag(guild.id, author.id) == []
    assert await db.get_balance(guild.id, author.id) == 31


# ── /inventory ────────────────────────────────────────────────────────────────
@pytest.mark.cogs("cogs.inventory", "cogs.activities")
async def test_inventory_autocompletes_scope_to_what_you_own(bot):
    guild = bot.guilds[0]
    user = guild.members[0]
    cog = bot.get_cog("Inventory")
    interaction = _stub_interaction(guild, user)

    await db.add_item(guild.id, user.id, "iron_ore", 4)  # sellable, not usable
    await db.add_item(guild.id, user.id, "lucky_charm", 1)  # usable, not sellable

    usable = {c.value for c in await cog._use_ac(interaction, "")}
    sellable = {c.value for c in await cog._sell_ac(interaction, "")}
    owned = {c.value for c in await cog._give_ac(interaction, "")}
    assert "lucky_charm" in usable and "iron_ore" not in usable
    assert "iron_ore" in sellable and "lucky_charm" not in sellable
    assert owned == {"iron_ore", "lucky_charm"}

    # /inventory info works on items you don't own, so it lists the catalogue.
    catalogue = {c.value for c in await cog._info_ac(interaction, "treasure")}
    assert "treasure_chest" in catalogue


# ── /shop ─────────────────────────────────────────────────────────────────────
@pytest.mark.cogs("cogs.economy")
async def test_shop_autocomplete_shows_price_and_affordability(bot):
    guild = bot.guilds[0]
    user = guild.members[0]
    cog = bot.get_cog("Economy")
    interaction = _stub_interaction(guild, user)

    item_id = await db.add_shop_item(guild.id, "Custom Colour", 500, kind="custom")
    hidden_id = await db.add_shop_item(guild.id, "Retired Perk", 100, kind="custom")
    await db.edit_shop_item(guild.id, hidden_id, enabled=False)

    buyable = await cog._shop_buy_ac(interaction, "")
    assert [c.value for c in buyable] == [str(item_id)]  # hidden item not offered
    assert buyable[0].name.startswith("🔒")  # can't afford it yet

    await db.add_coins(guild.id, user.id, 500)
    buyable = await cog._shop_buy_ac(interaction, "")
    assert buyable[0].name.startswith("✅")

    # Admin pickers see hidden items too.
    admin = {c.value for c in await cog._shop_edit_ac(interaction, "")}
    assert admin == {str(item_id), str(hidden_id)}


@pytest.mark.cogs("cogs.economy")
async def test_shop_fulfill_autocomplete_lists_the_pending_queue(bot):
    guild = bot.guilds[0]
    user = guild.members[0]
    cog = bot.get_cog("Economy")
    interaction = _stub_interaction(guild, user)

    assert await cog._shop_fulfill_ac(interaction, "") == []

    item_id = await db.add_shop_item(guild.id, "Shoutout", 10, kind="custom")
    await db.add_coins(guild.id, user.id, 10)
    res = await db.purchase_item(guild.id, item_id, user.id)
    assert res["ok"]

    choices = await cog._shop_fulfill_ac(interaction, "")
    assert len(choices) == 1
    assert isinstance(choices[0].value, int)
    assert "Shoutout" in choices[0].name


# ── /progress title ───────────────────────────────────────────────────────────
@pytest.mark.cogs("cogs.progression")
async def test_title_autocomplete_offers_only_earned_titles(bot):
    from cogs.progression.definitions import ACHIEVEMENTS

    guild = bot.guilds[0]
    user = guild.members[0]
    cog = bot.get_cog("Progression")
    interaction = _stub_interaction(guild, user)

    # Nothing earned: only the "clear it" option.
    choices = await cog._title_ac(interaction, "")
    assert [c.value for c in choices] == ["none"]

    titled = next(a for a in ACHIEVEMENTS if a.reward.get("title"))
    await db.try_award_achievement(guild.id, user.id, titled.key)
    choices = await cog._title_ac(interaction, "")
    assert titled.reward["title"] in [c.value for c in choices]


# ── /casino ───────────────────────────────────────────────────────────────────
@pytest.mark.cogs("cogs.casino")
async def test_roulette_autocomplete_covers_outside_bets_and_numbers(bot):
    guild = bot.guilds[0]
    cog = bot.get_cog("Casino")
    interaction = _stub_interaction(guild, guild.members[0])

    from cogs.casino.helpers import parse_roulette_space

    choices = await cog._roulette_ac(interaction, "")
    values = [c.value for c in choices]
    assert values[:6] == ["red", "black", "odd", "even", "high", "low"]
    assert len(choices) <= 25
    assert all(parse_roulette_space(v) is not None for v in values)

    # Typing digits narrows to matching numbers only.
    numeric = await cog._roulette_ac(interaction, "1")
    assert [c.value for c in numeric] == [
        "1",
        "10",
        "11",
        "12",
        "13",
        "14",
        "15",
        "16",
        "17",
        "18",
        "19",
    ]
