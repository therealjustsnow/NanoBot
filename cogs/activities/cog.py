"""
cogs/activities.py
Economy activities — five distinct risk/reward profiles that pay out NanoCoins
(or ore/loot items members can sell) beyond /daily and fishing.

  /work              — SAFE. Steady, low-risk pay with a 10-step career ladder:
                       the more shifts you rack up, the higher your title
                       climbs, and each promotion adds a small pay bonus.
  /mine              — a dig every cooldown window. Yields a vein of ore items
                       (stone → coal → iron → gold → diamond), each rolled on
                       its own, with an occasional cave-in (no yield) and a
                       rare bonus treasure key. A coin-priced pickaxe ladder
                       shifts the odds toward rarer ore.
  /adventure hunt    — MEDIUM risk. A bag of pelts and meat with a rare golden
                       antler trophy, but a chance of getting injured (a small
                       coin fine) — and a small chance of finding a padlock
                       that blocks /rob for a day.
  /adventure explore — LONG SHOT. High-variance outcomes from nothing at all
                       to a big coin find, plus treasure keys/chests and
                       lucky charms.
  /rob               — PVP RISK. Try to steal a cut of another member's coins.
                       Guarded by minimum balances and a rob-shield item;
                       failure costs a fine.

Three things make this a loop rather than a set of timers, all documented at
length in constants.py:

  charges     — every activity but /rob banks runs while you're away
                (ACTIVITY_MAX_CHARGES), so an hour off comes back as three taps
                rather than one. Same long-run rate, different shaped visit.
  encounters  — ~8% of runs open a follow-up choice on buttons (ENCOUNTERS):
                a safe option that always pays and a greedy one that might not.
  daily streak— the first run of each day stamps a streak that multiplies coin
                payouts up to +25%, so there's a reason to come back tomorrow.

The dashboard is the front door to all of it: /adventure renders the card *and*
a button per runnable activity (cogs/activities/views.py), so a member who has
four things ready doesn't have to type four more commands to do them. Every
button goes through `run_activity`, the same path the commands take.

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
                                  your daily streak, every activity's banked
                                  charges, and a button to run each of them
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
from dataclasses import dataclass
from typing import Optional

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
    ENCOUNTERS,
    EXPLORE_COINS_BIG,
    EXPLORE_COINS_SMALL,
    EXPLORE_FLAVOR,
    HUNT_CATCHES,
    ORES,
    PICKAXES,
    ROB_FINE,
    ROB_MIN_ROBBER_BALANCE,
    ROB_MIN_TARGET_BALANCE,
    STREAK_BONUS_CAP,
)
from .helpers import (
    apply_streak,
    career_info,
    charge_state,
    effective_cooldown,
    hunt_injury_fine,
    max_charges,
    next_adventure_streak,
    next_career,
    next_pickaxe,
    outcome_coins,
    pick_explore_outcome,
    pick_hunt_catch,
    pick_ore,
    pick_work_scene,
    pickaxe_info,
    resolve_encounter,
    rob_steal_amount,
    rob_success,
    roll_cave_in,
    roll_coin_amount,
    roll_encounter,
    roll_hunt_bag,
    roll_hunt_injury,
    roll_hunt_padlock,
    roll_mine_treasure_key,
    roll_vein,
    roll_work_pay,
    streak_multiplier,
)
from .views import BUTTON_ACTIVITIES, AdventureView, EncounterView

log = logging.getLogger("NanoBot.activities")


@dataclass
class ActivityRun:
    """One completed (or refused) activity, ready to be shown.

    The activities used to reply to a `Context` from inside their own bodies,
    which meant the dashboard's buttons had no way to reuse them. Returning the
    embed instead of sending it is what lets one code path serve a prefix
    command, a slash command and a button press — so a change to how a dig
    resolves can't drift between them.
    """

    embed: discord.Embed
    view: Optional[discord.ui.View] = None
    # False when the run was refused (cooldown, or switched off here). A
    # refusal is only interesting to the person who asked, so on slash it goes
    # out ephemerally rather than putting "not yet" in the channel.
    claimed: bool = True


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

    def _charges(self, cfg: dict, stats: dict, activity: str, now: float = 0) -> dict:
        """A member's charge bucket for one activity — read-only.

        The claim in db.try_claim_activity is still the only thing that decides
        whether a run happens; this is what the dashboard paints with, and it
        may be a second or two stale by the time a button is pressed. That's
        fine: the press re-claims.
        """
        return charge_state(
            stats.get(f"last_{activity}", 0) or 0,
            now or time.time(),
            self._cooldown(cfg, activity),
            max_charges(activity),
        )

    def _remaining(self, cfg: dict, stats: dict, activity: str) -> int:
        """Seconds until this activity can next be run (0 = ready now)."""
        return self._charges(cfg, stats, activity)["next_in"]

    def _status_line(self, cfg: dict, stats: dict, activity: str) -> str:
        """ "Ready now ×3" / "Ready in 12m" / "Disabled" for one activity."""
        if not cfg[f"{activity}_enabled"]:
            return "❌ Disabled here"
        charges = self._charges(cfg, stats, activity)
        if not charges["ready"]:
            return f"⏳ Ready in {h.fmt_duration(charges['next_in'])}"
        if charges["max"] > 1:
            return f"✅ Ready now ×{charges['ready']}"
        return "✅ Ready now"

    def _next_up(self, cfg: dict, activity: str, stats: dict | None = None) -> str:
        """Footer text telling a member what's left and when more arrives.

        A bare "next in 20m" was the right footer when one run emptied the tank.
        With charges the more useful half is how many are still banked — that is
        what decides whether they press the button again right now.
        """
        interval = h.fmt_duration(self._cooldown(cfg, activity))
        if stats is None:
            return f"Next {activity} in {interval}"
        charges = self._charges(cfg, stats, activity)
        if charges["ready"]:
            return f"{charges['ready']} more ready now · +1 every {interval}"
        return f"Next {activity} in {h.fmt_duration(charges['next_in'])}"

    # ══════════════════════════════════════════════════════════════════════════
    #  The shared run path — one activity, whatever invoked it
    # ══════════════════════════════════════════════════════════════════════════
    async def run_activity(
        self, guild: discord.Guild, member: discord.abc.User, activity: str
    ) -> ActivityRun:
        """Check, claim, resolve, and describe one run of an activity.

        The single entry point for /work, /mine dig, /adventure hunt|explore
        and every dashboard button. /rob is not here: it takes a target and has
        four guard rails of its own, none of which a button could ask about.

        Order matters. The claim comes before anything is rolled or paid, so a
        refusal costs nothing; the streak is stamped straight after, because the
        payouts below are multiplied by it.
        """
        info = ACTIVITY_INFO[activity]
        cfg = await self._cfg(guild.id)
        if not cfg[f"{activity}_enabled"]:
            return ActivityRun(h.err(info["disabled"]), claimed=False)

        retry = await db.try_claim_activity(
            member.id,
            activity,
            time.time(),
            self._cooldown(cfg, activity),
            max_charges(activity),
        )
        if retry:
            return ActivityRun(
                h.warn(
                    info["wait"].format(wait=h.fmt_duration(retry)), info["wait_title"]
                ),
                claimed=False,
            )

        await globalxp.award(member.id, "activity")
        stats = await db.get_activity_stats(member.id)
        streak, streak_started = await self._touch_streak(member.id, stats)
        econ = await db.get_econ_config(guild.id)
        # A timed coin multiplier (voting, consumables) — read once and handed
        # to the resolvers, so honouring it costs no extra query.
        boost = await db.get_active_effects(member.id)

        resolver = {
            "work": self._resolve_work,
            "mine": self._resolve_dig,
            "hunt": self._resolve_hunt,
            "explore": self._resolve_explore,
        }[activity]
        embed = await resolver(member, cfg, econ, stats, streak, boost)

        if streak_started and streak > 1:
            bonus = streak_multiplier(streak) - 1
            embed.description += (
                f"\n\n🔥 **{streak}-day streak** — coin rewards are "
                f"+{bonus:.0%} for the rest of today."
            )
        elif streak_started:
            embed.description += (
                "\n\n🔥 Streak started. Come back tomorrow and coin rewards "
                "start climbing."
            )

        return ActivityRun(*self._maybe_encounter(embed, member, guild, activity))

    async def collect_all(
        self, guild: discord.Guild, member: discord.abc.User
    ) -> ActivityRun:
        """Run every banked charge across every activity, and report the lot.

        The point of the big charge caps is that a member who turns up twice a
        day loses nothing — but "nothing lost" turned into thirteen separate
        button presses, which is its own way of disrespecting someone's time.
        This is the one press that empties the buckets.

        Each run still goes through `run_activity`, so every claim is the same
        atomic one and a charge that isn't really there is refused rather than
        paid. Totals are measured as balance and inventory *deltas* around the
        batch rather than reported by each resolver: that way the summary
        describes what actually landed, including the streak multiplier, an
        injury fine, or a cave-in that paid nothing.

        At most one encounter survives a collect. They fire per run, so a full
        bucket would otherwise stack a pile of two-button choices on one
        message; the first is kept and the rest are dropped unrolled.
        """
        before_coins = await db.get_balance(member.id)
        before_items = {
            row["item_key"]: row["qty"] for row in await db.get_inventory(member.id)
        }

        ran: dict[str, int] = {}
        view: Optional[discord.ui.View] = None
        for activity in BUTTON_ACTIVITIES:
            # Bounded by the cap as well as by the claim, so a bug in the
            # bucket arithmetic can't turn this into an infinite payout loop.
            for _ in range(max_charges(activity)):
                run = await self.run_activity(guild, member, activity)
                if not run.claimed:
                    break
                ran[activity] = ran.get(activity, 0) + 1
                if view is None and run.view is not None:
                    view = run.view

        if not ran:
            cfg = await self._cfg(guild.id)
            stats = await db.get_activity_stats(member.id)
            soonest = min(
                (
                    self._charges(cfg, stats, a)["next_in"]
                    for a in BUTTON_ACTIVITIES
                    if cfg[f"{a}_enabled"]
                ),
                default=0,
            )
            return ActivityRun(
                h.warn(
                    (
                        f"Nothing banked yet — the next one lands in "
                        f"**{h.fmt_duration(soonest)}**."
                        if soonest
                        else "Every activity is switched off in this server."
                    ),
                    "🧭 Nothing To Collect",
                ),
                claimed=False,
            )

        econ = await db.get_econ_config(guild.id)
        after_coins = await db.get_balance(member.id)
        after_items = {
            row["item_key"]: row["qty"] for row in await db.get_inventory(member.id)
        }
        gained = {
            key: qty - before_items.get(key, 0)
            for key, qty in after_items.items()
            if qty > before_items.get(key, 0)
        }

        total_runs = sum(ran.values())
        desc = [
            f"You worked through **{total_runs}** banked "
            f"{'run' if total_runs == 1 else 'runs'}.",
            " · ".join(
                f"{ACTIVITY_INFO[a]['emoji']} {a.title()} ×{n}" for a, n in ran.items()
            ),
        ]
        delta = after_coins - before_coins
        if delta:
            verb = "Earned" if delta > 0 else "Net"
            desc.append(f"\n🪙 {verb} {self._money(econ, abs(delta))}")
        if gained:
            desc.append(
                "🎒 "
                + ", ".join(
                    f"{item_catalogue.display(k)} ×{q}" for k, q in gained.items()
                )
            )
        desc.append(f"\nBalance: {self._money(econ, after_coins)}")
        embed = h.ok("\n".join(desc), "🧭 Collected")
        embed.set_footer(text="Sell what you found with /inventory sell")
        return ActivityRun(embed, view)

    async def _touch_streak(self, user_id: int, stats: dict) -> tuple[int, bool]:
        """Advance the adventure daily streak, once per day.

        Returns (streak, started_today). The write is the conditional stamp in
        db.try_claim_adventure_streak, so two activities run in the same second
        can't both decide they were the first of the day; the loser re-reads
        rather than guessing, since the winner may have moved the number.
        """
        today = int(time.time() // 86_400)
        last_day = stats.get("last_day") or 0
        current = stats.get("streak_days") or 0
        if last_day == today:
            return max(1, current), False
        streak = next_adventure_streak(last_day, today, current)
        if await db.try_claim_adventure_streak(user_id, today, streak):
            return streak, True
        fresh = await db.get_activity_stats(user_id)
        return max(1, fresh["streak_days"] or 0), False

    def _maybe_encounter(
        self,
        embed: discord.Embed,
        member: discord.abc.User,
        guild: discord.Guild,
        activity: str,
    ) -> tuple[discord.Embed, Optional[discord.ui.View]]:
        """Roll for an encounter and, if one fires, hang it off the result.

        It rides the same message rather than a second one: the encounter is
        part of what just happened, and a separate post would bury the result
        it belongs to.
        """
        key = roll_encounter(activity, random.random(), random.random())
        if key is None:
            return embed, None
        encounter = ENCOUNTERS[key]
        embed.add_field(
            name=f"{encounter['emoji']} {encounter['title']}",
            value=encounter["prompt"],
            inline=False,
        )
        return embed, EncounterView(self, member.id, guild.id, key)

    async def resolve_encounter(
        self,
        guild: discord.Guild,
        member: discord.abc.User,
        encounter_key: str,
        option_key: str,
        outcome_roll: float,
        amount_roll: float,
    ) -> discord.Embed:
        """Pay out a chosen encounter option. Called only by EncounterView.

        The view's single-use guard is what stops this being called twice for
        one encounter; nothing here re-checks, because there is no row to check
        against — an encounter exists only for as long as its message does.
        """
        econ = await db.get_econ_config(guild.id)
        encounter = ENCOUNTERS.get(encounter_key)
        outcome = resolve_encounter(encounter_key, option_key, outcome_roll)
        if encounter is None or outcome is None:
            # Only reachable if the registry changed under a live message.
            return h.warn("That encounter is over.", "🌫️ Gone")

        lines = [outcome["text"]]
        coins = outcome_coins(outcome, amount_roll)
        if coins > 0:
            # Only the reward half is boosted — an option that charges for
            # itself must not get more expensive because you voted.
            coins = h.apply_coin_boost(coins, await db.get_active_effects(member.id))
        if coins:
            new_balance = await db.add_coins(member.id, coins)
            verb = "You earned" if coins > 0 else "It cost you"
            lines.append(
                f"{verb} {self._money(econ, abs(coins))}.\n"
                f"Balance: {self._money(econ, new_balance)}"
            )
        if "item" in outcome:
            item_key, qty = outcome["item"]
            await db.add_item(member.id, item_key, qty)
            lines.append(f"You pocket {item_catalogue.display(item_key)} ×{qty}.")

        title = f"{encounter['emoji']} {encounter['title']}"
        return (h.err if coins < 0 and "item" not in outcome else h.ok)(
            "\n".join(lines), title
        )

    async def _send_run(self, ctx: commands.Context, run: ActivityRun):
        """Reply with a finished run, wiring up an encounter view if it opened."""
        message = await ctx.reply(
            embed=run.embed, view=run.view, ephemeral=not run.claimed
        )
        if run.view is not None:
            run.view.message = message

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
        await self._send_run(
            ctx, await self.run_activity(ctx.guild, ctx.author, "work")
        )

    async def _resolve_work(
        self, member, cfg: dict, econ: dict, stats: dict, streak: int, boost: dict
    ) -> discord.Embed:
        info = career_info(stats["work_shifts"])
        # `stats` is read after the claim, so work_shifts already counts this
        # shift — the tier before it is the one a shift earlier.
        promoted = info["tier"] > career_info(max(0, stats["work_shifts"] - 1))["tier"]
        pay = h.apply_coin_boost(
            apply_streak(roll_work_pay(random.random(), info["bonus"]), streak), boost
        )
        scene = pick_work_scene(random.random())
        new_bal = await db.add_coins(member.id, pay)

        desc = (
            f"{scene}\nYou earned {self._money(econ, pay)}.\n"
            f"Balance: {self._money(econ, new_bal)}"
        )
        if promoted:
            desc += f"\n\n🎉 **Promoted!** You're now a {info['title']}."
        embed = h.ok(desc, f"💼 {info['title']}")
        nxt = next_career(stats["work_shifts"])
        if nxt:
            embed.set_footer(
                text=f"Next promotion at {nxt['shifts']:,} shifts "
                f"({stats['work_shifts']:,} so far) · "
                f"{self._next_up(cfg, 'work', stats)}"
            )
        else:
            embed.set_footer(text=self._next_up(cfg, "work", stats))
        return embed

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
        await self._send_run(
            ctx, await self.run_activity(ctx.guild, ctx.author, "mine")
        )

    async def _resolve_dig(
        self, member, cfg: dict, econ: dict, stats: dict, streak: int, boost: dict
    ) -> discord.Embed:
        if roll_cave_in(random.random()):
            embed = h.warn(
                "The tunnel collapses behind you — you scramble out empty-handed.",
                "⛏️ Cave-In",
            )
            embed.set_footer(text=self._next_up(cfg, "mine", stats))
            return embed

        pickaxe = pickaxe_info(stats["pickaxe_level"])
        # One vein, rolled ore by ore: a seam of four diamonds is possible and
        # rare, which is the whole reason the size and the rarity are separate
        # rolls rather than one lot of N identical rocks.
        vein = roll_vein(random.random(), pickaxe["vein"])
        haul: dict[str, int] = {}
        for _ in range(vein):
            ore_key = pick_ore(random.random(), pickaxe["luck"])
            haul[ore_key] = haul.get(ore_key, 0) + 1
        for ore_key, qty in haul.items():
            await db.add_item(member.id, ore_key, qty)

        found = ", ".join(
            f"{ORES[k]['emoji']} **{ORES[k]['name']}**" + (f" ×{q}" if q > 1 else "")
            for k, q in haul.items()
        )
        desc = f"You dig up {found}!"
        if vein >= 3:
            desc = f"💥 The seam opens up — {found}!"
        if roll_mine_treasure_key(random.random()):
            await db.add_item(member.id, "treasure_key", 1)
            desc += (
                f"\n{item_catalogue.display('treasure_key')} You also found a "
                "treasure key!"
            )
        desc += "\n\nSell it with `/inventory sell`."
        embed = h.ok(desc, "⛏️ Dig")
        embed.set_footer(text=self._next_up(cfg, "mine", stats))
        return embed

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
                f"Luck is now **{nxt['luck']:.0%}** — fewer rocks, more diamonds."
                + self._vein_line(nxt, prefix="\n"),
                "⛏️ Upgraded",
            )
        )

    @staticmethod
    def _vein_line(pickaxe: dict, prefix: str = "") -> str:
        """The pickaxe's extra-ore perk, or nothing at the bottom two tiers."""
        bonus = pickaxe.get("vein", 0)
        if not bonus:
            return ""
        return f"{prefix}It breaks **+{bonus} extra ore** out of every seam."

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
            f"Seam: **+{pickaxe['vein']} ore** on top of every dig\n"
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
                f"Luck goes to **{nxt['luck']:.0%}**"
                + (
                    f" and every seam gives **+{nxt['vein']} ore**"
                    if nxt["vein"] > pickaxe["vein"]
                    else ""
                )
                + ". Buy it with `/mine upgrade`."
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
        await self._send_dashboard(ctx)

    async def _send_dashboard(self, ctx: commands.Context):
        embed, state = await self.adventure_dashboard(ctx.guild, ctx.author)
        view = AdventureView(self, ctx.author.id, state)
        view.message = await ctx.reply(embed=embed, view=view)

    async def adventure_dashboard(
        self, guild: discord.Guild, member: discord.abc.User
    ) -> tuple[discord.Embed, dict]:
        """The member-facing landing card, plus the state its buttons need.

        Read-only and cheap — two queries (the guild's switches, your stats
        row); the career tier, the pickaxe tier and every charge bucket are
        derived from that same row, so nothing here costs an extra round trip.
        The settings dump stays on /adventure config, and prestige/achievements
        stay on /progress — this card deliberately doesn't restate them.

        Returns the embed and a `{"charges": …, "enabled": …}` dict, because
        AdventureView needs the same numbers the card just rendered and
        recomputing them would let the two disagree.
        """
        cfg = await self._cfg(guild.id)
        stats = await db.get_activity_stats(member.id)
        now = time.time()
        charges = {a: self._charges(cfg, stats, a, now) for a in ACTIVITY_NAMES}
        enabled = {a: bool(cfg[f"{a}_enabled"]) for a in ACTIVITY_NAMES}
        state = {"charges": charges, "enabled": enabled}

        # The headline counts *runs* available, not activities: with charges
        # banked, "4 ready" would badly undersell a bucket holding nine.
        runs = sum(charges[a]["ready"] for a in ACTIVITY_NAMES if enabled[a])
        if runs:
            ready = [a for a in ACTIVITY_NAMES if enabled[a] and charges[a]["ready"]]
            headline = (
                f"**{runs}** run{'s' if runs != 1 else ''} banked and waiting: "
                + ", ".join(f"`/{a}`" for a in ready)
            )
        else:
            soonest = min(
                (charges[a]["next_in"] for a in ACTIVITY_NAMES if enabled[a]),
                default=0,
            )
            headline = (
                f"Nothing ready yet — next one in **{h.fmt_duration(soonest)}**."
                if soonest
                else "Every activity is switched off in this server."
            )
        embed = h.embed(
            "🧭 Adventure",
            f"{headline}\nTap a button to run one — no need to type another "
            "command.",
            h.BLUE,
        )

        # ── The daily streak ─────────────────────────────────────────────────
        # Shown whether or not it's live today, because the number a member
        # wants is "what am I about to lose", not "what did I have".
        streak = stats.get("streak_days") or 0
        today = int(now // 86_400)
        last_day = stats.get("last_day") or 0
        if streak and last_day >= today - 1:
            bonus = streak_multiplier(streak) - 1
            streak_line = f"🔥 **{streak}-day streak** · +{bonus:.0%} on coin rewards"
            if last_day != today:
                streak_line += "\n└ Run anything today to keep it going."
            elif bonus < STREAK_BONUS_CAP:
                streak_line += "\n└ Comes back tomorrow a little bigger."
        else:
            streak_line = (
                "🔥 **No streak** · run anything today to start one — coin "
                f"rewards climb to +{STREAK_BONUS_CAP:.0%}"
            )
        embed.add_field(name="Daily streak", value=streak_line, inline=False)

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
            + (f" · +{pickaxe['vein']} ore" if pickaxe["vein"] else "")
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
            bucket = charges[activity]
            pace = f"every {h.fmt_duration(self._cooldown(cfg, activity))}"
            if bucket["max"] > 1:
                pace += f", up to {bucket['max']} banked"
            embed.add_field(
                name=f"{info['emoji']} {info['command']}",
                value=f"{info['blurb']}\n{self._status_line(cfg, stats, activity)} · "
                f"{pace}\nDone **{count:,}×**",
                inline=True,
            )
        embed.set_footer(
            text="Sell what you find with /inventory · badges on /progress"
        )
        return embed, state

    # ── /adventure collect — the whole bucket, one command ───────────────────
    @adventure.command(
        name="collect",
        description="Run everything you have banked, in one go.",
    )
    async def adventure_collect(self, ctx: commands.Context):
        await self._send_run(ctx, await self.collect_all(ctx.guild, ctx.author))

    # ── /adventure hunt — medium risk ────────────────────────────────────────
    @adventure.command(
        name="hunt",
        description="Hunt for pelts, meat, and rare trophies — a bit riskier.",
    )
    async def hunt(self, ctx: commands.Context):
        await self._send_run(
            ctx, await self.run_activity(ctx.guild, ctx.author, "hunt")
        )

    async def _resolve_hunt(
        self, member, cfg: dict, econ: dict, stats: dict, streak: int, boost: dict
    ) -> discord.Embed:
        # Each catch in the bag is rolled on its own, so a good hunt can turn up
        # a trophy alongside the pelts rather than three of the same thing.
        bag: dict[str, int] = {}
        for _ in range(roll_hunt_bag(random.random())):
            catch_key = pick_hunt_catch(random.random())
            bag[catch_key] = bag.get(catch_key, 0) + 1
        for catch_key, qty in bag.items():
            await db.add_item(member.id, catch_key, qty)

        caught = ", ".join(
            f"{HUNT_CATCHES[k]['emoji']} **{HUNT_CATCHES[k]['name']}**"
            + (f" ×{q}" if q > 1 else "")
            for k, q in bag.items()
        )
        desc = f"You bring back {caught}!"

        if roll_hunt_injury(random.random()):
            fine = hunt_injury_fine(random.random())
            if fine > 0:
                await db.add_coins(member.id, -fine)
                desc += (
                    f"\n🤕 You took a tumble on the way back and paid "
                    f"{self._money(econ, fine)} for a bandage."
                )

        if roll_hunt_padlock(random.random()):
            await db.add_item(member.id, "padlock", 1)
            desc += f"\n{item_catalogue.display('padlock')} You also found a padlock!"

        desc += "\n\nSell loot with `/inventory sell`."
        embed = h.embed("🏹 Hunt", desc, h.BLUE)
        embed.set_footer(text=self._next_up(cfg, "hunt", stats))
        return embed

    # ── /adventure explore — long shot ───────────────────────────────────────
    @adventure.command(
        name="explore",
        description="Explore for a long-shot reward — mostly nothing, occasionally huge.",
    )
    async def explore(self, ctx: commands.Context):
        await self._send_run(
            ctx, await self.run_activity(ctx.guild, ctx.author, "explore")
        )

    async def _resolve_explore(
        self, member, cfg: dict, econ: dict, stats: dict, streak: int, boost: dict
    ) -> discord.Embed:
        outcome = pick_explore_outcome(random.random())
        flavor = EXPLORE_FLAVOR[outcome]

        if outcome == "nothing":
            embed = h.embed("🧭 Explore", flavor, h.GREY)
        elif outcome in ("coins_small", "coins_big"):
            lo, hi = (
                EXPLORE_COINS_SMALL if outcome == "coins_small" else EXPLORE_COINS_BIG
            )
            amount = h.apply_coin_boost(
                apply_streak(roll_coin_amount(random.random(), lo, hi), streak), boost
            )
            new_bal = await db.add_coins(member.id, amount)
            title = "🧭 Big Find!" if outcome == "coins_big" else "🧭 Explore"
            embed = h.ok(
                f"{flavor}\nYou found {self._money(econ, amount)}!\n"
                f"Balance: {self._money(econ, new_bal)}",
                title,
            )
        else:
            await db.add_item(member.id, outcome, 1)
            embed = h.ok(
                f"{flavor}\nYou found {item_catalogue.display(outcome)}!", "🧭 Explore"
            )
        embed.set_footer(text=self._next_up(cfg, "explore", stats))
        return embed

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
                embed=h.err(ACTIVITY_INFO["rob"]["disabled"]), ephemeral=True
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
                    ACTIVITY_INFO["rob"]["wait"].format(wait=h.fmt_duration(retry)),
                    ACTIVITY_INFO["rob"]["wait_title"],
                ),
                ephemeral=True,
            )

        await globalxp.award(ctx.author.id, "activity")
        # /rob resolves on its own (it has a target and four guard rails that
        # no button could ask about), but it is still one of the five, so it
        # keeps the daily streak alive. The multiplier deliberately doesn't
        # touch the payout: a robbery moves someone else's coins, and scaling
        # that up would mint the difference out of nothing.
        await self._touch_streak(
            ctx.author.id, await db.get_activity_stats(ctx.author.id)
        )
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
