"""
cogs/fishing.py
Fishing minigame — a direct NanoCoin economy tie-in.

Members cast a line on a per-guild cooldown and pull up fish across seven
rarity tiers (junk → common → uncommon → rare → epic → legendary → treasure).
Fish land in a bag and sell for NanoCoins; treasure pays coins on the spot.
Better rods — bought with coins, a deliberate money sink — shift the odds away
from junk toward the high tiers. Lifetime earnings drive a fishing
leaderboard, a per-species dex tracks collection progress, and personal bests
remember the heaviest catch. Coins ride the existing economy tables
(db.add_coins), so /fish earnings spend anywhere coins do: /shop, /pay,
/coin gamble.

Slash command budget: one group (/fish …) — subcommands cost no extra
top-level slots.

──────────────────────────────────────────────────────
Commands
──────────────────────────────────────────────────────
  /fish                       → cast your line (same as /fish cast)
  /fish cast                  → cast your line (cooldown per server)
  /fish bag                   → what you've caught, grouped by species
  /fish sell [fish]           → sell one species — or everything — for coins
  /fish rod                   → your rod + the next upgrade
  /fish upgrade               → buy the next rod tier with coins
  /fish dex                   → species collection progress
  /fish stats [member]        → casts, catches, earnings, best catch
  /fish top [page]            → top earners leaderboard
  /fish toggle                → enable/disable fishing     (Manage Server)
  /fish cooldown <seconds>    → set the cast cooldown      (Manage Server)
  /fish config                → show settings              (Manage Server)
"""

import asyncio
import logging
import random
import time
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from utils import db
from utils import helpers as h

from .constants import (
    COOLDOWN_MAX,
    COOLDOWN_MIN,
    FISH,
    FISH_BY_RARITY,
    RARITIES,
    RODS,
)
from .helpers import (
    catch_value,
    find_fish,
    fmt_weight,
    next_rod,
    pick_fish,
    pick_rarity,
    rod_info,
    roll_weight,
    treasure_coins,
)

log = logging.getLogger("NanoBot.fishing")


class Fishing(commands.Cog):
    """Cast, collect, and sell fish for NanoCoins — per server."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Per-(guild, user) locks serialize sell/upgrade read-check-write (the
        # economy /daily pattern) so a double-send can't sell the same fish
        # twice or buy the same rod twice. Cast needs no lock — its cooldown
        # claim is a single atomic upsert in the DB layer.
        self._locks: dict[tuple[int, int], asyncio.Lock] = {}

    def _lock(self, guild_id: int, user_id: int) -> asyncio.Lock:
        key = (guild_id, user_id)
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        return lock

    def _money(self, econ: dict, amount: int) -> str:
        name = econ["currency_name"]
        label = name if abs(amount) == 1 else f"{name}s"
        return f"{econ['currency_emoji']} **{amount:,}** {label}"

    # ══════════════════════════════════════════════════════════════════════════
    #  /fish  group
    # ══════════════════════════════════════════════════════════════════════════
    @commands.hybrid_group(
        name="fish",
        description="Fishing minigame: catch fish and sell them for coins.",
        invoke_without_command=True,
        extras={
            "category": "🪙 Economy",
            "short": "Catch and sell fish for coins",
            "usage": "fish [subcommand]",
            "desc": "Cast a line, collect fish across seven rarity tiers, and sell "
            "them for coins. Buy better rods to improve your odds, climb the "
            "earnings leaderboard, and complete the species dex. Admins can "
            "toggle fishing and tune the cast cooldown.",
            "args": [],
            "perms": "Admin subcommands require Manage Server",
            "example": "{prefix}fish\n{prefix}fish sell\n{prefix}fish rod",
        },
    )
    @commands.guild_only()
    async def fish(self, ctx: commands.Context):
        if ctx.invoked_subcommand is None:
            await self._do_cast(ctx)

    # ── /fish cast ───────────────────────────────────────────────────────────
    @fish.command(name="cast", description="Cast your line and see what bites.")
    async def fish_cast(self, ctx: commands.Context):
        await self._do_cast(ctx)

    async def _do_cast(self, ctx: commands.Context):
        cfg = await db.get_fishing_config(ctx.guild.id)
        if not cfg["enabled"]:
            return await ctx.reply(
                embed=h.err("Fishing is disabled on this server."), ephemeral=True
            )
        retry = await db.try_claim_cast(
            ctx.guild.id, ctx.author.id, time.time(), cfg["cooldown"]
        )
        if retry:
            return await ctx.reply(
                embed=h.warn(
                    f"Your line is still in the water. Cast again in "
                    f"**{h.fmt_duration(retry)}**.",
                    "🎣 Not Yet",
                ),
                ephemeral=True,
            )

        fisher = await db.get_fisher(ctx.guild.id, ctx.author.id)
        luck = rod_info(fisher["rod_level"])["luck"]
        rarity = pick_rarity(random.random(), luck)
        key = pick_fish(rarity, random.random())
        entry = FISH[key]
        label, color = RARITIES[rarity]
        econ = await db.get_econ_config(ctx.guild.id)

        if rarity == "treasure":
            coins = treasure_coins(key, random.random())
            await db.add_coins(ctx.guild.id, ctx.author.id, coins)
            await db.add_fishing_earned(ctx.guild.id, ctx.author.id, coins)
            embed = h.embed(
                f"{entry['emoji']} {entry['name']}!",
                f"{label}\nYou found {self._money(econ, coins)} inside — "
                "added straight to your balance!",
                color,
            )
            return await ctx.reply(embed=embed)

        weight = roll_weight(key, random.random())
        value = catch_value(key, weight)
        await db.record_catch(
            ctx.guild.id,
            ctx.author.id,
            key,
            weight,
            value,
            track_best=rarity != "junk",
        )
        if rarity == "junk":
            desc = f"{label}\nWell… it's something. Worth {self._money(econ, value)}."
        else:
            desc = (
                f"{label}\nWeight: **{fmt_weight(weight)}**\n"
                f"Worth {self._money(econ, value)} when sold."
            )
            if weight > fisher["best_weight"]:
                desc += "\n🏆 **New personal best!**"
        embed = h.embed(f"{entry['emoji']} {entry['name']}!", desc, color)
        bag = await db.get_bag(ctx.guild.id, ctx.author.id)
        embed.set_footer(
            text=f"Bag: {sum(row['qty'] for row in bag)} · Sell with /fish sell"
        )
        await ctx.reply(embed=embed)

    # ── /fish bag ────────────────────────────────────────────────────────────
    @fish.command(name="bag", description="See the fish you're carrying.")
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def fish_bag(self, ctx: commands.Context):
        bag = await db.get_bag(ctx.guild.id, ctx.author.id)
        econ = await db.get_econ_config(ctx.guild.id)
        if not bag:
            return await ctx.reply(
                embed=h.info("Your bag is empty. Try `/fish cast`!", "🎒 Bag")
            )
        lines = []
        for row in bag:
            entry = FISH.get(row["fish_key"])
            if not entry:
                continue
            lines.append(
                f"{entry['emoji']} **{entry['name']}** ×{row['qty']} — "
                f"{self._money(econ, row['total_value'])}"
            )
        total = sum(row["total_value"] for row in bag)
        embed = h.embed("🎒 Bag", "\n".join(lines), h.BLUE)
        embed.set_footer(text="Sell everything with /fish sell")
        embed.add_field(name="Total value", value=self._money(econ, total))
        await ctx.reply(embed=embed)

    # ── /fish sell ───────────────────────────────────────────────────────────
    @fish.command(
        name="sell",
        description="Sell your catch for coins — one species, or everything.",
    )
    @app_commands.describe(fish="Which fish to sell (leave blank to sell everything)")
    async def fish_sell(self, ctx: commands.Context, *, fish: Optional[str] = None):
        key = None
        if fish is not None:
            key = find_fish(fish)
            if key is None:
                return await ctx.reply(
                    embed=h.err(f"I don't know a fish called **{fish}**."),
                    ephemeral=True,
                )
        econ = await db.get_econ_config(ctx.guild.id)
        async with self._lock(ctx.guild.id, ctx.author.id):
            count, total = await db.sell_catches(ctx.guild.id, ctx.author.id, key)
            if count == 0:
                what = f"any **{FISH[key]['name']}**" if key else "anything to sell"
                return await ctx.reply(
                    embed=h.warn(
                        f"You don't have {what}. Try `/fish cast`!", "🎒 Empty"
                    ),
                    ephemeral=True,
                )
            new_bal = await db.add_coins(ctx.guild.id, ctx.author.id, total)
            await db.add_fishing_earned(ctx.guild.id, ctx.author.id, total)
        what = FISH[key]["name"] if key else "item"
        await ctx.reply(
            embed=h.ok(
                f"Sold **{count}** {what}{'' if count == 1 else 's'} for "
                f"{self._money(econ, total)}.\n"
                f"Balance: {self._money(econ, new_bal)}",
                "💰 Sold",
            )
        )

    # ── /fish rod ────────────────────────────────────────────────────────────
    @fish.command(name="rod", description="See your rod and the next upgrade.")
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def fish_rod(self, ctx: commands.Context):
        fisher = await db.get_fisher(ctx.guild.id, ctx.author.id)
        econ = await db.get_econ_config(ctx.guild.id)
        rod = rod_info(fisher["rod_level"])
        nxt = next_rod(fisher["rod_level"])
        desc = (
            f"{rod['emoji']} **{rod['name']}** "
            f"(tier {fisher['rod_level'] + 1}/{len(RODS)})\n"
            f"Luck: **{rod['luck']:.0%}** less junk, better rare odds"
        )
        if nxt:
            desc += (
                f"\n\nNext: {nxt['emoji']} **{nxt['name']}** — "
                f"{self._money(econ, nxt['price'])}\n"
                "Buy it with `/fish upgrade`."
            )
        else:
            desc += "\n\nYou own the best rod there is. 🎉"
        await ctx.reply(embed=h.embed("🎣 Your Rod", desc, h.BLUE))

    # ── /fish upgrade ────────────────────────────────────────────────────────
    @fish.command(name="upgrade", description="Buy the next rod tier with coins.")
    async def fish_upgrade(self, ctx: commands.Context):
        econ = await db.get_econ_config(ctx.guild.id)
        async with self._lock(ctx.guild.id, ctx.author.id):
            fisher = await db.get_fisher(ctx.guild.id, ctx.author.id)
            nxt = next_rod(fisher["rod_level"])
            if nxt is None:
                return await ctx.reply(
                    embed=h.info("You already own the best rod there is!", "🎣 Maxed"),
                    ephemeral=True,
                )
            if not await db.try_debit_coins(ctx.guild.id, ctx.author.id, nxt["price"]):
                balance = await db.get_balance(ctx.guild.id, ctx.author.id)
                return await ctx.reply(
                    embed=h.err(
                        f"{nxt['emoji']} **{nxt['name']}** costs "
                        f"{self._money(econ, nxt['price'])} — you have "
                        f"{self._money(econ, balance)}."
                    ),
                    ephemeral=True,
                )
            upgraded = await db.set_rod_level(
                ctx.guild.id,
                ctx.author.id,
                fisher["rod_level"] + 1,
                expected=fisher["rod_level"],
            )
            if not upgraded:
                # Someone (a second racing invocation) already advanced the rod
                # — hand the coins back.
                await db.add_coins(ctx.guild.id, ctx.author.id, nxt["price"])
                return await ctx.reply(
                    embed=h.warn("That upgrade already went through.", "🎣 Rod"),
                    ephemeral=True,
                )
        await ctx.reply(
            embed=h.ok(
                f"You bought the {nxt['emoji']} **{nxt['name']}** for "
                f"{self._money(econ, nxt['price'])}!\n"
                f"Luck is now **{nxt['luck']:.0%}** — fewer boots, more trophies.",
                "🎣 Upgraded",
            )
        )

    # ── /fish dex ────────────────────────────────────────────────────────────
    @fish.command(name="dex", description="Your species collection progress.")
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def fish_dex(self, ctx: commands.Context):
        counts = await db.get_species_counts(ctx.guild.id, ctx.author.id)
        # Treasure never enters the bag/species table, so it isn't collectible.
        collectible = [r for r, _ in RARITIES.items() if r != "treasure"]
        total = sum(len(FISH_BY_RARITY[r]) for r in collectible)
        found = sum(1 for k in counts if FISH[k]["rarity"] != "treasure")
        embed = h.embed(
            "📖 Fish Dex",
            f"Discovered **{found}/{total}** species.",
            h.BLUE,
        )
        for rarity in collectible:
            label, _color = RARITIES[rarity]
            keys = FISH_BY_RARITY[rarity]
            caught = [k for k in keys if k in counts]
            if not caught:
                value = f"Nothing yet (0/{len(keys)})"
            else:
                value = ", ".join(
                    f"{FISH[k]['emoji']} {FISH[k]['name']} ×{counts[k]}" for k in caught
                )
                value = f"({len(caught)}/{len(keys)}) {value}"
            embed.add_field(name=label, value=value[:1024], inline=False)
        await ctx.reply(embed=embed)

    # ── /fish stats ──────────────────────────────────────────────────────────
    @fish.command(name="stats", description="Fishing stats for you or another member.")
    @app_commands.describe(member="Whose stats to show (defaults to you)")
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def fish_stats(
        self, ctx: commands.Context, member: Optional[discord.Member] = None
    ):
        member = member or ctx.author
        if member.bot:
            return await ctx.reply(embed=h.err("Bots don't fish."), ephemeral=True)
        fisher = await db.get_fisher(ctx.guild.id, member.id)
        econ = await db.get_econ_config(ctx.guild.id)
        rod = rod_info(fisher["rod_level"])
        rank = await db.get_fishing_rank(ctx.guild.id, member.id)

        embed = h.embed(f"🎣 {member.display_name}", color=h.BLUE)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="Casts", value=f"**{fisher['casts']:,}**", inline=True)
        embed.add_field(name="Caught", value=f"**{fisher['caught']:,}**", inline=True)
        embed.add_field(
            name="Earned", value=self._money(econ, fisher["earned"]), inline=True
        )
        embed.add_field(name="Rod", value=f"{rod['emoji']} {rod['name']}", inline=True)
        embed.add_field(
            name="Rank", value=f"**#{rank[0]}**" if rank else "Unranked", inline=True
        )
        if fisher["best_key"] and fisher["best_key"] in FISH:
            best = FISH[fisher["best_key"]]
            embed.add_field(
                name="Best catch",
                value=f"{best['emoji']} {best['name']} — "
                f"**{fmt_weight(fisher['best_weight'])}**",
                inline=True,
            )
        await ctx.reply(embed=embed)

    # ── /fish top ────────────────────────────────────────────────────────────
    @fish.command(name="top", description="The server's top-earning anglers.")
    @app_commands.describe(page="Page number (10 per page)")
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def fish_top(self, ctx: commands.Context, page: int = 1):
        econ = await db.get_econ_config(ctx.guild.id)
        page = max(1, page)
        per = 10
        total = await db.count_fishers(ctx.guild.id)
        if total == 0:
            return await ctx.reply(
                embed=h.info(
                    "No one has earned anything yet. Try `/fish`!", "🎣 Anglers"
                )
            )
        pages = (total + per - 1) // per
        page = min(page, pages)
        offset = (page - 1) * per
        rows = await db.get_fishing_leaderboard(ctx.guild.id, per, offset)

        medals = {1: "🥇", 2: "🥈", 3: "🥉"}
        lines = []
        for i, row in enumerate(rows):
            pos = offset + i + 1
            member = ctx.guild.get_member(row["user_id"])
            name = member.display_name if member else f"User {row['user_id']}"
            badge = medals.get(pos, f"`#{pos}`")
            lines.append(
                f"{badge} **{name}** — {self._money(econ, row['earned'])} "
                f"({row['caught']:,} caught)"
            )
        embed = h.embed("🎣 Top Anglers", "\n".join(lines), h.BLUE)
        embed.set_footer(text=f"Page {page}/{pages} · {total} anglers")
        await ctx.reply(embed=embed)

    # ══════════════════════════════════════════════════════════════════════════
    #  Admin subcommands (Manage Server)
    # ══════════════════════════════════════════════════════════════════════════
    @fish.command(name="toggle", description="Turn fishing on or off for this server.")
    @commands.has_permissions(manage_guild=True)
    async def fish_toggle(self, ctx: commands.Context):
        cfg = await db.get_fishing_config(ctx.guild.id)
        enabled = not cfg["enabled"]
        await db.set_fishing_config(ctx.guild.id, enabled=enabled)
        if enabled:
            await ctx.reply(embed=h.ok("Fishing is now **enabled**. 🎣"))
        else:
            await ctx.reply(embed=h.ok("Fishing is now **disabled**."))

    @fish.command(name="cooldown", description="Set how many seconds between casts.")
    @app_commands.describe(
        seconds=f"Seconds between casts ({COOLDOWN_MIN}–{COOLDOWN_MAX})"
    )
    @commands.has_permissions(manage_guild=True)
    async def fish_cooldown(self, ctx: commands.Context, seconds: int):
        if not COOLDOWN_MIN <= seconds <= COOLDOWN_MAX:
            return await ctx.reply(
                embed=h.err(
                    f"Cooldown must be between {COOLDOWN_MIN} and "
                    f"{COOLDOWN_MAX} seconds."
                ),
                ephemeral=True,
            )
        await db.set_fishing_config(ctx.guild.id, cooldown=seconds)
        await ctx.reply(
            embed=h.ok(f"Cast cooldown set to **{h.fmt_duration(seconds)}**.")
        )

    @fish.command(name="config", description="Show the fishing settings.")
    @commands.has_permissions(manage_guild=True)
    async def fish_config(self, ctx: commands.Context):
        cfg = await db.get_fishing_config(ctx.guild.id)
        embed = h.embed("🎣 Fishing Settings", color=h.BLUE)
        embed.add_field(
            name="Enabled", value="✅ Yes" if cfg["enabled"] else "❌ No", inline=True
        )
        embed.add_field(
            name="Cast cooldown", value=h.fmt_duration(cfg["cooldown"]), inline=True
        )
        embed.add_field(name="Species", value=str(len(FISH)), inline=True)
        embed.add_field(
            name="Rod tiers",
            value=" → ".join(f"{r['emoji']} {r['name']}" for r in RODS),
            inline=False,
        )
        await ctx.reply(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(Fishing(bot))
