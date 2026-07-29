"""
Command-level tests for cogs/inventory/ under dpytest (parse → check → DB →
reply) — plus the effect-stacking cap regression from the release audit.

Bulk sells finish on a button, which dpytest can't dispatch, so those tests
drive the SellConfirmView's handlers directly with a duck-typed interaction
(the BlackjackView pattern in tests/test_casino_commands.py).
"""

import types

import pytest
from discord.ext import commands
from discord.ext import test as dpytest

import utils.db as db
from utils import items
from cogs.inventory.constants import EFFECT_MAX_DURATION, EFFECT_MAX_USES
from tests.conftest import config


def views():
    """The view module the *live* cog is using.

    Loading a cog replaces `cogs.inventory.views` in sys.modules, so a class
    bound at collection time is a different object from the one the cog builds —
    an `isinstance` against it passes in isolation and fails in a full run, which
    is the most annoying possible way to learn this (see tests/test_fishing_views).
    """
    import cogs.inventory.views as module

    return module


class _FakeResponse:
    def __init__(self):
        self.edited = None
        self.sent = None
        self.deferred = False

    async def edit_message(self, **kwargs):
        self.edited = kwargs

    async def send_message(self, **kwargs):
        self.sent = kwargs

    async def defer(self, **kwargs):
        self.deferred = True


class _FakeMessage:
    def __init__(self):
        self.edits = []

    async def edit(self, **kwargs):
        self.edits.append(kwargs)


class _FakeInteraction:
    """Minimal duck-typed discord.Interaction for the confirm buttons and the
    gear picker's select."""

    def __init__(self, user_id: int):
        self.user = types.SimpleNamespace(id=user_id)
        self.response = _FakeResponse()
        self.edited = None

    async def edit_original_response(self, **kwargs):
        self.edited = kwargs
        return _FakeMessage()


class _FakeCtx:
    """Just enough Context for the picker — dpytest doesn't surface the view a
    reply carried, and the view is the thing under test."""

    def __init__(self, member, guild):
        self.author = member
        self.guild = guild
        self.replied = None

    async def reply(self, **kwargs):
        self.replied = kwargs
        return _FakeMessage()


def _pending_sell(bot, user_id):
    """The live confirmation the last bulk-sell command put up."""
    return bot.get_cog("Inventory")._pending_sell[user_id]


@pytest.mark.cogs("cogs.inventory")
async def test_bare_inventory_empty_message(bot):
    author = config().members[0]
    await dpytest.message("!inventory", member=author)
    sent = dpytest.get_message()
    assert "empty" in sent.embeds[0].description.lower()


@pytest.mark.cogs("cogs.inventory")
async def test_inventory_view_is_reachable_as_a_slash_subcommand(bot):
    """The gap this closes: /inventory offered use/sell/give/info and no way to
    simply look at what you own, because a hybrid group's own callback isn't
    invocable over slash without `fallback`."""
    group = bot.get_command("inventory")
    assert isinstance(group, commands.HybridGroup)
    assert group.fallback == "view"
    assert "view" in {c.name for c in group.app_command.commands}


@pytest.mark.cogs("cogs.inventory", "cogs.activities", "cogs.fishing")
async def test_inventory_overview_summarises_and_filters_by_category(bot):
    author = config().members[0]
    await db.add_item(author.id, "iron_ore", 5)
    await db.add_item(author.id, "bait_worm", 3)

    await dpytest.message("!inventory", member=author)
    embed = dpytest.get_message().embeds[0]
    # The overview leads with totals rather than dumping a raw list.
    assert "8" in embed.description  # 5 + 3 items
    assert "2" in embed.description  # across two stacks
    names = {f.name for f in embed.fields}
    assert len(names) >= 2

    # A category narrows it to one section and says so.
    await dpytest.message("!inventory bait", member=author)
    embed = dpytest.get_message().embeds[0]
    assert "only" in embed.description
    body = "\n".join(f.value for f in embed.fields)
    assert "worm" in body.lower() and "iron" not in body.lower()

    # An unknown category is reported, not silently ignored.
    await dpytest.message("!inventory nonsense", member=author)
    assert "no **nonsense** category" in dpytest.get_message().embeds[0].description


@pytest.mark.cogs("cogs.inventory", "cogs.activities", "cogs.fishing")
async def test_inventory_category_autocomplete_marks_what_you_own(bot):
    author = config().members[0]
    await db.add_item(author.id, "bait_worm", 3)
    cog = bot.get_cog("Inventory")
    interaction = types.SimpleNamespace(user=types.SimpleNamespace(id=author.id))
    choices = await cog._category_ac(interaction, "")
    by_value = {c.value: c.name for c in choices}
    assert "bait" in by_value and "item(s)" in by_value["bait"]
    # Categories you own nothing in still list, so the picker is never blank.
    assert "none yet" in by_value["treasure"]
    # Typing filters the list.
    filtered = await cog._category_ac(interaction, "bait")
    assert {c.value for c in filtered} == {"bait"}


@pytest.mark.cogs("cogs.inventory")
async def test_use_timed_effect_grants_and_consumes(bot):
    guild = config().guilds[0]
    author = config().members[0]
    await db.add_item(author.id, "lucky_charm", 2)

    await dpytest.message("!inventory use lucky_charm", member=author)
    sent = dpytest.get_message()
    assert "luck" in sent.embeds[0].description
    assert await db.get_item_qty(author.id, "lucky_charm") == 1
    effs = await db.get_active_effects(author.id)
    assert effs["luck"]["magnitude"] == 0.5


@pytest.mark.cogs("cogs.inventory")
async def test_use_bulk_timed_effect_capped_at_duration_ceiling(bot):
    """Audit regression: qty × duration must not exceed EFFECT_MAX_DURATION,
    and only the clamped qty may be consumed (no items eaten for nothing)."""
    guild = config().guilds[0]
    author = config().members[0]
    await db.add_item(author.id, "lucky_charm", 1000)

    await dpytest.message("!inventory use lucky_charm 1000", member=author)
    dpytest.get_message()
    effs = await db.get_active_effects(author.id)
    granted = effs["luck"]["expires_at"] - __import__("time").time()
    assert granted <= EFFECT_MAX_DURATION + 5
    # lucky_charm is 1800s a use → cap allows 48; the other 952 stay owned.
    expected_used = EFFECT_MAX_DURATION // 1800
    assert await db.get_item_qty(author.id, "lucky_charm") == 1000 - expected_used


@pytest.mark.cogs("cogs.inventory", "cogs.fishing")
async def test_use_arms_several_items_from_one_command(bot):
    """A loadout is bait *and* a charm, and arming it was one command each.

    Quoted here because a prefix invocation splits on whitespace — `item` is
    positional so that `qty` can follow it — and slash hands the whole list over
    as one option, which is the surface this is for.
    """
    author = config().members[0]
    await db.add_item(author.id, "lucky_charm", 1)
    await db.add_item(author.id, "bait_worm", 1)

    await dpytest.message('!inventory use "lucky_charm, bait_worm"', member=author)
    sent = dpytest.get_message()

    assert "Armed 2 of 2" in sent.embeds[0].title
    effects = await db.get_active_effects(author.id)
    assert {"luck", "fish_bait"} <= set(effects)
    assert await db.get_item_qty(author.id, "lucky_charm") == 0
    assert await db.get_item_qty(author.id, "bait_worm") == 0


@pytest.mark.cogs("cogs.inventory", "cogs.fishing")
async def test_one_bad_pick_in_a_list_does_not_stop_the_rest(bot):
    """Each line carries its own refusal — a charm you've run out of can't
    swallow the bait you have."""
    author = config().members[0]
    await db.add_item(author.id, "bait_worm", 1)

    await dpytest.message('!inventory use "lucky_charm, bait_worm"', member=author)
    desc = dpytest.get_message().embeds[0].description

    assert "lucky" in desc.lower() and "Worm" in desc
    assert "fish_bait" in await db.get_active_effects(author.id)


@pytest.mark.cogs("cogs.inventory")
async def test_bare_use_opens_the_gear_picker(bot):
    author, guild = config().members[0], config().guilds[0]
    await db.add_item(author.id, "lucky_charm", 2)
    cog = bot.get_cog("Inventory")
    ctx = _FakeCtx(author, guild)

    await cog._use_picker(ctx)

    view = ctx.replied["view"]
    # By class *name*: loading several cogs can reload the views module, so the
    # live class is not always the object an import at collection time bound.
    assert type(view).__name__ == "UseGearView"
    assert [o.value for o in view.select.options] == ["lucky_charm"]
    # Nothing is armed by opening it.
    assert await db.get_active_effects(author.id) == {}


@pytest.mark.cogs("cogs.inventory", "cogs.fishing")
async def test_the_picker_arms_everything_selected_in_one_press(bot):
    author, guild = config().members[0], config().guilds[0]
    await db.add_item(author.id, "lucky_charm", 1)
    await db.add_item(author.id, "bait_worm", 1)
    cog = bot.get_cog("Inventory")
    ctx = _FakeCtx(author, guild)
    await cog._use_picker(ctx)
    view = ctx.replied["view"]
    view.select._values = ["lucky_charm", "bait_worm"]
    interaction = _FakeInteraction(author.id)

    await view._on_select(interaction)

    assert {"luck", "fish_bait"} <= set(await db.get_active_effects(author.id))
    # The card is repainted with the result, and the emptied list closes itself.
    assert interaction.edited["embed"].title.startswith("✨ Armed")
    assert view.children == []


@pytest.mark.cogs("cogs.inventory")
async def test_the_picker_is_invoker_only(bot):
    author, other, guild = config().members[0], config().members[1], config().guilds[0]
    await db.add_item(author.id, "lucky_charm", 1)
    cog = bot.get_cog("Inventory")
    ctx = _FakeCtx(author, guild)
    await cog._use_picker(ctx)
    view = ctx.replied["view"]

    assert await view.interaction_check(_FakeInteraction(author.id)) is True
    theirs = _FakeInteraction(other.id)
    assert await view.interaction_check(theirs) is False
    assert theirs.response.sent["ephemeral"] is True


@pytest.mark.cogs("cogs.inventory", "cogs.fishing")
async def test_the_use_picker_offers_a_use_one_of_each_row_with_whole_keys(bot):
    """A choice value is capped at 100 chars, and a truncated key would come
    back as "no item called bait_gl"."""
    author = config().members[0]
    await db.add_item(author.id, "lucky_charm", 1)
    await db.add_item(author.id, "bait_worm", 1)
    cog = bot.get_cog("Inventory")
    interaction = types.SimpleNamespace(
        user=types.SimpleNamespace(id=author.id), guild_id=config().guilds[0].id
    )

    choices = await cog._use_ac(interaction, "")

    assert choices[0].name.startswith("✨")
    keys = [k.strip() for k in choices[0].value.split(",")]
    assert set(keys) == {"lucky_charm", "bait_worm"}
    # Whole keys only — every one still resolves in the catalogue.
    assert all(items.get(k) is not None for k in keys)


@pytest.mark.cogs("cogs.inventory")
async def test_effect_uses_cap_constant_sane():
    # Charge cap guards the same stacking hole for charge-based items.
    assert 0 < EFFECT_MAX_USES <= 100


@pytest.mark.cogs("cogs.inventory")
async def test_give_rejects_self(bot):
    guild = config().guilds[0]
    author = config().members[0]
    await db.add_item(author.id, "lucky_charm", 1)
    await dpytest.message(
        f"!inventory give {author.mention} lucky_charm", member=author
    )
    sent = dpytest.get_message()
    assert "Error" in sent.embeds[0].title
    assert await db.get_item_qty(author.id, "lucky_charm") == 1


@pytest.mark.cogs("cogs.inventory")
async def test_give_transfers_between_members(bot):
    guild = config().guilds[0]
    author, other = config().members[0], config().members[1]
    await db.add_item(author.id, "lucky_charm", 3)
    await dpytest.message(
        f"!inventory give {other.mention} lucky_charm 2", member=author
    )
    dpytest.get_message()
    assert await db.get_item_qty(author.id, "lucky_charm") == 1
    assert await db.get_item_qty(other.id, "lucky_charm") == 2


@pytest.mark.cogs("cogs.inventory")
async def test_sell_unsellable_item_refused(bot):
    guild = config().guilds[0]
    author = config().members[0]
    await db.add_item(author.id, "treasure_key", 1)
    await dpytest.message("!inventory sell treasure_key", member=author)
    sent = dpytest.get_message()
    assert "can't be sold" in sent.embeds[0].description
    assert await db.get_item_qty(author.id, "treasure_key") == 1


@pytest.mark.cogs("cogs.inventory", "cogs.activities")
async def test_sell_all_previews_without_selling_anything(bot):
    """The command only shows what would go — the sale waits for the button."""
    author = config().members[0]
    await db.add_item(author.id, "iron_ore", 4)  # material
    await db.add_item(author.id, "golden_antler", 1)  # treasure

    await dpytest.message("!inventory sell all", member=author)
    sent = dpytest.get_message()
    assert "Sell these?" in sent.embeds[0].title
    # Read the prices from the catalogue rather than spelling them out: these
    # are balance numbers and this test is about the confirmation flow.
    expected = 4 * items.get("iron_ore").value + items.get("golden_antler").value
    assert f"{expected:,}" in sent.embeds[0].description
    assert await db.get_item_qty(author.id, "iron_ore") == 4
    assert await db.get_item_qty(author.id, "golden_antler") == 1
    assert await db.get_balance(author.id) == 0


@pytest.mark.cogs("cogs.inventory", "cogs.activities")
async def test_sell_all_confirmed_clears_every_sellable_stack(bot):
    """One command instead of one per item: confirming `sell all` empties the
    inventory of anything sellable and pays for the lot in a single credit."""
    author = config().members[0]
    await db.add_item(author.id, "iron_ore", 4)
    await db.add_item(author.id, "golden_antler", 1)
    await db.add_item(author.id, "treasure_key", 2)  # value 0 — must survive

    await dpytest.message("!inventory sell all", member=author)
    dpytest.get_message()
    view = _pending_sell(bot, author.id)
    interaction = _FakeInteraction(author.id)
    await view._on_confirm(interaction)

    assert "Sold" in interaction.response.edited["embed"].title
    assert await db.get_item_qty(author.id, "iron_ore") == 0
    assert await db.get_item_qty(author.id, "golden_antler") == 0
    assert await db.get_item_qty(author.id, "treasure_key") == 2
    assert await db.get_balance(author.id) == (
        4 * items.get("iron_ore").value + items.get("golden_antler").value
    )


@pytest.mark.cogs("cogs.inventory", "cogs.activities")
async def test_sell_all_cancelled_keeps_everything(bot):
    author = config().members[0]
    await db.add_item(author.id, "iron_ore", 4)

    await dpytest.message("!inventory sell all", member=author)
    dpytest.get_message()
    view = _pending_sell(bot, author.id)
    interaction = _FakeInteraction(author.id)
    await view._on_cancel(interaction)

    assert "Cancelled" in interaction.response.edited["embed"].title
    assert await db.get_item_qty(author.id, "iron_ore") == 4
    assert await db.get_balance(author.id) == 0


@pytest.mark.cogs("cogs.inventory", "cogs.activities")
async def test_sell_confirmation_is_single_use_and_owner_only(bot):
    """A second press can't sell twice, and it isn't anyone else's button."""
    author, other = config().members[0], config().members[1]
    await db.add_item(author.id, "iron_ore", 4)

    await dpytest.message("!inventory sell all", member=author)
    dpytest.get_message()
    view = _pending_sell(bot, author.id)

    assert await view.interaction_check(_FakeInteraction(other.id)) is False
    assert await view.interaction_check(_FakeInteraction(author.id)) is True

    await view._on_confirm(_FakeInteraction(author.id))
    second = _FakeInteraction(author.id)
    await view._on_confirm(second)  # already settled -> ephemeral no-op
    assert second.response.edited is None
    assert await db.get_balance(author.id) == 4 * items.get("iron_ore").value


@pytest.mark.cogs("cogs.inventory", "cogs.activities")
async def test_sell_confirmation_timeout_sells_nothing(bot):
    author = config().members[0]
    await db.add_item(author.id, "iron_ore", 4)

    await dpytest.message("!inventory sell all", member=author)
    dpytest.get_message()
    view = _pending_sell(bot, author.id)
    view.message = None  # no gateway edit in tests; the DB effect is the point
    await view.on_timeout()

    assert await db.get_item_qty(author.id, "iron_ore") == 4
    assert await db.get_balance(author.id) == 0
    assert author.id not in bot.get_cog("Inventory")._pending_sell


@pytest.mark.cogs("cogs.inventory", "cogs.activities")
async def test_second_bulk_sell_retires_the_stale_confirmation(bot):
    """A stale preview can't be pressed later against a changed inventory."""
    author = config().members[0]
    await db.add_item(author.id, "iron_ore", 4)

    await dpytest.message("!inventory sell all", member=author)
    dpytest.get_message()
    first = _pending_sell(bot, author.id)
    first.message = None

    await dpytest.message("!inventory sell all", member=author)
    dpytest.get_message()
    second = _pending_sell(bot, author.id)
    assert second is not first

    stale = _FakeInteraction(author.id)
    await first._on_confirm(stale)  # retired -> ephemeral no-op
    assert stale.response.edited is None
    assert await db.get_item_qty(author.id, "iron_ore") == 4

    await second._on_confirm(_FakeInteraction(author.id))
    assert await db.get_item_qty(author.id, "iron_ore") == 0


@pytest.mark.cogs("cogs.inventory", "cogs.activities")
async def test_sell_category_only_touches_that_category(bot):
    author = config().members[0]
    await db.add_item(author.id, "iron_ore", 2)  # material
    await db.add_item(author.id, "golden_antler", 1)  # treasure

    await dpytest.message("!inventory sell cat:material", member=author)
    dpytest.get_message()
    await _pending_sell(bot, author.id)._on_confirm(_FakeInteraction(author.id))

    assert await db.get_item_qty(author.id, "iron_ore") == 0
    assert await db.get_item_qty(author.id, "golden_antler") == 1
    assert await db.get_balance(author.id) == 2 * items.get("iron_ore").value


@pytest.mark.cogs("cogs.inventory", "cogs.activities")
async def test_sell_bare_category_name_works(bot):
    """A bare category name is accepted too — no item is named after one."""
    author = config().members[0]
    await db.add_item(author.id, "golden_antler", 2)

    await dpytest.message("!inventory sell treasure", member=author)
    dpytest.get_message()
    await _pending_sell(bot, author.id)._on_confirm(_FakeInteraction(author.id))

    assert await db.get_item_qty(author.id, "golden_antler") == 0
    assert await db.get_balance(author.id) == 2 * items.get("golden_antler").value


@pytest.mark.cogs("cogs.inventory", "cogs.activities")
async def test_sell_all_with_qty_caps_each_stack(bot):
    author = config().members[0]
    await db.add_item(author.id, "iron_ore", 5)
    await db.add_item(author.id, "coal", 5)  # material, 12 ea

    await dpytest.message("!inventory sell all 2", member=author)
    dpytest.get_message()
    await _pending_sell(bot, author.id)._on_confirm(_FakeInteraction(author.id))

    assert await db.get_item_qty(author.id, "iron_ore") == 3
    assert await db.get_item_qty(author.id, "coal") == 3
    assert await db.get_balance(author.id) == (
        2 * items.get("iron_ore").value + 2 * items.get("coal").value
    )


@pytest.mark.cogs("cogs.inventory", "cogs.activities")
async def test_sell_confirmed_after_items_spent_pays_only_what_is_left(bot):
    """The preview is a snapshot — the sale re-reads before consuming."""
    author = config().members[0]
    await db.add_item(author.id, "iron_ore", 4)
    await db.add_item(author.id, "coal", 5)

    await dpytest.message("!inventory sell all", member=author)
    dpytest.get_message()
    view = _pending_sell(bot, author.id)
    await db.try_consume_item(author.id, "iron_ore", 4)  # spent behind the preview

    await view._on_confirm(_FakeInteraction(author.id))
    assert (
        await db.get_balance(author.id) == 5 * items.get("coal").value
    )  # coal only, no phantom ore


@pytest.mark.cogs("cogs.inventory")
async def test_sell_all_with_nothing_sellable_pays_nothing(bot):
    author = config().members[0]
    await db.add_item(author.id, "treasure_key", 1)

    await dpytest.message("!inventory sell all", member=author)
    sent = dpytest.get_message()
    assert "Nothing to Sell" in sent.embeds[0].title
    assert author.id not in bot.get_cog("Inventory")._pending_sell
    assert await db.get_item_qty(author.id, "treasure_key") == 1
    assert await db.get_balance(author.id) == 0


@pytest.mark.cogs("cogs.inventory")
async def test_chest_requires_key(bot):
    guild = config().guilds[0]
    author = config().members[0]
    await db.add_item(author.id, "treasure_chest", 1)
    await dpytest.message("!inventory use treasure_chest", member=author)
    sent = dpytest.get_message()
    assert "Key" in sent.embeds[0].description
    assert await db.get_item_qty(author.id, "treasure_chest") == 1


@pytest.mark.cogs("cogs.inventory")
async def test_chest_opens_with_key_and_pays(bot):
    guild = config().guilds[0]
    author = config().members[0]
    await db.add_item(author.id, "treasure_chest", 1)
    await db.add_item(author.id, "treasure_key", 1)
    await dpytest.message("!inventory use treasure_chest", member=author)
    sent = dpytest.get_message()
    assert "Opened" in sent.embeds[0].title
    assert await db.get_item_qty(author.id, "treasure_chest") == 0
    assert await db.get_item_qty(author.id, "treasure_key") == 0
    assert await db.get_balance(author.id) >= 250
