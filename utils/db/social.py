"""utils/db.social — reputation and cookies: the account's social counters.

Part of the utils/db package. Two small features share one table because they
are the same shape: a tally that belongs to a *person*, carries no coin value,
and is interesting mainly next to somebody's name.

  * **rep** — "this person is cool", given with `/profile rep @user` once a day.
    `rep` is what you have received; `last_rep` is when *you* last gave one, so
    the daily limit follows the giver rather than the pair.
  * **cookies** — `/fun cookie @user`. Both halves are counted (`cookies_sent`,
    `cookies_received`) because the dashboard is about generosity as much as
    popularity.

Both are GLOBAL, keyed by user_id alone, for the same reason the wallet is: a
member would be rightly baffled if the rep they were given followed them into
one server and not another. And because neither is worth coins, neither is a
faucet — nothing here can be farmed into anything spendable, which is why the
daily limit is a courtesy against spam rather than an economic guard.
"""

from ._core import (
    count_ahead,
    _commit,
    _conn,
    _read_conn,
    fetch_one_returning,
    register_init,
    transaction,
)

# Seconds between one member's rep gifts. A day, matching /daily, so "once a
# day" means the same thing everywhere in the bot.
REP_COOLDOWN = 86_400

_DEFAULTS = {
    "rep": 0,
    "last_rep": 0.0,
    "cookies_sent": 0,
    "cookies_received": 0,
}


async def _ensure_social_tables():
    await _conn().execute("""
        CREATE TABLE IF NOT EXISTS user_social (
            user_id          TEXT PRIMARY KEY,
            rep              INTEGER NOT NULL DEFAULT 0,
            last_rep         REAL NOT NULL DEFAULT 0,
            cookies_sent     INTEGER NOT NULL DEFAULT 0,
            cookies_received INTEGER NOT NULL DEFAULT 0
        )
    """)
    # Both boards rank on a single column, so each gets its own index — the
    # same reasoning as fishing_stats' per-stat indexes.
    for column in ("rep", "cookies_received", "cookies_sent"):
        await _conn().execute(
            f"CREATE INDEX IF NOT EXISTS user_social_{column} "
            f"ON user_social ({column} DESC)"
        )
    await _commit()


register_init(_ensure_social_tables)


async def get_social(user_id: int) -> dict:
    async with _conn().execute(
        "SELECT rep, last_rep, cookies_sent, cookies_received "
        "FROM user_social WHERE user_id=?",
        (str(user_id),),
    ) as cur:
        row = await cur.fetchone()
    if not row:
        return dict(_DEFAULTS)
    return {
        "rep": row["rep"],
        "last_rep": row["last_rep"],
        "cookies_sent": row["cookies_sent"],
        "cookies_received": row["cookies_received"],
    }


# ── Reputation ────────────────────────────────────────────────────────────────
async def try_claim_rep(giver_id: int, now: float, cooldown: int = REP_COOLDOWN) -> int:
    """Atomically claim the giver's daily rep. 0 on success, else seconds left.

    One conditional upsert (the try_claim_cast pattern), so double-tapping the
    command can't spend the same day's rep twice.
    """
    cur = await _conn().execute(
        "INSERT INTO user_social (user_id, last_rep) VALUES (?,?) "
        "ON CONFLICT(user_id) DO UPDATE SET last_rep=excluded.last_rep "
        "WHERE excluded.last_rep - user_social.last_rep >= ?",
        (str(giver_id), float(now), int(cooldown)),
    )
    await _commit()
    if cur.rowcount > 0:
        return 0
    social = await get_social(giver_id)
    return max(1, int(cooldown - (now - social["last_rep"])))


async def refund_rep(giver_id: int) -> None:
    """Hand a claimed rep back — the giver never spent it.

    Used when the claim succeeded but the award didn't, so a failed command
    doesn't cost somebody a day.
    """
    await _conn().execute(
        "UPDATE user_social SET last_rep=0 WHERE user_id=?", (str(giver_id),)
    )
    await _commit()


async def add_rep(user_id: int, amount: int = 1) -> int:
    """Add to a member's received rep. Returns the new total."""
    row = await fetch_one_returning(
        "INSERT INTO user_social (user_id, rep) VALUES (?,MAX(0,?)) "
        "ON CONFLICT(user_id) DO UPDATE SET rep=MAX(0, rep + ?)",
        (str(user_id), int(amount), int(amount)),
        returning="rep",
    )
    if row is not None:
        return row[0]
    return (await get_social(user_id))["rep"]


async def get_rep_rank(user_id: int) -> tuple[int, int] | None:
    """(rank, rep) across every account; None for a member with no rep yet."""
    rep = (await get_social(user_id))["rep"]
    if rep <= 0:
        return None
    ahead = await count_ahead("SELECT COUNT(*) FROM user_social WHERE rep > ?", rep)
    return ahead + 1, rep


# ── Cookies ───────────────────────────────────────────────────────────────────
async def give_cookie(giver_id: int, target_id: int) -> dict:
    """Hand one cookie over. Returns both members' new totals.

    Wrapped in a transaction because the two halves are one event: a cookie
    that was sent but never received (or the reverse) would show up on the
    dashboards as a permanent discrepancy.
    """
    async with transaction():
        await _conn().execute(
            "INSERT INTO user_social (user_id, cookies_sent) VALUES (?,1) "
            "ON CONFLICT(user_id) DO UPDATE SET cookies_sent=cookies_sent+1",
            (str(giver_id),),
        )
        await _commit()
        await _conn().execute(
            "INSERT INTO user_social (user_id, cookies_received) VALUES (?,1) "
            "ON CONFLICT(user_id) DO UPDATE SET cookies_received=cookies_received+1",
            (str(target_id),),
        )
        await _commit()
    return {
        "sent": (await get_social(giver_id))["cookies_sent"],
        "received": (await get_social(target_id))["cookies_received"],
    }


async def get_cookie_rank(user_id: int) -> tuple[int, int] | None:
    """(rank, cookies received); None for a member who has none yet."""
    received = (await get_social(user_id))["cookies_received"]
    if received <= 0:
        return None
    ahead = await count_ahead(
        "SELECT COUNT(*) FROM user_social WHERE cookies_received > ?", received
    )
    return ahead + 1, received


async def social_leaderboard(
    column: str, limit: int = 10, offset: int = 0
) -> list[dict]:
    """Top accounts by one social column.

    `column` is checked against a fixed whitelist — it is substituted into the
    SQL, so it must never come from user input unvalidated.
    """
    if column not in ("rep", "cookies_received", "cookies_sent"):
        raise ValueError(f"not a rankable social column: {column}")
    async with _read_conn().execute(
        f"SELECT user_id, {column} AS value FROM user_social WHERE {column} > 0 "
        f"ORDER BY {column} DESC, user_id ASC LIMIT ? OFFSET ?",
        (int(limit), int(offset)),
    ) as cur:
        rows = await cur.fetchall()
    return [{"user_id": int(r["user_id"]), "value": r["value"]} for r in rows]
