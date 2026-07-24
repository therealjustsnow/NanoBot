"""
cogs/fishing.py
Fishing minigame — a direct NanoCoin economy tie-in.

Fishing progress is GLOBAL: rod, XP/level, bag, dex, quests, streak, and
lifetime earnings belong to the user and follow them into every server. Each
server still owns whether fishing is enabled and how long the cast cooldown
is (the cooldown itself is claimed per user, so it can't be dodged by hopping
servers).

Members cast a line on a per-guild cooldown and pull up fish across seven
rarity tiers (junk → common → uncommon → rare → epic → legendary → treasure).
Fish land in a bag and sell for NanoCoins; treasure pays coins on the spot.
Better rods — bought with coins, a deliberate money sink — shift the odds away
from junk toward the high tiers, alongside a fishing level (earned from XP per
catch) and bait bought at the /fish shop (armed via /inventory use — see
cogs/fishing/items.py). Lifetime earnings drive a fishing leaderboard (plus a
cross-server /fish global one), a per-species dex tracks collection progress,
and personal bests remember the heaviest catch. A once-a-day streak rewards
logging in to fish, a daily quest gives members something concrete to chase,
and rare server-wide events (feeding frenzy, double XP, lucky waters) spice up
a session. Coins ride the existing economy tables (db.add_coins), so /fish
earnings spend anywhere coins do: /shop, /pay, /coin gamble.

Slash command budget: one group (/fish …) — subcommands cost no extra
top-level slots.

──────────────────────────────────────────────────────
Commands
──────────────────────────────────────────────────────
  /fish                       → cast your line (same as /fish cast)
  /fish cast                  → cast your line (cooldown per server)
  /fish bag                   → what you've caught, grouped by species
  /fish sell [fish|all]       → sell one species — or everything — for coins
  /fish rod                   → your rod + the next upgrade
  /fish upgrade               → buy the next rod tier with coins
  /fish dex                   → species collection progress
  /fish stats [member]        → casts, catches, earnings, level, best catch
  /fish top [page] [scope]    → top earners (this server or every server)
  /fish global [stat] [page]  → cross-server leaderboard
  /fish buy <item> [qty]      → buy bait or an XP potion with coins
  /fish bait                  → your owned bait + what's currently armed
  /fish quest                 → today's quest and your progress
  /fish events                → active fishing events and time left
  /fish toggle                → enable/disable fishing     (Manage Server)
  /fish cooldown <seconds>    → set the cast cooldown      (Manage Server)
  /fish event <key> [minutes] → force-start a fishing event (Manage Server)
  /fish config                → show settings              (Manage Server)
"""

import logging
import random
import time
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from utils import db
from utils import helpers as h
from utils.helpers import SCOPE_CHOICES
from utils import items as item_catalog

from . import items as _fishing_items  # noqa: F401 - registers bait/consumable defs
from .constants import (
    COOLDOWN_MAX,
    COOLDOWN_MIN,
    EVENT_DURATION_RANGE,
    EVENT_LABELS,
    EVENT_POOL,
    EVENT_CHANCE,
    FISH,
    FISH_BY_RARITY,
    MAX_LUCK,
    RARITIES,
    RODS,
    XP_PER_RARITY,
)
from .helpers import (
    catch_value,
    fish_level,
    find_fish,
    fmt_weight,
    generate_quest,
    level_luck_bonus,
    level_progress,
    next_rod,
    next_streak,
    pick_event,
    pick_fish,
    pick_rarity,
    quest_label,
    quest_reward,
    rod_info,
    roll_event_duration,
    roll_weight,
    streak_bonus_coins,
    treasure_coins,
)

log = logging.getLogger("NanoBot.fishing")


class Fishing(commands.Cog):
    """Cast, collect, and sell fish for NanoCoins — one angler profile per
    user, shared across servers."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Per-user (global — the wallet and bag are too) locks serialize
        # sell/upgrade read-check-write (the economy /daily pattern) so a
        # double-send, even from two different servers at once, can't sell the
        # same fish twice or buy the same rod twice. Cast needs no lock — its cooldown
        # claim is a single atomic upsert in the DB layer.
        self._locks = h.KeyedLocks()

    def _lock(self, user_id: int):
        # Returns an async context manager; existing `async with self._lock(...)`
        # call sites are unchanged. KeyedLocks refcounts holder + waiters and
        # drops an entry when the last interested task releases, so the map no
        # longer grows for the lifetime of the process.
        return self._locks.hold(user_id)

    def _money(self, econ: dict, amount: int) -> str:
        return h.fmt_coins(amount, econ["currency_name"], econ["currency_emoji"])

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
        retry = await db.try_claim_cast(ctx.author.id, time.time(), cfg["cooldown"])
        if retry:
            return await ctx.reply(
                embed=h.warn(
                    f"Your line is still in the water. Cast again in "
                    f"**{h.fmt_duration(retry)}**.",
                    "🎣 Not Yet",
                ),
                ephemeral=True,
            )

        econ = await db.get_econ_config(ctx.guild.id)
        fisher = await db.get_fisher(ctx.author.id)
        today = int(time.time() // 86400)

        # First cast of a new UTC day: bump the login streak + a small bonus.
        streak_note = None
        if fisher["last_day"] != today:
            new_streak = next_streak(fisher["last_day"], today, fisher["streak_days"])
            if await db.try_claim_daily_streak(ctx.author.id, today, new_streak):
                bonus = streak_bonus_coins(new_streak)
                if bonus:
                    await db.add_coins(ctx.author.id, bonus)
                    await db.add_fishing_earned(ctx.author.id, bonus)
                    streak_note = (
                        f"🔥 **{new_streak}**-day streak! +{self._money(econ, bonus)}"
                    )
                else:
                    streak_note = f"🔥 **{new_streak}**-day streak!"

        # Luck: rod tier + fishing level + any armed bait/luck effect + a
        # guild-wide lucky-waters event, clamped to the safe [0, MAX_LUCK] range
        # rarity_odds expects (it also clamps internally, this is belt-and-braces).
        effects = await db.get_active_effects(ctx.author.id)
        events = await db.get_active_events(ctx.guild.id)
        generic_luck = effects.get("luck", {}).get("magnitude", 0.0)
        bait_luck = 0.0
        if "fish_bait" in effects:
            if await db.consume_effect_use(ctx.author.id, "fish_bait"):
                bait_luck = effects["fish_bait"]["magnitude"]
        xp_effect_mult = effects.get("fish_xp", {}).get("magnitude") or 1.0
        event_luck = 0.0
        value_mult = 1.0
        xp_event_mult = 1.0
        for ev in events:
            if ev["event_key"] == "lucky_waters":
                event_luck = max(event_luck, ev["magnitude"])
            elif ev["event_key"] == "frenzy":
                value_mult = max(value_mult, ev["magnitude"])
            elif ev["event_key"] == "double_xp":
                xp_event_mult = max(xp_event_mult, ev["magnitude"])

        rod_luck = rod_info(fisher["rod_level"])["luck"]
        level_bonus = level_luck_bonus(fish_level(fisher["xp"]))
        total_luck = max(
            0.0,
            min(
                MAX_LUCK,
                rod_luck + level_bonus + generic_luck + bait_luck + event_luck,
            ),
        )

        rarity = pick_rarity(random.random(), total_luck)
        key = pick_fish(rarity, random.random())
        entry = FISH[key]
        label, color = RARITIES[rarity]

        # Today's quest — regenerated deterministically, created on first read.
        qgen = generate_quest(ctx.author.id, today)
        quest = await db.get_or_create_quest(
            ctx.author.id, today, qgen["quest_key"], qgen["target"]
        )

        old_xp = fisher["xp"]

        if rarity == "treasure":
            coins = treasure_coins(key, random.random())
            coins = max(1, int(coins * value_mult))
            await db.add_coins(ctx.author.id, coins)
            await db.add_fishing_earned(ctx.author.id, coins)
            xp_gain = max(
                1, round(XP_PER_RARITY["treasure"] * xp_effect_mult * xp_event_mult)
            )
            new_xp = await db.add_fishing_xp(ctx.author.id, xp_gain)
            quest = await self._bump_quest(ctx, quest, today, kind="earn", coins=coins)
            quest_note = await self._award_quest_if_complete(ctx, today, quest)

            desc = (
                f"{label}\nYou found {self._money(econ, coins)} inside — "
                "added straight to your balance!"
            )
            desc += self._catch_extras(fish_level(old_xp), fish_level(new_xp))
            if streak_note:
                desc += f"\n{streak_note}"
            if quest_note:
                desc += f"\n{quest_note}"
            embed = h.embed(f"{entry['emoji']} {entry['name']}!", desc, color)
            await ctx.reply(embed=embed)
            await self._maybe_start_event(ctx)
            return

        weight = roll_weight(key, random.random())
        value = catch_value(key, weight)
        value = max(1, int(value * value_mult))
        await db.record_catch(
            ctx.author.id,
            key,
            weight,
            value,
            track_best=rarity != "junk",
        )
        xp_gain = max(1, round(XP_PER_RARITY[rarity] * xp_effect_mult * xp_event_mult))
        new_xp = await db.add_fishing_xp(ctx.author.id, xp_gain)
        quest = await self._bump_quest(ctx, quest, today, kind="catch", rarity=rarity)
        quest_note = await self._award_quest_if_complete(ctx, today, quest)

        if rarity == "junk":
            desc = f"{label}\nWell… it's something. Worth {self._money(econ, value)}."
        else:
            desc = (
                f"{label}\nWeight: **{fmt_weight(weight)}**\n"
                f"Worth {self._money(econ, value)} when sold."
            )
            if weight > fisher["best_weight"]:
                desc += "\n🏆 **New personal best!**"
        desc += self._catch_extras(fish_level(old_xp), fish_level(new_xp))
        if streak_note:
            desc += f"\n{streak_note}"
        if quest_note:
            desc += f"\n{quest_note}"
        embed = h.embed(f"{entry['emoji']} {entry['name']}!", desc, color)
        bag = await db.get_bag(ctx.author.id)
        embed.set_footer(
            text=f"Bag: {sum(row['qty'] for row in bag)} · Sell with /fish sell"
        )
        await ctx.reply(embed=embed)
        await self._maybe_start_event(ctx)

    def _catch_extras(self, old_level: int, new_level: int) -> str:
        """A level-up line to append to a catch embed, or "" if none happened."""
        if new_level > old_level:
            return f"\n🎉 Fishing level up! You're now level **{new_level}**."
        return ""

    async def _bump_quest(
        self,
        ctx: commands.Context,
        quest: dict,
        today: int,
        *,
        kind: str,
        rarity: Optional[str] = None,
        coins: int = 0,
    ) -> dict:
        """Advance today's quest if this event matches its type. Returns the
        (possibly updated) quest dict."""
        if quest["claimed"]:
            return quest
        quest_key = quest["quest_key"]
        amount = 0
        if kind == "catch" and quest_key == "catch_any":
            amount = 1
        elif (
            kind == "catch"
            and quest_key.startswith("catch_rarity:")
            and quest_key.split(":", 1)[1] == rarity
        ):
            amount = 1
        elif kind == "earn" and quest_key == "earn_coins":
            amount = coins
        if amount <= 0:
            return quest
        new_progress = await db.bump_quest_progress(ctx.author.id, today, amount)
        return {**quest, "progress": new_progress}

    async def _award_quest_if_complete(
        self, ctx: commands.Context, today: int, quest: dict
    ) -> Optional[str]:
        """Claim + pay out a just-completed quest once. Returns an embed note,
        or None if nothing was awarded."""
        if quest["claimed"] or quest["progress"] < quest["target"]:
            return None
        if not await db.try_claim_quest_reward(ctx.author.id, today):
            return None
        coins, xp = quest_reward(quest["quest_key"], quest["target"])
        if coins:
            await db.add_coins(ctx.author.id, coins)
            await db.add_fishing_earned(ctx.author.id, coins)
        if xp:
            await db.add_fishing_xp(ctx.author.id, xp)
        econ = await db.get_econ_config(ctx.guild.id)
        return f"✅ Daily quest complete! +{self._money(econ, coins)}, +{xp} XP"

    async def _maybe_start_event(self, ctx: commands.Context) -> None:
        """~EVENT_CHANCE odds per cast to kick off a guild fishing event, if
        none is already running (guild-scoped or global)."""
        if random.random() >= EVENT_CHANCE:
            return
        if await db.get_active_events(ctx.guild.id):
            return
        event = pick_event(random.random())
        duration = roll_event_duration(random.random(), EVENT_DURATION_RANGE)
        await db.start_event(ctx.guild.id, event["key"], event["magnitude"], duration)
        label = EVENT_LABELS.get(event["key"], event["key"])
        embed = h.embed(
            f"🌊 {label} has begun!",
            f"Something's stirring in the water — this bonus lasts "
            f"**{h.fmt_duration(duration)}**. Check `/fish events` any time.",
            RARITIES["treasure"][1],
        )
        try:
            await ctx.channel.send(embed=embed)
        except discord.HTTPException:
            log.warning("Failed to announce fishing event in guild %s", ctx.guild.id)

    # ── /fish bag ────────────────────────────────────────────────────────────
    @fish.command(name="bag", description="See the fish you're carrying.")
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def fish_bag(self, ctx: commands.Context):
        bag = await db.get_bag(ctx.author.id)
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
    @app_commands.describe(
        fish="Pick a species from your bag (leave blank to sell everything)"
    )
    async def fish_sell(self, ctx: commands.Context, *, fish: Optional[str] = None):
        key = None
        if fish is not None and fish.strip().lower() not in ("all", "everything", "*"):
            key = find_fish(fish)
            if key is None:
                return await ctx.reply(
                    embed=h.err(
                        f"I don't know a fish called **{fish}**. Check `/fish bag`, "
                        "or run `/fish sell` with no fish to sell everything."
                    ),
                    ephemeral=True,
                )
        econ = await db.get_econ_config(ctx.guild.id)
        async with self._lock(ctx.author.id):
            count, total = await db.sell_catches(ctx.author.id, key)
            if count == 0:
                what = f"any **{FISH[key]['name']}**" if key else "anything to sell"
                return await ctx.reply(
                    embed=h.warn(
                        f"You don't have {what}. Try `/fish cast`!", "🎒 Empty"
                    ),
                    ephemeral=True,
                )
            new_bal = await db.add_coins(ctx.author.id, total)
            await db.add_fishing_earned(ctx.author.id, total)

        today = int(time.time() // 86400)
        qgen = generate_quest(ctx.author.id, today)
        quest = await db.get_or_create_quest(
            ctx.author.id, today, qgen["quest_key"], qgen["target"]
        )
        quest = await self._bump_quest(ctx, quest, today, kind="earn", coins=total)
        quest_note = await self._award_quest_if_complete(ctx, today, quest)

        what = FISH[key]["name"] if key else "item"
        desc = (
            f"Sold **{count}** {what}{'' if count == 1 else 's'} for "
            f"{self._money(econ, total)}.\n"
            f"Balance: {self._money(econ, new_bal)}"
        )
        if quest_note:
            desc += f"\n{quest_note}"
        await ctx.reply(embed=h.ok(desc, "💰 Sold"))

    @fish_sell.autocomplete("fish")
    async def _fish_sell_ac(self, interaction: discord.Interaction, current: str):
        """Pick from what's actually in your bag — no typing species names."""
        q = (current or "").strip().lower()
        bag = await db.get_bag(interaction.user.id) if interaction.guild_id else []
        choices: list[app_commands.Choice[str]] = []
        total = sum(row["total_value"] for row in bag)
        if bag and (not q or q in "everything" or q in "all"):
            choices.append(
                app_commands.Choice(
                    name=f"💰 Everything in your bag — {total:,} coins", value="all"
                )
            )
        for row in bag:
            entry = FISH.get(row["fish_key"])
            if not entry:
                continue
            if (
                q
                and q not in row["fish_key"].lower()
                and q not in entry["name"].lower()
            ):
                continue
            choices.append(
                app_commands.Choice(
                    name=f"{entry['emoji']} {entry['name']} ×{row['qty']} — "
                    f"{row['total_value']:,} coins"[:100],
                    value=row["fish_key"],
                )
            )
        return choices[:25]

    # ── /fish rod ────────────────────────────────────────────────────────────
    @fish.command(name="rod", description="See your rod and the next upgrade.")
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def fish_rod(self, ctx: commands.Context):
        fisher = await db.get_fisher(ctx.author.id)
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
        async with self._lock(ctx.author.id):
            fisher = await db.get_fisher(ctx.author.id)
            nxt = next_rod(fisher["rod_level"])
            if nxt is None:
                return await ctx.reply(
                    embed=h.info("You already own the best rod there is!", "🎣 Maxed"),
                    ephemeral=True,
                )
            if not await db.try_debit_coins(ctx.author.id, nxt["price"]):
                balance = await db.get_balance(ctx.author.id)
                return await ctx.reply(
                    embed=h.err(
                        f"{nxt['emoji']} **{nxt['name']}** costs "
                        f"{self._money(econ, nxt['price'])} — you have "
                        f"{self._money(econ, balance)}."
                    ),
                    ephemeral=True,
                )
            upgraded = await db.set_rod_level(
                ctx.author.id,
                fisher["rod_level"] + 1,
                expected=fisher["rod_level"],
            )
            if not upgraded:
                # Someone (a second racing invocation) already advanced the rod
                # — hand the coins back.
                await db.add_coins(ctx.author.id, nxt["price"])
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
        counts = await db.get_species_counts(ctx.author.id)
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
        fisher = await db.get_fisher(member.id)
        econ = await db.get_econ_config(ctx.guild.id)
        rod = rod_info(fisher["rod_level"])
        rank = await db.get_fishing_rank(member.id)

        embed = h.embed(f"🎣 {member.display_name}", color=h.BLUE)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="Casts", value=f"**{fisher['casts']:,}**", inline=True)
        embed.add_field(name="Caught", value=f"**{fisher['caught']:,}**", inline=True)
        embed.add_field(
            name="Earned", value=self._money(econ, fisher["earned"]), inline=True
        )
        embed.add_field(name="Rod", value=f"{rod['emoji']} {rod['name']}", inline=True)
        embed.add_field(
            name="Global rank",
            value=f"**#{rank[0]}**" if rank else "Unranked",
            inline=True,
        )
        level, into, needed = level_progress(fisher["xp"])
        embed.add_field(
            name="Level", value=f"**{level}** ({into}/{needed} XP)", inline=True
        )
        if fisher["streak_days"]:
            embed.add_field(
                name="Streak", value=f"🔥 **{fisher['streak_days']}** days", inline=True
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
    @fish.command(name="top", description="Top-earning anglers, here or everywhere.")
    @app_commands.describe(
        page="Page number (10 per page)",
        scope="This server's members, or every server (progress is global)",
    )
    @app_commands.choices(scope=SCOPE_CHOICES)
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def fish_top(
        self, ctx: commands.Context, page: int = 1, scope: str = "server"
    ):
        econ = await db.get_econ_config(ctx.guild.id)
        per = 10
        if scope == "global":
            total = await db.count_fishers()
            pages = max(1, (total + per - 1) // per)
            page = max(1, min(page, pages))
            offset = (page - 1) * per
            rows = await db.get_fishing_leaderboard(per, offset)
        else:
            all_rows = await db.get_fishing_leaderboard_for(h.member_ids(ctx.guild))
            rows, page, pages, total = h.page_rows(
                all_rows, lambda r: (-r["earned"], r["user_id"]), page, per
            )
            offset = (page - 1) * per
        if total == 0:
            return await ctx.reply(
                embed=h.info(
                    "No one has earned anything yet. Try `/fish`!", "🎣 Anglers"
                )
            )

        medals = {1: "🥇", 2: "🥈", 3: "🥉"}
        lines = []
        for i, row in enumerate(rows):
            pos = offset + i + 1
            badge = medals.get(pos, f"`#{pos}`")
            lines.append(
                f"{badge} **{self._name_for(ctx, row['user_id'])}** — "
                f"{self._money(econ, row['earned'])} ({row['caught']:,} caught)"
            )
        title = "🎣 Top Anglers" + (" (Global)" if scope == "global" else "")
        embed = h.embed(title, "\n".join(lines), h.BLUE)
        embed.set_footer(text=f"Page {page}/{pages} · {total} anglers")
        await ctx.reply(embed=embed)

    def _name_for(self, ctx: commands.Context, user_id: int) -> str:
        """Display name for a leaderboard row — a global board lists anglers who
        may not be in this server."""
        member = ctx.guild.get_member(user_id) if ctx.guild else None
        if member:
            return member.display_name
        user = self.bot.get_user(user_id)
        return user.display_name if user else f"User {user_id}"

    # ── /fish global ─────────────────────────────────────────────────────────
    @fish.command(name="global", description="Cross-server fishing leaderboard.")
    @app_commands.describe(
        stat="Which stat to rank by", page="Page number (10 per page)"
    )
    @app_commands.choices(
        stat=[
            app_commands.Choice(name=v["label"], value=k)
            for k, v in db.GLOBAL_STATS.items()
        ]
    )
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def fish_global(
        self, ctx: commands.Context, stat: str = "earned", page: int = 1
    ):
        if stat not in db.GLOBAL_STATS:
            valid = ", ".join(db.GLOBAL_STATS)
            return await ctx.reply(
                embed=h.err(f"I don't know the stat **{stat}**. Try: {valid}."),
                ephemeral=True,
            )
        label = db.GLOBAL_STATS[stat]["label"]
        econ = await db.get_econ_config(ctx.guild.id)
        page = max(1, page)
        per = 10
        total = await db.count_global_leaderboard(stat)
        if total == 0:
            return await ctx.reply(
                embed=h.info(f"No one has any {label} yet.", "🌐 Global Leaderboard")
            )
        pages = (total + per - 1) // per
        page = min(page, pages)
        offset = (page - 1) * per
        rows = await db.get_global_leaderboard(stat, per, offset)

        medals = {1: "🥇", 2: "🥈", 3: "🥉"}
        lines = []
        for i, row in enumerate(rows):
            pos = offset + i + 1
            user = self.bot.get_user(row["user_id"])
            name = user.display_name if user else f"User {row['user_id']}"
            badge = medals.get(pos, f"`#{pos}`")
            if stat == "earned":
                display = self._money(econ, int(row["value"]))
            elif stat == "best_weight":
                display = fmt_weight(row["value"])
            else:
                display = f"{int(row['value']):,}"
            lines.append(f"{badge} **{name}** — {display}")
        embed = h.embed(f"🌐 Global {label}", "\n".join(lines), h.BLUE)
        embed.set_footer(
            text=f"Page {page}/{pages} · {total} anglers · every server combined"
        )
        await ctx.reply(embed=embed)

    # ── /fish buy ────────────────────────────────────────────────────────────
    @fish.command(name="buy", description="Buy bait or fishing consumables with coins.")
    @app_commands.describe(
        item="Pick an item from the bait shop list",
        qty="How many to buy (default 1)",
    )
    async def fish_buy(self, ctx: commands.Context, item: str, qty: int = 1):
        d = item_catalog.find(item)
        if d is None or not d.key.startswith(("bait_", "fish_")) or d.price <= 0:
            stock = ", ".join(
                f"{s.name} ({s.price:,})" for s in self._bait_shop_items()
            )
            return await ctx.reply(
                embed=h.err(
                    f"**{item}** isn't sold at the bait shop. In stock: {stock}."
                ),
                ephemeral=True,
            )
        qty = max(1, min(qty, 100))
        total = d.price * qty
        econ = await db.get_econ_config(ctx.guild.id)
        async with self._lock(ctx.author.id):
            if not await db.try_debit_coins(ctx.author.id, total):
                balance = await db.get_balance(ctx.author.id)
                return await ctx.reply(
                    embed=h.err(
                        f"{d.emoji} **{d.name}** ×{qty} costs "
                        f"{self._money(econ, total)} — you have "
                        f"{self._money(econ, balance)}."
                    ),
                    ephemeral=True,
                )
            new_qty = await db.add_item(ctx.author.id, d.key, qty)
        await ctx.reply(
            embed=h.ok(
                f"Bought **{qty}** {d.emoji} **{d.name}** for "
                f"{self._money(econ, total)}.\nYou now have **{new_qty}**. Arm it "
                f"with `/inventory use {d.name}`.",
                "🛒 Bought",
            )
        )

    def _bait_shop_items(self) -> list:
        """Everything /fish buy sells, cheapest first."""
        return sorted(
            (
                d
                for d in item_catalog.ITEMS.values()
                if d.key.startswith(("bait_", "fish_")) and d.price > 0
            ),
            key=lambda d: d.price,
        )

    @fish_buy.autocomplete("item")
    async def _fish_buy_ac(self, interaction: discord.Interaction, current: str):
        """Bait shop as a tap-to-pick list, priced and marked by affordability."""
        q = (current or "").strip().lower()
        balance = (
            await db.get_balance(interaction.user.id) if interaction.guild_id else 0
        )
        choices: list[app_commands.Choice[str]] = []
        for d in self._bait_shop_items():
            if q and q not in d.key.lower() and q not in d.name.lower():
                continue
            mark = "✅" if balance >= d.price else "🔒"
            choices.append(
                app_commands.Choice(
                    name=f"{mark} {d.emoji} {d.name} — {d.price:,} coins"[:100],
                    value=d.key,
                )
            )
        return choices[:25]

    # ── /fish bait ───────────────────────────────────────────────────────────
    @fish.command(
        name="bait", description="Your owned bait/consumables and what's armed."
    )
    async def fish_bait(self, ctx: commands.Context):
        inv = await db.get_inventory(ctx.author.id)
        owned_lines = [
            f"{d.emoji} **{d.name}** ×{row['qty']}"
            for row in inv
            if (d := item_catalog.get(row["item_key"]))
            and d.key.startswith(("bait_", "fish_"))
        ]
        effects = await db.get_active_effects(ctx.author.id)
        armed_lines = []
        if "fish_bait" in effects:
            eff = effects["fish_bait"]
            armed_lines.append(
                f"🪱 Bait armed: +{eff['magnitude']:.0%} luck, "
                f"**{eff['uses_left']}** cast(s) left"
            )
        if "fish_xp" in effects:
            eff = effects["fish_xp"]
            remaining = max(0, int(eff["expires_at"] - time.time()))
            armed_lines.append(
                f"⭐ XP boost armed: **{eff['magnitude']:g}x** XP, "
                f"{h.fmt_duration(remaining)} left"
            )
        if not owned_lines and not armed_lines:
            return await ctx.reply(
                embed=h.info(
                    "You don't have any bait yet. Buy some with `/fish buy`!",
                    "🪱 Bait",
                )
            )
        embed = h.embed("🪱 Bait", color=h.BLUE)
        embed.add_field(
            name="Owned",
            value="\n".join(owned_lines) if owned_lines else "Nothing owned yet.",
            inline=False,
        )
        embed.add_field(
            name="Armed",
            value=(
                "\n".join(armed_lines)
                if armed_lines
                else "Nothing armed. Use `/inventory use <bait>` before casting."
            ),
            inline=False,
        )
        await ctx.reply(embed=embed)

    # ── /fish quest ──────────────────────────────────────────────────────────
    @fish.command(name="quest", description="Today's fishing quest and your progress.")
    async def fish_quest(self, ctx: commands.Context):
        today = int(time.time() // 86400)
        qgen = generate_quest(ctx.author.id, today)
        quest = await db.get_or_create_quest(
            ctx.author.id, today, qgen["quest_key"], qgen["target"]
        )
        label = quest_label(quest["quest_key"], quest["target"])
        econ = await db.get_econ_config(ctx.guild.id)
        coins, xp = quest_reward(quest["quest_key"], quest["target"])
        if quest["claimed"]:
            desc = (
                f"**{label}**\n✅ Complete! You claimed "
                f"{self._money(econ, coins)} + **{xp}** XP today."
            )
        else:
            desc = (
                f"**{label}**\nProgress: **{quest['progress']}/{quest['target']}**\n"
                f"Reward: {self._money(econ, coins)} + **{xp}** XP"
            )
        await ctx.reply(embed=h.embed("🗺️ Today's Quest", desc, h.BLUE))

    # ── /fish events ─────────────────────────────────────────────────────────
    @fish.command(name="events", description="Active fishing events and time left.")
    async def fish_events(self, ctx: commands.Context):
        events = await db.get_active_events(ctx.guild.id)
        if not events:
            return await ctx.reply(
                embed=h.info("No fishing events running right now.", "🌊 Events")
            )
        now = time.time()
        lines = []
        for ev in events:
            remaining = max(0, int(ev["ends_at"] - now))
            label = EVENT_LABELS.get(ev["event_key"], ev["event_key"])
            lines.append(f"{label} — **{h.fmt_duration(remaining)}** left")
        embed = h.embed("🌊 Active Events", "\n".join(lines), RARITIES["treasure"][1])
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

    @fish.command(name="event", description="Force-start a fishing event.")
    @app_commands.describe(
        key="Which event to start", minutes="Duration in minutes (default 15)"
    )
    @app_commands.choices(
        key=[
            app_commands.Choice(
                name=EVENT_LABELS.get(e["key"], e["key"]), value=e["key"]
            )
            for e in EVENT_POOL
        ]
    )
    @commands.has_permissions(manage_guild=True)
    async def fish_event(self, ctx: commands.Context, key: str, minutes: int = 15):
        key = key.strip().lower()
        event_def = next((e for e in EVENT_POOL if e["key"] == key), None)
        if event_def is None:
            valid = ", ".join(e["key"] for e in EVENT_POOL)
            return await ctx.reply(
                embed=h.err(f"Unknown event **{key}**. Choose from: {valid}."),
                ephemeral=True,
            )
        minutes = max(1, min(180, minutes))
        await db.start_event(
            ctx.guild.id, event_def["key"], event_def["magnitude"], minutes * 60
        )
        label = EVENT_LABELS.get(event_def["key"], event_def["key"])
        await ctx.reply(embed=h.ok(f"{label} started for **{minutes}** minute(s)."))

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
