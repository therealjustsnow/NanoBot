"""
cogs/progression/cog.py
Achievements, badges, weekly objectives, and prestige — the long-term
progression layer sitting on top of the existing economy features.

All progression here is GLOBAL — achievements, titles, weekly objectives, and
prestige are keyed by user, so a badge earned in one server shows in every
server and can never be re-earned (or re-paid) by joining another.

Zero cross-cog hooks: achievements and weekly objectives are evaluated
LAZILY, purely by reading the lifetime stats other features (fishing, the
casino, activities, the core economy) already persist — through the flat
`utils.db` accessors those features expose to everyone else. No other cog is
modified, and nothing here listens for `/fish`, `/work`, `/coin gamble`, etc.
Evaluation happens whenever a member checks their own progress (`/progress`,
`/progress achievements`) — see cogs/progression/stats.py for the batched
stat-provider registry this reads through.

Achievements are a registry of `AchievementDef`s (cogs/progression/
definitions.py) covering fishing, wealth, casino, and activity milestones.
Crossing a threshold is awarded exactly once — an INSERT OR IGNORE into
`achievements_earned` — and the reward (coins / an inventory item / a cosmetic
title) is only granted when that insert actually lands, so re-evaluating a
member's stats (which happens on every profile/achievements view) can never
double-pay. Rewarded titles become selectable via `/progress title`; the
highest-points earned title is shown by default.

Weekly objectives are three deterministically-drawn picks per (member, ISO
week) — the same `random.Random(f"{user}:{period}")` seeding fishing's daily
quest uses, just keyed by week instead of day. A weekly
objective's "progress" is a lifetime stat minus a baseline snapshotted into
the row the moment it's first created, so a monotonic lifetime counter (fish
caught, work shifts, …) becomes a week-scoped counter with no listener on the
originating command. Claiming is a conditional UPDATE that re-checks
completion in the same statement, so it can't double-pay either. The `week`
column is really just a *period key* — a future "season" mode is a longer-
lived period key (e.g. "2026-summer") slotted into the exact same column and
claim logic, no schema change needed.

Prestige is a deliberately non-destructive endgame sink: nothing is reset or
lost on prestiging. Advancing costs both achievement points (gates progress
behind actually playing the other features) and a steep, scaling coin price
(a genuine sink, unlike a faucet-adjacent reward). The only mechanical payoff
is a small (+5% per rank) bonus applied exclusively to weekly-objective coin
payouts — kept inside this cog, no other cog reads it — plus a cosmetic
"Prestige N" title and a star badge on the profile. Capped at rank 10.
Advancing is a conditional UPDATE guarded on the expected prior rank (the
utils.db.fishing.set_rod_level pattern), serialized per-member with an
asyncio.Lock (the /daily pattern), with the coin debit refunded if the CAS
loses to a race.

Slash command budget: exactly ONE new top-level command — the hybrid group
`/progress` — everything else below is a subcommand (subcommand groups cost
no extra top-level slots either).

──────────────────────────────────────────────────────
Commands
──────────────────────────────────────────────────────
  /progress                        → your profile: achievements/points, badges,
                                      prestige rank, this week's objective status
  /progress achievements [member]  → earned achievements + what's next, by category
  /progress badges [member]        → a compact wall of earned achievement badges
  /progress weekly                 → this week's 3 objectives (auto-claims completed ones)
  /progress title <name>           → pick which earned title is shown on your profile
  /progress prestige                → prestige requirements + your current rank
  /progress prestige confirm        → spend achievement points + coins to prestige
"""

import asyncio
import logging
from typing import Optional

import discord
from discord.ext import commands

from utils import db
from utils import globalxp
from utils import helpers as h
from utils import items as item_catalog

from . import stats as stats_mod
from .constants import (
    PRESTIGE_MAX,
    PRESTIGE_WEEKLY_BONUS_PER_RANK,
    WEEKLY_OBJECTIVE_COUNT,
)
from .definitions import ACHIEVEMENTS, ACHIEVEMENTS_BY_KEY, WEEKLY_POOL
from .helpers import (
    can_prestige,
    earned_titles,
    objective_complete,
    objective_progress,
    period_key,
    pick_weekly_objectives,
    prestige_bonus_multiplier,
    prestige_requirement,
    prestige_title,
    render_bar,
    total_points,
)

log = logging.getLogger("NanoBot.progression")


class Progression(commands.Cog):
    """Achievements, badges, weekly objectives, and prestige — per user,
    earned once and carried into every server."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Serializes the prestige confirm read-check-debit-advance flow per
        # member (the /daily lock pattern) so a double-click can't double-charge.
        self._locks = h.KeyedLocks()

    def _lock(self, user_id: int):
        # Returns an async context manager; existing `async with self._lock(...)`
        # call sites are unchanged. KeyedLocks refcounts holder + waiters and
        # drops an entry when the last interested task releases, so the map no
        # longer grows for the lifetime of the process.
        return self._locks.hold(user_id)

    # ── Reward granting ──────────────────────────────────────────────────────
    async def _grant_reward(self, user_id: int, reward: dict) -> list[str]:
        """Apply a reward dict (only ever called after a reward-earning insert
        actually landed) and return human-readable notes for the embed."""
        notes: list[str] = []
        coins = reward.get("coins")
        if coins:
            await db.add_coins(user_id, coins)
            notes.append(f"{coins:,} coins")
        item_key = reward.get("item")
        if item_key:
            qty = reward.get("qty", 1)
            item_def = item_catalog.get(item_key)
            if item_def is None:
                log.warning(
                    "progression reward references unknown item %r — skipping grant",
                    item_key,
                )
            else:
                await db.add_item(user_id, item_key, qty)
                notes.append(f"{qty}x {item_def.emoji} {item_def.name}")
        title = reward.get("title")
        if title:
            notes.append(f'title "{title}"')
        return notes

    # ── Achievement evaluation ───────────────────────────────────────────────
    async def evaluate_achievements(self, user_id: int):
        """Public entry point for other features (the /profile card) to refresh
        someone's achievements before displaying them. Optional coupling: the
        caller looks the cog up with bot.get_cog and skips this if progression
        isn't loaded — no import, no hard dependency."""
        return await self._evaluate_achievements(user_id)

    async def _evaluate_achievements(self, user_id: int):
        """Award any achievement whose threshold is newly crossed. Returns the
        list of (AchievementDef, reward_notes) newly earned THIS call.

        Batches stat reads: fetches only the stat keys pending achievements
        need (compute_stats itself fetches each underlying source row once),
        so evaluating ~40 defs costs a handful of DB round-trips, not one per
        achievement.
        """
        earned = await db.get_earned_achievements(user_id)
        pending = [a for a in ACHIEVEMENTS if a.key not in earned]
        if not pending:
            return []
        stats = await stats_mod.compute_stats(user_id, [a.stat for a in pending])
        newly = []
        for a in pending:
            if stats.get(a.stat, 0) >= a.threshold:
                if await db.try_award_achievement(user_id, a.key):
                    await globalxp.award(user_id, "achievement")
                    notes = await self._grant_reward(user_id, a.reward)
                    newly.append((a, notes))
        return newly

    # ── Weekly objective evaluation ──────────────────────────────────────────
    async def _get_weekly(self, user_id: int, now: Optional[float] = None):
        """This week's (period's) objective rows, auto-claiming any newly
        completed one. Returns (period, [{"def", "progress", "complete",
        "claimed", "newly_claimed", "payout"}, …])."""
        period = period_key(now)
        picks = pick_weekly_objectives(
            user_id, period, WEEKLY_POOL, WEEKLY_OBJECTIVE_COUNT
        )
        stats = await stats_mod.compute_stats(user_id, [d.stat for d in picks])
        results = []
        for d in picks:
            current = stats.get(d.stat, 0)
            # Baseline is snapshotted at *this* current value only the first
            # time this period's row is created — later calls just fetch it.
            row = await db.get_or_create_objective(
                user_id, period, d.key, d.target, current
            )
            progress = objective_progress(current, row["baseline"], row["target"])
            complete = objective_complete(current, row["baseline"], row["target"])
            claimed = row["claimed"]
            newly_claimed = False
            payout = 0
            if complete and not claimed:
                if await db.try_claim_objective(user_id, period, d.key, current):
                    prog = await db.get_progression(user_id)
                    bonus = prestige_bonus_multiplier(prog["prestige"])
                    payout = round(d.coins * bonus)
                    await db.add_coins(user_id, payout)
                    newly_claimed = True
                    claimed = True
            results.append(
                {
                    "def": d,
                    "progress": progress,
                    "complete": complete,
                    "claimed": claimed,
                    "newly_claimed": newly_claimed,
                    "payout": payout,
                }
            )
        return period, results

    async def _profile_title(self, user_id: int) -> str:
        earned = await db.get_earned_achievements(user_id)
        titles = earned_titles(earned.keys(), ACHIEVEMENTS_BY_KEY)
        progression = await db.get_progression(user_id)
        selected = progression["selected_title"]
        if selected and any(t == selected for t, _pts in titles):
            return selected
        return titles[0][0] if titles else ""

    # ══════════════════════════════════════════════════════════════════════════
    #  /progress  group
    # ══════════════════════════════════════════════════════════════════════════
    @commands.hybrid_group(
        name="progress",
        description="Your achievements, badges, weekly objectives, and prestige rank.",
        invoke_without_command=True,
        fallback="overview",
        extras={
            "category": "🪙 Economy",
            "sub": "🏆 Progress & Profile",
            "short": "Achievements, badges, weekly goals, and prestige",
            "usage": "progress [subcommand]",
            "desc": "Your progression profile: achievements earned across fishing, "
            "wealth, the casino, and activities, this week's objectives, and your "
            "prestige rank. Achievements/objectives are evaluated automatically "
            "whenever you check them — nothing to opt into.",
            "args": [],
            "perms": "None",
            "example": "{prefix}progress\n{prefix}progress achievements\n{prefix}progress weekly",
        },
    )
    @commands.guild_only()
    async def progress(
        self, ctx: commands.Context, member: Optional[discord.Member] = None
    ):
        await self._show_profile(ctx, member)

    async def _show_profile(
        self, ctx: commands.Context, member: Optional[discord.Member]
    ):
        member = member or ctx.author
        if member.bot:
            return await ctx.reply(
                embed=h.err("Bots don't have a progress profile."), ephemeral=True
            )
        user_id = member.id

        newly = []
        if member.id == ctx.author.id:
            # Only lazily-evaluate/award against the viewer's own stats — looking
            # up someone else's profile shouldn't silently award them anything
            # (they still get evaluated for real whenever they check their own).
            newly = await self._evaluate_achievements(user_id)
        earned = await db.get_earned_achievements(user_id)
        points = total_points(earned.keys(), ACHIEVEMENTS_BY_KEY)
        display_title = await self._profile_title(user_id)
        progression = await db.get_progression(user_id)
        rank = progression["prestige"]
        period, weekly_rows = await self._get_weekly(user_id)
        weekly_done = sum(1 for w in weekly_rows if w["claimed"])

        embed = h.embed(f"📈 {member.display_name}'s Progress", color=h.BLUE)
        embed.set_thumbnail(url=member.display_avatar.url)
        header = f"**{len(earned)}/{len(ACHIEVEMENTS)}** earned · **{points:,}** pts"
        if display_title:
            header += f"\n🏷️ {display_title}"
        embed.add_field(name="🏅 Achievements", value=header, inline=True)
        embed.add_field(
            name="📅 Weekly",
            value=f"**{weekly_done}/{len(weekly_rows)}** complete ({period})",
            inline=True,
        )
        prestige_value = f"Rank **{rank}**/{PRESTIGE_MAX}"
        if rank:
            prestige_value += f"\n⭐ {prestige_title(rank)}"
        embed.add_field(name="⭐ Prestige", value=prestige_value, inline=True)
        if newly:
            lines = [
                f"{a.emoji} **{a.name}**" + (f" — {', '.join(notes)}" if notes else "")
                for a, notes in newly
            ]
            embed.add_field(
                name="🎉 New Achievements!", value="\n".join(lines)[:1024], inline=False
            )
        await ctx.reply(embed=embed)

    # ── /progress achievements ───────────────────────────────────────────────
    @progress.command(
        name="achievements", description="See your earned achievements and what's next."
    )
    async def progress_achievements(
        self, ctx: commands.Context, member: Optional[discord.Member] = None
    ):
        member = member or ctx.author
        if member.bot:
            return await ctx.reply(
                embed=h.err("Bots don't earn achievements."), ephemeral=True
            )
        user_id = member.id

        newly = []
        if member.id == ctx.author.id:
            newly = await self._evaluate_achievements(user_id)
        earned = await db.get_earned_achievements(user_id)
        points = total_points(earned.keys(), ACHIEVEMENTS_BY_KEY)

        embed = h.embed(f"🏅 {member.display_name}'s Achievements", color=h.BLUE)
        embed.description = (
            f"**{len(earned)}/{len(ACHIEVEMENTS)}** earned · **{points:,}** points"
        )
        by_category: dict[str, list] = {}
        for a in ACHIEVEMENTS:
            by_category.setdefault(a.category, []).append(a)
        for category, defs in by_category.items():
            cat_earned = [a for a in defs if a.key in earned]
            pending = sorted(
                (a for a in defs if a.key not in earned), key=lambda a: a.threshold
            )
            lines = []
            if cat_earned:
                lines.append("Earned: " + " ".join(a.emoji for a in cat_earned))
            if pending:
                nxt = pending[0]
                lines.append(f"Next: {nxt.emoji} **{nxt.name}** — {nxt.description}")
            else:
                lines.append("All complete! 🎉")
            embed.add_field(
                name=f"{category} ({len(cat_earned)}/{len(defs)})",
                value="\n".join(lines)[:1024],
                inline=False,
            )
        if newly:
            lines = [
                f"{a.emoji} **{a.name}**" + (f" — {', '.join(notes)}" if notes else "")
                for a, notes in newly
            ]
            embed.add_field(
                name="🎉 New Achievements!", value="\n".join(lines)[:1024], inline=False
            )
        await ctx.reply(embed=embed)

    # ── /progress badges ─────────────────────────────────────────────────────
    @progress.command(
        name="badges", description="A compact wall of your earned achievement badges."
    )
    async def progress_badges(
        self, ctx: commands.Context, member: Optional[discord.Member] = None
    ):
        member = member or ctx.author
        if member.bot:
            return await ctx.reply(
                embed=h.err("Bots don't earn badges."), ephemeral=True
            )
        user_id = member.id
        earned = await db.get_earned_achievements(user_id)
        if not earned:
            return await ctx.reply(
                embed=h.info(
                    f"{member.display_name} hasn't earned any badges yet.", "🏅 Badges"
                )
            )
        wall = " ".join(
            ACHIEVEMENTS_BY_KEY[k].emoji for k in earned if k in ACHIEVEMENTS_BY_KEY
        )
        embed = h.embed(f"🏅 {member.display_name}'s Badges", wall[:4000], h.BLUE)
        await ctx.reply(embed=embed)

    # ── /progress weekly ─────────────────────────────────────────────────────
    @progress.command(
        name="weekly", description="This week's objectives and your progress."
    )
    async def progress_weekly(self, ctx: commands.Context):
        user_id = ctx.author.id
        period, rows = await self._get_weekly(user_id)
        embed = h.embed(f"📅 Weekly Objectives — {period}", color=h.BLUE)
        for row in rows:
            d = row["def"]
            bar = render_bar(row["progress"], d.target, 10)
            target_label = (
                f"{d.target:,.0f}" if d.target == int(d.target) else f"{d.target:,.1f}"
            )
            progress_label = (
                f"{row['progress']:,.0f}"
                if row["progress"] == int(row["progress"])
                else f"{row['progress']:,.1f}"
            )
            status = (
                "✅ Claimed" if row["claimed"] else f"{progress_label}/{target_label}"
            )
            value = f"{d.description}\n{bar} {status}"
            if row["newly_claimed"]:
                value += f"\n🎉 **+{row['payout']:,} coins!**"
            embed.add_field(name=f"{d.emoji} {d.name}", value=value, inline=False)
        await ctx.reply(embed=embed)

    # ── /progress title ──────────────────────────────────────────────────────
    @progress.command(
        name="title", description="Pick which earned title is shown on your profile."
    )
    @discord.app_commands.describe(name="Pick one of the titles you've earned")
    async def progress_title(self, ctx: commands.Context, *, name: str):
        user_id = ctx.author.id
        earned = await db.get_earned_achievements(user_id)
        titles = earned_titles(earned.keys(), ACHIEVEMENTS_BY_KEY)
        cleaned = name.strip()
        if cleaned.lower() in ("none", "clear", "default"):
            await db.set_selected_title(user_id, "")
            return await ctx.reply(
                embed=h.ok(
                    "Cleared — your highest-points earned title shows by default.",
                    "🏷️ Title",
                )
            )
        match = next((t for t, _pts in titles if t.lower() == cleaned.lower()), None)
        if match is None:
            if not titles:
                return await ctx.reply(
                    embed=h.err("You haven't earned any titles yet."), ephemeral=True
                )
            available = ", ".join(f"**{t}**" for t, _pts in titles)
            return await ctx.reply(
                embed=h.err(f"You haven't earned that title. Available: {available}"),
                ephemeral=True,
            )
        await db.set_selected_title(user_id, match)
        await ctx.reply(
            embed=h.ok(f"Your displayed title is now **{match}**.", "🏷️ Title")
        )

    @progress_title.autocomplete("name")
    async def _title_ac(self, interaction: discord.Interaction, current: str):
        """Only titles you've actually earned — plus a clear option — so picking
        one never needs a trip to /progress achievements."""
        if not interaction.guild_id:
            return []
        q = (current or "").strip().lower()
        earned = await db.get_earned_achievements(interaction.user.id)
        titles = earned_titles(earned.keys(), ACHIEVEMENTS_BY_KEY)
        choices = [
            discord.app_commands.Choice(
                name=f"🏷️ {title} ({pts} pts)"[:100], value=title
            )
            for title, pts in titles
            if not q or q in title.lower()
        ]
        if not q or q in "none":
            choices.append(
                discord.app_commands.Choice(
                    name="✖️ None — show my highest-points title", value="none"
                )
            )
        return choices[:25]

    # ══════════════════════════════════════════════════════════════════════════
    #  /progress prestige  (nested group — still costs no extra top-level slot)
    # ══════════════════════════════════════════════════════════════════════════
    @progress.group(
        name="prestige",
        invoke_without_command=True,
        fallback="status",
        description="Prestige requirements and your current rank.",
    )
    async def progress_prestige(self, ctx: commands.Context):
        user_id = ctx.author.id
        progression = await db.get_progression(user_id)
        rank = progression["prestige"]
        earned = await db.get_earned_achievements(user_id)
        points = total_points(earned.keys(), ACHIEVEMENTS_BY_KEY)
        balance = await db.get_balance(user_id)

        embed = h.embed("⭐ Prestige", color=h.BLUE)
        rank_line = f"**{rank}**/{PRESTIGE_MAX}"
        if rank:
            rank_line += f" — {prestige_title(rank)}"
        embed.add_field(name="Current Rank", value=rank_line, inline=False)
        if rank >= PRESTIGE_MAX:
            embed.add_field(
                name="Status",
                value="You've reached the maximum prestige rank. 🎉",
                inline=False,
            )
        else:
            req_points, cost = prestige_requirement(rank)
            eligible, reason = can_prestige(rank, points, balance)
            embed.add_field(
                name="Requirements",
                value=f"**{req_points:,}** achievement points (you have {points:,})\n"
                f"**{cost:,}** coins (you have {balance:,})",
                inline=False,
            )
            embed.add_field(
                name="Status",
                value=(
                    "✅ Ready — run `/progress prestige confirm`."
                    if eligible
                    else f"❌ {reason}"
                ),
                inline=False,
            )
        bonus_pct = int(PRESTIGE_WEEKLY_BONUS_PER_RANK * 100 * rank)
        embed.add_field(
            name="Bonus",
            value=f"+{bonus_pct}% weekly objective coin payouts",
            inline=False,
        )
        await ctx.reply(embed=embed)

    # ── /progress prestige confirm ───────────────────────────────────────────
    @progress_prestige.command(
        name="confirm",
        description="Spend achievement points and coins to advance your prestige rank.",
    )
    async def progress_prestige_confirm(self, ctx: commands.Context):
        user_id = ctx.author.id
        async with self._lock(user_id):
            progression = await db.get_progression(user_id)
            rank = progression["prestige"]
            if rank >= PRESTIGE_MAX:
                return await ctx.reply(
                    embed=h.info(
                        "You're already at the maximum prestige rank.", "⭐ Maxed"
                    ),
                    ephemeral=True,
                )
            earned = await db.get_earned_achievements(user_id)
            points = total_points(earned.keys(), ACHIEVEMENTS_BY_KEY)
            req_points, cost = prestige_requirement(rank)
            if points < req_points:
                return await ctx.reply(
                    embed=h.err(
                        f"You need **{req_points:,}** achievement points to prestige "
                        f"(you have {points:,})."
                    ),
                    ephemeral=True,
                )
            if not await db.try_debit_coins(user_id, cost):
                balance = await db.get_balance(user_id)
                return await ctx.reply(
                    embed=h.err(
                        f"Prestiging costs **{cost:,}** coins — you have {balance:,}."
                    ),
                    ephemeral=True,
                )
            advanced = await db.try_advance_prestige(user_id, rank + 1, expected=rank)
            if not advanced:
                # A second racing confirm already advanced it first — refund.
                await db.add_coins(user_id, cost)
                return await ctx.reply(
                    embed=h.warn("That prestige already went through.", "⭐ Prestige"),
                    ephemeral=True,
                )
            new_bonus = int(PRESTIGE_WEEKLY_BONUS_PER_RANK * 100 * (rank + 1))
            await ctx.reply(
                embed=h.ok(
                    f"You are now **{prestige_title(rank + 1)}** (rank {rank + 1}). "
                    f"Weekly objective payouts are now +{new_bonus}%.",
                    "⭐ Prestige!",
                )
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(Progression(bot))
