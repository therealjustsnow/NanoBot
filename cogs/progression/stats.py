"""cogs/progression/stats.py — the stat provider registry.

This is the ONLY place progression reads other features' data, and it goes
through the same flat `utils.db` accessors those features already expose to
everyone else — no other cog is modified, no listener spies on `/fish`,
`/work`, `/coin gamble`, etc. Achievements and weekly objectives are evaluated
lazily, entirely from whatever these providers report right now.

`STAT_PROVIDERS` is the documented per-key contract: one async
``fn(guild_id, user_id) -> int | float`` per stat key. Evaluating ~30
achievements one provider call at a time would mean ~30 DB round-trips even
though most of them read the same handful of source rows (e.g. five fishing
achievements all come from one `fishing_stats` row) — so the actual bulk
evaluator, `compute_stats`, fetches each *source* at most once per call and
then derives every requested stat key from that cached read.
"""

from typing import Awaitable, Callable

from utils import db

# Each source is awaited at most once per compute_stats() call, no matter how
# many stat keys it feeds. Keep this in sync with _EXTRACTORS below.
_SOURCES: dict[str, Callable[[int, int], Awaitable]] = {
    "fisher": db.get_fisher,  # dict: caught/earned/best_weight/streak_days/xp/…
    "dex": db.get_species_counts,  # dict: fish_key -> lifetime caught count
    "casino": db.get_casino_stats,  # dict: games/wagered/won/biggest_win/streak/best_streak
    "activity": db.get_activity_stats,  # dict: work_shifts/mine_count/…/pickaxe_level
    "inventory": db.get_inventory,  # list[{item_key, qty}]
    "balance": db.get_balance,  # int
    "contribution": db.get_contribution,  # int
}

# stat key -> (source name, extractor(raw_source_value) -> int | float)
_EXTRACTORS: dict[str, tuple[str, Callable]] = {
    # Fishing
    "fish_caught": ("fisher", lambda r: r["caught"]),
    "fish_earned": ("fisher", lambda r: r["earned"]),
    "fish_best_weight": ("fisher", lambda r: r["best_weight"]),
    "fish_streak": ("fisher", lambda r: r["streak_days"]),
    "fish_xp": ("fisher", lambda r: r["xp"]),
    "fish_casts": ("fisher", lambda r: r["casts"]),
    "fish_dex_size": ("dex", lambda r: len(r)),
    # Wealth
    "balance": ("balance", lambda r: r),
    "contribution": ("contribution", lambda r: r),
    # Casino
    "casino_games": ("casino", lambda r: r["games"]),
    "casino_wagered": ("casino", lambda r: r["wagered"]),
    "casino_biggest_win": ("casino", lambda r: r["biggest_win"]),
    "casino_best_streak": ("casino", lambda r: r["best_streak"]),
    # Activities
    "work_shifts": ("activity", lambda r: r["work_shifts"]),
    "mine_count": ("activity", lambda r: r["mine_count"]),
    "hunt_count": ("activity", lambda r: r["hunt_count"]),
    "explore_count": ("activity", lambda r: r["explore_count"]),
    "rob_count": ("activity", lambda r: r["rob_count"]),
    "pickaxe_level": ("activity", lambda r: r["pickaxe_level"]),
    # Inventory
    "items_owned": ("inventory", lambda r: sum(i["qty"] for i in r)),
}


async def compute_stats(
    guild_id: int, user_id: int, keys: "list[str] | None" = None
) -> dict[str, float]:
    """Batch-read every requested stat key (default: every registered key),
    fetching each underlying source exactly once regardless of how many keys
    ride on it."""
    wanted = list(_EXTRACTORS) if keys is None else list(dict.fromkeys(keys))
    needed_sources = {_EXTRACTORS[k][0] for k in wanted if k in _EXTRACTORS}
    raw = {name: await _SOURCES[name](guild_id, user_id) for name in needed_sources}
    out: dict[str, float] = {}
    for key in wanted:
        entry = _EXTRACTORS.get(key)
        if entry is None:
            continue
        source, extract = entry
        out[key] = extract(raw[source])
    return out


def _make_provider(key: str) -> Callable[[int, int], Awaitable]:
    async def _provider(guild_id: int, user_id: int):
        stats = await compute_stats(guild_id, user_id, [key])
        return stats.get(key, 0)

    return _provider


# STAT_PROVIDERS: dict[str, async fn(guild_id, user_id) -> int|float] — the flat
# one-key-at-a-time contract. Registry well-formedness (every AchievementDef /
# WeeklyObjectiveDef `.stat` must be a valid key here) is asserted by
# tests/test_progression_helpers.py. Real evaluation calls compute_stats()
# directly for the batching described above; this registry is what external
# code (and those well-formedness tests) checks stat keys against.
STAT_PROVIDERS: dict[str, Callable[[int, int], Awaitable]] = {
    key: _make_provider(key) for key in _EXTRACTORS
}
