"""utils/db.casino — casino game storage (per-guild config + jackpot, GLOBAL
per-player stats, persisted blackjack hands, and daily challenges).

Scope split: a player's lifetime stats, streak, and daily challenges follow the
*user* across servers (keyed by user_id); the table limits, the on/off switch,
and the progressive jackpot pool stay per-guild, because a house pot fed by one
server's losses belongs to that server. Open blackjack hands keep a guild/
channel id — they're a live message to restore, not progression.

Part of the utils/db package. Coins themselves ride the existing economy
tables (db.try_debit_coins / db.add_coins) — this module only records bet
config, lifetime win/loss stats (incl. the streak), the jackpot pot, open
blackjack hands (restart-safe Hit/Stand), and daily-challenge progress.
"""

import json

from ._core import _conn, _read_conn, _ensure_columns, register_init, rows_for_users


async def _ensure_casino_tables():
    await _conn().execute("""
        CREATE TABLE IF NOT EXISTS casino_config (
            guild_id      TEXT PRIMARY KEY,
            enabled       INTEGER NOT NULL DEFAULT 1,
            min_bet       INTEGER NOT NULL DEFAULT 25,
            max_bet       INTEGER NOT NULL DEFAULT 3000,
            jackpot_pool  INTEGER NOT NULL DEFAULT 0
        )
    """)
    # Lifetime per-player aggregates. `won` is lifetime total payout received
    # (wins + pushes); `biggest_win`, `wins`, and the streak counters only
    # count true wins (payout > wagered for that game).
    await _conn().execute("""
        CREATE TABLE IF NOT EXISTS casino_stats (
            user_id      TEXT PRIMARY KEY,
            games        INTEGER NOT NULL DEFAULT 0,
            wagered      INTEGER NOT NULL DEFAULT 0,
            won          INTEGER NOT NULL DEFAULT 0,
            biggest_win  INTEGER NOT NULL DEFAULT 0,
            streak       INTEGER NOT NULL DEFAULT 0,
            best_streak  INTEGER NOT NULL DEFAULT 0
        )
    """)
    await _conn().execute(
        "CREATE INDEX IF NOT EXISTS casino_stats_net "
        "ON casino_stats ((won - wagered) DESC)"
    )
    # Lifetime win count — added after the baseline table; drives the
    # win_games daily challenge and the /casino stats "Wins" line.
    await _ensure_columns("casino_stats", {"wins": "INTEGER NOT NULL DEFAULT 0"})

    # Open /casino blackjack hands — persisted so a restart can resume or
    # auto-settle a hand instead of orphaning the already-debited bet (the
    # economy_raids restart-safety pattern). A row is written when the hand
    # is dealt, updated on every hit, and deleted the moment it's settled
    # (button press, cog-owned expiry timer, or a restart-restored expiry).
    await _conn().execute("""
        CREATE TABLE IF NOT EXISTS casino_blackjack (
            hand_id     INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id    TEXT NOT NULL,
            channel_id  TEXT NOT NULL,
            message_id  TEXT,
            user_id     TEXT NOT NULL,
            bet         INTEGER NOT NULL,
            streak      INTEGER NOT NULL DEFAULT 0,
            shoe        TEXT NOT NULL,
            player      TEXT NOT NULL,
            dealer      TEXT NOT NULL,
            created_at  REAL NOT NULL DEFAULT 0
        )
    """)

    # One row per (user, UTC day, challenge) — see
    # cogs.casino.helpers.generate_challenges for the deterministic pool/
    # target/reward; only progress and the one-time claim guard live here.
    await _conn().execute("""
        CREATE TABLE IF NOT EXISTS casino_challenges (
            user_id   TEXT NOT NULL,
            day       INTEGER NOT NULL,
            chal_key  TEXT NOT NULL,
            target    INTEGER NOT NULL DEFAULT 0,
            progress  INTEGER NOT NULL DEFAULT 0,
            claimed   INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (user_id, day, chal_key)
        )
    """)
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
    return {"enabled": True, "min_bet": 25, "max_bet": 3000, "jackpot_pool": 0}


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

    The CAS also loses when a concurrent add_to_jackpot (any other player's
    losing bet — no cooldown gates those) lands between the read and the
    UPDATE, which would silently rob a legitimate winner. That's a live-lock,
    not a correctness conflict, so retry the read-CAS a few times; each retry
    re-reads the (grown) pot, so the winner claims the fed-up value.
    """
    for _ in range(5):
        pot = (await get_casino_config(guild_id))["jackpot_pool"]
        if pot <= 0:
            return 0
        cur = await _conn().execute(
            "UPDATE casino_config SET jackpot_pool=0 "
            "WHERE guild_id=? AND jackpot_pool=?",
            (str(guild_id), pot),
        )
        await _conn().commit()
        if cur.rowcount > 0:
            return pot
    return 0


# ── Player stats ───────────────────────────────────────────────────────────────
def _stats_row(row) -> dict:
    return {
        "games": row["games"],
        "wagered": row["wagered"],
        "won": row["won"],
        "biggest_win": row["biggest_win"],
        "streak": row["streak"],
        "best_streak": row["best_streak"],
        "wins": row["wins"],
    }


async def get_casino_stats(user_id: int) -> dict:
    async with _conn().execute(
        "SELECT games, wagered, won, biggest_win, streak, best_streak, wins "
        "FROM casino_stats WHERE user_id=?",
        (str(user_id),),
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
        "wins": 0,
    }


async def record_casino_game(user_id: int, wagered: int, payout: int) -> dict:
    """Record one settled game as a single atomic upsert and return the new stats.

    `won` (the payout column) accumulates every payout including pushes; the
    streak/biggest_win/wins columns only reflect true wins (payout > wagered
    for this game), computed directly from the row's own previous values so
    two concurrent games can't corrupt each other's streak.
    """
    uid = str(user_id)
    wagered, payout = int(wagered), int(payout)
    win = payout > wagered
    biggest_win_param = payout if win else 0
    streak_new_row = 1 if win else 0
    wins_param = 1 if win else 0
    await _conn().execute(
        """
        INSERT INTO casino_stats
            (user_id, games, wagered, won, biggest_win, streak, best_streak, wins)
        VALUES (?, 1, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
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
            END),
            wins = wins + excluded.wins
        """,
        (
            uid,
            wagered,
            payout,
            biggest_win_param,
            streak_new_row,
            streak_new_row,
            wins_param,
        ),
    )
    await _conn().commit()
    return await get_casino_stats(user_id)


# ── Leaderboard (net winnings) ──────────────────────────────────────────────────
def _leaderboard_row(r) -> dict:
    return {
        "user_id": int(r["user_id"]),
        "games": r["games"],
        "wagered": r["wagered"],
        "won": r["won"],
        "net": r["net"],
    }


async def get_casino_leaderboard(limit: int = 10, offset: int = 0) -> list[dict]:
    """Biggest net winners across every server."""
    async with _read_conn().execute(
        "SELECT user_id, games, wagered, won, (won - wagered) AS net "
        "FROM casino_stats WHERE games > 0 "
        "ORDER BY net DESC, user_id ASC LIMIT ? OFFSET ?",
        (int(limit), int(offset)),
    ) as cur:
        rows = await cur.fetchall()
    return [_leaderboard_row(r) for r in rows]


async def get_casino_leaderboard_for(user_ids) -> list[dict]:
    """The same rows filtered to a set of users (a server-scoped board)."""
    rows = await rows_for_users(
        "SELECT user_id, games, wagered, won, (won - wagered) AS net FROM casino_stats",
        user_ids,
        positive_col="games",
    )
    return [_leaderboard_row(r) for r in rows]


async def count_casino_players() -> int:
    async with _read_conn().execute(
        "SELECT COUNT(*) FROM casino_stats WHERE games > 0"
    ) as cur:
        return (await cur.fetchone())[0]


async def get_casino_rank(user_id: int) -> tuple[int, int] | None:
    """Return (global rank, net) by net winnings; None if they haven't played."""
    stats = await get_casino_stats(user_id)
    if stats["games"] <= 0:
        return None
    net = stats["won"] - stats["wagered"]
    async with _read_conn().execute(
        "SELECT COUNT(*) FROM casino_stats WHERE games > 0 AND (won - wagered) > ?",
        (net,),
    ) as cur:
        ahead = (await cur.fetchone())[0]
    return ahead + 1, net


# ══════════════════════════════════════════════════════════════════════════════
#  Blackjack hands (open /casino blackjack Hit/Stand — persisted for
#  restart-safe buttons and to guarantee a debited bet is never lost)
# ══════════════════════════════════════════════════════════════════════════════
def _cards_to_json(cards) -> str:
    return json.dumps([[rank, suit] for rank, suit in cards])


def _cards_from_json(raw: str) -> list[tuple[str, str]]:
    return [(rank, suit) for rank, suit in json.loads(raw)]


def _bj_row(row) -> dict:
    return {
        "hand_id": row["hand_id"],
        "guild_id": int(row["guild_id"]),
        "channel_id": int(row["channel_id"]),
        "message_id": int(row["message_id"]) if row["message_id"] else None,
        "user_id": int(row["user_id"]),
        "bet": row["bet"],
        "streak": row["streak"],
        "shoe": _cards_from_json(row["shoe"]),
        "player": _cards_from_json(row["player"]),
        "dealer": _cards_from_json(row["dealer"]),
        "created_at": row["created_at"],
    }


async def create_blackjack_hand(
    guild_id: int,
    channel_id: int,
    user_id: int,
    bet: int,
    streak: int,
    shoe: list,
    player: list,
    dealer: list,
    created_at: float,
) -> int:
    """Insert a freshly-dealt open hand (bet already debited) and return its id
    (used in the Hit/Stand button custom_ids)."""
    cur = await _conn().execute(
        "INSERT INTO casino_blackjack "
        "(guild_id, channel_id, user_id, bet, streak, shoe, player, dealer, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (
            str(guild_id),
            str(channel_id),
            str(user_id),
            int(bet),
            int(streak),
            _cards_to_json(shoe),
            _cards_to_json(player),
            _cards_to_json(dealer),
            float(created_at),
        ),
    )
    await _conn().commit()
    return cur.lastrowid


async def set_blackjack_message(hand_id: int, message_id: int) -> None:
    await _conn().execute(
        "UPDATE casino_blackjack SET message_id=? WHERE hand_id=?",
        (str(message_id), int(hand_id)),
    )
    await _conn().commit()


async def update_blackjack_hand(
    hand_id: int, shoe: list, player: list, dealer: list
) -> None:
    """Persist the hand's state after a Hit (or the dealer playing out), so a
    restart resumes from exactly this point instead of the original deal."""
    await _conn().execute(
        "UPDATE casino_blackjack SET shoe=?, player=?, dealer=? WHERE hand_id=?",
        (
            _cards_to_json(shoe),
            _cards_to_json(player),
            _cards_to_json(dealer),
            int(hand_id),
        ),
    )
    await _conn().commit()


async def get_blackjack_hand(hand_id: int) -> dict | None:
    async with _conn().execute(
        "SELECT hand_id, guild_id, channel_id, message_id, user_id, bet, streak, "
        "shoe, player, dealer, created_at FROM casino_blackjack WHERE hand_id=?",
        (int(hand_id),),
    ) as cur:
        row = await cur.fetchone()
    return _bj_row(row) if row else None


async def get_open_blackjack_hands() -> list[dict]:
    """All persisted open hands (message_id may be NULL if a crash beat the
    message-id write) — used to rebuild views after a restart."""
    async with _conn().execute(
        "SELECT hand_id, guild_id, channel_id, message_id, user_id, bet, streak, "
        "shoe, player, dealer, created_at FROM casino_blackjack"
    ) as cur:
        rows = await cur.fetchall()
    return [_bj_row(r) for r in rows]


async def claim_blackjack_hand(hand_id: int) -> dict | None:
    """Atomically claim one hand for settlement, deleting its row.

    A button press, the cog's expiry timer, and a restart-restored expiry can
    all race to settle the same hand. Only one may pay out: this reads the
    row for its data, then deletes it by primary key — the DELETE's rowcount
    is the real arbiter (only one caller ever sees rowcount > 0, no matter how
    the two reads interleave), so a caller that gets back None must not pay
    anything. Returns the hand's data on success, None if already claimed.
    """
    row = await get_blackjack_hand(hand_id)
    if row is None:
        return None
    cur = await _conn().execute(
        "DELETE FROM casino_blackjack WHERE hand_id=?", (int(hand_id),)
    )
    await _conn().commit()
    return row if cur.rowcount > 0 else None


async def delete_blackjack_hand(hand_id: int) -> None:
    await _conn().execute(
        "DELETE FROM casino_blackjack WHERE hand_id=?", (int(hand_id),)
    )
    await _conn().commit()


# ══════════════════════════════════════════════════════════════════════════════
#  Daily challenges (2/day per member — see cogs.casino.helpers.generate_challenges)
# ══════════════════════════════════════════════════════════════════════════════
async def get_challenge_progress(user_id: int, day: int, chal_key: str) -> dict:
    """Today's progress on one challenge; zeros if nothing's been recorded yet."""
    async with _conn().execute(
        "SELECT progress, claimed FROM casino_challenges "
        "WHERE user_id=? AND day=? AND chal_key=?",
        (str(user_id), int(day), chal_key),
    ) as cur:
        row = await cur.fetchone()
    if row:
        return {"progress": row["progress"], "claimed": bool(row["claimed"])}
    return {"progress": 0, "claimed": False}


async def bump_challenge_progress(
    user_id: int,
    day: int,
    chal_key: str,
    target: int,
    amount: int,
    mode: str = "add",
) -> dict:
    """Advance (or refresh) today's progress on one challenge, creating the row
    on first touch. `mode='add'` sums (clamped at target, e.g. games played /
    coins wagered); `mode='max'` keeps a running maximum instead (the
    payout-threshold challenge, where a single game's payout either clears the
    bar or it doesn't). Returns the row's new {progress, claimed}."""
    uid, day, target, amount = str(user_id), int(day), int(target), int(amount)
    if mode == "max":
        seed = max(0, min(target, amount))
        await _conn().execute(
            "INSERT INTO casino_challenges "
            "(user_id, day, chal_key, target, progress, claimed) "
            "VALUES (?,?,?,?,?,0) "
            "ON CONFLICT(user_id, day, chal_key) DO UPDATE SET "
            "progress = MAX(progress, excluded.progress)",
            (uid, day, chal_key, target, seed),
        )
        await _conn().commit()
    elif amount > 0:
        seed = min(target, amount)
        await _conn().execute(
            "INSERT INTO casino_challenges "
            "(user_id, day, chal_key, target, progress, claimed) "
            "VALUES (?,?,?,?,?,0) "
            "ON CONFLICT(user_id, day, chal_key) DO UPDATE SET "
            "progress = MIN(target, progress + excluded.progress)",
            (uid, day, chal_key, target, seed),
        )
        await _conn().commit()
    return await get_challenge_progress(user_id, day, chal_key)


async def try_claim_challenge(user_id: int, day: int, chal_key: str) -> bool:
    """Atomically mark a completed challenge claimed (first claimer wins).

    Returns False if it isn't at target yet, or was already claimed — the
    conditional UPDATE makes double-claiming (e.g. two near-simultaneous
    games both pushing it over target) a single-winner race.
    """
    cur = await _conn().execute(
        "UPDATE casino_challenges SET claimed=1 "
        "WHERE user_id=? AND day=? AND chal_key=? AND claimed=0 AND progress>=target",
        (str(user_id), int(day), chal_key),
    )
    await _conn().commit()
    return cur.rowcount > 0


register_init(_ensure_casino_tables)
