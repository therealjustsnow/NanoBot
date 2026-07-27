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
    ("inventory view", "category"),
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
    ("adventure toggle", "activity"),
    ("profile cosmetics", "slot"),
    ("profile equip", "cosmetic"),
    ("profile unequip", "cosmetic"),
]

# NOT listed above, deliberately: /profile grant, grantall and revoke are
# bot-owner tools and are prefix-only now (with_app_command=False), so they have
# no slash options to offer a picker for. test_owner_commands_are_not_slash
# in tests/test_slash_surface.py is what holds that line.


@pytest.mark.cogs(
    "cogs.economy",
    "cogs.fishing",
    "cogs.inventory",
    "cogs.crafting",
    "cogs.casino",
    "cogs.progression",
    "cogs.activities",
    "cogs.identity",
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
        await db.add_item(user.id, item_key, need)
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

    await db.record_catch(user.id, "mackerel", 1.5, 11)
    await db.record_catch(user.id, "mackerel", 1.2, 9)
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

    await db.add_coins(user.id, cheapest.price)
    choices = await cog._fish_buy_ac(interaction, "")
    assert choices[0].name.startswith("✅")


@pytest.mark.cogs("cogs.fishing")
async def test_fish_sell_accepts_the_everything_choice(bot):
    """The 'all' value the picker hands back must sell the whole bag."""
    from discord.ext import test as dpytest

    guild = dpytest.get_config().guilds[0]
    author = dpytest.get_config().members[0]
    await db.record_catch(author.id, "mackerel", 1.5, 11)
    await db.record_catch(author.id, "cod", 2.0, 20)

    await dpytest.message("!fish sell all", member=author)
    sent = dpytest.get_message()
    assert "Sold" in sent.embeds[0].title
    assert await db.get_bag(author.id) == []
    assert await db.get_balance(author.id) == 31


# ── /inventory ────────────────────────────────────────────────────────────────
@pytest.mark.cogs("cogs.inventory", "cogs.activities")
async def test_inventory_autocompletes_scope_to_what_you_own(bot):
    guild = bot.guilds[0]
    user = guild.members[0]
    cog = bot.get_cog("Inventory")
    interaction = _stub_interaction(guild, user)

    await db.add_item(user.id, "iron_ore", 4)  # sellable, not usable
    await db.add_item(user.id, "lucky_charm", 1)  # usable, not sellable

    usable = {c.value for c in await cog._use_ac(interaction, "")}
    sellable = {c.value for c in await cog._sell_ac(interaction, "")}
    owned = {c.value for c in await cog._give_ac(interaction, "")}
    assert "lucky_charm" in usable and "iron_ore" not in usable
    assert "iron_ore" in sellable and "lucky_charm" not in sellable
    assert owned == {"iron_ore", "lucky_charm"}

    # /inventory info works on items you don't own, so it lists the catalogue.
    catalogue = {c.value for c in await cog._info_ac(interaction, "treasure")}
    assert "treasure_chest" in catalogue


@pytest.mark.cogs("cogs.inventory", "cogs.activities")
async def test_inventory_sell_autocomplete_offers_bulk_rows(bot):
    """Clearing an inventory shouldn't be one command per stack: the sell
    picker leads with everything-sellable, then a row per category."""
    guild = bot.guilds[0]
    user = guild.members[0]
    cog = bot.get_cog("Inventory")
    interaction = _stub_interaction(guild, user)

    assert await cog._sell_ac(interaction, "") == []  # nothing owned, nothing offered

    await db.add_item(user.id, "iron_ore", 4)  # material, 25 ea
    await db.add_item(user.id, "golden_antler", 1)  # treasure, 300 ea
    await db.add_item(user.id, "treasure_key", 2)  # unsellable — never listed

    choices = await cog._sell_ac(interaction, "")
    values = [c.value for c in choices]
    assert values[0] == "all"
    assert "400 coins" in choices[0].name  # 4×25 + 300, credited in one go
    assert "cat:material" in values and "cat:treasure" in values
    assert "treasure_key" not in values
    assert values.index("cat:material") < values.index("iron_ore")


@pytest.mark.cogs("cogs.inventory", "cogs.activities")
async def test_inventory_sell_autocomplete_hides_category_rows_when_pointless(bot):
    """One category is what 'everything' already covers — no duplicate row."""
    guild = bot.guilds[0]
    user = guild.members[0]
    cog = bot.get_cog("Inventory")
    interaction = _stub_interaction(guild, user)

    await db.add_item(user.id, "iron_ore", 2)
    values = [c.value for c in await cog._sell_ac(interaction, "")]
    assert values == ["all", "iron_ore"]


@pytest.mark.cogs("cogs.inventory", "cogs.activities")
async def test_inventory_sell_accepts_the_bulk_values_it_suggests(bot):
    """Every value the picker hands back must be a valid sell target — each
    one reaching the confirmation step rather than an unknown-item error."""
    from discord.ext import test as dpytest

    from tests.test_inventory_commands import _FakeInteraction

    cog = bot.get_cog("Inventory")
    author = dpytest.get_config().members[0]
    await db.add_item(author.id, "iron_ore", 2)
    await db.add_item(author.id, "golden_antler", 1)

    await dpytest.message("!inventory sell cat:treasure", member=author)
    assert "Sell these?" in dpytest.get_message().embeds[0].title
    await cog._pending_sell[author.id]._on_confirm(_FakeInteraction(author.id))
    assert await db.get_item_qty(author.id, "golden_antler") == 0

    await dpytest.message("!inventory sell all", member=author)
    assert "Sell these?" in dpytest.get_message().embeds[0].title
    await cog._pending_sell[author.id]._on_confirm(_FakeInteraction(author.id))
    assert await db.get_item_qty(author.id, "iron_ore") == 0
    assert await db.get_balance(author.id) == 350


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

    await db.add_coins(user.id, 500)
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
    await db.add_coins(user.id, 10)
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
    await db.try_award_achievement(user.id, titled.key)
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


# ── /profile ──────────────────────────────────────────────────────────────────
@pytest.mark.cogs("cogs.identity")
async def test_equip_autocomplete_orders_wearable_then_worn_then_locked(bot):
    """A new account still gets a full list: what it can wear now, and how to
    earn everything else — the /craft make shape, not an empty box."""
    from utils import cosmetics

    guild = bot.guilds[0]
    user = guild.members[0]
    cog = bot.get_cog("Identity")
    interaction = _stub_interaction(guild, user)

    choices = await cog._equip_ac(interaction, "")
    assert choices, "the picker must never come back empty"
    # The free "default" cosmetics are wearable straight away.
    assert choices[0].name.startswith("▫️")
    assert "banner_default" in [c.value for c in choices]
    locked = [c for c in choices if c.name.startswith("🔒")]
    assert locked, "locked cosmetics stay listed, with their unlock line"
    assert cosmetics.describe_unlock(cosmetics.get("banner_ember")) in "".join(
        c.name for c in locked
    )

    # Own one and wear it: it moves out of locked and gets the worn marker.
    await db.unlock_cosmetic(user.id, "banner_ember", at=0)
    await db.set_equipped(user.id, "banner", ["banner_ember"])
    choices = await cog._equip_ac(interaction, "")
    ember = next(c for c in choices if c.value == "banner_ember")
    assert ember.name.startswith("✅")
    # Wearable-now entries still sort ahead of the one already on.
    assert choices.index(ember) > choices.index(
        next(c for c in choices if c.value == "border_none")
    )
    assert all(len(c.name) <= 100 for c in choices)


@pytest.mark.cogs("cogs.identity")
async def test_equip_autocomplete_filters_by_name_key_and_slot(bot):
    guild = bot.guilds[0]
    cog = bot.get_cog("Identity")
    interaction = _stub_interaction(guild, guild.members[0])

    by_name = await cog._equip_ac(interaction, "ember")
    assert [c.value for c in by_name] == ["banner_ember"]

    by_key = await cog._equip_ac(interaction, "plate_neon")
    assert [c.value for c in by_key] == ["plate_neon"]

    by_slot = await cog._equip_ac(interaction, "border")
    assert by_slot and all(c.value.startswith("border_") for c in by_slot)


@pytest.mark.cogs("cogs.identity")
async def test_unequip_autocomplete_lists_only_what_is_worn(bot):
    guild = bot.guilds[0]
    user = guild.members[0]
    cog = bot.get_cog("Identity")
    interaction = _stub_interaction(guild, user)

    assert await cog._unequip_ac(interaction, "") == []

    await db.set_equipped(user.id, "badge", ["badge_veteran", "badge_angler"])
    choices = await cog._unequip_ac(interaction, "")
    values = [c.value for c in choices]
    # Listed in the order they sit on the card, not catalogue order.
    assert values[:2] == ["badge_veteran", "badge_angler"]
    # Multi-value slots also offer the clear-the-lot shortcut the command takes.
    assert values[-1] == "badge"
    assert "(2 worn)" in choices[-1].name


@pytest.mark.cogs("cogs.identity")
async def test_owner_cosmetic_grants_have_no_slash_options_to_leak(bot):
    """The cosmetic catalogue is public; the *server list* is not.

    /profile grantall used to take a guild picker, which meant an autocomplete
    listing every server the bot is in — and autocompletes fire before the
    command's own is_owner check, so it needed its own gate. Moving the three
    owner grants off slash removed the callback and the gate with it: there is
    no interaction path to the guild list at all now.
    """
    cog = bot.get_cog("Identity")
    assert not hasattr(cog, "_grantall_guild_ac")

    for name in ("profile_grant", "profile_grantall", "profile_revoke"):
        cmd = getattr(cog, name)
        assert cmd.app_command is None, f"{name} is back on the slash tree"


# ══════════════════════════════════════════════════════════════════════════════
#  Beyond the economy — the same rule applied to the rest of the surface
# ══════════════════════════════════════════════════════════════════════════════
_MOD_PICKER_OPTIONS = [
    ("tag use", "name"),
    ("tag preview", "name"),
    ("tag edit", "name"),
    ("tag delete", "name"),
    ("recurring pause", "reminder_id"),
    ("recurring resume", "reminder_id"),
    ("recurring cancel", "reminder_id"),
    ("recurring every", "interval"),
    ("automod badword remove", "word"),
    ("automod attachword remove", "word"),
    ("unban", "user_id"),
    ("purge", "only"),
    ("purge", "mode"),
    ("cban", "wait"),
    ("tempban", "duration"),
    ("freeze", "duration"),
    ("slow", "delay"),
    ("slow", "length"),
    ("level toggle", "state"),
    ("level reward", "action"),
    ("level ignore", "action"),
    ("birthday gifs", "state"),
    ("birthday voice", "state"),
    ("birthday ping", "state"),
    ("gatekeeper minage", "duration"),
    ("gatekeeper unmuteage", "duration"),
    ("gatekeeper kicktimeout", "duration"),
    ("remindme", "time"),
    ("reminders user", "time"),
    ("welcome set", "color"),
    ("leave set", "color"),
]


@pytest.mark.cogs(
    "cogs.tags",
    "cogs.recurring",
    "cogs.automod",
    "cogs.moderation",
    "cogs.leveling",
    "cogs.birthday",
    "cogs.gatekeeper",
    "cogs.reminders",
    "cogs.welcome",
)
async def test_non_economy_picker_options_offer_suggestions(bot):
    """The mobile-first picker rule isn't an economy-only rule."""
    missing = []
    for qualified_name, param in _MOD_PICKER_OPTIONS:
        option = _option(bot, qualified_name, param)
        if not (option.autocomplete or option.choices):
            missing.append(f"/{qualified_name} <{param}>")
    assert not missing, "Options with no picker: " + ", ".join(missing)


# ── /tag ──────────────────────────────────────────────────────────────────────
@pytest.mark.cogs("cogs.tags")
async def test_tag_autocomplete_lists_personal_then_global(bot):
    guild = bot.guilds[0]
    user = guild.members[0]
    cog = bot.get_cog("Tags")
    interaction = _stub_interaction(guild, user)

    assert await cog._tag_read_ac(interaction, "") == []

    await db.set_tag(guild.id, str(user.id), "mine", "a personal note", None)
    await db.set_tag(guild.id, "global", "rules", "read the rules", None)
    choices = await cog._tag_read_ac(interaction, "")
    assert [c.value for c in choices] == ["mine", "rules"]
    assert choices[0].name.startswith("📌") and choices[1].name.startswith("🌐")
    assert "a personal note" in choices[0].name


@pytest.mark.cogs("cogs.tags")
async def test_tag_edit_autocomplete_hides_global_without_manage_messages(bot):
    """/tag edit refuses a global tag without Manage Messages, so the picker
    must not offer one."""
    from tests.conftest import grant_perms

    guild = bot.guilds[0]
    user = guild.members[0]
    cog = bot.get_cog("Tags")
    interaction = _stub_interaction(guild, user)

    await db.set_tag(guild.id, str(user.id), "mine", "personal", None)
    await db.set_tag(guild.id, "global", "rules", "global", None)

    assert [c.value for c in await cog._tag_write_ac(interaction, "")] == ["mine"]
    # But reading one is fine for everyone.
    assert "rules" in [c.value for c in await cog._tag_read_ac(interaction, "")]

    await grant_perms(user, manage_messages=True)
    assert [c.value for c in await cog._tag_write_ac(interaction, "")] == [
        "mine",
        "rules",
    ]


@pytest.mark.cogs("cogs.tags")
async def test_tag_autocomplete_skips_a_shadowed_global(bot):
    """A personal tag wins the lookup, so listing both would be a lie."""
    guild = bot.guilds[0]
    user = guild.members[0]
    cog = bot.get_cog("Tags")

    await db.set_tag(guild.id, str(user.id), "same", "mine", None)
    await db.set_tag(guild.id, "global", "same", "theirs", None)
    choices = await cog._tag_read_ac(_stub_interaction(guild, user), "")
    assert [c.value for c in choices] == ["same"]
    assert choices[0].name.startswith("📌")


# ── /recurring ────────────────────────────────────────────────────────────────
@pytest.mark.cogs("cogs.recurring")
async def test_recurring_autocomplete_scopes_to_the_actionable_state(bot):
    guild = bot.guilds[0]
    user = guild.members[0]
    cog = bot.get_cog("Recurring")
    interaction = _stub_interaction(guild, user)

    base = {
        "target_id": str(user.id),
        "set_by_id": str(user.id),
        "guild_id": str(guild.id),
        "channel_id": str(guild.text_channels[0].id),
        "interval": 86400,
        "dm": 1,
        "fire_count": 0,
    }
    await db.set_recurring(
        {**base, "id": "run001", "message": "standup", "next_due": 1, "paused": 0}
    )
    await db.set_recurring(
        {
            **base,
            "id": "pau002",
            "message": "payday",
            "next_due": 2,
            "paused": 1,
            "label": "Payday",
        }
    )

    assert [c.value for c in await cog._pause_ac(interaction, "")] == ["run001"]
    assert [c.value for c in await cog._resume_ac(interaction, "")] == ["pau002"]
    assert {c.value for c in await cog._cancel_ac(interaction, "")} == {
        "run001",
        "pau002",
    }

    # The label beats the raw message, and the id is still in the name to type.
    paused = (await cog._resume_ac(interaction, ""))[0]
    assert "Payday" in paused.name and "pau002" in paused.name
    assert paused.name.startswith("⏸️")

    # Someone else's reminders are never suggested.
    other = _stub_interaction(guild, guild.members[1])
    assert await cog._cancel_ac(other, "") == []


# ── /automod ──────────────────────────────────────────────────────────────────
@pytest.mark.cogs("cogs.automod")
async def test_automod_word_autocompletes_list_the_configured_words(bot):
    from cogs.automod.autocomplete import (
        _attachment_word_autocomplete,
        _badword_autocomplete,
    )

    guild = bot.guilds[0]
    interaction = _stub_interaction(guild, guild.members[0])

    assert await _badword_autocomplete(interaction, "") == []

    await db.add_automod_badword(guild.id, "zebra")
    await db.add_automod_badword(guild.id, "apple")
    await db.add_automod_attachment_word(guild.id, "invoice")

    assert [c.value for c in await _badword_autocomplete(interaction, "")] == [
        "apple",
        "zebra",
    ]
    assert [c.value for c in await _badword_autocomplete(interaction, "zeb")] == [
        "zebra"
    ]
    # The two lists stay separate.
    assert [c.value for c in await _attachment_word_autocomplete(interaction, "")] == [
        "invoice"
    ]


# ── /unban ────────────────────────────────────────────────────────────────────
@pytest.mark.cogs("cogs.moderation")
async def test_unban_autocomplete_needs_ban_permissions(bot):
    """Who a server has banned isn't public, and autocomplete fires before the
    command's own permission check."""
    import types as _types

    from tests.conftest import grant_perms

    guild = bot.guilds[0]
    user = guild.members[0]
    cog = bot.get_cog("Moderation")

    banned = [(4242, "TrollUser#0001"), (99, "Spammer#0002")]

    async def _fake_bans(_guild):
        return banned

    cog._banned_users = _types.MethodType(lambda self, g: _fake_bans(g), cog)

    interaction = _stub_interaction(guild, user)
    assert await cog._unban_ac(interaction, "") == []

    await grant_perms(user, ban_members=True)
    choices = await cog._unban_ac(interaction, "")
    assert {c.value for c in choices} == {"4242", "99"}
    assert "TrollUser" in choices[0].name or "TrollUser" in choices[1].name

    # Filtering works on both the name and the raw id.
    assert [c.value for c in await cog._unban_ac(interaction, "troll")] == ["4242"]
    assert [c.value for c in await cog._unban_ac(interaction, "99")] == ["99"]

    # The most recent ban is floated to the top.
    bot.last_banned[guild.id] = 99
    assert (await cog._unban_ac(interaction, ""))[0].value == "99"


# ── duration pickers ──────────────────────────────────────────────────────────
async def test_duration_picker_suggests_without_restricting():
    """A duration option must never become a fixed list — every one of these
    commands parses free-form input."""
    from utils import helpers as h

    picker = h.duration_picker([("1 hour", "1h"), ("1 day", "1d")])

    listed = await picker(None, "")
    assert [c.value for c in listed] == ["1h", "1d"]

    # Something unlisted but valid comes back first, with its parsed length so
    # a typo is visible before sending.
    typed = await picker(None, "45m")
    assert typed[0].value == "45m"
    assert "45m" in typed[0].name

    # Nonsense isn't echoed — only the (filtered) suggestions remain. (A
    # multi-unit "3h30m" is not a typo the picker hides: parse_duration takes
    # one unit, so the commands reject it too.)
    assert [c.value for c in await picker(None, "banana")] == []
    assert [c.value for c in await picker(None, "3h30m")] == []

    # Typing a listed value doesn't duplicate it.
    assert [c.value for c in await picker(None, "1h")] == ["1h"]


async def test_colour_picker_keeps_hex_typable():
    from cogs.welcome import _color_autocomplete

    presets = await _color_autocomplete(None, "")
    assert presets and all(c.value.startswith("#") for c in presets)
    assert [c.value for c in await _color_autocomplete(None, "green")] == ["#57F287"]

    typed = await _color_autocomplete(None, "#123456")
    assert typed[0].value == "#123456"
    assert await _color_autocomplete(None, "nope") == []
