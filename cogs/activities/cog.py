"""
cogs/activities.py
Economy activities — five distinct risk/reward profiles that pay out NanoCoins
(or ore/loot items members can sell) beyond /daily and fishing.

  /work    — SAFE. Steady, low-risk pay with a 10-step career ladder: the more
             shifts you rack up, the higher your title climbs, and each
             promotion adds a small pay bonus. No downside.
  /mine    — a dig every cooldown window. Yields ore items (stone → coal →
             iron → gold → diamond) by rarity roll into your inventory, with
             an occasional cave-in (no yield) and a rare bonus treasure key.
             A coin-priced pickaxe ladder shifts the odds toward rarer ore.
  /hunt    — MEDIUM risk. Pelts and meat with a rare golden antler trophy, but
             a chance of getting injured (a small coin fine) — and a small
             chance of finding a padlock that blocks /rob for a day.
  /explore — LONG SHOT. High-variance outcomes from nothing at all to a big
             coin find, plus treasure keys/chests and lucky charms.
  /rob     — PVP RISK. Try to steal a cut of another member's coins. Guarded
             by minimum balances and a rob-shield item; failure costs a fine.

Coins ride the existing economy tables (db.add_coins et al.), and loot rides
the shared inventory (utils/db/items.py), so earnings from any activity spend
anywhere coins/items do (/shop, /pay, /coin gamble, /inventory sell).

Slash command budget: five flat commands (/work, /hunt, /explore, /rob) plus
one group (/mine …) and an admin group (/activities …) whose subcommands cost
no extra top-level slots.

──────────────────────────────────────────────────────
Commands
──────────────────────────────────────────────────────
  /work                          → work a shift for coins (safe)
  /mine                          → dig for ore                (same as /mine dig)
  /mine dig                      → dig for ore
  /mine upgrade                  → buy the next pickaxe tier with coins
  /mine stats                    → your pickaxe + dig stats
  /hunt                          → hunt for pelts/meat/trophies (medium risk)
  /explore                       → explore for a long-shot reward
  /rob <member>                  → try to steal a cut of a member's coins
  /activities toggle <activity>  → enable/disable an activity   (Manage Server)
  /activities cooldown <a> <s>   → set an activity's cooldown    (Manage Server)
  /activities config             → show settings                (Manage Server)
"""

import asyncio
import logging
import random
import time

import discord
from discord import app_commands
from discord.ext import commands

from utils import db
from utils import helpers as h
from utils import items as item_catalogue

from . import items as _register_items  # noqa: F401 - side-effect: registers item defs
from .constants import (
    ACTIVITY_COOLDOWN_BOUNDS,
    ACTIVITY_NAMES,
    EXPLORE_COINS_BIG,
    EXPLORE_COINS_SMALL,
    EXPLORE_FLAVOR,
    HUNT_CATCHES,
    ORES,
    PICKAXES,
    ROB_FINE,
    ROB_MIN_ROBBER_BALANCE,
    ROB_MIN_TARGET_BALANCE,
)
from .helpers import (
    career_info,
    hunt_injury_fine,
    next_career,
    next_pickaxe,
    pick_explore_outcome,
    pick_hunt_catch,
    pick_ore,
    pick_work_scene,
    pickaxe_info,
    rob_steal_amount,
    rob_success,
    roll_cave_in,
    roll_coin_amount,
    roll_hunt_injury,
    roll_hunt_padlock,
    roll_mine_treasure_key,
    roll_work_pay,
)

log = logging.getLogger("NanoBot.activities")


class Activities(commands.Cog):
    """Work, mine, hunt, explore, and rob for NanoCoins — per server."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Per-(guild, user) locks serialize /mine upgrade's read-check-write
        # (the /daily pattern), mirroring cogs.fishing.Fishing._locks.
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
    #  /work — flat, safe
    # ══════════════════════════════════════════════════════════════════════════
    @commands.hybrid_command(
        name="work",
        description="Work a shift for coins — safe, steady income.",
        extras={
            "category": "🪙 Economy",
            "short": "Work a shift for coins",
            "usage": "work",
            "desc": "Punch the clock for a low-risk paycheck. Racking up shifts "
            "climbs a 10-step career ladder — every promotion pays a little more.",
            "args": [],
            "perms": "None",
            "example": "{prefix}work",
        },
    )
    @commands.guild_only()
    async def work(self, ctx: commands.Context):
        cfg = await db.get_activities_config(ctx.guild.id)
        if not cfg["work_enabled"]:
            return await ctx.reply(
                embed=h.err("Working is disabled on this server."), ephemeral=True
            )
        before = await db.get_activity_stats(ctx.guild.id, ctx.author.id)
        retry = await db.try_claim_activity(
            ctx.guild.id, ctx.author.id, "work", time.time(), cfg["work_cooldown"]
        )
        if retry:
            return await ctx.reply(
                embed=h.warn(
                    f"You're still on shift. Try again in **{h.fmt_duration(retry)}**.",
                    "💼 Not Yet",
                ),
                ephemeral=True,
            )

        stats = await db.get_activity_stats(ctx.guild.id, ctx.author.id)
        info = career_info(stats["work_shifts"])
        old_info = career_info(before["work_shifts"])
        pay = roll_work_pay(random.random(), info["bonus"])
        scene = pick_work_scene(random.random())
        econ = await db.get_econ_config(ctx.guild.id)
        new_bal = await db.add_coins(ctx.guild.id, ctx.author.id, pay)

        desc = f"{scene}\nYou earned {self._money(econ, pay)}.\nBalance: {self._money(econ, new_bal)}"
        if info["tier"] > old_info["tier"]:
            desc += f"\n\n🎉 **Promoted!** You're now a {info['title']}."
        embed = h.ok(desc, f"💼 {info['title']}")
        nxt = next_career(stats["work_shifts"])
        if nxt:
            embed.set_footer(
                text=f"Next promotion at {nxt['shifts']:,} shifts ({stats['work_shifts']:,} so far)"
            )
        await ctx.reply(embed=embed)

    # ══════════════════════════════════════════════════════════════════════════
    #  /mine  group
    # ══════════════════════════════════════════════════════════════════════════
    @commands.hybrid_group(
        name="mine",
        description="Mining minigame: dig for ore and sell it for coins.",
        invoke_without_command=True,
        extras={
            "category": "🪙 Economy",
            "short": "Dig for ore",
            "usage": "mine [subcommand]",
            "desc": "Dig for ore — stone, coal, iron, gold, and rare diamond — sold "
            "via /inventory sell. Buy better pickaxes to improve your odds. "
            "There's a small chance of a cave-in with no yield.",
            "args": [],
            "perms": "Admin subcommands live under /activities",
            "example": "{prefix}mine\n{prefix}mine upgrade",
        },
    )
    @commands.guild_only()
    async def mine(self, ctx: commands.Context):
        if ctx.invoked_subcommand is None:
            await self._do_dig(ctx)

    @mine.command(name="dig", description="Dig for ore.")
    async def mine_dig(self, ctx: commands.Context):
        await self._do_dig(ctx)

    async def _do_dig(self, ctx: commands.Context):
        cfg = await db.get_activities_config(ctx.guild.id)
        if not cfg["mine_enabled"]:
            return await ctx.reply(
                embed=h.err("Mining is disabled on this server."), ephemeral=True
            )
        retry = await db.try_claim_activity(
            ctx.guild.id, ctx.author.id, "mine", time.time(), cfg["mine_cooldown"]
        )
        if retry:
            return await ctx.reply(
                embed=h.warn(
                    f"Your pickaxe needs a rest. Dig again in **{h.fmt_duration(retry)}**.",
                    "⛏️ Not Yet",
                ),
                ephemeral=True,
            )

        if roll_cave_in(random.random()):
            return await ctx.reply(
                embed=h.warn(
                    "The tunnel collapses behind you — you scramble out empty-handed.",
                    "⛏️ Cave-In",
                )
            )

        stats = await db.get_activity_stats(ctx.guild.id, ctx.author.id)
        pickaxe = pickaxe_info(stats["pickaxe_level"])
        ore_key = pick_ore(random.random(), pickaxe["luck"])
        ore = ORES[ore_key]
        await db.add_item(ctx.guild.id, ctx.author.id, ore_key, 1)

        desc = f"You dig up {ore['emoji']} **{ore['name']}**!"
        if roll_mine_treasure_key(random.random()):
            await db.add_item(ctx.guild.id, ctx.author.id, "treasure_key", 1)
            desc += f"\n{item_catalogue.display('treasure_key')} You also found a treasure key!"
        desc += "\n\nSell it with `/inventory sell`."
        await ctx.reply(embed=h.ok(desc, "⛏️ Dig"))

    # ── /mine upgrade ────────────────────────────────────────────────────────
    @mine.command(name="upgrade", description="Buy the next pickaxe tier with coins.")
    async def mine_upgrade(self, ctx: commands.Context):
        econ = await db.get_econ_config(ctx.guild.id)
        async with self._lock(ctx.guild.id, ctx.author.id):
            stats = await db.get_activity_stats(ctx.guild.id, ctx.author.id)
            nxt = next_pickaxe(stats["pickaxe_level"])
            if nxt is None:
                return await ctx.reply(
                    embed=h.info(
                        "You already own the best pickaxe there is!", "⛏️ Maxed"
                    ),
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
            upgraded = await db.set_pickaxe_level(
                ctx.guild.id,
                ctx.author.id,
                stats["pickaxe_level"] + 1,
                expected=stats["pickaxe_level"],
            )
            if not upgraded:
                # A racing second upgrade already went through — refund.
                await db.add_coins(ctx.guild.id, ctx.author.id, nxt["price"])
                return await ctx.reply(
                    embed=h.warn("That upgrade already went through.", "⛏️ Pickaxe"),
                    ephemeral=True,
                )
        await ctx.reply(
            embed=h.ok(
                f"You bought the {nxt['emoji']} **{nxt['name']}** for "
                f"{self._money(econ, nxt['price'])}!\n"
                f"Luck is now **{nxt['luck']:.0%}** — fewer rocks, more diamonds.",
                "⛏️ Upgraded",
            )
        )

    # ── /mine stats ──────────────────────────────────────────────────────────
    @mine.command(name="stats", description="Your pickaxe and dig stats.")
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def mine_stats(self, ctx: commands.Context):
        stats = await db.get_activity_stats(ctx.guild.id, ctx.author.id)
        pickaxe = pickaxe_info(stats["pickaxe_level"])
        nxt = next_pickaxe(stats["pickaxe_level"])
        desc = (
            f"{pickaxe['emoji']} **{pickaxe['name']}** "
            f"(tier {stats['pickaxe_level'] + 1}/{len(PICKAXES)})\n"
            f"Digs: **{stats['mine_count']:,}**"
        )
        if nxt:
            desc += f"\n\nNext: {nxt['emoji']} **{nxt['name']}** — buy with `/mine upgrade`."
        else:
            desc += "\n\nYou own the best pickaxe there is. 🎉"
        await ctx.reply(embed=h.embed("⛏️ Your Pickaxe", desc, h.BLUE))

    # ══════════════════════════════════════════════════════════════════════════
    #  /hunt — flat, medium risk
    # ══════════════════════════════════════════════════════════════════════════
    @commands.hybrid_command(
        name="hunt",
        description="Hunt for pelts, meat, and rare trophies — a bit riskier.",
        extras={
            "category": "🪙 Economy",
            "short": "Hunt for loot (medium risk)",
            "usage": "hunt",
            "desc": "Track down pelts, meat, and — rarely — a golden antler trophy. "
            "There's a chance you get injured and pay a small coin fine, and a "
            "small chance of finding a padlock that blocks /rob for a day.",
            "args": [],
            "perms": "None",
            "example": "{prefix}hunt",
        },
    )
    @commands.guild_only()
    async def hunt(self, ctx: commands.Context):
        cfg = await db.get_activities_config(ctx.guild.id)
        if not cfg["hunt_enabled"]:
            return await ctx.reply(
                embed=h.err("Hunting is disabled on this server."), ephemeral=True
            )
        retry = await db.try_claim_activity(
            ctx.guild.id, ctx.author.id, "hunt", time.time(), cfg["hunt_cooldown"]
        )
        if retry:
            return await ctx.reply(
                embed=h.warn(
                    f"You're still resting up. Hunt again in **{h.fmt_duration(retry)}**.",
                    "🏹 Not Yet",
                ),
                ephemeral=True,
            )

        catch_key = pick_hunt_catch(random.random())
        catch = HUNT_CATCHES[catch_key]
        await db.add_item(ctx.guild.id, ctx.author.id, catch_key, 1)
        econ = await db.get_econ_config(ctx.guild.id)
        desc = f"You bring back {catch['emoji']} **{catch['name']}**!"

        if roll_hunt_injury(random.random()):
            fine = hunt_injury_fine(random.random())
            if fine > 0:
                await db.add_coins(ctx.guild.id, ctx.author.id, -fine)
                desc += (
                    f"\n🤕 You took a tumble on the way back and paid "
                    f"{self._money(econ, fine)} for a bandage."
                )

        if roll_hunt_padlock(random.random()):
            await db.add_item(ctx.guild.id, ctx.author.id, "padlock", 1)
            desc += f"\n{item_catalogue.display('padlock')} You also found a padlock!"

        desc += "\n\nSell loot with `/inventory sell`."
        await ctx.reply(embed=h.embed("🏹 Hunt", desc, h.BLUE))

    # ══════════════════════════════════════════════════════════════════════════
    #  /explore — flat, long shot
    # ══════════════════════════════════════════════════════════════════════════
    @commands.hybrid_command(
        name="explore",
        description="Explore for a long-shot reward — mostly nothing, occasionally huge.",
        extras={
            "category": "🪙 Economy",
            "short": "Explore for a long-shot reward",
            "usage": "explore",
            "desc": "High variance: usually nothing or a modest coin find, but "
            "sometimes a treasure key, a treasure chest, a lucky charm, or a "
            "rare big coin payout.",
            "args": [],
            "perms": "None",
            "example": "{prefix}explore",
        },
    )
    @commands.guild_only()
    async def explore(self, ctx: commands.Context):
        cfg = await db.get_activities_config(ctx.guild.id)
        if not cfg["explore_enabled"]:
            return await ctx.reply(
                embed=h.err("Exploring is disabled on this server."), ephemeral=True
            )
        retry = await db.try_claim_activity(
            ctx.guild.id, ctx.author.id, "explore", time.time(), cfg["explore_cooldown"]
        )
        if retry:
            return await ctx.reply(
                embed=h.warn(
                    f"You're still recovering from the last trip. Explore again in "
                    f"**{h.fmt_duration(retry)}**.",
                    "🧭 Not Yet",
                ),
                ephemeral=True,
            )

        outcome = pick_explore_outcome(random.random())
        flavor = EXPLORE_FLAVOR[outcome]
        econ = await db.get_econ_config(ctx.guild.id)

        if outcome == "nothing":
            embed = h.embed("🧭 Explore", flavor, h.GREY)
        elif outcome in ("coins_small", "coins_big"):
            lo, hi = (
                EXPLORE_COINS_SMALL if outcome == "coins_small" else EXPLORE_COINS_BIG
            )
            amount = roll_coin_amount(random.random(), lo, hi)
            new_bal = await db.add_coins(ctx.guild.id, ctx.author.id, amount)
            title = "🧭 Big Find!" if outcome == "coins_big" else "🧭 Explore"
            embed = h.ok(
                f"{flavor}\nYou found {self._money(econ, amount)}!\n"
                f"Balance: {self._money(econ, new_bal)}",
                title,
            )
        else:
            await db.add_item(ctx.guild.id, ctx.author.id, outcome, 1)
            embed = h.ok(
                f"{flavor}\nYou found {item_catalogue.display(outcome)}!", "🧭 Explore"
            )
        await ctx.reply(embed=embed)

    # ══════════════════════════════════════════════════════════════════════════
    #  /rob  — flat, PvP risk
    # ══════════════════════════════════════════════════════════════════════════
    @commands.hybrid_command(
        name="rob",
        description="Try to steal a cut of another member's coins — risky!",
        extras={
            "category": "🪙 Economy",
            "short": "Try to steal coins (PvP risk)",
            "usage": "rob <member>",
            "desc": "Attempt to steal 10-20% of a member's balance (capped at 1,000). "
            "Needs a decent balance of your own and a wealthy-enough target. A "
            "padlock (found while hunting) blocks anyone from robbing you. Fail "
            "and you pay a fine.",
            "args": ["member — who to rob"],
            "perms": "None",
            "example": "{prefix}rob @Someone",
        },
    )
    @commands.guild_only()
    @app_commands.describe(member="Who to rob")
    async def rob(self, ctx: commands.Context, member: discord.Member):
        cfg = await db.get_activities_config(ctx.guild.id)
        econ = await db.get_econ_config(ctx.guild.id)
        if not cfg["rob_enabled"]:
            return await ctx.reply(
                embed=h.err("Robbing is disabled on this server."), ephemeral=True
            )
        if member.bot:
            return await ctx.reply(embed=h.err("You can't rob a bot."), ephemeral=True)
        if member.id == ctx.author.id:
            return await ctx.reply(
                embed=h.err("You can't rob yourself."), ephemeral=True
            )

        target_effects = await db.get_active_effects(ctx.guild.id, member.id)
        if "rob_shield" in target_effects:
            return await ctx.reply(
                embed=h.warn(
                    f"{member.display_name} is holding a padlock — they're protected "
                    "from robbery right now.",
                    "🔒 Shielded",
                ),
                ephemeral=True,
            )

        robber_balance = await db.get_balance(ctx.guild.id, ctx.author.id)
        if robber_balance < ROB_MIN_ROBBER_BALANCE:
            return await ctx.reply(
                embed=h.err(
                    f"You need at least {self._money(econ, ROB_MIN_ROBBER_BALANCE)} "
                    "to attempt a robbery."
                ),
                ephemeral=True,
            )
        target_balance = await db.get_balance(ctx.guild.id, member.id)
        if target_balance < ROB_MIN_TARGET_BALANCE:
            return await ctx.reply(
                embed=h.err(
                    f"{member.display_name} doesn't have enough coins to be worth robbing "
                    f"(needs at least {self._money(econ, ROB_MIN_TARGET_BALANCE)})."
                ),
                ephemeral=True,
            )

        retry = await db.try_claim_activity(
            ctx.guild.id, ctx.author.id, "rob", time.time(), cfg["rob_cooldown"]
        )
        if retry:
            return await ctx.reply(
                embed=h.warn(
                    f"Lying low. Try again in **{h.fmt_duration(retry)}**.",
                    "🥷 Not Yet",
                ),
                ephemeral=True,
            )

        robber_effects = await db.get_active_effects(ctx.guild.id, ctx.author.id)
        has_luck = "luck" in robber_effects
        if rob_success(random.random(), has_luck):
            steal = rob_steal_amount(random.random(), target_balance)
            ok = await db.try_debit_coins(ctx.guild.id, member.id, steal)
            if not ok:
                # The target's balance moved between the check and the debit
                # (e.g. they spent it) — steal whatever's left instead.
                fresh_balance = await db.get_balance(ctx.guild.id, member.id)
                steal = min(steal, fresh_balance)
                ok = steal > 0 and await db.try_debit_coins(
                    ctx.guild.id, member.id, steal
                )
            if ok and steal > 0:
                new_bal = await db.add_coins(ctx.guild.id, ctx.author.id, steal)
                return await ctx.reply(
                    embed=h.ok(
                        f"🥷 You snuck off with {self._money(econ, steal)} from "
                        f"{member.display_name}!\nBalance: {self._money(econ, new_bal)}",
                        "🥷 Heist!",
                    )
                )
            return await ctx.reply(
                embed=h.info(
                    f"You got away clean, but {member.display_name} had nothing "
                    "left to take.",
                    "🥷 Empty-Handed",
                )
            )

        ok = await db.try_debit_coins(ctx.guild.id, ctx.author.id, ROB_FINE)
        if not ok:
            # Can't cover the full fine — take whatever they have (add_coins
            # clamps at 0, so this never goes negative).
            await db.add_coins(ctx.guild.id, ctx.author.id, -ROB_FINE)
        new_bal = await db.get_balance(ctx.guild.id, ctx.author.id)
        await ctx.reply(
            embed=h.err(
                f"🚨 You got caught trying to rob {member.display_name} and paid a "
                f"{self._money(econ, ROB_FINE)} fine.\nBalance: {self._money(econ, new_bal)}",
                "🚨 Caught!",
            )
        )

    # ══════════════════════════════════════════════════════════════════════════
    #  /activities  — admin group (Manage Server)
    # ══════════════════════════════════════════════════════════════════════════
    @commands.hybrid_group(
        name="activities",
        description="Configure /work, /mine, /hunt, /explore, and /rob (Manage Server).",
        invoke_without_command=True,
        extras={
            "category": "🪙 Economy",
            "short": "Configure economy activities",
            "usage": "activities [subcommand]",
            "desc": "Enable/disable and tune the cooldown for each activity: work, "
            "mine, hunt, explore, rob.",
            "args": [],
            "perms": "Manage Server",
            "example": "{prefix}activities toggle rob\n{prefix}activities cooldown mine 900",
        },
    )
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    async def activities(self, ctx: commands.Context):
        await self._show_activities_config(ctx)

    @activities.command(
        name="toggle", description="Enable or disable an activity (Manage Server)."
    )
    @app_commands.describe(activity="Which activity to toggle")
    @app_commands.choices(
        activity=[app_commands.Choice(name=a, value=a) for a in ACTIVITY_NAMES]
    )
    @commands.has_permissions(manage_guild=True)
    async def activities_toggle(self, ctx: commands.Context, activity: str):
        activity = activity.lower().strip()
        if activity not in ACTIVITY_NAMES:
            return await ctx.reply(
                embed=h.err(f"Unknown activity `{activity}`."), ephemeral=True
            )
        cfg = await db.get_activities_config(ctx.guild.id)
        key = f"{activity}_enabled"
        enabled = not cfg[key]
        await db.set_activities_config(ctx.guild.id, **{key: enabled})
        state = "enabled" if enabled else "disabled"
        await ctx.reply(embed=h.ok(f"`/{activity}` is now **{state}**."))

    @activities.command(
        name="cooldown",
        description="Set an activity's cooldown, in seconds (Manage Server).",
    )
    @app_commands.describe(
        activity="Which activity to configure", seconds="Cooldown in seconds"
    )
    @app_commands.choices(
        activity=[app_commands.Choice(name=a, value=a) for a in ACTIVITY_NAMES]
    )
    @commands.has_permissions(manage_guild=True)
    async def activities_cooldown(
        self, ctx: commands.Context, activity: str, seconds: int
    ):
        activity = activity.lower().strip()
        if activity not in ACTIVITY_NAMES:
            return await ctx.reply(
                embed=h.err(f"Unknown activity `{activity}`."), ephemeral=True
            )
        lo, hi = ACTIVITY_COOLDOWN_BOUNDS[activity]
        if not lo <= seconds <= hi:
            return await ctx.reply(
                embed=h.err(
                    f"Cooldown for `/{activity}` must be between {lo} and {hi} seconds."
                ),
                ephemeral=True,
            )
        await db.set_activities_config(
            ctx.guild.id, **{f"{activity}_cooldown": seconds}
        )
        await ctx.reply(
            embed=h.ok(f"`/{activity}` cooldown set to **{h.fmt_duration(seconds)}**.")
        )

    @activities.command(
        name="config", description="Show the activities settings (Manage Server)."
    )
    @commands.has_permissions(manage_guild=True)
    async def activities_config(self, ctx: commands.Context):
        await self._show_activities_config(ctx)

    async def _show_activities_config(self, ctx: commands.Context):
        cfg = await db.get_activities_config(ctx.guild.id)
        embed = h.embed("🪙 Activities Settings", color=h.BLUE)
        for activity in ACTIVITY_NAMES:
            enabled = cfg[f"{activity}_enabled"]
            cooldown = cfg[f"{activity}_cooldown"]
            embed.add_field(
                name=f"/{activity}",
                value=f"{'✅ Enabled' if enabled else '❌ Disabled'}\n"
                f"Cooldown: {h.fmt_duration(cooldown)}",
                inline=True,
            )
        await ctx.reply(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(Activities(bot))
