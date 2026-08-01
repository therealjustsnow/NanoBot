"""
`/craft make all` end to end — the bulk craft that mirrors /inventory sell all.

It finishes on a button, which dpytest can't dispatch, so these drive the
CraftAllConfirmView's handlers directly with a duck-typed interaction (the
SellConfirmView pattern in tests/test_inventory_commands.py).
"""

import types

import pytest
from discord.ext import test as dpytest

import utils.db as db
from tests.conftest import config


class _FakeResponse:
    def __init__(self):
        self.edited = None
        self.sent = None

    async def edit_message(self, **kwargs):
        self.edited = kwargs

    async def send_message(self, **kwargs):
        self.sent = kwargs


class _FakeMessage:
    def __init__(self):
        self.edits = []

    async def edit(self, **kwargs):
        self.edits.append(kwargs)


class _FakeInteraction:
    def __init__(self, user_id: int):
        self.user = types.SimpleNamespace(id=user_id)
        self.response = _FakeResponse()


def _pending(bot, user_id):
    """The live confirmation the last craft-all command put up."""
    return bot.get_cog("Crafting")._pending_craft[user_id]


async def _stock(user_id):
    """Materials for three fur coats and one gem ring."""
    await db.add_item(user_id, "pelt", 17)
    await db.add_item(user_id, "gold_ore", 2)
    await db.add_item(user_id, "diamond", 1)


@pytest.mark.cogs("cogs.crafting")
async def test_craft_all_previews_without_making_anything(bot):
    """Crafting all empties whole material stacks — including the diamond
    someone was saving for a treasure key — so nothing moves before the press."""
    author = config().members[0]
    await _stock(author.id)

    await dpytest.message("!craft make all", member=author)
    embed = dpytest.get_message().embeds[0]

    assert "Craft these?" in embed.title
    assert any(f.name == "What will be made" for f in embed.fields)
    assert await db.get_item_qty(author.id, "pelt") == 17
    assert await db.get_item_qty(author.id, "craft_fur_coat") == 0


@pytest.mark.cogs("cogs.crafting")
async def test_craft_all_confirm_makes_everything(bot):
    author = config().members[0]
    await _stock(author.id)
    await dpytest.message("!craft make all", member=author)
    dpytest.get_message()

    view = _pending(bot, author.id)
    interaction = _FakeInteraction(author.id)
    await view._on_confirm(interaction)

    assert "Crafted" in interaction.response.edited["embed"].title
    assert await db.get_item_qty(author.id, "craft_fur_coat") == 3
    assert await db.get_item_qty(author.id, "craft_gem_ring") == 1
    assert await db.get_item_qty(author.id, "pelt") == 2  # the remainder stays


@pytest.mark.cogs("cogs.crafting")
async def test_craft_all_settles_exactly_once(bot):
    author = config().members[0]
    await _stock(author.id)
    await dpytest.message("!craft make all", member=author)
    dpytest.get_message()

    view = _pending(bot, author.id)
    await view._on_confirm(_FakeInteraction(author.id))
    second = _FakeInteraction(author.id)
    await view._on_confirm(second)

    assert "already settled" in second.response.sent["embed"].description
    assert await db.get_item_qty(author.id, "craft_fur_coat") == 3


@pytest.mark.cogs("cogs.crafting")
async def test_craft_all_cancel_makes_nothing(bot):
    author = config().members[0]
    await _stock(author.id)
    await dpytest.message("!craft make all", member=author)
    dpytest.get_message()

    view = _pending(bot, author.id)
    await view._on_cancel(_FakeInteraction(author.id))

    assert await db.get_item_qty(author.id, "pelt") == 17
    assert await db.get_item_qty(author.id, "craft_fur_coat") == 0


@pytest.mark.cogs("cogs.crafting")
async def test_craft_all_timeout_makes_nothing(bot):
    author = config().members[0]
    await _stock(author.id)
    await dpytest.message("!craft make all", member=author)
    dpytest.get_message()

    view = _pending(bot, author.id)
    view.message = _FakeMessage()
    await view.on_timeout()

    assert "timed out" in view.message.edits[0]["embed"].description
    assert await db.get_item_qty(author.id, "pelt") == 17


@pytest.mark.cogs("cogs.crafting")
async def test_a_second_craft_all_retires_the_first(bot):
    """A stale preview must not stay pressable against a changed inventory."""
    author = config().members[0]
    await _stock(author.id)
    await dpytest.message("!craft make all", member=author)
    dpytest.get_message()
    first = _pending(bot, author.id)
    first.message = _FakeMessage()

    await dpytest.message("!craft make all", member=author)
    dpytest.get_message()
    second = _pending(bot, author.id)

    assert second is not first
    assert "Replaced" in first.message.edits[0]["embed"].description
    stale = _FakeInteraction(author.id)
    await first._on_confirm(stale)
    assert await db.get_item_qty(author.id, "craft_fur_coat") == 0


@pytest.mark.cogs("cogs.crafting")
async def test_craft_all_is_invoker_only(bot):
    author, other = config().members[0], config().members[1]
    await _stock(author.id)
    await dpytest.message("!craft make all", member=author)
    dpytest.get_message()

    view = _pending(bot, author.id)
    intruder = _FakeInteraction(other.id)

    assert await view.interaction_check(intruder) is False
    assert intruder.response.sent["ephemeral"] is True


@pytest.mark.cogs("cogs.crafting")
async def test_craft_all_confirmed_after_the_materials_went_pays_what_is_left(bot):
    """The preview is a snapshot; the craft re-reads the inventory, so a stack
    spent in between costs that one recipe rather than corrupting the rest."""
    author = config().members[0]
    await _stock(author.id)
    await dpytest.message("!craft make all", member=author)
    dpytest.get_message()
    await db.try_consume_item(author.id, "diamond", 1)  # spent elsewhere

    view = _pending(bot, author.id)
    await view._on_confirm(_FakeInteraction(author.id))

    assert await db.get_item_qty(author.id, "craft_fur_coat") == 3
    assert await db.get_item_qty(author.id, "craft_gem_ring") == 0
    assert await db.get_item_qty(author.id, "gold_ore") == 2  # handed back


@pytest.mark.cogs("cogs.crafting")
async def test_craft_all_with_nothing_to_make_says_so(bot):
    author = config().members[0]

    await dpytest.message("!craft make all", member=author)
    embed = dpytest.get_message().embeds[0]

    assert "Nothing to Craft" in embed.title
    assert author.id not in bot.get_cog("Crafting")._pending_craft
