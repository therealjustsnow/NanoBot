"""
web/engine/progression.py
Achievements, titles, weekly objectives and prestige.

Everything here is *derived* — the whole point of the progression system is that
it has no cross-cog hooks: achievements are evaluated lazily from the lifetime
stats other features already persist, read through the `STAT_PROVIDERS`
registry. This module reads the same way, so a page load can't award something a
`/progress` run wouldn't.

The one thing it does write is the same lazy award the cog does, and only for a
member looking at their *own* progress — `try_award_achievement` is an
INSERT OR IGNORE whose rowcount gates the reward, so re-evaluating on every page
load can never double-pay.

What the web adds is the shape an embed can't hold: every achievement with how
far along it is, so "closest to earning" is a real, sortable thing rather than a
list you scan.
"""

from __future__ import annotations

from typing import Optional

from cogs.progression.constants import WEEKLY_OBJECTIVE_COUNT
from cogs.progression.definitions import (
    ACHIEVEMENTS,
    ACHIEVEMENTS_BY_KEY,
    WEEKLY_POOL,
)
from cogs.progression.helpers import (
    can_prestige,
    category_accent,
    earned_titles,
    objective_complete,
    objective_progress,
    period_key,
    pick_weekly_objectives,
    prestige_bonus_multiplier,
    prestige_requirement,
    prestige_title,
    total_points,
    trophy_tier,
)
from cogs.progression.stats import compute_stats
from utils import db, globalxp
from utils import items as item_catalog

from .fishing import PlayError


def _achievement_payload(spec, stats: dict, earned: dict) -> dict:
    """One achievement, with how close it is."""
    value = float(stats.get(spec.stat, 0) or 0)
    threshold = float(spec.threshold)
    row = earned.get(spec.key)
    return {
        "key": spec.key,
        "name": spec.name,
        "emoji": spec.emoji,
        "description": spec.description,
        "category": spec.category,
        "accent": category_accent(spec.category),
        "points": spec.points,
        "stat": spec.stat,
        "threshold": threshold,
        "value": min(value, threshold),
        "raw_value": value,
        "progress": min(1.0, value / threshold) if threshold else 1.0,
        "earned": row is not None,
        "earned_at": (
            row
            if isinstance(row, (int, float))
            else (row or {}).get("earned_at") if row else None
        ),
        "reward": dict(spec.reward) if spec.reward else None,
    }


async def _award_new(user_id: int, stats: dict, earned: dict) -> list[dict]:
    """Award anything newly qualified — the cog's own lazy rule, once each.

    The INSERT OR IGNORE's rowcount is what gates the reward, so this is safe to
    run on every page load: an achievement already in the table pays nothing.
    """
    fresh = []
    for spec in ACHIEVEMENTS:
        if spec.key in earned:
            continue
        if float(stats.get(spec.stat, 0) or 0) < float(spec.threshold):
            continue
        if not await db.try_award_achievement(user_id, spec.key):
            continue
        await globalxp.award(user_id, "achievement")
        # Same reward dict the cog's `_grant_reward` reads — `qty`, not
        # `item_qty`, and an unknown item key is skipped rather than crashing
        # the page somebody just loaded.
        reward = spec.reward or {}
        if reward.get("coins"):
            await db.add_coins(user_id, int(reward["coins"]))
        item_key = reward.get("item")
        if item_key and item_catalog.get(item_key) is not None:
            await db.add_item(user_id, item_key, int(reward.get("qty", 1)))
        fresh.append(
            {
                "key": spec.key,
                "name": spec.name,
                "emoji": spec.emoji,
                "points": spec.points,
            }
        )
    return fresh


async def state(
    user_id: int, guild_id: int, *, viewer_id: Optional[int] = None
) -> dict:
    """The whole progression picture for one member.

    `viewer_id` decides whether anything is *awarded*: looking someone else up
    must never silently grant them an achievement, which is the rule every view
    in the cog holds.
    """
    is_self = viewer_id is None or int(viewer_id) == int(user_id)
    stats = await compute_stats(user_id)
    earned = await db.get_earned_achievements(user_id)

    newly = []
    if is_self:
        newly = await _award_new(user_id, stats, earned)
        if newly:
            earned = await db.get_earned_achievements(user_id)
            stats = await compute_stats(user_id)

    rows = [_achievement_payload(spec, stats, earned) for spec in ACHIEVEMENTS]
    points = total_points(earned.keys(), ACHIEVEMENTS_BY_KEY)
    progression = await db.get_progression(user_id)
    rank = progression["prestige"]
    balance = await db.get_balance(user_id)
    need_points, need_coins = prestige_requirement(rank)
    ok, reason = can_prestige(rank, points, balance)

    gxp = await db.get_global_xp(user_id)
    level, into, need = globalxp.level_progress(gxp)
    server_xp = await db.get_xp(guild_id, user_id)

    categories = []
    for category in sorted({spec.category for spec in ACHIEVEMENTS}):
        group = [r for r in rows if r["category"] == category]
        categories.append(
            {
                "key": category,
                "accent": category_accent(category),
                "earned": sum(1 for r in group if r["earned"]),
                "total": len(group),
                "points": sum(r["points"] for r in group if r["earned"]),
            }
        )

    titles = earned_titles(earned.keys(), ACHIEVEMENTS_BY_KEY)
    return {
        "achievements": rows,
        "categories": categories,
        "newly_earned": newly,
        "points": points,
        "tier": trophy_tier(points),
        "earned_count": len(earned),
        "total_count": len(ACHIEVEMENTS),
        "titles": [
            {
                "name": name,
                "points": pts,
                "selected": name == progression["selected_title"],
            }
            for name, pts in titles
        ],
        "selected_title": progression["selected_title"],
        "prestige": {
            "rank": rank,
            "title": prestige_title(rank) if rank else None,
            "multiplier": prestige_bonus_multiplier(rank),
            "next": {
                "points": need_points,
                "coins": need_coins,
                "have_points": points,
                "have_coins": balance,
                "ready": ok,
                "reason": reason,
            },
        },
        "levels": {
            "global": {"level": level, "into": into, "need": need, "xp": gxp},
            "server": {"xp": server_xp},
        },
        "weekly": await weekly(user_id),
        # The next three within reach — the "you're nearly there" list, which is
        # the one thing a long achievement list is bad at surfacing.
        "closest": sorted(
            [r for r in rows if not r["earned"] and r["progress"] > 0],
            key=lambda r: -r["progress"],
        )[:3],
        "stats": stats,
    }


async def weekly(user_id: int) -> dict:
    """This week's objectives, with progress measured from the snapshot baseline.

    Progress is `current stat − a baseline taken when the week's rows were
    created`, which is what turns lifetime counters into weekly ones with no
    hooks anywhere. Claiming re-verifies completion inside its own WHERE clause.
    """
    period = period_key()
    stats = await compute_stats(user_id)
    chosen = pick_weekly_objectives(
        user_id, period, WEEKLY_POOL, WEEKLY_OBJECTIVE_COUNT
    )
    progression = await db.get_progression(user_id)
    multiplier = prestige_bonus_multiplier(progression["prestige"])

    rows = []
    claimed_now = []
    for spec in chosen:
        current = float(stats.get(spec.stat, 0) or 0)
        # Argument order is the accessor's: target, then the value to snapshot
        # as the baseline — and the baseline is only taken the first time this
        # period's row is created.
        row = await db.get_or_create_objective(
            user_id, period, spec.key, float(spec.target), current
        )
        done = objective_complete(current, row["baseline"], row["target"])
        if done and not row["claimed"]:
            if await db.try_claim_objective(user_id, period, spec.key, current):
                reward = round(spec.coins * multiplier)
                await db.add_coins(user_id, reward)
                await globalxp.award(user_id, "objective")
                claimed_now.append({"name": spec.name, "coins": reward})
                row = {**row, "claimed": 1}
        rows.append(
            {
                "key": spec.key,
                "name": spec.name,
                "description": spec.description,
                "emoji": spec.emoji,
                "target": spec.target,
                # Absolute progress toward the target, not a fraction — the
                # helper clamps to [0, target] so a spendable stat can't read
                # as negative.
                "progress": round(
                    objective_progress(current, row["baseline"], row["target"]), 2
                ),
                "done": done,
                "claimed": bool(row["claimed"]),
                "reward": round(spec.coins * multiplier),
            }
        )
    return {
        "period": period,
        "objectives": rows,
        "claimed_now": claimed_now,
        "multiplier": multiplier,
    }


async def set_title(user_id: int, title: str) -> dict:
    """Wear a title you've earned."""
    earned = await db.get_earned_achievements(user_id)
    available = {
        name for name, _pts in earned_titles(earned.keys(), ACHIEVEMENTS_BY_KEY)
    }
    if title and title not in available:
        raise PlayError("You haven't earned that title.", code="not_earned")
    await db.set_selected_title(user_id, title or "")
    return {"title": title or None}


async def prestige(user_id: int, guild_id: int) -> dict:
    """Advance a prestige rank.

    The compare-and-swap is `try_advance_prestige(… expected=rank)`, and the
    coins are refunded if it loses — the same order the cog uses, because the
    alternative is charging somebody for a rank they didn't get.
    """
    earned = await db.get_earned_achievements(user_id)
    points = total_points(earned.keys(), ACHIEVEMENTS_BY_KEY)
    progression = await db.get_progression(user_id)
    rank = progression["prestige"]
    balance = await db.get_balance(user_id)

    ok, reason = can_prestige(rank, points, balance)
    if not ok:
        raise PlayError(reason, code="not_ready")

    _need_points, need_coins = prestige_requirement(rank)
    if not await db.try_debit_coins(user_id, need_coins):
        raise PlayError("You can't afford that any more.", code="broke")
    if not await db.try_advance_prestige(user_id, rank + 1, expected=rank):
        await db.add_coins(user_id, need_coins)
        raise PlayError("That prestige already went through.", code="race")

    await globalxp.award(user_id, "prestige")
    return {
        "rank": rank + 1,
        "title": prestige_title(rank + 1),
        "spent": need_coins,
        "multiplier": prestige_bonus_multiplier(rank + 1),
        "balance": await db.get_balance(user_id),
    }
