"""utils/db.casino — casino game storage (per-guild config, per-player stats,
and the progressive jackpot pool).

Part of the utils/db package. Coins themselves ride the existing economy
tables (db.try_debit_coins / db.add_coins) — this module only records bet
config, lifetime win/loss stats (incl. the streak), and the jackpot pot.
"""

from ._core import _conn, register_init


async def _ensure_casino_tables():
    await _conn().execute("""
        CREATE TABLE IF NOT EXISTS casino_config (
            guild_id      TEXT PRIMARY KEY,
            enabled       INTEGER NOT NULL DEFAULT 1,
            min_bet       INTEGER NOT NULL DEFAULT 10,
            max_bet       INTEGER NOT NULL DEFAULT 1000,
            jackpot_pool  INTEGER NOT NULL DEFAULT 0
        )
    """)
    # Lifetime per-player aggregates. `won` is lifetime total payout received
    # (wins + pushes); `biggest_win` and the streak counters only count true
    # wins (payout > wagered for that game).
    await _conn().execute("""
        CREATE TABLE IF NOT EXISTS casino_stats (
            guild_id     TEXT NOT NULL,
            user_id      TEXT NOT NULL,
            games        INTEGER NOT NULL DEFAULT 0,
            wagered      INTEGER NOT NULL DEFAULT 0,
            won          INTEGER NOT NULL DEFAULT 0,
            biggest_win  INTEGER NOT NULL DEFAULT 0,
            streak       INTEGER NOT NULL DEFAULT 0,
            best_streak  INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (guild_id, user_id)
        )
    """)
    await _conn().execute(
        "CREATE INDEX IF NOT EXISTS casino_stats_net "
        "ON casino_stats (guild_id, (won - wagered) DESC)"
    )
    await _conn().commit()


# ── Config ─────────────────────────────────────────────────────────────────────
async def get_casino_config(guild_id: int) -> dict:
    async with _conn().execute(
        "SELECT enabled, min_bet, max_bet, jackpot_pool FROM casino_config "
        "WHERE guild_id=?",
        (str(guild_id),),
    ) as cur:
        row = await cur.fetchone()
    if row:
        return {
            "enabled": bool(row["enabled"]),
            "min_bet": row["min_bet"],
            "max_bet": row["max_bet"],
            "jackpot_pool": row["jackpot_pool"],
        }
    return {"enabled": True, "min_bet": 10, "max_bet": 1000, "jackpot_pool": 0}


async def set_casino_config(guild_id: int, **kwargs) -> None:
    """Partial update of enabled/min_bet/max_bet.

    Never touches jackpot_pool — that column is only ever mutated by the
    atomic add_to_jackpot/try_claim_jackpot below, so a concurrent admin
    settings change can't clobber (or roll back) an in-flight jackpot update.
    """
    current = await get_casino_config(guild_id)
    for key in ("enabled", "min_bet", "max_bet"):
        if key in kwargs:
            current[key] = kwargs[key]
    await _conn().execute(
        "INSERT INTO casino_config (guild_id, enabled, min_bet, max_bet, jackpot_pool) "
        "VALUES (?,?,?,?,0) "
        "ON CONFLICT(guild_id) DO UPDATE SET enabled=excluded.enabled, "
        "min_bet=excluded.min_bet, max_bet=excluded.max_bet",
        (
            str(guild_id),
            1 if current["enabled"] else 0,
            int(current["min_bet"]),
            int(current["max_bet"]),
        ),
    )
    await _conn().commit()


# ── Jackpot ────────────────────────────────────────────────────────────────────
async def add_to_jackpot(guild_id: int, amount: int) -> int:
    """Atomically add to the progressive pot. Returns the new pool value."""
    amount = max(0, int(amount))
    if amount:
        await _conn().execute(
            "INSERT INTO casino_config (guild_id, enabled, min_bet, max_bet, "
            "jackpot_pool) VALUES (?,1,10,1000,?) "
            "ON CONFLICT(guild_id) DO UPDATE SET "
            "jackpot_pool=jackpot_pool+excluded.jackpot_pool",
            (str(guild_id), amount),
        )
        await _conn().commit()
    return (await get_casino_config(guild_id))["jackpot_pool"]


async def try_claim_jackpot(guild_id: int) -> int:
    """Conditionally zero the pool and return what was claimed (0 if empty).

    A read-then-conditional-UPDATE compare-and-swap (mirrors set_rod_level):
    two simultaneous triple-7 hits can't both drain the same pot — the loser's
    UPDATE matches zero rows because the winner already changed the value.
    """
    pot = (await get_casino_config(guild_id))["jackpot_pool"]
    if pot <= 0:
        return 0
    cur = await _conn().execute(
        "UPDATE casino_config SET jackpot_pool=0 WHERE guild_id=? AND jackpot_pool=?",
        (str(guild_id), pot),
    )
    await _conn().commit()
    return pot if cur.rowcount > 0 else 0


# ── Player stats ───────────────────────────────────────────────────────────────
def _stats_row(row) -> dict:
    return {
        "games": row["games"],
        "wagered": row["wagered"],
        "won": row["won"],
        "biggest_win": row["biggest_win"],
        "streak": row["streak"],
        "best_streak": row["best_streak"],
    }


async def get_casino_stats(guild_id: int, user_id: int) -> dict:
    async with _conn().execute(
        "SELECT games, wagered, won, biggest_win, streak, best_streak "
        "FROM casino_stats WHERE guild_id=? AND user_id=?",
        (str(guild_id), str(user_id)),
    ) as cur:
        row = await cur.fetchone()
    if row:
        return _stats_row(row)
    return {
        "games": 0,
        "wagered": 0,
        "won": 0,
        "biggest_win": 0,
        "streak": 0,
        "best_streak": 0,
    }


async def record_casino_game(
    guild_id: int, user_id: int, wagered: int, payout: int
) -> dict:
    """Record one settled game as a single atomic upsert and return the new stats.

    `won` (the payout column) accumulates every payout including pushes; the
    streak/biggest_win columns only reflect true wins (payout > wagered for
    this game), computed directly from the row's own previous values so two
    concurrent games can't corrupt each other's streak.
    """
    gid, uid = str(guild_id), str(user_id)
    wagered, payout = int(wagered), int(payout)
    win = payout > wagered
    biggest_win_param = payout if win else 0
    streak_new_row = 1 if win else 0
    await _conn().execute(
        """
        INSERT INTO casino_stats
            (guild_id, user_id, games, wagered, won, biggest_win, streak, best_streak)
        VALUES (?, ?, 1, ?, ?, ?, ?, ?)
        ON CONFLICT(guild_id, user_id) DO UPDATE SET
            games = games + 1,
            wagered = wagered + excluded.wagered,
            won = won + excluded.won,
            biggest_win = MAX(biggest_win, excluded.biggest_win),
            streak = CASE
                WHEN excluded.won > excluded.wagered THEN streak + 1
                WHEN excluded.won < excluded.wagered THEN 0
                ELSE streak
            END,
            best_streak = MAX(best_streak, CASE
                WHEN excluded.won > excluded.wagered THEN streak + 1
                ELSE best_streak
            END)
        """,
        (gid, uid, wagered, payout, biggest_win_param, streak_new_row, streak_new_row),
    )
    await _conn().commit()
    return await get_casino_stats(guild_id, user_id)


# ── Leaderboard (net winnings) ──────────────────────────────────────────────────
async def get_casino_leaderboard(
    guild_id: int, limit: int = 10, offset: int = 0
) -> list[dict]:
    async with _conn().execute(
        "SELECT user_id, games, wagered, won, (won - wagered) AS net "
        "FROM casino_stats WHERE guild_id=? AND games > 0 "
        "ORDER BY net DESC, user_id ASC LIMIT ? OFFSET ?",
        (str(guild_id), int(limit), int(offset)),
    ) as cur:
        rows = await cur.fetchall()
    return [
        {
            "user_id": int(r["user_id"]),
            "games": r["games"],
            "wagered": r["wagered"],
            "won": r["won"],
            "net": r["net"],
        }
        for r in rows
    ]


async def count_casino_players(guild_id: int) -> int:
    async with _conn().execute(
        "SELECT COUNT(*) FROM casino_stats WHERE guild_id=? AND games > 0",
        (str(guild_id),),
    ) as cur:
        return (await cur.fetchone())[0]


async def get_casino_rank(guild_id: int, user_id: int) -> tuple[int, int] | None:
    """Return (rank, net) by net winnings; None if the player hasn't played."""
    stats = await get_casino_stats(guild_id, user_id)
    if stats["games"] <= 0:
        return None
    net = stats["won"] - stats["wagered"]
    async with _conn().execute(
        "SELECT COUNT(*) FROM casino_stats WHERE guild_id=? AND games > 0 "
        "AND (won - wagered) > ?",
        (str(guild_id), net),
    ) as cur:
        ahead = (await cur.fetchone())[0]
    return ahead + 1, net


register_init(_ensure_casino_tables)
