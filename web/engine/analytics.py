"""
web/engine/analytics.py
Numbers worth a chart, and only those.

The temptation with an analytics page is to plot everything that can be counted.
Most of it answers no question an admin actually has, and a wall of charts
nobody reads is worse than three that get looked at — so every series here is
tied to a decision:

  *Is the economy inflating?* — coins held, and the split between what the
  faucets can mint per day and what the sinks can absorb.
  *Is anybody playing?* — how many distinct members have used each system, and
  how recently the whole guild last did anything.
  *Where is the money?* — the distribution across members, because a mean is a
  bad summary of a curve with a long tail.
  *Is moderation load rising?* — warnings over time.

**What this cannot do, and says so.** NanoBot stores *lifetime totals*, not a
time series: there is no daily snapshot of anybody's balance, and inventing one
retroactively is impossible. So the time-ranged charts here are the ones with a
real timestamp column behind them (`shop_purchases.bought_at`, warnings,
`fishing_catches.caught_at`), and everything else is reported as a current
distribution rather than a fake trend line. A chart that implies history the
database doesn't have is a lie with axes on it.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Iterable, Optional

from cogs.activities.helpers import adventure_coins_per_day
from cogs.economy.helpers import coins_per_hour
from utils import db
from utils.db._core import _read_conn, rows_for_users
from utils.db.activities import _ACTIVITY_COLUMNS as ACTIVITY_COLUMNS

RANGES = {
    "7d": {"label": "7 days", "days": 7, "bucket": 86400, "buckets": 7},
    "30d": {"label": "30 days", "days": 30, "bucket": 86400, "buckets": 30},
    "90d": {"label": "90 days", "days": 90, "bucket": 7 * 86400, "buckets": 13},
}
DEFAULT_RANGE = "30d"


def _range(key: Optional[str]) -> dict:
    return RANGES.get(key or DEFAULT_RANGE, RANGES[DEFAULT_RANGE])


def _buckets(spec: dict, now: float) -> list[dict]:
    """Empty buckets, newest last, so a series with gaps still draws a line."""
    size = spec["bucket"]
    count = spec["buckets"]
    start = (int(now) // size) * size - size * (count - 1)
    return [{"t": start + i * size, "value": 0} for i in range(count)]


def _fill(
    series: list[dict], rows: Iterable[tuple], spec: dict, now: float
) -> list[dict]:
    """Drop (timestamp, amount) rows into the bucket they belong to."""
    size = spec["bucket"]
    first = series[0]["t"] if series else 0
    index = {b["t"]: b for b in series}
    for stamp, amount in rows:
        slot = (int(stamp) // size) * size
        if slot < first:
            continue
        bucket = index.get(slot)
        if bucket is not None:
            bucket["value"] += amount
    return series


async def _timeseries(sql: str, params: tuple, spec: dict, now: float) -> list[dict]:
    """One time series, bucketed. Read-only, on the read connection."""
    series = _buckets(spec, now)
    since = series[0]["t"] if series else 0
    async with _read_conn().execute(sql, (*params, since)) as cur:
        rows = [(r[0], r[1]) for r in await cur.fetchall()]
    return _fill(series, rows, spec, now)


async def overview(
    guild_id: int, member_ids: list[int], range_key: str = DEFAULT_RANGE
) -> dict:
    """Everything the analytics page draws, in one read."""
    spec = _range(range_key)
    now = time.time()
    econ = await db.get_econ_config(guild_id)

    wallets = await db.get_econ_leaderboard_for(member_ids)
    coins = sorted((row["coins"] for row in wallets), reverse=True)
    total = sum(coins)
    holders = len(coins)

    anglers = await db.get_fishing_leaderboard_for(member_ids)
    casino_players = await db.get_casino_leaderboard_for(member_ids)
    contributors = await db.get_contrib_leaderboard_for(member_ids)

    return {
        "range": {"key": range_key if range_key in RANGES else DEFAULT_RANGE, **spec},
        "ranges": [{"key": k, "label": v["label"]} for k, v in RANGES.items()],
        "currency": {"name": econ["currency_name"], "emoji": econ["currency_emoji"]},
        "generated_at": now,
        "economy": {
            "total_coins": total,
            "holders": holders,
            "members": len(member_ids),
            "median": coins[holders // 2] if holders else 0,
            "mean": round(total / holders) if holders else 0,
            "top_share": (
                round(sum(coins[: max(1, holders // 10)]) / total, 4) if total else 0
            ),
            # Buckets rather than a histogram of raw values: "how many people
            # have roughly this much" is the readable version of a long tail.
            "distribution": _distribution(coins),
            "faucets": await _faucets(guild_id),
        },
        "participation": {
            "series": [
                {
                    "key": "wallets",
                    "label": "Have coins",
                    "value": holders,
                    "emoji": "🪙",
                },
                {
                    "key": "fishing",
                    "label": "Have fished",
                    "value": len(anglers),
                    "emoji": "🎣",
                },
                {
                    "key": "casino",
                    "label": "Have gambled",
                    "value": len(casino_players),
                    "emoji": "🎰",
                },
                {
                    "key": "coop",
                    "label": "Have co-oped",
                    "value": len(contributors),
                    "emoji": "🤝",
                },
                {
                    "key": "adventure",
                    "label": "Have adventured",
                    "value": await _adventurers(member_ids),
                    "emoji": "🧭",
                },
            ],
            "of": len(member_ids),
        },
        "charts": {
            "shop_spend": await _shop_spend(guild_id, spec, now),
            "catches": await _catches(member_ids, spec, now),
            "warnings": await _warnings(guild_id, spec, now),
        },
        "moderation": await _moderation(guild_id),
        "notes": [
            "NanoBot stores lifetime totals, not daily snapshots — so wealth is "
            "shown as it is right now, not as a trend. The charts below are the "
            "ones with a real timestamp behind them.",
        ],
    }


def _distribution(coins: list[int]) -> list[dict]:
    """Wealth in readable bands, not a raw histogram."""
    bands = [
        (0, 100, "Under 100"),
        (100, 1_000, "100 – 1K"),
        (1_000, 10_000, "1K – 10K"),
        (10_000, 100_000, "10K – 100K"),
        (100_000, 1_000_000, "100K – 1M"),
        (1_000_000, None, "1M+"),
    ]
    out = []
    for low, high, label in bands:
        count = sum(1 for c in coins if c >= low and (high is None or c < high))
        out.append({"label": label, "value": count})
    return out


async def _faucets(guild_id: int) -> list[dict]:
    """What each source could mint for one active member in a day.

    Recomputed from the same balance model `tests/test_economy_balance.py`
    checks, so this agrees with the game rather than with a number typed here.
    """
    rewards = await db.get_reward_amounts()
    from cogs.economy.constants import REWARD_DEFAULTS

    daily = rewards.get("daily", REWARD_DEFAULTS["daily"])
    return [
        {
            "key": "fishing",
            "label": "Fishing (1h)",
            "value": round(coins_per_hour()),
            "emoji": "🎣",
        },
        {
            "key": "adventure",
            "label": "Adventure (a day)",
            "value": round(adventure_coins_per_day()),
            "emoji": "🧭",
        },
        {
            "key": "daily",
            "label": "/daily (average)",
            "value": daily * 2,
            "emoji": "🎁",
        },
    ]


# The lifetime-count columns, from the accessor's own whitelist rather than
# typed out here — work's is `work_shifts`, not `work_count`, and guessing that
# is exactly the kind of mistake a chart quietly renders as a zero.
_RUN_COLUMNS = " + ".join(
    ACTIVITY_COLUMNS[name][1] for name in ("work", "mine", "hunt", "explore")
)


async def _adventurers(member_ids: list[int]) -> int:
    """How many members have run any activity at least once."""
    rows = await rows_for_users(
        f"SELECT user_id, ({_RUN_COLUMNS}) AS runs FROM activities_stats",
        member_ids,
        positive_col=f"({_RUN_COLUMNS})",
    )
    return len(rows)


async def _shop_spend(guild_id: int, spec: dict, now: float) -> dict:
    """Coins leaving circulation through this server's shop — a real sink."""
    series = await _timeseries(
        "SELECT bought_at, price FROM shop_purchases WHERE guild_id=? AND bought_at >= ?",
        (str(guild_id),),
        spec,
        now,
    )
    return {
        "label": "Coins spent in your shop",
        "unit": "coins",
        "points": series,
        "total": sum(p["value"] for p in series),
        "why": "The one sink you control. If nothing is being bought, prices "
        "are probably wrong for what people actually earn.",
    }


async def _catches(member_ids: list[int], spec: dict, now: float) -> dict:
    """Fishing activity, from the one economy table with a timestamp on it.

    `rows_for_users` builds its own WHERE (it is what chunks the member id
    list), so the range filter happens here rather than in the SQL — the row
    count is bounded by what people are carrying, not by history.
    """
    series = _buckets(spec, now)
    since = series[0]["t"] if series else 0
    rows = await rows_for_users(
        "SELECT user_id, caught_at FROM fishing_catches", member_ids
    )
    series = _fill(
        series,
        [(r["caught_at"], 1) for r in rows if (r["caught_at"] or 0) >= since],
        spec,
        now,
    )
    return {
        "label": "Fish caught (still in bags)",
        "unit": "catches",
        "points": series,
        "total": sum(p["value"] for p in series),
        "why": "Only unsold catches carry a timestamp, so this undercounts — "
        "treat it as a floor on activity, not a total.",
    }


async def _warnings(guild_id: int, spec: dict, now: float) -> dict:
    """Warnings over time.

    `warnings.created_at` is an ISO-8601 *string*, not an epoch — so the range
    filter is a lexical comparison (which is exactly right for ISO-8601) and the
    parse back to a timestamp happens here. A row that predates the format, or
    was written by hand, is skipped rather than crashing the page.
    """
    series = _buckets(spec, now)
    since = series[0]["t"] if series else 0
    cutoff = datetime.fromtimestamp(since, tz=timezone.utc).isoformat()
    async with _read_conn().execute(
        "SELECT created_at FROM warnings WHERE guild_id=? AND created_at >= ?",
        (str(guild_id), cutoff),
    ) as cur:
        rows = await cur.fetchall()
    stamps = []
    for row in rows:
        try:
            stamps.append((datetime.fromisoformat(row["created_at"]).timestamp(), 1))
        except (TypeError, ValueError):
            continue
    series = _fill(series, stamps, spec, now)
    return {
        "label": "Warnings issued",
        "unit": "warnings",
        "points": series,
        "total": sum(p["value"] for p in series),
        "why": "A rising line usually means a rule needs saying out loud rather "
        "than enforcing one member at a time.",
    }


async def _moderation(guild_id: int) -> dict:
    """Standing moderation state — counts, not a trend."""
    async with _read_conn().execute(
        "SELECT COUNT(*) FROM warnings WHERE guild_id=?", (str(guild_id),)
    ) as cur:
        warnings = (await cur.fetchone())[0]
    async with _read_conn().execute(
        "SELECT COUNT(DISTINCT user_id) FROM warnings WHERE guild_id=?",
        (str(guild_id),),
    ) as cur:
        warned_members = (await cur.fetchone())[0]
    return {
        "warnings": warnings,
        "warned_members": warned_members,
        "open_tickets": await _open_tickets(guild_id),
        "pending_rewards": await db.count_pending_purchases(guild_id),
    }


async def _open_tickets(guild_id: int) -> int:
    """How many tickets are open right now.

    `db.count_open_tickets` counts *one member's* open tickets (it backs the
    per-user cap); the guild-wide number has no accessor, so it is read here
    rather than adding one for a single read-only chart.
    """
    async with _read_conn().execute(
        "SELECT COUNT(*) FROM tickets WHERE guild_id=? AND status='open'",
        (str(guild_id),),
    ) as cur:
        return (await cur.fetchone())[0]
