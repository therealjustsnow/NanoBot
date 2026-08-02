"""
cogs/identity/cog.py
The account card: /profile, cosmetics, and the global (account-wide) level.

This is the identity layer that sits *above* every feature. Whatever someone
plays — fishing, the casino, activities, the shop — it all feeds one account,
and this cog is where that account is looked at and dressed up.

Two level systems live side by side on purpose (see utils/globalxp.py):

  * **Server level** — cogs/leveling.py, unchanged. Per-guild chat XP with the
    server's own rate, cooldown, role rewards and announcements. Admins own it.
  * **Global level** — this cog's `on_message` listener plus award calls from
    the feature cogs. One hard-coded curve, one flat XP value per *normalized*
    action, no per-guild configuration, so a 5x-XP server can't inflate it and
    a server with leveling switched off can't stall it.

Both appear on the card, and `/rank` shows them together too.

Cosmetics (banners, borders, nameplates, badges) are registry-driven —
`utils/cosmetics.py` holds the definitions, `utils/profile_card.py` draws them
from a palette + glyph when no artwork file exists, and the database only ever
stores keys. Adding a badge is one registry entry (or one line of JSON in
data/cosmetics.json); adding a whole new *slot* is one entry in SLOTS.

Slash command budget: ZERO new top-level commands — /profile was already a
flat command and becomes a group (its `card` fallback is the bare card), so
everything here rides subcommands.

Every cosmetic option is a tap-to-pick autocomplete (the /craft make + /fish
buy pattern), because `banner_ember` is not something anyone should type on a
phone. The pickers share `_cosmetic_choice`/`_matches` so a row reads the same
wherever it appears, and locked cosmetics stay in the equip list carrying their
unlock line — the picker answers "how do I get that one?" instead of coming
back empty on a fresh account.

A loadout is six slots and the badge showcase holds six of its own, so one
cosmetic per command meant a dozen commands to dress a card. `equip`/`unequip`
take a comma-separated list (`helpers.split_targets`), and run bare they open the
`CosmeticPickerView` multi-select in cogs/identity/views.py — which lists only
what a press would actually *do*, since a select row that refuses is a dead row.

──────────────────────────────────────────────────────
Commands
──────────────────────────────────────────────────────
  /profile card [member]        → the profile card image (bare `n!profile`)
  /profile cosmetics [slot]     → what you own, and how to unlock the rest
  /profile equip [cosmetic]     → wear a banner/border/nameplate/badge; takes
                                  several, comma-separated, and opens a
                                  multi-select picker when left empty
  /profile unequip [cosmetic]   → take one (or several) off, or clear a slot
  /profile preview <cosmetic>   → try one on your card without owning it
  /profile badges [member]      → the badge gallery
  /profile rep [member]         → give someone rep (once a day), or see yours

Owner tools — prefix only, deliberately kept out of the slash picker so a
command nobody but the owner can run does not sit in every member's /profile
list (`with_app_command=False`):

  n!profile grant <member> <cosmetic>   → award a cosmetic
  n!profile grantall <cosmetic> [guild] → award it to a whole server
  n!profile revoke <member> <cosmetic>  → take one back

Global level-up announcements
─────────────────────────────
An award can happen anywhere — a message listener, a /fish cast, someone
else's /raid payout — so `utils/globalxp` only *records* the level-up
(`global_levels.pending_level`) and this cog delivers it the next time it sees
the member, trying in order:

  1. the channel the *server* nominated (see below),
  2. their DMs,
  3. nowhere — it stays pending and is retried the next time they turn up in
     any server.

The claim is atomic and handed back on a failed send, so a level is announced
exactly once and never silently dropped.

The level is account-wide but a *channel* belongs to one server, so where the
message lands is the server's decision, held in `level_config` next to the
server-level announcement settings and resolved by `helpers.announce_channel_id`:
`/level globalannounce #channel` pins it, `/level globalannounce off` stops
global level-ups appearing in that server at all (the member still gets a DM),
and with neither set it follows `/level announce` if that's configured, else
the channel they were talking in — never a channel excluded from XP with
`/level ignore`, since a server that muted a channel for levels didn't mean
"except this one kind". No new top-level slash command: it's a subcommand of
the existing /level group, which costs no slot against the 100 cap.
"""

import asyncio
import io
import logging
import time
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from utils import cosmetics, db, globalxp
from utils import helpers as h
from utils import profile_card
from utils.db.social import REP_COOLDOWN

# Pure data/helpers borrowed from the feature packages for display only — the
# cogs/images.py → cogs/fun/sources.py precedent. No cog state is touched.
from cogs.activities.constants import PICKAXES
from cogs.activities.helpers import career_info, pickaxe_info
from cogs.fishing.constants import RODS
from cogs.fishing.helpers import fish_level, rod_info
from cogs.leveling import level_progress as server_level_progress
from cogs.progression.definitions import ACHIEVEMENTS, ACHIEVEMENTS_BY_KEY
from cogs.progression.helpers import earned_titles, prestige_title, total_points
from cogs.progression.stats import compute_stats_with_sources

from .helpers import (
    announce_channel_id,
    equip_result,
    interleave_by_slot,
    newly_unlocked,
    rarity_marker,
    resolve_loadout,
    unlock_context,
)
from .views import CosmeticPickerView

log = logging.getLogger("NanoBot.identity")

ACCENT = 0x5865F2

# How many cosmetics one equip/unequip may move. A full loadout is six slots
# and the badge showcase holds six, so this covers dressing a card from bare in
# one go with room to spare — it exists so a pasted list can't turn into a
# hundred writes.
MAX_EQUIP_TARGETS = 12

# The tap-to-dress picker's lifetime. Nothing is owed by a press (a cosmetic is
# already owned before it can be worn), so an expiry costs one re-run.
EQUIP_PICKER_TIMEOUT = 180.0


class Identity(commands.Cog):
    """Profile cards, cosmetics, and account-wide levels."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Card rendering is CPU work; one at a time keeps a burst of /profile
        # calls from starving the event loop's thread pool.
        self._render_lock = asyncio.Semaphore(2)
        cosmetics.load_file()  # optional data/cosmetics.json extensions

    # ══════════════════════════════════════════════════════════════════════════
    #  Global XP: chat
    # ══════════════════════════════════════════════════════════════════════════
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Award account XP for chatting.

        Deliberately independent of the per-guild leveling cog: it doesn't read
        that guild's rate, cooldown, enabled flag, or ignored channels, because
        global XP must mean the same thing in every server. The only gate is
        the fixed per-user cooldown in utils/globalxp.py.
        """
        if message.author.bot or not message.guild:
            return
        try:
            await globalxp.award_message(message.author.id)
            await self.deliver_levelup(message.author, message.channel)
        except Exception:  # pragma: no cover - never break message handling
            log.exception("global XP award failed for %s", message.author.id)

    @commands.Cog.listener()
    async def on_command_completion(self, ctx: commands.Context):
        """Deliver a pending level-up right after any command finishes.

        Covers the case the message listener can't: a member who only ever uses
        commands (or whose level-up came from someone else's /raid) still hears
        about it, in the channel they're already looking at.
        """
        if ctx.author.bot:
            return
        try:
            await self.deliver_levelup(ctx.author, ctx.channel)
        except Exception:  # pragma: no cover - never break command handling
            log.exception("level-up delivery failed for %s", ctx.author.id)

    async def _announce_channel(self, channel):
        """Resolve where this guild wants global level-ups posted (or None).

        `channel` is where the member just turned up; the guild's level config
        decides whether that's an acceptable place for a level-up to go off.
        """
        guild = getattr(channel, "guild", None)
        if guild is None:  # a DM, or a stub in tests — nothing to configure
            return channel
        cfg = await db.get_level_config(guild.id)
        ignored = await db.get_level_ignored_channels(guild.id)
        target_id = announce_channel_id(cfg, ignored, getattr(channel, "id", None))
        if target_id is None:
            return None
        if target_id == getattr(channel, "id", None):
            return channel
        return guild.get_channel(target_id)

    async def deliver_levelup(self, user: discord.abc.User, channel) -> bool:
        """Announce a pending global level-up. Returns True if one went out.

        Channel → DM → keep it pending. The claim is taken first so two
        deliveries can't both fire, and handed straight back if neither send
        lands, which is what makes "tell them next time" work.

        Which channel is the guild's call (see `_announce_channel`): a server
        can point global level-ups at one channel or switch them off entirely,
        and a channel it excluded from XP is never announced in.
        """
        level = await db.claim_pending_levelup(user.id)
        if not level:
            return False
        embed = self._levelup_embed(user, level)
        try:
            target = await self._announce_channel(channel)
        except Exception:  # pragma: no cover - config read must never eat a level
            log.exception("global level-up channel lookup failed")
            target = channel
        sends = []
        if target is not None:
            sends.append(lambda: target.send(content=user.mention, embed=embed))
        sends.append(lambda: user.send(embed=embed))
        for send in sends:
            try:
                await send()
                return True
            except (discord.HTTPException, discord.Forbidden, AttributeError):
                continue
        # Couldn't reach them anywhere — put it back for next time.
        await db.set_pending_levelup(user.id, level)
        log.debug("level-up for %s deferred: nowhere to send", user.id)
        return False

    def _levelup_embed(self, user: discord.abc.User, level: int) -> discord.Embed:
        embed = h.embed(
            "🌐 Global level up!",
            f"**{user.display_name}** reached **global level {level}**.",
            ACCENT,
        )
        unlocks = [
            d.name
            for d in cosmetics.auto_unlockable()
            if (d.unlock or {}).get("kind") == "global_level"
            and int((d.unlock or {}).get("value", 0)) == level
        ]
        if unlocks:
            embed.add_field(
                name="🎁 Unlocked", value=", ".join(f"**{n}**" for n in unlocks)
            )
        embed.set_footer(text="See it on /profile")
        return embed

    # ══════════════════════════════════════════════════════════════════════════
    #  /profile  group
    # ══════════════════════════════════════════════════════════════════════════
    @commands.hybrid_group(
        name="profile",
        aliases=["card", "pc"],
        description="Your account card: levels, cosmetics, and lifetime stats.",
        invoke_without_command=True,
        fallback="card",
        extras={
            "category": "🪙 Economy",
            "sub": "🏆 Progress & Profile",
            "short": "Your account card and cosmetics",
            "usage": "profile [subcommand]",
            "desc": "Your whole account on one image: global level, this "
            "server's level, coins, fishing, casino, activities, achievements, "
            "prestige, and the badges/banner you've equipped.",
            "args": ["member — whose profile to show (defaults to you)"],
            "perms": "None",
            "example": "{prefix}profile\n{prefix}profile @Friend\n"
            "{prefix}profile equip Gilded Frame",
        },
    )
    @commands.guild_only()
    @app_commands.describe(member="Whose profile to show (defaults to you)")
    @commands.cooldown(1, 8, commands.BucketType.user)
    async def profile(
        self, ctx: commands.Context, member: Optional[discord.Member] = None
    ):
        if ctx.invoked_subcommand is not None:
            return
        await self._send_card(ctx, member)

    # ── the card ─────────────────────────────────────────────────────────────
    async def _send_card(self, ctx: commands.Context, member: Optional[discord.Member]):
        member = member or ctx.author
        if member.bot:
            return await ctx.reply(
                embed=h.err("Bots don't have profiles."), ephemeral=True
            )
        # A render is ~200ms of CPU plus an avatar fetch, so tell Discord we're
        # working (slash: defer, prefix: typing) before doing either. Both are
        # best-effort — a failed defer must never cost the card.
        try:
            await ctx.defer()
        except Exception:
            pass
        data, notes = await self._collect(ctx, member)
        data["avatar"] = await self._avatar_bytes(member)
        async with self._render_lock:
            png = await asyncio.to_thread(profile_card.render_card, data)
        file = discord.File(
            fp=io.BytesIO(png),
            filename=f"profile-{member.id}.{profile_card.IMAGE_EXT}",
        )
        content = None
        if notes and member.id == ctx.author.id:
            content = "🎁 Unlocked: " + ", ".join(f"**{n}**" for n in notes[:4])
        await ctx.reply(content=content, file=file)

    async def _avatar_bytes(self, member: discord.abc.User) -> Optional[bytes]:
        """The member's avatar as PNG bytes, or None (the card then draws an
        initial tile). Never let a CDN hiccup fail the whole command."""
        try:
            asset = member.display_avatar.replace(size=256, static_format="png")
            return await asyncio.wait_for(asset.read(), timeout=5)
        except Exception:
            log.debug("avatar fetch failed for %s", member.id, exc_info=True)
            return None

    async def _collect(self, ctx: commands.Context, member: discord.Member):
        """Gather everything the card draws — and quietly hand out any cosmetic
        the member has newly qualified for. Returns (card_data, unlock_names)."""
        uid = member.id
        # One batched read of every economy source row; `raw` hands back the
        # rows themselves so the fishing/casino/activity sections below don't
        # re-query what this already fetched.
        stats, raw = await compute_stats_with_sources(uid)
        econ = await db.get_econ_config(ctx.guild.id)
        gxp = await db.get_global_xp(uid)
        g_level, g_into, g_need = globalxp.level_progress(gxp)

        # Server level — the per-guild system, read exactly as its own cog does.
        level_cfg = await db.get_level_config(ctx.guild.id)
        server_xp = await db.get_xp(ctx.guild.id, uid)
        s_level, s_into, s_need = server_level_progress(server_xp)

        progression = await db.get_progression(uid)
        earned = await db.get_earned_achievements(uid)
        social = await db.get_social(uid)
        fisher = raw["fisher"]
        casino = raw["casino"]
        activity = raw["activity"]

        # Lazily unlock anything they've earned (own view only, mirroring how
        # achievements are awarded).
        ctx_unlock = unlock_context(
            global_level=g_level,
            prestige=progression["prestige"],
            achievements=earned.keys(),
            stats=stats,
        )
        owned = await db.get_unlocked_cosmetics(uid)
        notes: list[str] = []
        if member.id == ctx.author.id:
            for d in newly_unlocked(owned, ctx_unlock):
                if await db.unlock_cosmetic(uid, d.key, at=time.time()):
                    notes.append(d.name)
            if notes:
                owned = await db.get_unlocked_cosmetics(uid)

        loadout = await self._loadout(uid, owned)
        titles = earned_titles(earned.keys(), ACHIEVEMENTS_BY_KEY)
        title = progression["selected_title"] or (titles[0][0] if titles else "")
        if progression["prestige"]:
            pres = prestige_title(progression["prestige"])
            title = f"{title} · {pres}" if title else pres

        rod = rod_info(fisher["rod_level"])
        pickaxe = pickaxe_info(activity["pickaxe_level"])
        career = career_info(activity["work_shifts"])
        net = casino["won"] - casino["wagered"]
        currency = econ["currency_name"]

        chips = [
            ("Coins", f"{stats.get('balance', 0):,.0f} {currency}s"),
            (
                "Achievements",
                f"{len(earned)} / {len(ACHIEVEMENTS)} · "
                f"{total_points(earned.keys(), ACHIEVEMENTS_BY_KEY):,} pts",
            ),
            (
                "Fishing",
                f"Lv {fish_level(fisher['xp'])} · {rod['name']} "
                f"({fisher['rod_level'] + 1}/{len(RODS)})",
            ),
            (
                "Casino",
                (
                    f"{net:+,} net · {casino['games']:,} "
                    f"game{'' if casino['games'] == 1 else 's'}"
                    if casino["games"]
                    else "No games yet"
                ),
            ),
            (
                "Work & Mining",
                f"{career['title'].split(' ', 1)[-1]} · {pickaxe['name']} "
                f"({activity['pickaxe_level'] + 1}/{len(PICKAXES)})",
            ),
            ("Items", f"{int(stats.get('items_owned', 0)):,} carried"),
        ]

        data = {
            "name": member.display_name,
            "title": title,
            "prestige": progression["prestige"],
            "global_level": g_level,
            "global_into": g_into,
            "global_need": g_need,
            "server_level": s_level,
            "server_into": s_into,
            "server_need": s_need,
            "server_enabled": bool(level_cfg["enabled"]),
            "rep": social["rep"],
            "chips": chips,
            "badges": [
                cosmetics.get(k) for k in loadout.get("badge", []) if cosmetics.get(k)
            ],
            "banner": cosmetics.get((loadout.get("banner") or [""])[0]),
            "border": cosmetics.get((loadout.get("border") or [""])[0]),
            "nameplate": cosmetics.get((loadout.get("nameplate") or [""])[0]),
        }
        return data, notes

    async def _loadout(self, user_id: int, owned: dict) -> dict[str, list[str]]:
        """Equipped cosmetics, filtered to what's still owned and topped up with
        the defaults so a brand-new card looks finished."""
        return resolve_loadout(await db.get_equipped(user_id), owned)

    # ── /profile cosmetics ───────────────────────────────────────────────────
    @profile.command(
        name="cosmetics",
        description="Everything you own, and how to unlock the rest.",
    )
    @app_commands.describe(slot="Only show one slot")
    @app_commands.choices(
        slot=[
            app_commands.Choice(name=s.label, value=s.key)
            for s in cosmetics.SLOTS.values()
        ]
    )
    async def profile_cosmetics(
        self, ctx: commands.Context, slot: Optional[str] = None
    ):
        owned = await db.get_unlocked_cosmetics(ctx.author.id)
        equipped = await db.get_equipped(ctx.author.id)
        slots = [slot] if slot in cosmetics.SLOTS else list(cosmetics.SLOTS)
        embed = h.embed(
            "🎨 Your Cosmetics",
            "Equip anything you own with `/profile equip <name>`.",
            ACCENT,
        )
        for key in slots:
            slot_def = cosmetics.SLOTS[key]
            worn = set(equipped.get(key, []))
            lines = []
            for d in cosmetics.in_slot(key):
                default = (d.unlock or {}).get("kind") == "default"
                has = d.key in owned or default
                mark = "✅" if d.key in worn else ("▫️" if has else "🔒")
                line = f"{mark} {rarity_marker(d.rarity)} **{d.name}**"
                if not has:
                    line += f" — {cosmetics.describe_unlock(d)}"
                lines.append(line)
            embed.add_field(
                name=f"{slot_def.label} ({len(worn)}/{slot_def.max_equipped} worn)",
                value="\n".join(lines)[:1024] or "Nothing here yet.",
                inline=False,
            )
        embed.set_footer(text="✅ equipped · ▫️ owned · 🔒 locked")
        await ctx.reply(embed=embed)

    # ── tap-to-pick plumbing ─────────────────────────────────────────────────
    # Every cosmetic option is a picker (nobody types `banner_ember` from
    # memory on a phone), and they all render a choice the same way so the
    # lists read alike wherever they turn up.
    def _cosmetic_choice(
        self, d: cosmetics.CosmeticDef, mark: str, detail: str = ""
    ) -> app_commands.Choice[str]:
        """One picker row: state marker, rarity, slot, name, then the useful bit.

        Choice names are plain text (no markdown) and hard-capped at 100 chars
        by Discord, so the detail is what gets truncated, never the name.
        """
        label = (
            f"{mark} {rarity_marker(d.rarity)} "
            f"{cosmetics.SLOTS[d.slot].label}: {d.name}"
        )
        if detail:
            label += f" — {detail}"
        return app_commands.Choice(name=label[:100], value=d.key)

    @staticmethod
    def _matches(d: cosmetics.CosmeticDef, q: str) -> bool:
        """Filter on anything someone might type: name, key, or slot."""
        if not q:
            return True
        return q in d.name.lower() or q in d.key.lower() or q in d.slot.lower()

    # ── /profile equip ───────────────────────────────────────────────────────
    @profile.command(name="equip", description="Wear cosmetics on your card.")
    @app_commands.describe(
        cosmetic="Pick one — or several, separated by commas. Locked ones show "
        "how to unlock"
    )
    async def profile_equip(
        self, ctx: commands.Context, *, cosmetic: Optional[str] = None
    ):
        """Wear one cosmetic, several, or open the picker and tap a few.

        A loadout is six slots, so dressing a card was up to six commands — and
        filling the badge showcase alone was six on its own. `cosmetic` takes a
        comma-separated list, and left empty it opens a multi-select of
        everything wearable right now.
        """
        targets = h.split_targets(cosmetic or "", MAX_EQUIP_TARGETS)
        if not targets:
            return await self._cosmetic_picker(ctx, "equip")
        embed, changed = await self.run_equip(ctx.author.id, targets)
        await ctx.reply(embed=embed, ephemeral=not changed)

    async def run_equip(
        self, user_id: int, targets: list[str]
    ) -> tuple[discord.Embed, int]:
        """Equip every named cosmetic, and say what landed.

        The loadout is carried through the loop in memory and written once per
        slot it actually changed: two badges picked together must both land, and
        a second write per badge would be the same row twice. A refusal is per
        line — one locked pick can't stop the rest going on.
        """
        owned = await db.get_unlocked_cosmetics(user_id)
        equipped = await db.get_equipped(user_id)
        lines: list[str] = []
        singles: list[discord.Embed] = []
        changed: set[str] = set()
        for target in targets:
            slot, line, embed = self._equip_one(target, owned, equipped)
            lines.append(line)
            singles.append(embed)
            if slot:
                changed.add(slot)
        for slot in changed:
            await db.set_equipped(user_id, slot, equipped[slot])
        if len(targets) == 1:
            # One pick keeps its own wording — a locked or full answer is read as
            # the reply to a question, not as a one-line report.
            return singles[0], len(changed)
        embed = (h.ok if changed else h.warn)(
            "\n".join(lines),
            "🎨 Equipped" if changed else "🎨 Nothing Equipped",
        )
        if changed:
            embed.set_footer(text="See it with /profile")
        return embed, len(changed)

    def _equip_one(
        self, target: str, owned: set | dict, equipped: dict[str, list[str]]
    ) -> tuple[Optional[str], str, discord.Embed]:
        """Work out one pick against the loadout in hand.

        Pure apart from updating `equipped` in place, which is what lets a
        multi-pick fill a badge showcase two at a time and still write each slot
        once. Returns (slot changed or None, summary line, standalone embed).
        """
        d = cosmetics.find(target)
        if d is None:
            missing = f"There's no cosmetic called **{target}**."
            return (
                None,
                f"❔ {missing}",
                h.err(f"{missing} See `/profile cosmetics`."),
            )
        if d.key not in owned and (d.unlock or {}).get("kind") != "default":
            unlock = cosmetics.describe_unlock(d)
            return (
                None,
                f"🔒 **{d.name}** — {unlock}.",
                h.warn(
                    f"You haven't unlocked **{d.name}** yet — {unlock}.", "🔒 Locked"
                ),
            )
        new_keys, outcome = equip_result(d.slot, equipped.get(d.slot, []), d.key)
        label = cosmetics.SLOTS[d.slot].label.lower()
        if outcome == "already":
            return (
                None,
                f"▫️ **{d.name}** is already on.",
                h.info(f"**{d.name}** is already equipped."),
            )
        if outcome == "full":
            worn = ", ".join(
                cosmetics.get(k).name for k in new_keys if cosmetics.get(k)
            )
            return (
                None,
                f"🈵 Your {label} are full ({worn}) — take one off first.",
                h.warn(
                    f"Your {label} are full ({worn}). Take one off with "
                    "`/profile unequip` first.",
                    "Slot Full",
                ),
            )
        equipped[d.slot] = new_keys
        return (
            d.slot,
            f"✅ {rarity_marker(d.rarity)} **{d.name}** ({label})",
            h.ok(
                f"Equipped {rarity_marker(d.rarity)} **{d.name}**. "
                "Check it with `/profile card`.",
                "🎨 Equipped",
            ),
        )

    @profile_equip.autocomplete("cosmetic")
    async def _equip_ac(self, interaction: discord.Interaction, current: str):
        """Tap-to-pick loadout, the /craft make + /fish buy pattern.

        Wearable-now first, then what's already on, then what's still locked
        with the unlock line attached — so the picker doubles as the "how do I
        get that one?" answer instead of coming back empty on a new account.
        The markers match /profile cosmetics: ✅ worn · ▫️ owned · 🔒 locked.

        Each bucket is interleaved across slots (see `interleave_by_slot`) so
        the 25 rows Discord shows cover banners, borders, plates, badges and
        the wallet pair rather than 25 badges.
        """
        q = (current or "").strip().lower()
        owned = await db.get_unlocked_cosmetics(interaction.user.id)
        equipped = await db.get_equipped(interaction.user.id)
        worn = {key for keys in equipped.values() for key in keys}
        ready: list[tuple[str, app_commands.Choice[str]]] = []
        already: list[tuple[str, app_commands.Choice[str]]] = []
        locked: list[tuple[str, app_commands.Choice[str]]] = []
        for d in sorted(
            cosmetics.COSMETICS.values(), key=lambda c: (c.slot, c.sort, c.name)
        ):
            if not self._matches(d, q):
                continue
            default = (d.unlock or {}).get("kind") == "default"
            if d.key in worn:
                already.append((d.slot, self._cosmetic_choice(d, "✅", "already worn")))
            elif d.key in owned or default:
                ready.append((d.slot, self._cosmetic_choice(d, "▫️", d.description)))
            else:
                locked.append(
                    (
                        d.slot,
                        self._cosmetic_choice(d, "🔒", cosmetics.describe_unlock(d)),
                    )
                )
        return (
            interleave_by_slot(ready)
            + interleave_by_slot(already)
            + interleave_by_slot(locked)
        )[:25]

    # ── /profile unequip ─────────────────────────────────────────────────────
    @profile.command(name="unequip", description="Take cosmetics off your card.")
    @app_commands.describe(
        cosmetic="Pick what to take off, several separated by commas, or a whole slot"
    )
    async def profile_unequip(
        self, ctx: commands.Context, *, cosmetic: Optional[str] = None
    ):
        """Take one off, several off, or open the picker of what's on."""
        targets = h.split_targets(cosmetic or "", MAX_EQUIP_TARGETS)
        if not targets:
            return await self._cosmetic_picker(ctx, "unequip")
        embed, changed = await self.run_unequip(ctx.author.id, targets)
        await ctx.reply(embed=embed, ephemeral=not changed)

    async def run_unequip(
        self, user_id: int, targets: list[str]
    ) -> tuple[discord.Embed, int]:
        """Take every named cosmetic off, and say what came off.

        A slot name ("badge") still clears the whole showcase, which is the one
        shortcut worth keeping now that a multi-pick can do the same thing the
        long way.
        """
        equipped = await db.get_equipped(user_id)
        lines: list[str] = []
        singles: list[discord.Embed] = []
        changed: set[str] = set()
        for target in targets:
            wanted = target.strip().lower()
            if wanted in cosmetics.SLOTS:
                label = cosmetics.SLOTS[wanted].label.lower()
                equipped[wanted] = []
                changed.add(wanted)
                lines.append(f"🧹 Cleared your {label}.")
                singles.append(h.ok(f"Cleared your {label}."))
                continue
            d = cosmetics.find(target)
            if d is None:
                missing = f"There's no cosmetic called **{target}**."
                lines.append(f"❔ {missing}")
                singles.append(h.err(missing))
                continue
            keys = [k for k in equipped.get(d.slot, []) if k != d.key]
            if len(keys) == len(equipped.get(d.slot, [])):
                lines.append(f"▫️ **{d.name}** isn't on.")
                singles.append(h.info(f"**{d.name}** isn't equipped."))
                continue
            equipped[d.slot] = keys
            changed.add(d.slot)
            lines.append(f"✅ Took off **{d.name}**.")
            singles.append(h.ok(f"Took off **{d.name}**."))
        for slot in changed:
            await db.set_equipped(user_id, slot, equipped[slot])
        if len(targets) == 1:
            return singles[0], len(changed)
        return (h.ok if changed else h.warn)(
            "\n".join(lines),
            "🎨 Taken Off" if changed else "🎨 Nothing Changed",
        ), len(changed)

    # ── the tap-to-dress picker ──────────────────────────────────────────────
    async def _cosmetic_picker(self, ctx: commands.Context, mode: str):
        """What a bare `/profile equip` or `/profile unequip` opens.

        The autocomplete hands over one cosmetic at a time, which is the right
        shape for a name and the wrong one for a loadout: six slots meant six
        commands. This is the same list as a multi-select, so a whole card gets
        dressed in one press.
        """
        rows = await self.picker_rows(ctx.author.id, mode)
        if not rows:
            empty = (
                "You've nothing unlocked to wear yet — earn cosmetics by playing, "
                "or buy one with `/shop profile`."
                if mode == "equip"
                else "Your card is bare — nothing to take off."
            )
            return await ctx.reply(embed=h.info(empty, "🎨 Cosmetics"), ephemeral=True)
        view = CosmeticPickerView(
            self,
            user_id=ctx.author.id,
            mode=mode,
            rows=rows,
            timeout=EQUIP_PICKER_TIMEOUT,
        )
        view.message = await ctx.reply(embed=self._picker_embed(mode, rows), view=view)

    async def picker_rows(self, user_id: int, mode: str) -> list[dict]:
        """The rows a picker offers: wearable-now to equip, worn to unequip.

        Locked cosmetics are deliberately absent here even though the *typing*
        picker lists them — a select is a set of things to do, and an option that
        refuses on press is a dead row. The autocomplete stays the place that
        answers "how do I get that one?".
        """
        owned = await db.get_unlocked_cosmetics(user_id)
        equipped = await db.get_equipped(user_id)
        worn = {key for keys in equipped.values() for key in keys}
        rows: list[tuple[str, dict]] = []
        for d in sorted(
            cosmetics.COSMETICS.values(), key=lambda c: (c.slot, c.sort, c.name)
        ):
            default = (d.unlock or {}).get("kind") == "default"
            if mode == "unequip":
                if d.key not in worn:
                    continue
            elif d.key in worn or not (d.key in owned or default):
                continue
            rows.append(
                (
                    d.slot,
                    {
                        "key": d.key,
                        "label": f"{cosmetics.SLOTS[d.slot].label}: {d.name}",
                        "description": (d.description or "")[:100] or None,
                        "emoji": None,
                    },
                )
            )
        # Interleaved across slots for the same reason the autocomplete is: the
        # badge list alone is longer than the 25 rows Discord will show.
        return interleave_by_slot(rows)[:25]

    def _picker_embed(self, mode: str, rows: list[dict]) -> discord.Embed:
        """The card above the picker: what's on offer, and what one press does."""
        body = "\n".join(f"▫️ {row['label']}" for row in rows)
        if mode == "equip":
            return h.embed(
                "🎨 Dress Your Card",
                "Pick everything you want on — banners and plates swap, badges "
                "fill up to six.\n\n" + body[:3500],
                ACCENT,
            )
        return h.embed(
            "🎨 Take Cosmetics Off",
            "Pick everything to remove.\n\n" + body[:3500],
            ACCENT,
        )

    @profile_unequip.autocomplete("cosmetic")
    async def _unequip_ac(self, interaction: discord.Interaction, current: str):
        """Only what's actually on the card right now, plus the clear-a-slot
        shortcuts the command already accepts."""
        q = (current or "").strip().lower()
        equipped = await db.get_equipped(interaction.user.id)
        choices: list[app_commands.Choice[str]] = []
        for slot in cosmetics.SLOTS:
            for key in equipped.get(slot, []):
                d = cosmetics.get(key)
                if d is None or not self._matches(d, q):
                    continue
                choices.append(self._cosmetic_choice(d, "✅", "worn — tap to remove"))
        # Multi-value slots only: for a single-value slot, taking the one thing
        # off *is* clearing it, so a second entry would just be a duplicate.
        for slot_def in cosmetics.SLOTS.values():
            worn = len(equipped.get(slot_def.key, []))
            if slot_def.max_equipped > 1 and worn and (not q or q in slot_def.key):
                choices.append(
                    app_commands.Choice(
                        name=f"🧹 Clear all {slot_def.label.lower()} ({worn} worn)"[
                            :100
                        ],
                        value=slot_def.key,
                    )
                )
        return choices[:25]

    # ── /profile preview ─────────────────────────────────────────────────────
    @profile.command(
        name="preview",
        description="See a cosmetic on your own card before you buy or equip it.",
    )
    @app_commands.describe(cosmetic="Pick one — owned or not, this only previews")
    @commands.cooldown(1, 8, commands.BucketType.user)
    async def profile_preview(self, ctx: commands.Context, *, cosmetic: str):
        """Try a cosmetic on without owning it.

        The shop can show what a banner looks like, but not what it looks like
        *with your name, avatar and stats on top of it* — which is the thing
        actually being bought. Nothing is equipped and nothing is charged; the
        card is rendered with the pick swapped in and thrown away.
        """
        d = cosmetics.find(cosmetic)
        if d is None:
            return await ctx.reply(
                embed=h.err(
                    f"There's no cosmetic called **{cosmetic}**. "
                    "See `/profile cosmetics` or `/shop profile`."
                ),
                ephemeral=True,
            )
        try:
            await ctx.defer()
        except Exception:
            pass
        data, _notes = await self._collect(ctx, ctx.author)
        data["avatar"] = await self._avatar_bytes(ctx.author)
        if d.slot == "badge":
            worn = [b for b in (data.get("badges") or []) if b.key != d.key]
            limit = cosmetics.SLOTS["badge"].max_equipped
            # Show it in the last slot rather than dropping it when the
            # showcase is full — a preview that silently omits the thing being
            # previewed is worse than useless.
            data["badges"] = (worn[: limit - 1]) + [d]
        else:
            data[d.slot] = d

        async with self._render_lock:
            png = await asyncio.to_thread(profile_card.render_card, data)
        owned = await db.get_unlocked_cosmetics(ctx.author.id)
        if d.key in owned or (d.unlock or {}).get("kind") == "default":
            note = f"Preview — wear it for real with `/profile equip {d.name}`."
        elif (d.unlock or {}).get("kind") == "purchase":
            note = f"Preview — buy it with `/shop unlock {d.name}` ({d.price:,} coins)."
        else:
            note = f"Preview — {cosmetics.describe_unlock(d)}."
        await ctx.reply(
            content=f"👁️ {note}",
            file=discord.File(
                fp=io.BytesIO(png),
                filename=f"preview-{ctx.author.id}.{profile_card.IMAGE_EXT}",
            ),
        )

    @profile_preview.autocomplete("cosmetic")
    async def _preview_ac(self, interaction: discord.Interaction, current: str):
        """Everything in the catalogue — previewing is exactly the case where
        you have not got it yet, so nothing is filtered out by ownership."""
        q = (current or "").strip().lower()
        owned = await db.get_unlocked_cosmetics(interaction.user.id)
        rows: list[tuple[str, app_commands.Choice[str]]] = []
        for d in sorted(
            cosmetics.COSMETICS.values(), key=lambda c: (c.slot, c.sort, c.name)
        ):
            if not self._matches(d, q):
                continue
            if d.key in owned or (d.unlock or {}).get("kind") == "default":
                detail = "owned"
            elif (d.unlock or {}).get("kind") == "purchase":
                detail = f"{d.price:,} coins"
            else:
                detail = cosmetics.describe_unlock(d)
            rows.append((d.slot, self._cosmetic_choice(d, "👁️", detail)))
        return interleave_by_slot(rows)[:25]

    # ── /profile badges ──────────────────────────────────────────────────────
    @profile.command(name="badges", description="The badge gallery.")
    @app_commands.describe(member="Whose badges to show (defaults to you)")
    async def profile_badges(
        self, ctx: commands.Context, member: Optional[discord.Member] = None
    ):
        member = member or ctx.author
        owned = await db.get_unlocked_cosmetics(member.id)
        equipped = set((await db.get_equipped(member.id)).get("badge", []))
        badges = cosmetics.in_slot("badge")
        have = [d for d in badges if d.key in owned]
        lines = [
            f"{'✅' if d.key in equipped else '▫️'} {rarity_marker(d.rarity)} "
            f"**{d.name}** — {d.description}"
            for d in have
        ]
        locked = [
            f"🔒 **{d.name}** — {cosmetics.describe_unlock(d)}"
            for d in badges
            if d.key not in owned
        ]
        embed = h.embed(
            f"🏅 {member.display_name}'s Badges",
            f"**{len(have)}/{len(badges)}** unlocked · up to "
            f"{cosmetics.SLOTS['badge'].max_equipped} can be worn at once.",
            ACCENT,
        )
        embed.add_field(
            name="Unlocked",
            value="\n".join(lines)[:1024] if lines else "None yet — go play!",
            inline=False,
        )
        if locked:
            embed.add_field(
                name="Still locked", value="\n".join(locked)[:1024], inline=False
            )
        await ctx.reply(embed=embed)

    # ── /profile rep ─────────────────────────────────────────────────────────
    @profile.command(
        name="rep",
        description="Give someone a reputation point — once every 24 hours.",
    )
    @app_commands.describe(member="Who to rep (leave blank to see your own)")
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def profile_rep(
        self, ctx: commands.Context, member: Optional[discord.Member] = None
    ):
        """ "This person is cool", one a day, and it shows on their card.

        Rep is global, like everything else on the account: it is a thing said
        about a *person*, so it would be odd for it to follow them into one
        server and not another. It buys nothing — no coins, no items — which is
        why the daily limit is only there to keep it meaningful.
        """
        target = member
        if target is None:
            return await self._show_own_rep(ctx)
        if target.bot:
            return await ctx.reply(
                embed=h.err("Bots are above this sort of thing."), ephemeral=True
            )
        if target.id == ctx.author.id:
            return await ctx.reply(
                embed=h.err("Repping yourself doesn't count. Nice try."),
                ephemeral=True,
            )

        # Claim the day first: the conditional UPDATE is what stops a
        # double-tap spending one day's rep twice.
        retry = await db.try_claim_rep(ctx.author.id, time.time())
        if retry:
            return await ctx.reply(
                embed=h.warn(
                    f"You've already given your rep today. Next one in "
                    f"**{h.fmt_duration(retry)}**.",
                    "⏳ Once a day",
                ),
                ephemeral=True,
            )
        try:
            total = await db.add_rep(target.id)
        except Exception:
            # The day was claimed but nothing was awarded — give it back rather
            # than charging someone for a failure.
            await db.refund_rep(ctx.author.id)
            raise

        await globalxp.award(ctx.author.id, "rep")
        await ctx.reply(
            embed=h.ok(
                f"{ctx.author.mention} repped {target.mention}.\n"
                f"**{target.display_name}** now has **{total:,}** rep.",
                "👍 Reputation",
            )
        )

    async def _show_own_rep(self, ctx: commands.Context):
        social = await db.get_social(ctx.author.id)
        rank = await db.get_rep_rank(ctx.author.id)
        left = max(0, int(REP_COOLDOWN - (time.time() - social["last_rep"])))
        lines = [f"You have **{social['rep']:,}** rep."]
        if rank:
            lines.append(f"That's **#{rank[0]:,}** overall.")
        lines.append(
            "You can give rep **now**."
            if not left
            else f"Your next rep is ready in **{h.fmt_duration(left)}**."
        )
        await ctx.reply(
            embed=h.embed(
                "👍 Reputation",
                "\n".join(lines) + "\n\nGive one with `/profile rep @user`.",
                ACCENT,
            )
        )

    # ── owner-only grants (events, staff awards) ─────────────────────────────
    # These three are prefix-only (`with_app_command=False`). They exist for the
    # bot owner running an event drop, not for the members whose slash picker
    # they would otherwise sit in — every server on the bot would see three
    # entries under /profile that none of them can run. Owner tooling lives on
    # the prefix, the same place !econ, !cooldown and !reload already do.
    @profile.command(
        name="grant",
        description="Award a cosmetic to a member (bot owner).",
        with_app_command=False,
    )
    @commands.is_owner()
    async def profile_grant(
        self, ctx: commands.Context, member: discord.Member, *, cosmetic: str
    ):
        d = cosmetics.find(cosmetic)
        if d is None:
            return await ctx.reply(
                embed=h.err(f"There's no cosmetic called **{cosmetic}**."),
                ephemeral=True,
            )
        first = await db.unlock_cosmetic(member.id, d.key, at=time.time())
        await ctx.reply(
            embed=h.ok(
                f"{'Granted' if first else 'Already had'} "
                f"{rarity_marker(d.rarity)} **{d.name}** → {member.mention}."
            )
        )

    @profile.command(
        name="grantall",
        description="Award a cosmetic to everyone in a server (bot owner).",
        with_app_command=False,
    )
    @commands.is_owner()
    async def profile_grantall(
        self, ctx: commands.Context, cosmetic: str, guild: Optional[str] = None
    ):
        """Bulk grant — for one-off drops like handing a beta badge to everyone
        who was there. Idempotent: re-running only picks up members who joined
        since, because each grant is an INSERT OR IGNORE.

        `cosmetic` is a plain positional (a `guild` argument follows it), so a
        multi-word *name* needs quotes — `"Beta Tester"` — or just use the key,
        `badge_beta_tester`.
        """
        d = cosmetics.find(cosmetic)
        if d is None:
            return await ctx.reply(
                embed=h.err(
                    f"There's no cosmetic called **{cosmetic}**. Multi-word "
                    'names need quotes here ("Beta Tester"), or use the key '
                    "(`badge_beta_tester`)."
                ),
                ephemeral=True,
            )
        target = ctx.guild
        if guild:
            raw = guild.strip()
            if not raw.isdigit():
                return await ctx.reply(
                    embed=h.err("Give a numeric server id."), ephemeral=True
                )
            target = self.bot.get_guild(int(raw))
            if target is None:
                return await ctx.reply(
                    embed=h.err(f"I'm not in a server with id `{raw}`."),
                    ephemeral=True,
                )
        try:
            await ctx.defer()
        except Exception:
            pass
        # A big guild may not be fully cached; chunk before counting so nobody
        # is silently skipped.
        if not target.chunked:
            try:
                await target.chunk()
            except Exception:
                log.warning(
                    "Could not chunk %s — granting to cached members only", target.id
                )

        granted = already = 0
        for member in target.members:
            if member.bot:
                continue
            if await db.unlock_cosmetic(member.id, d.key, at=time.time()):
                granted += 1
            else:
                already += 1
        log.info(
            "grantall %s → guild %s: %d new, %d already had it",
            d.key,
            target.id,
            granted,
            already,
        )
        await ctx.reply(
            embed=h.ok(
                f"Awarded {rarity_marker(d.rarity)} **{d.name}** in "
                f"**{target.name}**.\n"
                f"**{granted}** newly granted · **{already}** already had it.\n\n"
                "They can wear it with `/profile equip "
                f"{d.name}`.",
                "🎁 Bulk Grant",
            )
        )

    @profile.command(
        name="revoke",
        description="Remove a cosmetic from a member (bot owner).",
        with_app_command=False,
    )
    @commands.is_owner()
    async def profile_revoke(
        self, ctx: commands.Context, member: discord.Member, *, cosmetic: str
    ):
        d = cosmetics.find(cosmetic)
        if d is None:
            return await ctx.reply(
                embed=h.err(f"There's no cosmetic called **{cosmetic}**."),
                ephemeral=True,
            )
        removed = await db.revoke_cosmetic(member.id, d.key)
        await ctx.reply(
            embed=h.ok(
                f"{'Removed' if removed else 'They did not have'} **{d.name}** "
                f"({member.mention})."
            )
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Identity(bot))
