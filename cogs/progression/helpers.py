"""cogs/progression/helpers.py — pure, Discord-free helpers covered by
tests/test_progression_helpers.py.

Every random choice here is deterministically seeded (the fishing
generate_quest / random.Random(f"{user}:{...}") pattern), and every
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
    TROPHY_TIER_POINTS,
)
from .definitions import CATEGORY_ACCENTS, STAT_TOPPERS


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


def count_label(n: int, word: str, plural: str | None = None) -> str:
    """ "**1** shift" / "**2** shifts" — the profile card counts a lot of things
    and "1 shifts" reads badly on every one of them."""
    return f"**{n:,}** {word if n == 1 else (plural or word + 's')}"


def pick_weekly_objectives(user_id: int, period: str, pool: list, count: int) -> list:
    """Deterministically pick `count` distinct objective defs from `pool`.

    Seeded on (user_id, period) — not the guild — so a member's week is the
    same set of objectives everywhere, matching the one global row per
    objective. Callers regenerate on demand instead of persisting the
    selection itself (only the rows' baseline/claimed state is stored).
    """
    rng = random.Random(f"{int(user_id)}:{period}")
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
    """(points_required, coin_cost) to advance from `rank` to `rank + 1`.

    Points stay linear (they come from achievements, which the cast cooldown
    doesn't accelerate); the coin cost is quadratic — see PRESTIGE_COST_BASE.
    """
    return PRESTIGE_POINTS_BASE * (rank + 1), PRESTIGE_COST_BASE * (rank + 1) ** 2


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


# ── Trophy case ──────────────────────────────────────────────────────────────────
def trophy_tier(points: int) -> int:
    """Which trophy an achievement worth `points` stands as (0-3).

    The renderer knows nothing about achievements, so the mapping from what
    something is worth to how impressive it looks lives here, next to the
    registry it reads.
    """
    return sum(1 for threshold in TROPHY_TIER_POINTS if points >= threshold)


def category_accent(category: str) -> str:
    return CATEGORY_ACCENTS.get(category, "#5865F2")


def trophy_topper(stat: str, step: int = 0) -> str:
    """Which figure stands on the trophy for rung `step` of `stat`'s ladder.

    The subject *and* which rung of it: catching ten fish and catching a
    thousand are not one story told twice, so the first carries a minnow and
    the last a marlin. `step` is the achievement's position among the ones
    measuring that stat, ordered by threshold (see `trophy_groups`).

    Nothing here can fail to render: an unmapped stat falls back to a plain
    star, and a rung past the end of a ladder reuses its last figure — so
    adding an achievement is a drawing decision, never a crash.
    """
    ladder = STAT_TOPPERS.get(stat)
    if not ladder:
        return "star"
    return ladder[min(max(0, int(step)), len(ladder) - 1)]


def trophy_groups(achievements, earned_keys) -> list[dict]:
    """The registry as the shelves of a trophy case, in registry order.

    Pure: takes the defs and the set of keys already earned, hands back the
    plain dicts `utils.trophy_card` draws. Locked achievements are included —
    a case is as much the list of what is still out there as a record of what
    isn't, and it means a brand-new account gets a full case rather than an
    empty box. The category label drops its emoji: the card's font has no
    colour emoji, and a category renders as a heading rather than a chat line.
    """
    earned = set(earned_keys or ())
    # Which rung of its own stat each achievement is: the figure ladders are
    # ordered by threshold, and the registry is written in that order but isn't
    # guaranteed to stay that way, so derive it rather than trusting it.
    rungs: dict[str, list[str]] = {}
    for a in sorted(achievements, key=lambda a: (a.stat, a.threshold)):
        rungs.setdefault(a.stat, []).append(a.key)

    groups: dict[str, dict] = {}
    for a in achievements:
        group = groups.setdefault(
            a.category,
            {"label": a.category.split(" ", 1)[-1], "items": []},
        )
        group["items"].append(
            {
                "name": a.name,
                "tier": trophy_tier(a.points),
                "topper": trophy_topper(a.stat, rungs[a.stat].index(a.key)),
                "accent": category_accent(a.category),
                "earned": a.key in earned,
            }
        )
    return list(groups.values())


# ── Display ──────────────────────────────────────────────────────────────────────
def render_bar(into: float, span: float, width: int = 12) -> str:
    """Render a unicode progress bar for `into`/`span`."""
    if span <= 0:
        filled = width
    else:
        filled = int(width * into / span)
    filled = max(0, min(width, filled))
    return "█" * filled + "░" * (width - filled)
