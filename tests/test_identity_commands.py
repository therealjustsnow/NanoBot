"""
Command-level tests for cogs/identity/ under dpytest: the profile card,
cosmetics equipping, and the account-wide XP listener.
"""

import pytest
from discord.ext import commands
from discord.ext import test as dpytest

import types

import utils.db as db
from utils import cosmetics, globalxp, profile_card
from tests.conftest import config


def views():
    """The view module the *live* cog is using — loading a cog replaces it in
    sys.modules, so classes are resolved at call time (see
    tests/test_fishing_views.py for the full story)."""
    import cogs.identity.views as module

    return module


class _FakeResponse:
    def __init__(self):
        self.sent = None
        self.deferred = False

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
    """Duck-typed discord.Interaction for the cosmetic picker's select —
    dpytest can't dispatch components."""

    def __init__(self, user_id: int):
        self.user = types.SimpleNamespace(id=user_id)
        self.response = _FakeResponse()
        self.edited = None

    async def edit_original_response(self, **kwargs):
        self.edited = kwargs
        return _FakeMessage()


class _FakeCtx:
    """Just enough Context for the picker: dpytest doesn't surface the view a
    reply carried, and the view is the thing under test."""

    def __init__(self, member, guild):
        self.author = member
        self.guild = guild
        self.replied = None

    async def reply(self, **kwargs):
        self.replied = kwargs
        return _FakeMessage()


@pytest.mark.cogs("cogs.identity")
async def test_profile_sends_a_card_image(bot):
    author = config().members[0]
    await db.add_coins(author.id, 5_000)
    await db.add_global_xp(author.id, 900)

    await dpytest.message("!profile", member=author)
    sent = dpytest.get_message()
    assert sent.attachments, "the profile should come back as an image"
    attachment = sent.attachments[0]
    assert attachment.filename.endswith(f".{profile_card.IMAGE_EXT}")
    assert attachment.size > 1000  # a real render, not an empty file


@pytest.mark.cogs("cogs.identity")
async def test_profile_works_for_a_brand_new_account(bot):
    """No coins, no XP, nothing equipped — still a card, never an error."""
    author = config().members[1]
    await dpytest.message("!profile", member=author)
    assert dpytest.get_message().attachments


@pytest.mark.cogs("cogs.identity")
async def test_preview_renders_a_cosmetic_you_do_not_own_without_granting_it(bot):
    """Try-before-you-buy: the card comes back wearing it, but nothing is
    unlocked, equipped or charged."""
    author = config().members[0]
    await dpytest.message("!profile preview Nebula", member=author)
    sent = dpytest.get_message()

    # The note that says it is only a preview rides on `content`, which dpytest
    # cannot see on a file-carrying (multipart) send — the load-bearing part is
    # that nothing was granted, equipped or charged.
    assert sent.attachments, "a preview should come back as a card image"
    assert "banner_nebula" not in await db.get_unlocked_cosmetics(author.id)
    assert (await db.get_equipped(author.id)).get("banner", []) == []


@pytest.mark.cogs("cogs.identity")
async def test_preview_shows_a_badge_even_when_the_showcase_is_full(bot):
    """A preview that silently drops the thing being previewed is worse than
    no preview."""
    author = config().members[1]
    worn = [d.key for d in cosmetics.in_slot("badge")[:6]]
    for key in worn:
        await db.unlock_cosmetic(author.id, key, at=0)
    await db.set_equipped(author.id, "badge", worn)

    cog = bot.get_cog("Identity")
    bot.get_command("profile preview")._buckets._cache.clear()
    await dpytest.message("!profile preview Infinite", member=author)
    assert dpytest.get_message().attachments
    # Still exactly six worn, and the preview never touched the loadout.
    assert (await db.get_equipped(author.id)).get("badge") == worn
    assert cog is not None


@pytest.mark.cogs("cogs.identity")
async def test_preview_rejects_an_unknown_name(bot):
    author = config().members[0]
    bot.get_command("profile preview")._buckets._cache.clear()
    await dpytest.message("!profile preview not a real cosmetic", member=author)
    assert dpytest.get_message().embeds


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
async def test_equip_dresses_a_whole_card_from_one_list(bot):
    """Six slots meant six commands, and filling the badge showcase alone was
    six more. A comma-separated list does the lot — and two badges picked
    together must *both* land, which is what carrying the loadout through the
    loop in memory is for."""
    author = config().members[0]
    badges = [d.key for d in cosmetics.in_slot("badge")[:2]]
    for key in ("banner_ember", *badges):
        await db.unlock_cosmetic(author.id, key)
    cog = bot.get_cog("Identity")

    names = ["Ember"] + [cosmetics.get(k).name for k in badges]
    embed, changed = await cog.run_equip(author.id, names)

    assert changed == 2  # one banner slot, one badge slot
    assert "Equipped" in embed.title
    equipped = await db.get_equipped(author.id)
    assert equipped["banner"] == ["banner_ember"]
    assert equipped["badge"] == badges


@pytest.mark.cogs("cogs.identity")
async def test_a_locked_pick_in_a_list_does_not_block_the_rest(bot):
    author = config().members[0]
    await db.unlock_cosmetic(author.id, "banner_ember")
    cog = bot.get_cog("Identity")

    embed, changed = await cog.run_equip(author.id, ["Aurora", "Ember"])

    assert changed == 1
    assert "🔒" in embed.description
    assert (await db.get_equipped(author.id))["banner"] == ["banner_ember"]


@pytest.mark.cogs("cogs.identity")
async def test_a_single_pick_keeps_its_own_wording(bot):
    """The one-pick refusals read as answers to a question, so they aren't
    reduced to a line in a report."""
    author = config().members[0]
    cog = bot.get_cog("Identity")

    embed, changed = await cog.run_equip(author.id, ["Ember"])
    assert changed == 0 and "Locked" in embed.title

    embed, changed = await cog.run_equip(author.id, ["not a real cosmetic"])
    assert changed == 0 and "no cosmetic called" in embed.description


@pytest.mark.cogs("cogs.identity")
async def test_bare_equip_opens_a_picker_of_what_is_wearable(bot):
    author, guild = config().members[0], config().guilds[0]
    await db.unlock_cosmetic(author.id, "banner_ember")
    cog = bot.get_cog("Identity")
    ctx = _FakeCtx(author, guild)

    await cog._cosmetic_picker(ctx, "equip")

    view = ctx.replied["view"]
    assert type(view).__name__ == "CosmeticPickerView"
    assert "banner_ember" in {o.value for o in view.select.options}
    # A locked cosmetic is not offered: a select row that refuses on press is a
    # dead row, and the autocomplete is where unlock lines belong.
    assert "banner_aurora" not in {o.value for o in view.select.options}
    assert await db.get_equipped(author.id) == {}


@pytest.mark.cogs("cogs.identity")
async def test_the_picker_equips_everything_selected_in_one_press(bot):
    author, guild = config().members[0], config().guilds[0]
    badges = [d.key for d in cosmetics.in_slot("badge")[:2]]
    for key in ("banner_ember", *badges):
        await db.unlock_cosmetic(author.id, key)
    cog = bot.get_cog("Identity")
    ctx = _FakeCtx(author, guild)
    await cog._cosmetic_picker(ctx, "equip")
    view = ctx.replied["view"]
    view.select._values = ["banner_ember", *badges]
    interaction = _FakeInteraction(author.id)

    await view._on_select(interaction)

    equipped = await db.get_equipped(author.id)
    assert equipped["banner"] == ["banner_ember"] and equipped["badge"] == badges
    assert "Equipped" in interaction.edited["embed"].title
    # The menu re-reads, so what's now worn is off the list.
    assert "banner_ember" not in {o.value for o in view.select.options}


@pytest.mark.cogs("cogs.identity")
async def test_the_picker_is_invoker_only(bot):
    author, other = config().members[0], config().members[1]
    await db.unlock_cosmetic(author.id, "banner_ember")
    cog = bot.get_cog("Identity")
    ctx = _FakeCtx(author, config().guilds[0])
    await cog._cosmetic_picker(ctx, "equip")
    view = ctx.replied["view"]

    assert await view.interaction_check(_FakeInteraction(author.id)) is True
    theirs = _FakeInteraction(other.id)
    assert await view.interaction_check(theirs) is False
    assert theirs.response.sent["ephemeral"] is True


@pytest.mark.cogs("cogs.identity")
async def test_unequip_takes_several_off_and_still_clears_a_slot(bot):
    author = config().members[0]
    badges = [d.key for d in cosmetics.in_slot("badge")[:2]]
    for key in ("banner_ember", *badges):
        await db.unlock_cosmetic(author.id, key)
    await db.set_equipped(author.id, "banner", ["banner_ember"])
    await db.set_equipped(author.id, "badge", badges)
    cog = bot.get_cog("Identity")

    embed, changed = await cog.run_unequip(
        author.id, [cosmetics.get(badges[0]).name, "Ember"]
    )

    assert changed == 2 and "Taken Off" in embed.title
    equipped = await db.get_equipped(author.id)
    assert equipped.get("banner", []) == [] and equipped["badge"] == [badges[1]]

    # The slot-name shortcut still clears a whole showcase.
    embed, changed = await cog.run_unequip(author.id, ["badge"])
    assert changed == 1
    assert (await db.get_equipped(author.id)).get("badge", []) == []


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


# ── Global level-up announcements ─────────────────────────────────────────────
@pytest.mark.cogs("cogs.identity")
async def test_levelup_announces_in_the_channel_they_used(bot):
    """First choice: wherever they just talked or ran a command."""
    author = config().members[0]
    # One XP short of level 1, then a command that awards.
    await db.add_global_xp(author.id, globalxp.cum_xp_for_level(1) - 1)
    await db.set_pending_levelup(author.id, 1)

    await dpytest.message("!profile badges", member=author)
    dpytest.get_message()  # the badges embed
    announcement = dpytest.get_message()
    assert "Global level up" in announcement.embeds[0].title
    assert "level 1" in announcement.embeds[0].description
    assert await db.get_pending_levelup(author.id) == 0  # claimed


@pytest.mark.cogs("cogs.identity")
async def test_levelup_is_announced_exactly_once(bot):
    author = config().members[0]
    await db.set_pending_levelup(author.id, 4)

    await dpytest.message("!profile badges", member=author)
    dpytest.get_message()
    assert "Global level up" in dpytest.get_message().embeds[0].title

    # A second command must not re-announce it.
    await dpytest.message("!profile badges", member=author)
    dpytest.get_message()
    assert dpytest.verify().message().nothing()


@pytest.mark.cogs("cogs.identity")
async def test_levelup_falls_back_to_dm_then_stays_pending(bot, monkeypatch):
    """Channel blocked → DM. DM blocked too → keep it for next time."""
    import discord

    author = config().members[0]
    cog = bot.get_cog("Identity")

    class DeadChannel:
        async def send(self, *a, **kw):
            raise discord.Forbidden(_Resp(), "no")

    dmed = []

    async def fake_dm(*a, **kw):
        dmed.append(kw.get("embed"))

    await db.set_pending_levelup(author.id, 7)
    monkeypatch.setattr(type(author), "send", fake_dm, raising=False)
    assert await cog.deliver_levelup(author, DeadChannel()) is True
    assert dmed and "level 7" in dmed[0].description
    assert await db.get_pending_levelup(author.id) == 0

    # Now neither works: the level must survive for the next attempt.
    async def dead_dm(*a, **kw):
        raise discord.Forbidden(_Resp(), "closed dms")

    await db.set_pending_levelup(author.id, 8)
    monkeypatch.setattr(type(author), "send", dead_dm, raising=False)
    assert await cog.deliver_levelup(author, DeadChannel()) is False
    assert await db.get_pending_levelup(author.id) == 8  # handed back

    # …and it goes out the next time they're reachable anywhere.
    monkeypatch.setattr(type(author), "send", fake_dm, raising=False)
    assert await cog.deliver_levelup(author, DeadChannel()) is True
    assert await db.get_pending_levelup(author.id) == 0


@pytest.mark.cogs("cogs.identity")
async def test_levelup_uses_the_channel_the_server_nominated(bot):
    """`/level globalannounce #channel` moves it out of wherever they typed."""
    guild, author = config().guilds[0], config().members[0]
    here, there = config().channels[0], config().channels[1]
    await db.set_level_config(guild.id, global_announce_channel=there.id)
    cog = bot.get_cog("Identity")

    await db.set_pending_levelup(author.id, 3)
    assert await cog.deliver_levelup(author, here) is True
    sent = dpytest.get_message()
    assert sent.channel.id == there.id
    assert "level 3" in sent.embeds[0].description


@pytest.mark.cogs("cogs.identity")
async def test_levelup_follows_the_server_level_channel_when_unset(bot):
    guild, author = config().guilds[0], config().members[0]
    here, there = config().channels[0], config().channels[1]
    await db.set_level_config(guild.id, announce_channel=there.id)
    cog = bot.get_cog("Identity")

    await db.set_pending_levelup(author.id, 5)
    assert await cog.deliver_levelup(author, here) is True
    assert dpytest.get_message().channel.id == there.id


@pytest.mark.cogs("cogs.identity")
async def test_levelup_skips_a_channel_that_earns_no_xp(bot, monkeypatch):
    """The venting-channel report: no post there, DM instead."""
    guild, author = config().guilds[0], config().members[0]
    here = config().channels[0]
    await db.add_level_ignored_channel(guild.id, here.id)
    cog = bot.get_cog("Identity")

    dmed = []

    async def fake_dm(*a, **kw):
        dmed.append(kw.get("embed"))

    monkeypatch.setattr(type(author), "send", fake_dm, raising=False)
    await db.set_pending_levelup(author.id, 6)
    assert await cog.deliver_levelup(author, here) is True
    assert dpytest.verify().message().nothing()
    assert dmed and "level 6" in dmed[0].description


@pytest.mark.cogs("cogs.identity")
async def test_levelup_off_for_a_server_still_reaches_the_member(bot, monkeypatch):
    guild, author = config().guilds[0], config().members[0]
    here = config().channels[0]
    await db.set_level_config(guild.id, global_announce=False)
    cog = bot.get_cog("Identity")

    dmed = []

    async def fake_dm(*a, **kw):
        dmed.append(kw.get("embed"))

    monkeypatch.setattr(type(author), "send", fake_dm, raising=False)
    await db.set_pending_levelup(author.id, 9)
    assert await cog.deliver_levelup(author, here) is True
    assert dpytest.verify().message().nothing()
    assert dmed and "level 9" in dmed[0].description
    assert await db.get_pending_levelup(author.id) == 0


@pytest.mark.cogs("cogs.identity")
async def test_a_deleted_announce_channel_never_falls_back_in_place(bot, monkeypatch):
    """ "Not in here" has to survive the channel being deleted."""
    guild, author = config().guilds[0], config().members[0]
    here = config().channels[0]
    await db.set_level_config(guild.id, global_announce_channel=123456789)
    cog = bot.get_cog("Identity")

    dmed = []

    async def fake_dm(*a, **kw):
        dmed.append(kw.get("embed"))

    monkeypatch.setattr(type(author), "send", fake_dm, raising=False)
    await db.set_pending_levelup(author.id, 2)
    assert await cog.deliver_levelup(author, here) is True
    assert dpytest.verify().message().nothing()
    assert dmed


class _Resp:
    status = 403
    reason = "Forbidden"


@pytest.mark.cogs("cogs.identity")
async def test_awarding_xp_records_the_pending_levelup(bot):
    """The award itself never sends anything — it records, delivery is separate."""
    author = config().members[0]
    await db.add_global_xp(author.id, globalxp.cum_xp_for_level(1) - 1)
    res = await globalxp.award(author.id, "achievement")
    assert res["levelled_up"] is True
    assert await db.get_pending_levelup(author.id) == res["level"]


@pytest.mark.cogs("cogs.identity")
async def test_levelup_message_names_cosmetics_unlocked_at_that_level(bot):
    author = config().members[0]
    cog = bot.get_cog("Identity")
    embed = cog._levelup_embed(author, 25)  # Ember banner unlocks at 25
    assert "Ember" in "".join(f.value for f in embed.fields)


# ── Bulk grant ────────────────────────────────────────────────────────────────
@pytest.mark.cogs("cogs.identity")
async def test_grantall_is_owner_only(bot):
    author = config().members[0]
    with pytest.raises(commands.NotOwner):
        await dpytest.message("!profile grantall badge_beta_tester", member=author)


@pytest.mark.cogs("cogs.identity")
async def test_grantall_awards_every_human_member_and_is_idempotent(bot, monkeypatch):
    guild = config().guilds[0]
    author = config().members[0]
    monkeypatch.setattr(bot, "is_owner", _always_owner)

    await dpytest.message("!profile grantall badge_beta_tester", member=author)
    reply = dpytest.get_message().embeds[0]
    humans = [m for m in guild.members if not m.bot]
    assert f"**{len(humans)}** newly granted" in reply.description
    for member in humans:
        assert "badge_beta_tester" in await db.get_unlocked_cosmetics(member.id)
    # Bots are skipped.
    for member in guild.members:
        if member.bot:
            assert await db.get_unlocked_cosmetics(member.id) == {}

    # Re-running only picks up anyone new — nobody is granted twice.
    await dpytest.message("!profile grantall badge_beta_tester", member=author)
    again = dpytest.get_message().embeds[0]
    assert "**0** newly granted" in again.description


@pytest.mark.cogs("cogs.identity")
async def test_grantall_targets_another_server_by_id(bot, monkeypatch):
    guild = config().guilds[0]
    author = config().members[0]
    monkeypatch.setattr(bot, "is_owner", _always_owner)

    await dpytest.message(
        f"!profile grantall badge_beta_tester {guild.id}", member=author
    )
    assert "Bulk Grant" in dpytest.get_message().embeds[0].title

    await dpytest.message(
        "!profile grantall badge_beta_tester 123456789", member=author
    )
    assert "not in a server" in dpytest.get_message().embeds[0].description


async def _always_owner(user):
    return True
