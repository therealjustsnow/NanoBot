"""utils/db.identity — account identity: global XP and profile cosmetics.

Three user-scoped tables (global, like the rest of the economy — see
docs/global-economy.md):

- ``global_levels``     — lifetime account XP, the chat-XP cooldown stamp, and
  a pending level-up announcement (see ``pending_level`` below).
  The curve and every award value live in ``utils/globalxp.py``; this layer
  only stores the number. Separate from ``user_levels`` (the per-guild chat
  leveling cog), which is untouched and stays guild-scoped.
- ``cosmetic_unlocks``  — which cosmetics a member owns: badges, banners,
  borders, nameplates, anything a future slot adds. One row per (user, key),
  so granting twice is a no-op.
- ``cosmetic_equipped`` — what they've put on their profile card. Keyed by
  (user, slot, position): a single-value slot uses position 0, a multi-value
  slot like the badge showcase uses 0…n-1. Adding a slot needs no schema
  change — it's a new entry in ``utils/cosmetics.py``.

Cosmetic *definitions* (name, art, unlock rule) are code/JSON-side in
utils/cosmetics.py; the DB only ever stores keys, exactly like user_items and
the item catalogue.
"""

from ._core import _conn, _read_conn, _ensure_columns, register_init


async def _ensure_identity_tables():
    await _conn().execute("""
        CREATE TABLE IF NOT EXISTS global_levels (
            user_id      TEXT PRIMARY KEY,
            xp           INTEGER NOT NULL DEFAULT 0,
            last_message REAL NOT NULL DEFAULT 0
        )
    """)
    await _conn().execute(
        "CREATE INDEX IF NOT EXISTS global_levels_xp ON global_levels (xp DESC)"
    )
    await _conn().commit()
    # A level-up the member hasn't been told about yet. Written when the award
    # crosses a level and cleared once an announcement actually goes out, so a
    # level earned while their DMs are closed and their channel is unwritable
    # is delivered the next time they turn up anywhere. 0 = nothing pending.
    await _ensure_columns(
        "global_levels", {"pending_level": "INTEGER NOT NULL DEFAULT 0"}
    )
    await _conn().execute("""
        CREATE TABLE IF NOT EXISTS cosmetic_unlocks (
            user_id     TEXT NOT NULL,
            key         TEXT NOT NULL,
            unlocked_at REAL NOT NULL DEFAULT 0,
            PRIMARY KEY (user_id, key)
        )
    """)
    await _conn().execute("""
        CREATE TABLE IF NOT EXISTS cosmetic_equipped (
            user_id  TEXT NOT NULL,
            slot     TEXT NOT NULL,
            position INTEGER NOT NULL DEFAULT 0,
            key      TEXT NOT NULL,
            PRIMARY KEY (user_id, slot, position)
        )
    """)
    await _conn().commit()


# ── Global XP ─────────────────────────────────────────────────────────────────
async def get_global_xp(user_id: int) -> int:
    async with _conn().execute(
        "SELECT xp FROM global_levels WHERE user_id=?", (str(user_id),)
    ) as cur:
        row = await cur.fetchone()
    return row["xp"] if row else 0


async def add_global_xp(user_id: int, amount: int) -> tuple[int, int]:
    """Add XP atomically. Returns (xp_before, xp_after).

    Single-statement upsert (the add_coins pattern) so two servers awarding at
    the same moment can't lose an update. The *before* value is what lets the
    caller tell whether this award crossed a level boundary.
    """
    amount = max(0, int(amount))
    before = await get_global_xp(user_id)
    if amount:
        await _conn().execute(
            "INSERT INTO global_levels (user_id, xp) VALUES (?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET xp = xp + ?",
            (str(user_id), amount, amount),
        )
        await _conn().commit()
    return before, before + amount


async def try_claim_global_message(user_id: int, now: float, cooldown: int) -> bool:
    """Claim the chat-XP cooldown slot. True when this call may award XP.

    Conditional upsert (the try_claim_cast pattern): messages sent in two
    servers inside the same window claim once between them, because the
    account — not the server — owns the cooldown.
    """
    cur = await _conn().execute(
        "INSERT INTO global_levels (user_id, last_message) VALUES (?,?) "
        "ON CONFLICT(user_id) DO UPDATE SET last_message=excluded.last_message "
        "WHERE excluded.last_message - global_levels.last_message >= ?",
        (str(user_id), float(now), int(cooldown)),
    )
    await _conn().commit()
    return cur.rowcount > 0


# ── Pending level-up announcements ────────────────────────────────────────────
async def set_pending_levelup(user_id: int, level: int) -> None:
    """Remember that `user_id` reached `level` and hasn't been told yet.

    MAX, not assignment: if someone levels twice before an announcement lands
    (or an announcement failed and a later level arrived), the message they
    eventually get names the level they're actually on.
    """
    await _conn().execute(
        "INSERT INTO global_levels (user_id, pending_level) VALUES (?,?) "
        "ON CONFLICT(user_id) DO UPDATE SET "
        "pending_level=MAX(pending_level, excluded.pending_level)",
        (str(user_id), int(level)),
    )
    await _conn().commit()


async def get_pending_levelup(user_id: int) -> int:
    async with _conn().execute(
        "SELECT pending_level FROM global_levels WHERE user_id=?", (str(user_id),)
    ) as cur:
        row = await cur.fetchone()
    return row["pending_level"] if row else 0


async def claim_pending_levelup(user_id: int) -> int:
    """Atomically take the pending announcement, returning the level (0 if none).

    A conditional UPDATE, so a message and a command completing at the same
    instant can't both announce the same level. The caller must hand it back
    with set_pending_levelup if it fails to deliver.
    """
    level = await get_pending_levelup(user_id)
    if level <= 0:
        return 0
    cur = await _conn().execute(
        "UPDATE global_levels SET pending_level=0 "
        "WHERE user_id=? AND pending_level=?",
        (str(user_id), int(level)),
    )
    await _conn().commit()
    return level if cur.rowcount > 0 else 0


async def get_global_xp_leaderboard(limit: int = 10, offset: int = 0) -> list[dict]:
    async with _read_conn().execute(
        "SELECT user_id, xp FROM global_levels WHERE xp > 0 "
        "ORDER BY xp DESC, user_id ASC LIMIT ? OFFSET ?",
        (int(limit), int(offset)),
    ) as cur:
        rows = await cur.fetchall()
    return [{"user_id": int(r["user_id"]), "xp": r["xp"]} for r in rows]


async def get_global_xp_rank(user_id: int) -> tuple[int, int] | None:
    """(rank, xp) across every account; None when they have no XP yet."""
    xp = await get_global_xp(user_id)
    if xp <= 0:
        return None
    async with _read_conn().execute(
        "SELECT COUNT(*) FROM global_levels WHERE xp > ?", (xp,)
    ) as cur:
        ahead = (await cur.fetchone())[0]
    return ahead + 1, xp


# ── Cosmetic ownership ────────────────────────────────────────────────────────
async def unlock_cosmetic(user_id: int, key: str, *, at: float = 0.0) -> bool:
    """Grant a cosmetic. True only the first time (INSERT OR IGNORE), so a
    caller can safely announce "new unlock!" on a True and stay silent on a
    re-evaluation — the try_award_achievement contract."""
    cur = await _conn().execute(
        "INSERT OR IGNORE INTO cosmetic_unlocks (user_id, key, unlocked_at) "
        "VALUES (?,?,?)",
        (str(user_id), key, float(at)),
    )
    await _conn().commit()
    return cur.rowcount > 0


async def get_unlocked_cosmetics(user_id: int) -> dict[str, float]:
    """Owned cosmetic keys → when they were unlocked."""
    async with _conn().execute(
        "SELECT key, unlocked_at FROM cosmetic_unlocks WHERE user_id=?",
        (str(user_id),),
    ) as cur:
        rows = await cur.fetchall()
    return {r["key"]: r["unlocked_at"] for r in rows}


async def revoke_cosmetic(user_id: int, key: str) -> bool:
    """Remove an unlock (admin correction) and unequip it in the same breath."""
    cur = await _conn().execute(
        "DELETE FROM cosmetic_unlocks WHERE user_id=? AND key=?",
        (str(user_id), key),
    )
    await _conn().execute(
        "DELETE FROM cosmetic_equipped WHERE user_id=? AND key=?",
        (str(user_id), key),
    )
    await _conn().commit()
    return cur.rowcount > 0


# ── Equipped cosmetics ────────────────────────────────────────────────────────
async def get_equipped(user_id: int) -> dict[str, list[str]]:
    """{slot: [key, …]} in position order — the card's loadout."""
    async with _conn().execute(
        "SELECT slot, position, key FROM cosmetic_equipped WHERE user_id=? "
        "ORDER BY slot ASC, position ASC",
        (str(user_id),),
    ) as cur:
        rows = await cur.fetchall()
    out: dict[str, list[str]] = {}
    for r in rows:
        out.setdefault(r["slot"], []).append(r["key"])
    return out


async def set_equipped(user_id: int, slot: str, keys: list[str]) -> None:
    """Replace everything equipped in one slot (empty list clears it).

    Written as delete-then-insert inside one commit so a slot is never left
    half-populated; positions come from the list order, which is what the card
    renders left-to-right.
    """
    await _conn().execute(
        "DELETE FROM cosmetic_equipped WHERE user_id=? AND slot=?",
        (str(user_id), slot),
    )
    if keys:
        await _conn().executemany(
            "INSERT INTO cosmetic_equipped (user_id, slot, position, key) "
            "VALUES (?,?,?,?)",
            [(str(user_id), slot, i, key) for i, key in enumerate(keys)],
        )
    await _conn().commit()


register_init(_ensure_identity_tables)
