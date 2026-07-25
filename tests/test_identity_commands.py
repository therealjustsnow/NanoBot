"""
Command-level tests for cogs/identity/ under dpytest: the profile card,
cosmetics equipping, and the account-wide XP listener.
"""

import pytest
from discord.ext import commands
from discord.ext import test as dpytest

import utils.db as db
from utils import cosmetics, globalxp
from tests.conftest import config


@pytest.mark.cogs("cogs.identity")
async def test_profile_sends_a_card_image(bot):
    author = config().members[0]
    await db.add_coins(author.id, 5_000)
    await db.add_global_xp(author.id, 900)

    await dpytest.message("!profile", member=author)
    sent = dpytest.get_message()
    assert sent.attachments, "the profile should come back as an image"
    attachment = sent.attachments[0]
    assert attachment.filename.endswith(".png")
    assert attachment.size > 1000  # a real render, not an empty file


@pytest.mark.cogs("cogs.identity")
async def test_profile_works_for_a_brand_new_account(bot):
    """No coins, no XP, nothing equipped — still a card, never an error."""
    author = config().members[1]
    await dpytest.message("!profile", member=author)
    assert dpytest.get_message().attachments


@pytest.mark.cogs("cogs.identity")
async def test_chat_awards_global_xp_once_per_cooldown(bot):
    author = config().members[0]
    await dpytest.message("hello", member=author)
    first = await db.get_global_xp(author.id)
    assert first == globalxp.XP_AWARDS["message"]

    await dpytest.message("hello again", member=author)
    assert await db.get_global_xp(author.id) == first  # inside the cooldown


@pytest.mark.cogs("cogs.identity")
async def test_earned_cosmetics_unlock_when_you_open_your_card(bot):
    """Unlocks are evaluated lazily, exactly like achievements."""
    author = config().members[0]
    # Global level 25 unlocks the Ember banner.
    await db.add_global_xp(author.id, globalxp.cum_xp_for_level(25))

    await dpytest.message("!profile", member=author)
    dpytest.get_message()
    owned = await db.get_unlocked_cosmetics(author.id)
    assert "banner_ember" in owned
    assert "badge_developer" not in owned  # manual grants are never automatic


@pytest.mark.cogs("cogs.identity")
async def test_equip_requires_owning_it_then_shows_on_the_card(bot):
    author = config().members[0]

    await dpytest.message("!profile equip Ember", member=author)
    assert "Locked" in dpytest.get_message().embeds[0].title

    await db.unlock_cosmetic(author.id, "banner_ember")
    await dpytest.message("!profile equip Ember", member=author)
    assert "Equipped" in dpytest.get_message().embeds[0].title
    assert (await db.get_equipped(author.id))["banner"] == ["banner_ember"]

    # A single-value slot swaps rather than stacking.
    await db.unlock_cosmetic(author.id, "banner_aurora")
    await dpytest.message("!profile equip Aurora", member=author)
    dpytest.get_message()
    assert (await db.get_equipped(author.id))["banner"] == ["banner_aurora"]


@pytest.mark.cogs("cogs.identity")
async def test_badge_showcase_fills_up_then_asks_you_to_remove_one(bot):
    author = config().members[0]
    badges = [d.key for d in cosmetics.in_slot("badge")]
    limit = cosmetics.SLOTS["badge"].max_equipped
    for key in badges[: limit + 1]:
        await db.unlock_cosmetic(author.id, key)
    await db.set_equipped(author.id, "badge", badges[:limit])

    extra = cosmetics.get(badges[limit])
    await dpytest.message(f"!profile equip {extra.name}", member=author)
    assert "Full" in dpytest.get_message().embeds[0].title

    await dpytest.message(
        f"!profile unequip {cosmetics.get(badges[0]).name}", member=author
    )
    dpytest.get_message()
    assert len((await db.get_equipped(author.id))["badge"]) == limit - 1


@pytest.mark.cogs("cogs.identity")
async def test_unequip_a_whole_slot(bot):
    author = config().members[0]
    await db.unlock_cosmetic(author.id, "badge_veteran")
    await db.set_equipped(author.id, "badge", ["badge_veteran"])
    await dpytest.message("!profile unequip badge", member=author)
    dpytest.get_message()
    assert "badge" not in await db.get_equipped(author.id)


@pytest.mark.cogs("cogs.identity")
async def test_cosmetics_list_shows_owned_and_locked(bot):
    author = config().members[0]
    await db.unlock_cosmetic(author.id, "badge_veteran")
    await dpytest.message("!profile cosmetics", member=author)
    embed = dpytest.get_message().embeds[0]
    body = "\n".join(f.value for f in embed.fields)
    assert "Veteran" in body
    assert "🔒" in body  # the locked ones explain how to get them


@pytest.mark.cogs("cogs.identity")
async def test_grant_is_owner_only(bot):
    author, target = config().members[0], config().members[1]
    with pytest.raises(commands.NotOwner):
        await dpytest.message(
            f"!profile grant {target.mention} Developer", member=author
        )
    assert await db.get_unlocked_cosmetics(target.id) == {}


@pytest.mark.cogs("cogs.identity")
async def test_badges_gallery_lists_progress(bot):
    author = config().members[0]
    await db.unlock_cosmetic(author.id, "badge_veteran")
    await dpytest.message("!profile badges", member=author)
    embed = dpytest.get_message().embeds[0]
    assert "1/" in embed.description
    assert "Veteran" in "\n".join(f.value for f in embed.fields)
