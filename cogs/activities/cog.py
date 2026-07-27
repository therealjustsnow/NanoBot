"""
cogs/activities.py
Economy activities — five distinct risk/reward profiles that pay out NanoCoins
(or ore/loot items members can sell) beyond /daily and fishing.

  /work              — SAFE. Steady, low-risk pay with a 10-step career ladder:
                       the more shifts you rack up, the higher your title
                       climbs, and each promotion adds a small pay bonus.
  /mine              — a dig every cooldown window. Yields ore items (stone →
                       coal → iron → gold → diamond) by rarity roll into your
                       inventory, with an occasional cave-in (no yield) and a
                       rare bonus treasure key. A coin-priced pickaxe ladder
                       shifts the odds toward rarer ore.
  /adventure hunt    — MEDIUM risk. Pelts and meat with a rare golden antler
                       trophy, but a chance of getting injured (a small coin
                       fine) — and a small chance of finding a padlock that
                       blocks /rob for a day.
  /adventure explore — LONG SHOT. High-variance outcomes from nothing at all
                       to a big coin find, plus treasure keys/chests and
                       lucky charms.
  /rob               — PVP RISK. Try to steal a cut of another member's coins.
                       Guarded by minimum balances and a rob-shield item;
                       failure costs a fine.

Coins ride the existing economy tables (db.add_coins et al.), and loot rides
the shared inventory (utils/db/items.py), so earnings from any activity spend
anywhere coins/items do (/shop, /pay, /coin gamble, /inventory sell).

Stats, tools, and cooldowns are GLOBAL — keyed by user, not (guild, user). A
pickaxe bought in one server digs in all of them, and a cooldown claimed
anywhere applies everywhere (which is what stops /work being farmed once per
server). A server still owns whether an activity is enabled there, but NOT how
long its cooldown lasts: because the claim is shared, a per-guild length only
ever meant "the most permissive server sets everyone's pace". Lengths are
bot-wide and owner-only (`!cooldown`, cogs/admin), read through
`Activities._cfg`/`_cooldown`. See "Cross-server farming" in constants.py.

Slash command budget: two flat commands (/work, /rob) plus two groups
(/mine …, /adventure …) whose subcommands cost no extra top-level slots —
hunt, explore, and the Manage-Server settings all live under /adventure.

──────────────────────────────────────────────────────
Commands
──────────────────────────────────────────────────────
  /work                         → work a shift for coins (safe)
  /mine                         → dig for ore                (same as /mine dig)
  /mine dig                     → dig for ore
  /mine upgrade                 → buy the next pickaxe tier with coins
  /mine stats                   → your pickaxe + dig stats
  /adventure                    → the dashboard: your career + pickaxe tier,
                                  every activity's live status and cooldown,
                                  and how many times you've run each
  /adventure dashboard          → same card (the slash-reachable `fallback`;
                                  a group's own callback can't be invoked over
                                  slash, so without it the card was prefix-only)
  /adventure hunt               → hunt for pelts/meat/trophies (medium risk)
  /adventure explore            → explore for a long-shot reward
  /rob <member>                 → try to steal a cut of a member's coins
  /adventure toggle <activity>  → enable/disable an activity   (Manage Server)
  /adventure config             → show settings                (Manage Server)

Cooldown lengths are not here: they are bot-wide, so `!cooldown` lives in the
owner-only admin cog.
"""

import logging
import random
import time

import discord
from discord import app_commands
from discord.ext import commands

from utils import db
from utils import globalxp
from utils import helpers as h
from utils import items as item_catalogue

from . import items as _register_items  # noqa: F401 - side-effect: registers item defs
from .constants import (
    ACTIVITY_INFO,
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
    effective_cooldown,
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
    """Work, mine, hunt, explore, and rob for NanoCoins — stats, tools, and
    cooldowns are per user and shared across servers."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Per-(guild, user) locks serialize /mine upgrade's read-check-write
        # (the /daily pattern), mirroring cogs.fishing.Fishing._locks.
        self._locks = h.KeyedLocks()

    def _lock(self, user_id: int):
        # Returns an async context manager; existing `async with self._lock(...)`
        # call sites are unchanged. KeyedLocks refcounts holder + waiters and
        # drops an entry when the last interested task releases, so the map no
        # longer grows for the lifetime of the process.
        return self._locks.hold(user_id)

    def _money(self, econ: dict, amount: int) -> str:
        return h.fmt_coins(amount, econ["currency_name"], econ["currency_emoji"])

    # ── shared status helpers ────────────────────────────────────────────────
    async def _cfg(self, guild_id: int) -> dict:
        """This server's activity settings, with the bot-wide cooldowns folded in.

        The guild row only holds the on/off switches now. Cooldown *claims* are
        global (one stats row per user, not per guild+user), so the lengths are
        global too: they live in `bot_settings`, are set by the bot owner alone
        (`!cooldown`), and are merged in here so every call site keeps reading
        one dict. See the "Cross-server farming" note in constants.py.
        """
        cfg = dict(await db.get_activities_config(guild_id))
        for activity, seconds in (await db.get_activity_cooldowns()).items():
            cfg[f"{activity}_cooldown"] = seconds
        return cfg

    def _cooldown(self, cfg: dict, activity: str) -> int:
        """An activity's cooldown length — the single place it is read.

        Missing from `cfg` means the owner set no override, and
        `effective_cooldown` answers with the activity's default.
        """
        return effective_cooldown(activity, cfg.get(f"{activity}_cooldown"))

    def _remaining(self, cfg: dict, stats: dict, activity: str) -> int:
        """Seconds left on an activity's cooldown (0 = ready). Read-only — the
        claim itself still happens atomically in db.try_claim_activity."""
        last = stats.get(f"last_{activity}", 0) or 0
        if not last:
            return 0
        return max(0, int(self._cooldown(cfg, activity) - (time.time() - last)))

    def _status_line(self, cfg: dict, stats: dict, activity: str) -> str:
        """ "Ready now" / "Ready in 12m" / "Disabled" for one activity."""
        if not cfg[f"{activity}_enabled"]:
            return "❌ Disabled here"
        remaining = self._remaining(cfg, stats, activity)
        if remaining:
            return f"⏳ Ready in {h.fmt_duration(remaining)}"
        return "✅ Ready now"

    def _next_up(self, cfg: dict, activity: str) -> str:
        """Footer text telling a member when this activity comes back."""
        return f"Next {activity} in {h.fmt_duration(self._cooldown(cfg, activity))}"

    # ══════════════════════════════════════════════════════════════════════════
    #  /work — flat, safe
    # ══════════════════════════════════════════════════════════════════════════
    @commands.hybrid_command(
        name="work",
        description="Work a shift for coins — safe, steady income.",
        extras={
            "category": "🪙 Economy",
            "sub": "⛏️ Activities",
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
        cfg = await self._cfg(ctx.guild.id)
        if not cfg["work_enabled"]:
            return await ctx.reply(
                embed=h.err("Working is disabled on this server."), ephemeral=True
            )
        before = await db.get_activity_stats(ctx.author.id)
        retry = await db.try_claim_activity(
            ctx.author.id, "work", time.time(), self._cooldown(cfg, "work")
        )
        if retry:
            return await ctx.reply(
                embed=h.warn(
                    f"You're still on shift. Try again in **{h.fmt_duration(retry)}**.",
                    "💼 Not Yet",
                ),
                ephemeral=True,
            )

        await globalxp.award(ctx.author.id, "activity")
        stats = await db.get_activity_stats(ctx.author.id)
        info = career_info(stats["work_shifts"])
        old_info = career_info(before["work_shifts"])
        pay = roll_work_pay(random.random(), info["bonus"])
        scene = pick_work_scene(random.random())
        econ = await db.get_econ_config(ctx.guild.id)
        new_bal = await db.add_coins(ctx.author.id, pay)

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
            "sub": "⛏️ Activities",
            "short": "Dig for ore",
            "usage": "mine [subcommand]",
            "desc": "Dig for ore — stone, coal, iron, gold, and rare diamond — sold "
            "via /inventory sell. Buy better pickaxes to improve your odds. "
            "There's a small chance of a cave-in with no yield.",
            "args": [],
            "perms": "None (admin settings live under /adventure)",
            "example": "{prefix}mine\n{prefix}mine stats\n{prefix}mine upgrade",
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
        cfg = await self._cfg(ctx.guild.id)
        if not cfg["mine_enabled"]:
            return await ctx.reply(
                embed=h.err("Mining is disabled on this server."), ephemeral=True
            )
        retry = await db.try_claim_activity(
            ctx.author.id, "mine", time.time(), self._cooldown(cfg, "mine")
        )
        if retry:
            return await ctx.reply(
                embed=h.warn(
                    f"Your pickaxe needs a rest. Dig again in **{h.fmt_duration(retry)}**.",
                    "⛏️ Not Yet",
                ),
                ephemeral=True,
            )

        await globalxp.award(ctx.author.id, "activity")
        if roll_cave_in(random.random()):
            embed = h.warn(
                "The tunnel collapses behind you — you scramble out empty-handed.",
                "⛏️ Cave-In",
            )
            embed.set_footer(text=self._next_up(cfg, "mine"))
            return await ctx.reply(embed=embed)

        stats = await db.get_activity_stats(ctx.author.id)
        pickaxe = pickaxe_info(stats["pickaxe_level"])
        ore_key = pick_ore(random.random(), pickaxe["luck"])
        ore = ORES[ore_key]
        await db.add_item(ctx.author.id, ore_key, 1)

        desc = f"You dig up {ore['emoji']} **{ore['name']}**!"
        if roll_mine_treasure_key(random.random()):
            await db.add_item(ctx.author.id, "treasure_key", 1)
            desc += f"\n{item_catalogue.display('treasure_key')} You also found a treasure key!"
        desc += "\n\nSell it with `/inventory sell`."
        embed = h.ok(desc, "⛏️ Dig")
        embed.set_footer(text=self._next_up(cfg, "mine"))
        await ctx.reply(embed=embed)

    # ── /mine upgrade ────────────────────────────────────────────────────────
    @mine.command(name="upgrade", description="Buy the next pickaxe tier with coins.")
    async def mine_upgrade(self, ctx: commands.Context):
        econ = await db.get_econ_config(ctx.guild.id)
        async with self._lock(ctx.author.id):
            stats = await db.get_activity_stats(ctx.author.id)
            nxt = next_pickaxe(stats["pickaxe_level"])
            if nxt is None:
                return await ctx.reply(
                    embed=h.info(
                        "You already own the best pickaxe there is!", "⛏️ Maxed"
                    ),
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
            upgraded = await db.set_pickaxe_level(
                ctx.author.id,
                stats["pickaxe_level"] + 1,
                expected=stats["pickaxe_level"],
            )
            if not upgraded:
                # A racing second upgrade already went through — refund.
                await db.add_coins(ctx.author.id, nxt["price"])
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
        cfg = await self._cfg(ctx.guild.id)
        econ = await db.get_econ_config(ctx.guild.id)
        stats = await db.get_activity_stats(ctx.author.id)
        pickaxe = pickaxe_info(stats["pickaxe_level"])
        nxt = next_pickaxe(stats["pickaxe_level"])
        desc = (
            f"{pickaxe['emoji']} **{pickaxe['name']}** "
            f"(tier {stats['pickaxe_level'] + 1}/{len(PICKAXES)})\n"
            f"Luck: **{pickaxe['luck']:.0%}** less stone, better rare-ore odds\n"
            f"Digs: **{stats['mine_count']:,}**\n"
            f"{self._status_line(cfg, stats, 'mine')}"
        )
        if nxt:
            # The price used to be invisible until the purchase failed — the
            # only way to learn it was to try to buy and be told you're broke.
            balance = await db.get_balance(ctx.author.id)
            afford = (
                "✅ you can afford it"
                if balance >= nxt["price"]
                else (f"🔒 you have {self._money(econ, balance)}")
            )
            desc += (
                f"\n\nNext: {nxt['emoji']} **{nxt['name']}** — "
                f"{self._money(econ, nxt['price'])} ({afford})\n"
                f"Luck goes to **{nxt['luck']:.0%}**. Buy it with `/mine upgrade`."
            )
        else:
            desc += "\n\nYou own the best pickaxe there is. 🎉"
        await ctx.reply(embed=h.embed("⛏️ Your Pickaxe", desc, h.BLUE))

    # ══════════════════════════════════════════════════════════════════════════
    #  /adventure — hunt + explore + activity settings (one top-level slot)
    # ══════════════════════════════════════════════════════════════════════════
    @commands.hybrid_group(
        name="adventure",
        description="Hunt and explore for loot, and configure economy activities.",
        invoke_without_command=True,
        # `fallback` is what makes the bare landing card reachable as a slash
        # command (/adventure dashboard) — without it the overview below is
        # prefix-only, since Discord has no way to invoke a group itself.
        # /mine and /fish don't need one: their bare form is a dig/cast, which
        # already has its own `dig`/`cast` subcommand.
        fallback="dashboard",
        extras={
            "category": "🪙 Economy",
            "sub": "⛏️ Activities",
            "short": "Hunt, explore, and manage activities",
            "usage": "adventure [subcommand]",
            "desc": "Run it bare to see every activity, what it pays, and whether "
            "you're off cooldown. Hunt for pelts, meat, and rare trophies (medium "
            "risk) or explore for a long-shot reward. Admins can enable or disable "
            "any of the five activities here: work, mine, hunt, explore, rob.",
            "args": [],
            "perms": "Admin subcommands require Manage Server",
            "example": "{prefix}adventure\n{prefix}adventure hunt\n"
            "{prefix}adventure explore",
        },
    )
    @commands.guild_only()
    async def adventure(self, ctx: commands.Context):
        await self._show_adventure_overview(ctx)

    async def _show_adventure_overview(self, ctx: commands.Context):
        """The member-facing landing card: your two progression tracks, every
        activity's live status, and what you've done so far.

        Read-only and cheap — two queries (the guild's switches, your stats
        row); the career tier and pickaxe tier are derived from that same row,
        so the progression block costs nothing extra. The settings dump stays
        on /adventure config, and prestige/achievements stay on /progress —
        this card deliberately doesn't restate them.
        """
        cfg = await self._cfg(ctx.guild.id)
        stats = await db.get_activity_stats(ctx.author.id)

        ready = [
            a
            for a in ACTIVITY_NAMES
            if cfg[f"{a}_enabled"] and not self._remaining(cfg, stats, a)
        ]
        if ready:
            headline = f"**{len(ready)}** ready right now: " + ", ".join(
                f"`/{a}`" for a in ready
            )
        else:
            soonest = min(
                (
                    self._remaining(cfg, stats, a)
                    for a in ACTIVITY_NAMES
                    if cfg[f"{a}_enabled"]
                ),
                default=0,
            )
            headline = (
                f"Nothing ready yet — next one in **{h.fmt_duration(soonest)}**."
                if soonest
                else "Every activity is switched off in this server."
            )
        embed = h.embed(
            "🧭 Adventure",
            f"{headline}\nEvery way to earn beyond `/daily` and `/fish` — each on "
            "its own cooldown.",
            h.BLUE,
        )

        # ── The two progression tracks these activities feed ──────────────────
        career = career_info(stats["work_shifts"])
        nxt_career = next_career(stats["work_shifts"])
        career_line = f"💼 **{career['title']}**"
        if career["bonus"]:
            career_line += f" · +{career['bonus']} per shift"
        if nxt_career:
            togo = nxt_career["shifts"] - stats["work_shifts"]
            career_line += f"\n└ **{togo:,}** more shift(s) → {nxt_career['title']}"
        else:
            career_line += "\n└ Top of the career ladder. 🎉"

        pickaxe = pickaxe_info(stats["pickaxe_level"])
        nxt_pick = next_pickaxe(stats["pickaxe_level"])
        pick_line = (
            f"{pickaxe['emoji']} **{pickaxe['name']}** "
            f"(tier {stats['pickaxe_level'] + 1}/{len(PICKAXES)}) · "
            f"{pickaxe['luck']:.0%} luck"
        )
        pick_line += (
            f"\n└ Next: **{nxt_pick['name']}** — see `/mine upgrade`"
            if nxt_pick
            else "\n└ Best pickaxe there is. 🎉"
        )
        embed.add_field(
            name="Progression", value=f"{career_line}\n{pick_line}", inline=False
        )

        for activity in ACTIVITY_NAMES:
            info = ACTIVITY_INFO[activity]
            count = stats.get(f"{activity}_count") or (
                stats["work_shifts"] if activity == "work" else 0
            )
            embed.add_field(
                name=f"{info['emoji']} {info['command']}",
                value=f"{info['blurb']}\n{self._status_line(cfg, stats, activity)} · "
                f"every {h.fmt_duration(self._cooldown(cfg, activity))}\n"
                f"Done **{count:,}×**",
                inline=True,
            )
        embed.set_footer(
            text="Sell what you find with /inventory · badges on /progress"
        )
        await ctx.reply(embed=embed)

    # ── /adventure hunt — medium risk ────────────────────────────────────────
    @adventure.command(
        name="hunt",
        description="Hunt for pelts, meat, and rare trophies — a bit riskier.",
    )
    async def hunt(self, ctx: commands.Context):
        cfg = await self._cfg(ctx.guild.id)
        if not cfg["hunt_enabled"]:
            return await ctx.reply(
                embed=h.err("Hunting is disabled on this server."), ephemeral=True
            )
        retry = await db.try_claim_activity(
            ctx.author.id, "hunt", time.time(), self._cooldown(cfg, "hunt")
        )
        if retry:
            return await ctx.reply(
                embed=h.warn(
                    f"You're still resting up. Hunt again in **{h.fmt_duration(retry)}**.",
                    "🏹 Not Yet",
                ),
                ephemeral=True,
            )

        await globalxp.award(ctx.author.id, "activity")
        catch_key = pick_hunt_catch(random.random())
        catch = HUNT_CATCHES[catch_key]
        await db.add_item(ctx.author.id, catch_key, 1)
        econ = await db.get_econ_config(ctx.guild.id)
        desc = f"You bring back {catch['emoji']} **{catch['name']}**!"

        if roll_hunt_injury(random.random()):
            fine = hunt_injury_fine(random.random())
            if fine > 0:
                await db.add_coins(ctx.author.id, -fine)
                desc += (
                    f"\n🤕 You took a tumble on the way back and paid "
                    f"{self._money(econ, fine)} for a bandage."
                )

        if roll_hunt_padlock(random.random()):
            await db.add_item(ctx.author.id, "padlock", 1)
            desc += f"\n{item_catalogue.display('padlock')} You also found a padlock!"

        desc += "\n\nSell loot with `/inventory sell`."
        embed = h.embed("🏹 Hunt", desc, h.BLUE)
        embed.set_footer(text=self._next_up(cfg, "hunt"))
        await ctx.reply(embed=embed)

    # ── /adventure explore — long shot ───────────────────────────────────────
    @adventure.command(
        name="explore",
        description="Explore for a long-shot reward — mostly nothing, occasionally huge.",
    )
    async def explore(self, ctx: commands.Context):
        cfg = await self._cfg(ctx.guild.id)
        if not cfg["explore_enabled"]:
            return await ctx.reply(
                embed=h.err("Exploring is disabled on this server."), ephemeral=True
            )
        retry = await db.try_claim_activity(
            ctx.author.id, "explore", time.time(), self._cooldown(cfg, "explore")
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

        await globalxp.award(ctx.author.id, "activity")
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
            new_bal = await db.add_coins(ctx.author.id, amount)
            title = "🧭 Big Find!" if outcome == "coins_big" else "🧭 Explore"
            embed = h.ok(
                f"{flavor}\nYou found {self._money(econ, amount)}!\n"
                f"Balance: {self._money(econ, new_bal)}",
                title,
            )
        else:
            await db.add_item(ctx.author.id, outcome, 1)
            embed = h.ok(
                f"{flavor}\nYou found {item_catalogue.display(outcome)}!", "🧭 Explore"
            )
        embed.set_footer(text=self._next_up(cfg, "explore"))
        await ctx.reply(embed=embed)

    # ══════════════════════════════════════════════════════════════════════════
    #  /rob  — flat, PvP risk
    # ══════════════════════════════════════════════════════════════════════════
    @commands.hybrid_command(
        name="rob",
        description="Try to steal a cut of another member's coins — risky!",
        extras={
            "category": "🪙 Economy",
            "sub": "⛏️ Activities",
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
        cfg = await self._cfg(ctx.guild.id)
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

        target_effects = await db.get_active_effects(member.id)
        if "rob_shield" in target_effects:
            return await ctx.reply(
                embed=h.warn(
                    f"{member.display_name} is holding a padlock — they're protected "
                    "from robbery right now.",
                    "🔒 Shielded",
                ),
                ephemeral=True,
            )

        robber_balance = await db.get_balance(ctx.author.id)
        if robber_balance < ROB_MIN_ROBBER_BALANCE:
            return await ctx.reply(
                embed=h.err(
                    f"You need at least {self._money(econ, ROB_MIN_ROBBER_BALANCE)} "
                    "to attempt a robbery."
                ),
                ephemeral=True,
            )
        target_balance = await db.get_balance(member.id)
        if target_balance < ROB_MIN_TARGET_BALANCE:
            return await ctx.reply(
                embed=h.err(
                    f"{member.display_name} doesn't have enough coins to be worth robbing "
                    f"(needs at least {self._money(econ, ROB_MIN_TARGET_BALANCE)})."
                ),
                ephemeral=True,
            )

        retry = await db.try_claim_activity(
            ctx.author.id, "rob", time.time(), self._cooldown(cfg, "rob")
        )
        if retry:
            return await ctx.reply(
                embed=h.warn(
                    f"Lying low. Try again in **{h.fmt_duration(retry)}**.",
                    "🥷 Not Yet",
                ),
                ephemeral=True,
            )

        await globalxp.award(ctx.author.id, "activity")
        robber_effects = await db.get_active_effects(ctx.author.id)
        has_luck = "luck" in robber_effects
        if rob_success(random.random(), has_luck):
            steal = rob_steal_amount(random.random(), target_balance)
            ok = await db.try_debit_coins(member.id, steal)
            if not ok:
                # The target's balance moved between the check and the debit
                # (e.g. they spent it) — steal whatever's left instead.
                fresh_balance = await db.get_balance(member.id)
                steal = min(steal, fresh_balance)
                ok = steal > 0 and await db.try_debit_coins(member.id, steal)
            if ok and steal > 0:
                new_bal = await db.add_coins(ctx.author.id, steal)
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

        ok = await db.try_debit_coins(ctx.author.id, ROB_FINE)
        if not ok:
            # Can't cover the full fine — take whatever they have (add_coins
            # clamps at 0, so this never goes negative).
            await db.add_coins(ctx.author.id, -ROB_FINE)
        new_bal = await db.get_balance(ctx.author.id)
        await ctx.reply(
            embed=h.err(
                f"🚨 You got caught trying to rob {member.display_name} and paid a "
                f"{self._money(econ, ROB_FINE)} fine.\nBalance: {self._money(econ, new_bal)}",
                "🚨 Caught!",
            )
        )

    # ── /adventure admin subcommands (Manage Server) ─────────────────────────
    @adventure.command(
        name="toggle", description="Enable or disable an activity (Manage Server)."
    )
    @app_commands.describe(activity="Pick an activity (the list shows its state)")
    @commands.has_permissions(manage_guild=True)
    async def activities_toggle(self, ctx: commands.Context, activity: str):
        activity = activity.lower().strip()
        if activity not in ACTIVITY_NAMES:
            return await ctx.reply(
                embed=h.err(f"Unknown activity `{activity}`."), ephemeral=True
            )
        cfg = await self._cfg(ctx.guild.id)
        key = f"{activity}_enabled"
        enabled = not cfg[key]
        await db.set_activities_config(ctx.guild.id, **{key: enabled})
        state = "enabled" if enabled else "disabled"
        await ctx.reply(embed=h.ok(f"`/{activity}` is now **{state}**."))

    @activities_toggle.autocomplete("activity")
    async def _toggle_activity_ac(self, interaction: discord.Interaction, current: str):
        return await self._activity_choices(interaction, current)

    async def _activity_choices(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """The five activities with their live state, so a mod can see what
        they're about to change before they change it."""
        q = (current or "").strip().lower()
        cfg = await self._cfg(interaction.guild_id) if interaction.guild_id else None
        choices: list[app_commands.Choice[str]] = []
        for activity in ACTIVITY_NAMES:
            if q and q not in activity:
                continue
            info = ACTIVITY_INFO[activity]
            label = f"{info['emoji']} {activity}"
            if cfg:
                state = "✅ enabled" if cfg[f"{activity}_enabled"] else "❌ disabled"
                label += (
                    f" — {state} · every "
                    f"{h.fmt_duration(self._cooldown(cfg, activity))}"
                )
            choices.append(app_commands.Choice(name=label[:100], value=activity))
        return choices

    @adventure.command(
        name="config", description="Show the activities settings (Manage Server)."
    )
    @commands.has_permissions(manage_guild=True)
    async def activities_config(self, ctx: commands.Context):
        await self._show_activities_config(ctx)

    async def _show_activities_config(self, ctx: commands.Context):
        cfg = await self._cfg(ctx.guild.id)
        embed = h.embed("🪙 Activities Settings", color=h.BLUE)
        for activity in ACTIVITY_NAMES:
            enabled = cfg[f"{activity}_enabled"]
            cooldown = self._cooldown(cfg, activity)
            embed.add_field(
                name=f"/{activity}",
                value=f"{'✅ Enabled' if enabled else '❌ Disabled'}\n"
                f"Cooldown: {h.fmt_duration(cooldown)}",
                inline=True,
            )
        embed.set_footer(
            text="Enable or disable an activity with /adventure toggle. "
            "Cooldowns are bot-wide and only the bot owner can change them."
        )
        await ctx.reply(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(Activities(bot))
