"""utils/db.refunds — the price-refund ledger.

When a ladder price is cut, everyone who already bought that tier paid the old
number. Nothing in the bot records what anyone *paid* — the rod, the pickaxe and
a spot charter are stored as "what you own", not as a receipt — so a refund is
derived from the tiers a member owns against a frozen table of price changes
(see cogs/economy/refunds.py). That works, but it makes the ledger the only
thing standing between a re-run and minted coins, so both tables here exist for
exactly one purpose: making a refund happen at most once.

- ``price_refunds`` — one row per (user, batch). Recording is a plain
  INSERT OR IGNORE and the *caller* credits coins only when the insert actually
  lands, the same contract as progression.try_award_achievement. Ordered that
  way round on purpose: a crash between the insert and the credit costs one
  member one refund, where crediting first would pay everybody twice on the
  next boot. It fails toward paying too little, which is the only direction a
  faucet may fail in.
- ``price_refund_batches`` — one row per batch that has been swept. This is not
  an optimisation, it is what bounds *who* gets paid: a member who buys the tier
  tomorrow at the new price is owed nothing, and without a closed batch the
  sweep would hand them the discount as well. A batch left unmarked (a crash
  mid-sweep) simply re-runs, and the per-user ledger keeps that safe.

``notified`` is separate from the credit for the reason the global level-up
announcement is: coins have to land whether or not the member's DMs are open, so
delivery is retried on later boots and can never gate the payment.
"""

import time

from ._core import _commit, _conn, register_init


async def _ensure_refund_tables():
    await _conn().execute("""
        CREATE TABLE IF NOT EXISTS price_refunds (
            user_id    TEXT    NOT NULL,
            batch      TEXT    NOT NULL,
            amount     INTEGER NOT NULL,
            detail     TEXT    NOT NULL DEFAULT '',
            created_at REAL    NOT NULL DEFAULT 0,
            notified   INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (user_id, batch)
        )
    """)
    # The delivery queue reads "everything still unsent", so it indexes on the
    # flag rather than on the member.
    await _conn().execute(
        "CREATE INDEX IF NOT EXISTS price_refunds_pending "
        "ON price_refunds (notified)"
    )
    await _conn().execute("""
        CREATE TABLE IF NOT EXISTS price_refund_batches (
            batch     TEXT PRIMARY KEY,
            swept_at  REAL NOT NULL DEFAULT 0,
            users     INTEGER NOT NULL DEFAULT 0,
            coins     INTEGER NOT NULL DEFAULT 0
        )
    """)
    await _commit()


register_init(_ensure_refund_tables)


async def batch_swept(batch: str) -> bool:
    """Whether this repricing batch has already been paid out."""
    async with _conn().execute(
        "SELECT 1 FROM price_refund_batches WHERE batch = ?", (batch,)
    ) as cur:
        return await cur.fetchone() is not None


async def close_batch(batch: str, *, users: int, coins: int) -> None:
    """Mark a batch as swept so it never pays a later buyer.

    Written *after* the members are paid: the batch marker bounds who is
    eligible, so recording it early would strand anyone not yet reached.
    """
    await _conn().execute(
        "INSERT OR REPLACE INTO price_refund_batches "
        "(batch, swept_at, users, coins) VALUES (?, ?, ?, ?)",
        (batch, time.time(), users, coins),
    )
    await _commit()


async def try_record_refund(
    user_id: int, batch: str, amount: int, detail: str = ""
) -> bool:
    """Atomically record that a member is owed `amount` for `batch`.

    Returns True the first (and only the first) time for a given pair. The
    caller must credit the coins only on True — a second sweep, a restart
    mid-run, or a manifest replayed by hand all see rowcount 0 and pay nothing.
    """
    cur = await _conn().execute(
        "INSERT OR IGNORE INTO price_refunds "
        "(user_id, batch, amount, detail, created_at) VALUES (?, ?, ?, ?, ?)",
        (str(user_id), batch, int(amount), detail, time.time()),
    )
    await _commit()
    return cur.rowcount > 0


async def pending_refund_notices(limit: int = 200) -> list[dict]:
    """Refunds that have been credited but not yet announced, oldest first."""
    async with _conn().execute(
        "SELECT user_id, batch, amount, detail FROM price_refunds "
        "WHERE notified = 0 ORDER BY created_at LIMIT ?",
        (limit,),
    ) as cur:
        rows = await cur.fetchall()
    return [
        {
            "user_id": int(r["user_id"]),
            "batch": r["batch"],
            "amount": r["amount"],
            "detail": r["detail"],
        }
        for r in rows
    ]


async def mark_refund_notified(user_id: int, batch: str) -> None:
    """Record that the member has been told. Failure to send simply leaves the
    row pending, so the next boot tries again."""
    await _conn().execute(
        "UPDATE price_refunds SET notified = 1 WHERE user_id = ? AND batch = ?",
        (str(user_id), batch),
    )
    await _commit()


async def refund_batch_totals(batch: str) -> dict:
    """What a swept batch paid out, for the owner's report."""
    async with _conn().execute(
        "SELECT users, coins, swept_at FROM price_refund_batches WHERE batch = ?",
        (batch,),
    ) as cur:
        row = await cur.fetchone()
    return dict(row) if row else {"users": 0, "coins": 0, "swept_at": 0.0}


async def get_refunds(user_id: int) -> list[dict]:
    """Every refund a member has been paid, newest first."""
    async with _conn().execute(
        "SELECT batch, amount, detail, created_at FROM price_refunds "
        "WHERE user_id = ? ORDER BY created_at DESC",
        (str(user_id),),
    ) as cur:
        rows = await cur.fetchall()
    return [dict(r) for r in rows]
