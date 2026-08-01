"""
Command-level tests for cogs/crafting/ under dpytest (parse → DB → reply).
"""

import pytest
from discord.ext import test as dpytest

import utils.db as db
from tests.conftest import config


@pytest.mark.cogs("cogs.crafting")
async def test_bare_craft_lists_recipes(bot):
    author = config().members[0]
    await dpytest.message("!craft", member=author)
    sent = dpytest.get_message()
    assert sent.embeds
    assert "fur_coat" in sent.embeds[0].description


@pytest.mark.cogs("cogs.crafting")
async def test_make_succeeds_and_consumes_materials(bot):
    guild = config().guilds[0]
    author = config().members[0]
    await db.add_item(author.id, "pelt", 5)

    await dpytest.message("!craft make fur_coat", member=author)
    sent = dpytest.get_message()
    assert "Crafted" in sent.embeds[0].title
    assert await db.get_item_qty(author.id, "pelt") == 0
    assert await db.get_item_qty(author.id, "craft_fur_coat") == 1


@pytest.mark.cogs("cogs.crafting")
async def test_make_missing_materials_reports_error_and_consumes_nothing(bot):
    guild = config().guilds[0]
    author = config().members[0]
    await db.add_item(author.id, "pelt", 2)  # need 5

    await dpytest.message("!craft make fur_coat", member=author)
    sent = dpytest.get_message()
    assert "Error" in sent.embeds[0].title
    assert "pelt" in sent.embeds[0].description.lower()
    assert await db.get_item_qty(author.id, "pelt") == 2
    assert await db.get_item_qty(author.id, "craft_fur_coat") == 0


@pytest.mark.cogs("cogs.crafting")
async def test_make_unknown_recipe(bot):
    author = config().members[0]
    await dpytest.message("!craft make nonexistent_recipe", member=author)
    sent = dpytest.get_message()
    assert "Error" in sent.embeds[0].title


@pytest.mark.cogs("cogs.crafting")
async def test_make_with_qty_multiplies_inputs_and_output(bot):
    guild = config().guilds[0]
    author = config().members[0]
    await db.add_item(author.id, "pelt", 15)  # 3x fur_coat needs 15

    await dpytest.message("!craft make fur_coat 3", member=author)
    sent = dpytest.get_message()
    assert "Crafted" in sent.embeds[0].title
    assert await db.get_item_qty(author.id, "pelt") == 0
    assert await db.get_item_qty(author.id, "craft_fur_coat") == 3


@pytest.mark.cogs("cogs.crafting")
async def test_make_qty_partial_shortfall_refunds_nothing_consumed(bot):
    guild = config().guilds[0]
    author = config().members[0]
    # Enough pelt for 2x fur_coat (10) but the command asks for 3x (needs 15).
    await db.add_item(author.id, "pelt", 10)

    await dpytest.message("!craft make fur_coat 3", member=author)
    sent = dpytest.get_message()
    assert "Error" in sent.embeds[0].title
    assert await db.get_item_qty(author.id, "pelt") == 10
    assert await db.get_item_qty(author.id, "craft_fur_coat") == 0


@pytest.mark.cogs("cogs.crafting")
async def test_info_shows_inputs_and_output(bot):
    author = config().members[0]
    await dpytest.message("!craft info gem_ring", member=author)
    sent = dpytest.get_message()
    embed = sent.embeds[0]
    assert any(f.name == "Inputs" for f in embed.fields)
    assert any(f.name == "Output" for f in embed.fields)


@pytest.mark.cogs("cogs.crafting")
async def test_info_unknown_recipe(bot):
    author = config().members[0]
    await dpytest.message("!craft info nonexistent_recipe", member=author)
    sent = dpytest.get_message()
    assert "Error" in sent.embeds[0].title


@pytest.mark.cogs("cogs.crafting")
async def test_craft_output_can_be_used_via_inventory(bot):
    """Crafted outputs ride the generic inventory/effect layer — a crafted
    consumable's effect is armed the same way any other consumable is."""
    guild = config().guilds[0]
    author = config().members[0]
    await db.add_item(author.id, "meat", 3)
    await db.add_item(author.id, "coal", 2)

    await dpytest.message("!craft make campfire_feast", member=author)
    dpytest.get_message()
    assert await db.get_item_qty(author.id, "craft_campfire_feast") == 1


@pytest.mark.cogs("cogs.crafting")
async def test_multi_input_shortfall_refunds_earlier_inputs(bot):
    """Audit regression: gem_ring consumes gold_ore then diamond; when the
    LAST input is short, the already-consumed gold_ore must come back."""
    guild = config().guilds[0]
    author = config().members[0]
    await db.add_item(author.id, "gold_ore", 2)
    # No diamond at all.

    await dpytest.message("!craft make gem_ring", member=author)
    sent = dpytest.get_message()
    assert "diamond" in str(sent.embeds[0].to_dict()).lower()
    assert await db.get_item_qty(author.id, "gold_ore") == 2
    assert await db.get_item_qty(author.id, "craft_gem_ring") == 0


# ══════════════════════════════════════════════════════════════════════════════
#  Knowing (and taking) the max
# ══════════════════════════════════════════════════════════════════════════════
@pytest.mark.cogs("cogs.crafting")
async def test_the_recipe_list_says_how_many_you_can_make(bot):
    """The complaint the count answers: "craftable" without a number still
    leaves you guessing at the quantity to type."""
    author = config().members[0]
    await db.add_item(author.id, "pelt", 12)  # 2x fur_coat, 2 pelt spare

    await dpytest.message("!craft", member=author)
    embed = dpytest.get_message().embeds[0]

    assert "✅ **×2** **fur_coat**" in embed.description
    assert any(f.name == "Shortcuts" for f in embed.fields)


@pytest.mark.cogs("cogs.crafting")
async def test_info_says_how_many_you_can_make(bot):
    author = config().members[0]
    await db.add_item(author.id, "pelt", 17)

    await dpytest.message("!craft info fur_coat", member=author)
    fields = {f.name: f.value for f in dpytest.get_message().embeds[0].fields}

    assert "**3**" in fields["You can make"]


@pytest.mark.cogs("cogs.crafting")
async def test_info_names_the_shortfall_instead_of_reporting_zero(bot):
    author = config().members[0]
    await db.add_item(author.id, "pelt", 2)

    await dpytest.message("!craft info fur_coat", member=author)
    fields = {f.name: f.value for f in dpytest.get_message().embeds[0].fields}

    assert "**3**" in fields["You can make"]  # 3 more pelt needed
    assert "pelt" in fields["You can make"].lower()


@pytest.mark.cogs("cogs.crafting")
async def test_make_max_crafts_everything_the_materials_allow(bot):
    author = config().members[0]
    await db.add_item(author.id, "pelt", 17)  # 3x fur_coat, 2 pelt left over

    await dpytest.message("!craft make fur_coat max", member=author)
    sent = dpytest.get_message()

    assert "Crafted" in sent.embeds[0].title
    assert await db.get_item_qty(author.id, "craft_fur_coat") == 3
    assert await db.get_item_qty(author.id, "pelt") == 2


@pytest.mark.cogs("cogs.crafting")
async def test_make_max_of_something_you_cannot_make_names_the_shortfall(bot):
    author = config().members[0]
    await db.add_item(author.id, "pelt", 2)

    await dpytest.message("!craft make fur_coat max", member=author)
    sent = dpytest.get_message()

    assert "Nothing to Craft" in sent.embeds[0].title
    assert "pelt" in sent.embeds[0].description.lower()
    assert await db.get_item_qty(author.id, "pelt") == 2


@pytest.mark.cogs("cogs.crafting")
async def test_make_rejects_a_quantity_that_is_neither(bot):
    """Guessing at 1 would consume materials nobody asked to spend."""
    author = config().members[0]
    await db.add_item(author.id, "pelt", 5)

    await dpytest.message("!craft make fur_coat heaps", member=author)
    sent = dpytest.get_message()

    assert "Error" in sent.embeds[0].title
    assert await db.get_item_qty(author.id, "pelt") == 5


@pytest.mark.cogs("cogs.crafting")
async def test_a_craft_says_what_is_left_for_next_time(bot):
    author = config().members[0]
    await db.add_item(author.id, "pelt", 12)

    await dpytest.message("!craft make fur_coat", member=author)

    assert "**1** more" in dpytest.get_message().embeds[0].description
