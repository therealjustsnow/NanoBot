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

──────────────────────────────────────────────────────
Commands
──────────────────────────────────────────────────────
  /profile card [member]        → the profile card image (bare `n!profile`)
  /profile cosmetics [slot]     → what you own, and how to unlock the rest
  /profile equip <cosmetic>     → wear a banner/border/nameplate/badge
  /profile unequip <cosmetic>   → take one off (or clear a slot)
  /profile badges [member]      → the badge gallery
  /profile grant <member> <cosmetic>  → award a cosmetic   (bot owner)
  /profile revoke <member> <cosmetic> → take one back      (bot owner)
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

# Pure data/helpers borrowed from the feature packages for display only — the
# cogs/images.py → cogs/fun/sources.py precedent. No cog state is touched.
from cogs.activities.constants import PICKAXES
from cogs.activities.helpers import career_info, pickaxe_info
from cogs.fishing.constants import RODS
from cogs.fishing.helpers import fish_level, rod_info
from cogs.leveling import level_progress as server_level_progress
from cogs.progression.definitions import ACHIEVEMENTS, ACHIEVEMENTS_BY_KEY
from cogs.progression.helpers import earned_titles, prestige_title, total_points
from cogs.progression.stats import compute_stats

from .helpers import equip_result, newly_unlocked, rarity_marker, unlock_context

log = logging.getLogger("NanoBot.identity")

ACCENT = 0x5865F2


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
        except Exception:  # pragma: no cover - never break message handling
            log.exception("global XP award failed for %s", message.author.id)

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
            "short": "Your account card and cosmetics",
            "usage": "profile [subcommand]",
            "desc": "Your whole account on one image: global level, this "
            "server's level, coins, fishing, casino, activities, achievements, "
            "prestige, and the badges/banner you've equipped. Your account is "
            "the same in every server — only the server level line changes.",
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
        file = discord.File(fp=io.BytesIO(png), filename=f"profile-{member.id}.png")
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
        stats = await compute_stats(uid)
        econ = await db.get_econ_config(ctx.guild.id)
        gxp = await db.get_global_xp(uid)
        g_level, g_into, g_need = globalxp.level_progress(gxp)

        # Server level — the per-guild system, read exactly as its own cog does.
        level_cfg = await db.get_level_config(ctx.guild.id)
        server_xp = await db.get_xp(ctx.guild.id, uid)
        s_level, s_into, s_need = server_level_progress(server_xp)

        progression = await db.get_progression(uid)
        earned = await db.get_earned_achievements(uid)
        fisher = await db.get_fisher(uid)
        casino = await db.get_casino_stats(uid)
        activity = await db.get_activity_stats(uid)

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
            "chips": chips,
            "badges": [
                cosmetics.get(k) for k in loadout.get("badge", []) if cosmetics.get(k)
            ],
            "banner": cosmetics.get((loadout.get("banner") or [""])[0]),
            "border": cosmetics.get((loadout.get("border") or [""])[0]),
            "nameplate": cosmetics.get((loadout.get("nameplate") or [""])[0]),
            "footer": f"{ctx.guild.name} · one account, every server",
        }
        return data, notes

    async def _loadout(self, user_id: int, owned: dict) -> dict[str, list[str]]:
        """Equipped cosmetics, filtered to what's still owned and topped up with
        the defaults so a brand-new card looks finished."""
        equipped = await db.get_equipped(user_id)
        out: dict[str, list[str]] = {}
        for slot in cosmetics.SLOTS:
            keys = [
                k
                for k in equipped.get(slot, [])
                if cosmetics.get(k)
                and (
                    k in owned
                    or (cosmetics.get(k).unlock or {}).get("kind") == "default"
                )
            ]
            if not keys:
                keys = [
                    k
                    for k in cosmetics.DEFAULT_LOADOUT.get(slot, [])
                    if cosmetics.get(k)
                ]
            out[slot] = keys
        return out

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

    # ── /profile equip ───────────────────────────────────────────────────────
    @profile.command(name="equip", description="Wear a cosmetic on your card.")
    @app_commands.describe(cosmetic="Pick something you own")
    async def profile_equip(self, ctx: commands.Context, *, cosmetic: str):
        d = cosmetics.find(cosmetic)
        if d is None:
            return await ctx.reply(
                embed=h.err(
                    f"There's no cosmetic called **{cosmetic}**. "
                    "See `/profile cosmetics`."
                ),
                ephemeral=True,
            )
        owned = await db.get_unlocked_cosmetics(ctx.author.id)
        if d.key not in owned and (d.unlock or {}).get("kind") != "default":
            return await ctx.reply(
                embed=h.warn(
                    f"You haven't unlocked **{d.name}** yet — "
                    f"{cosmetics.describe_unlock(d)}.",
                    "🔒 Locked",
                ),
                ephemeral=True,
            )
        equipped = await db.get_equipped(ctx.author.id)
        new_keys, outcome = equip_result(d.slot, equipped.get(d.slot, []), d.key)
        if outcome == "already":
            return await ctx.reply(
                embed=h.info(f"**{d.name}** is already equipped."), ephemeral=True
            )
        if outcome == "full":
            worn = ", ".join(
                cosmetics.get(k).name for k in new_keys if cosmetics.get(k)
            )
            return await ctx.reply(
                embed=h.warn(
                    f"Your {cosmetics.SLOTS[d.slot].label.lower()} are full "
                    f"({worn}). Take one off with `/profile unequip` first.",
                    "Slot Full",
                ),
                ephemeral=True,
            )
        await db.set_equipped(ctx.author.id, d.slot, new_keys)
        await ctx.reply(
            embed=h.ok(
                f"Equipped {rarity_marker(d.rarity)} **{d.name}**. "
                "Check it with `/profile`.",
                "🎨 Equipped",
            )
        )

    @profile_equip.autocomplete("cosmetic")
    async def _equip_ac(self, interaction: discord.Interaction, current: str):
        """Only what they can actually wear, labelled by slot."""
        q = (current or "").strip().lower()
        owned = await db.get_unlocked_cosmetics(interaction.user.id)
        choices = []
        for d in sorted(
            cosmetics.COSMETICS.values(), key=lambda c: (c.slot, c.sort, c.name)
        ):
            default = (d.unlock or {}).get("kind") == "default"
            if d.key not in owned and not default:
                continue
            if q and q not in d.name.lower() and q not in d.key.lower():
                continue
            label = f"{cosmetics.SLOTS[d.slot].label}: {d.name}"
            choices.append(app_commands.Choice(name=label[:100], value=d.key))
        return choices[:25]

    # ── /profile unequip ─────────────────────────────────────────────────────
    @profile.command(name="unequip", description="Take a cosmetic off your card.")
    @app_commands.describe(cosmetic="What to remove (or a whole slot)")
    async def profile_unequip(self, ctx: commands.Context, *, cosmetic: str):
        equipped = await db.get_equipped(ctx.author.id)
        wanted = (cosmetic or "").strip().lower()
        if wanted in cosmetics.SLOTS:  # "badge" clears the whole showcase
            await db.set_equipped(ctx.author.id, wanted, [])
            return await ctx.reply(
                embed=h.ok(f"Cleared your {cosmetics.SLOTS[wanted].label.lower()}.")
            )
        d = cosmetics.find(cosmetic)
        if d is None:
            return await ctx.reply(
                embed=h.err(f"There's no cosmetic called **{cosmetic}**."),
                ephemeral=True,
            )
        keys = [k for k in equipped.get(d.slot, []) if k != d.key]
        if len(keys) == len(equipped.get(d.slot, [])):
            return await ctx.reply(
                embed=h.info(f"**{d.name}** isn't equipped."), ephemeral=True
            )
        await db.set_equipped(ctx.author.id, d.slot, keys)
        await ctx.reply(embed=h.ok(f"Took off **{d.name}**."))

    @profile_unequip.autocomplete("cosmetic")
    async def _unequip_ac(self, interaction: discord.Interaction, current: str):
        q = (current or "").strip().lower()
        equipped = await db.get_equipped(interaction.user.id)
        choices = []
        for slot, keys in equipped.items():
            for key in keys:
                d = cosmetics.get(key)
                if d is None or (q and q not in d.name.lower()):
                    continue
                choices.append(
                    app_commands.Choice(
                        name=f"{cosmetics.SLOTS[slot].label}: {d.name}"[:100],
                        value=d.key,
                    )
                )
        for slot_def in cosmetics.SLOTS.values():
            if slot_def.max_equipped > 1 and (not q or q in slot_def.key):
                choices.append(
                    app_commands.Choice(
                        name=f"Clear all {slot_def.label.lower()}",
                        value=slot_def.key,
                    )
                )
        return choices[:25]

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

    # ── owner-only grants (events, staff awards) ─────────────────────────────
    @profile.command(
        name="grant", description="Award a cosmetic to a member (bot owner)."
    )
    @app_commands.describe(member="Who to award", cosmetic="Which cosmetic")
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
        name="revoke", description="Remove a cosmetic from a member (bot owner)."
    )
    @app_commands.describe(member="Who to take it from", cosmetic="Which cosmetic")
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

    @profile_grant.autocomplete("cosmetic")
    @profile_revoke.autocomplete("cosmetic")
    async def _catalogue_ac(self, interaction: discord.Interaction, current: str):
        q = (current or "").strip().lower()
        return [
            app_commands.Choice(
                name=f"{cosmetics.SLOTS[d.slot].label}: {d.name}"[:100], value=d.key
            )
            for d in sorted(
                cosmetics.COSMETICS.values(), key=lambda c: (c.slot, c.sort, c.name)
            )
            if not q or q in d.name.lower() or q in d.key.lower()
        ][:25]


async def setup(bot: commands.Bot):
    await bot.add_cog(Identity(bot))
