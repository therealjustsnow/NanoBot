"""cogs/progression/helpers.py — pure, Discord-free helpers covered by
tests/test_progression_helpers.py.

Every random choice here is deterministically seeded (the fishing
generate_quest / random.Random(f"{guild}:{user}:{...}") pattern), and every
stat-progress computation takes explicit numbers in, numbers out — no DB, no
Discord.
"""

import random
import time
from datetime import datetime, timezone

from .constants import (
    PRESTIGE_COST_BASE,
    PRESTIGE_MAX,
    PRESTIGE_POINTS_BASE,
    PRESTIGE_TITLES,
    PRESTIGE_WEEKLY_BONUS_PER_RANK,
)


# ── Period keys (weekly today; a season is just a longer-lived period key) ──────
def period_key(now: float | None = None) -> str:
    """The current ISO-week period key, e.g. "2026-W30".

    A future "season" mode slots into the exact same `week`/`period` column —
    a seasonal objective set just uses a longer-lived key like "2026-summer"
    instead of an ISO week; nothing about the storage or claim logic changes.
    """
    ts = datetime.fromtimestamp(
        now if now is not None else time.time(), tz=timezone.utc
    )
    iso_year, iso_week, _iso_weekday = ts.isocalendar()
    return f"{iso_year}-W{iso_week:02d}"


def pick_weekly_objectives(
    guild_id: int, user_id: int, period: str, pool: list, count: int
) -> list:
    """Deterministically pick `count` distinct objective defs from `pool`.

    Seeded on (guild_id, user_id, period), so the same trio always yields the
    same picks for a given member's week — callers regenerate on demand
    instead of persisting the selection itself (only the resulting rows are
    persisted, for their baseline/claimed state).
    """
    rng = random.Random(f"{int(guild_id)}:{int(user_id)}:{period}")
    return rng.sample(pool, min(count, len(pool)))


# ── Objective progress math ──────────────────────────────────────────────────────
def objective_progress(current_value: float, baseline: float, target: float) -> float:
    """Progress toward `target`, clamped to [0, target].

    A lifetime stat can only be read, never reset — subtracting the baseline
    turns it into a period-scoped delta. Clamping the lower bound to 0 handles
    a non-monotonic stat (e.g. a coin balance that can also be spent) so a net
    decrease during the period never shows as negative progress.
    """
    delta = current_value - baseline
    return max(0.0, min(float(target), delta))


def objective_complete(current_value: float, baseline: float, target: float) -> bool:
    return (current_value - baseline) >= target


# ── Achievement scoring ──────────────────────────────────────────────────────────
def total_points(earned_keys, achievements: dict) -> int:
    """Sum of `.points` for every earned key that's still a known achievement
    (an achievement removed from the registry silently stops counting)."""
    return sum(achievements[k].points for k in earned_keys if k in achievements)


def earned_titles(earned_keys, achievements: dict) -> list[tuple[str, int]]:
    """[(title, points), ...] for every earned achievement carrying a cosmetic
    title reward, highest-points first (that's the default displayed title
    when the member hasn't picked one via /progress title)."""
    titles = []
    seen = set()
    for key in earned_keys:
        d = achievements.get(key)
        if d is None:
            continue
        title = d.reward.get("title")
        if title and title not in seen:
            seen.add(title)
            titles.append((title, d.points))
    titles.sort(key=lambda t: -t[1])
    return titles


# ── Prestige ─────────────────────────────────────────────────────────────────────
def prestige_requirement(rank: int) -> tuple[int, int]:
    """(points_required, coin_cost) to advance from `rank` to `rank + 1`."""
    return PRESTIGE_POINTS_BASE * (rank + 1), PRESTIGE_COST_BASE * (rank + 1)


def prestige_bonus_multiplier(rank: int) -> float:
    """+5% (PRESTIGE_WEEKLY_BONUS_PER_RANK) per rank — applied only to weekly
    objective coin payouts, nowhere else."""
    return 1.0 + PRESTIGE_WEEKLY_BONUS_PER_RANK * max(0, rank)


def prestige_title(rank: int) -> str:
    return PRESTIGE_TITLES.get(rank, f"Prestige {rank}")


def can_prestige(rank: int, points: int, balance: int) -> tuple[bool, str]:
    """Pure eligibility check (no DB/debit) — the cog still performs the real
    debit + CAS advance; this is what /progress prestige shows as status."""
    if rank >= PRESTIGE_MAX:
        return False, "You're already at the maximum prestige rank."
    req_points, cost = prestige_requirement(rank)
    if points < req_points:
        return (
            False,
            f"Needs **{req_points:,}** achievement points (you have {points:,}).",
        )
    if balance < cost:
        return False, f"Needs **{cost:,}** coins (you have {balance:,})."
    return True, ""


# ── Display ──────────────────────────────────────────────────────────────────────
def render_bar(into: float, span: float, width: int = 12) -> str:
    """Render a unicode progress bar for `into`/`span`."""
    if span <= 0:
        filled = width
    else:
        filled = int(width * into / span)
    filled = max(0, min(width, filled))
    return "█" * filled + "░" * (width - filled)
