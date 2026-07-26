"""utils/db.items — generic inventory, active effects, and economy events.

Part of the utils/db package. Three tables shared by every economy feature:

- ``user_items``      — GLOBAL per-user item stacks: (user, item_key) → qty.
  Items follow the user across servers, exactly like their coin balance.
  Item semantics live in the code-side catalogue (utils/items.py), so new item
  types never need schema changes.
- ``user_effects``    — active buffs a user has (from consumables, bait, …),
  also global: a buff armed in one server applies in every server.
  An effect is timed (``expires_at`` epoch) or charge-based (``uses_left``);
  whichever cog cares interprets the effect key — this layer only stores it.
- ``economy_events``  — running server-wide (or, with guild_id='0', global)
  limited-time events (feeding frenzy, double XP, …). Feature cogs read the
  active set and apply whatever modifiers the event's key/magnitude mean to
  them, so new event types are data, not schema.

Quantity mutations mirror the economy-layer atomicity rules: grants are single
upsert statements, spends are conditional UPDATEs that fail rather than go
negative (the try_debit_coins pattern), so double-sends can't dupe or overspend
items.
"""

import json
import time

from ._core import _commit, _conn, fetch_one_returning, register_init, transaction


async def _ensure_items_tables():
    await _conn().execute("""
        CREATE TABLE IF NOT EXISTS user_items (
            user_id   TEXT NOT NULL,
            item_key  TEXT NOT NULL,
            qty       INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (user_id, item_key)
        )
    """)
    await _conn().execute("""
        CREATE TABLE IF NOT EXISTS user_effects (
            user_id    TEXT NOT NULL,
            effect_key TEXT NOT NULL,
            magnitude  REAL NOT NULL DEFAULT 0,
            expires_at REAL NOT NULL DEFAULT 0,
            uses_left  INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (user_id, effect_key)
        )
    """)
    await _conn().execute("""
        CREATE TABLE IF NOT EXISTS economy_events (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id   TEXT NOT NULL,
            event_key  TEXT NOT NULL,
            magnitude  REAL NOT NULL DEFAULT 1,
            data       TEXT NOT NULL DEFAULT '{}',
            started_at REAL NOT NULL DEFAULT 0,
            ends_at    REAL NOT NULL DEFAULT 0
        )
    """)
    await _conn().execute(
        "CREATE INDEX IF NOT EXISTS economy_events_guild "
        "ON economy_events (guild_id, ends_at)"
    )
    # The guild index can't serve the expiry sweep (guild_id isn't in its
    # predicate), so give prune_events its own leading-column index.
    await _conn().execute(
        "CREATE INDEX IF NOT EXISTS economy_events_ends ON economy_events (ends_at)"
    )
    await _commit()


# ── Inventory ──────────────────────────────────────────────────────────────────
async def get_inventory(user_id: int) -> list[dict]:
    """A member's item stacks: [{item_key, qty}, …] with qty > 0."""
    async with _conn().execute(
        "SELECT item_key, qty FROM user_items "
        "WHERE user_id=? AND qty > 0 ORDER BY item_key ASC",
        (str(user_id),),
    ) as cur:
        rows = await cur.fetchall()
    return [{"item_key": r["item_key"], "qty": r["qty"]} for r in rows]


async def get_item_qty(user_id: int, item_key: str) -> int:
    async with _conn().execute(
        "SELECT qty FROM user_items WHERE user_id=? AND item_key=?",
        (str(user_id), item_key),
    ) as cur:
        row = await cur.fetchone()
    return row["qty"] if row else 0


async def add_item(user_id: int, item_key: str, qty: int = 1) -> int:
    """Grant items (atomic upsert). Returns the new quantity."""
    row = await fetch_one_returning(
        "INSERT INTO user_items (user_id, item_key, qty) VALUES (?,?,MAX(0,?)) "
        "ON CONFLICT(user_id, item_key) DO UPDATE SET qty=MAX(0, qty + ?)",
        (str(user_id), item_key, int(qty), int(qty)),
        returning="qty",
    )
    if row is not None:
        return row["qty"]
    return await get_item_qty(user_id, item_key)


async def try_consume_item(user_id: int, item_key: str, qty: int = 1) -> bool:
    """Atomically spend `qty` of an item only if the member owns that many.

    Conditional UPDATE (the try_debit_coins pattern) so two concurrent uses
    can't both consume the last one. Returns False on qty <= 0 or short stock.
    """
    if qty <= 0:
        return False
    cur = await _conn().execute(
        "UPDATE user_items SET qty = qty - ? "
        "WHERE user_id=? AND item_key=? AND qty >= ?",
        (int(qty), str(user_id), item_key, int(qty)),
    )
    await _commit()
    return cur.rowcount > 0


async def transfer_item(from_id: int, to_id: int, item_key: str, qty: int = 1) -> bool:
    """Move items between members. False if the sender is short.

    One transaction, for the same reason as economy.transfer_coins: consuming
    and granting in separate commits could vanish the items in between.
    """
    async with transaction():
        if not await try_consume_item(from_id, item_key, qty):
            return False
        await add_item(to_id, item_key, qty)
    return True


# ── Effects ────────────────────────────────────────────────────────────────────
async def grant_effect(
    user_id: int,
    effect_key: str,
    magnitude: float,
    *,
    duration: float = 0,
    uses: int = 0,
    now: float | None = None,
) -> None:
    """Store an active effect. Timed effects extend from now; re-granting a
    charge effect refreshes its charges. A re-grant replaces the old magnitude
    (effects don't stack — the freshest consumable wins)."""
    now = time.time() if now is None else now
    expires_at = now + duration if duration > 0 else 0
    # Reclaim this member's dead rows here rather than on every read.
    await prune_effects(user_id, now)
    await _conn().execute(
        "INSERT INTO user_effects "
        "(user_id, effect_key, magnitude, expires_at, uses_left) "
        "VALUES (?,?,?,?,?) "
        "ON CONFLICT(user_id, effect_key) DO UPDATE SET "
        "magnitude=excluded.magnitude, expires_at=excluded.expires_at, "
        "uses_left=excluded.uses_left",
        (
            str(user_id),
            effect_key,
            float(magnitude),
            float(expires_at),
            int(uses),
        ),
    )
    await _commit()


async def get_active_effects(user_id: int, now: float | None = None) -> dict[str, dict]:
    """Live effects for a member: {effect_key: {magnitude, expires_at,
    uses_left}}. Expired timed effects are filtered out, never returned.

    This is a read: it does not write. Hiding an expired row in the WHERE
    clause is what callers actually need, and the DELETE+commit this used to do
    on every lookup cost a write transaction on the hottest read in the economy
    (/fish cast alone calls it every cast). Rows are reclaimed instead by
    `prune_effects`, which grant_effect runs on the way past.
    """
    now = time.time() if now is None else now
    async with _conn().execute(
        "SELECT effect_key, magnitude, expires_at, uses_left FROM user_effects "
        "WHERE user_id=? AND (expires_at = 0 OR expires_at > ?)",
        (str(user_id), float(now)),
    ) as cur:
        rows = await cur.fetchall()
    return {
        r["effect_key"]: {
            "magnitude": r["magnitude"],
            "expires_at": r["expires_at"],
            "uses_left": r["uses_left"],
        }
        for r in rows
    }


async def prune_effects(user_id: int, now: float | None = None) -> int:
    """Delete a member's expired timed effects. Returns rows removed.

    The reclaim half of the old prune-on-read: reads filter expired rows out,
    and this runs on the (far rarer) write paths so the table still doesn't
    accumulate dead rows.
    """
    now = time.time() if now is None else now
    cur = await _conn().execute(
        "DELETE FROM user_effects WHERE user_id=? "
        "AND expires_at > 0 AND expires_at <= ?",
        (str(user_id), float(now)),
    )
    await _commit()
    return cur.rowcount


async def consume_effect_use(user_id: int, effect_key: str) -> bool:
    """Spend one charge of a charge-based effect; the row is deleted at zero.

    Conditional UPDATE so concurrent consumers can't spend the same charge.
    Returns False when the effect is missing or out of charges.
    """
    cur = await _conn().execute(
        "UPDATE user_effects SET uses_left = uses_left - 1 "
        "WHERE user_id=? AND effect_key=? AND uses_left >= 1",
        (str(user_id), effect_key),
    )
    await _commit()
    if cur.rowcount == 0:
        return False
    await _conn().execute(
        "DELETE FROM user_effects "
        "WHERE user_id=? AND effect_key=? AND uses_left <= 0 AND expires_at = 0",
        (str(user_id), effect_key),
    )
    await _commit()
    return True


async def clear_effect(user_id: int, effect_key: str) -> None:
    await _conn().execute(
        "DELETE FROM user_effects WHERE user_id=? AND effect_key=?",
        (str(user_id), effect_key),
    )
    await _commit()


# ── Economy events ─────────────────────────────────────────────────────────────
GLOBAL_EVENT_GUILD = "0"  # guild_id sentinel: event applies in every guild


async def start_event(
    guild_id: int | None,
    event_key: str,
    magnitude: float,
    duration: float,
    *,
    data: dict | None = None,
    now: float | None = None,
) -> int:
    """Start a limited-time event. guild_id None = global. Returns the row id."""
    now = time.time() if now is None else now
    await prune_events(now)
    cur = await _conn().execute(
        "INSERT INTO economy_events "
        "(guild_id, event_key, magnitude, data, started_at, ends_at) "
        "VALUES (?,?,?,?,?,?)",
        (
            GLOBAL_EVENT_GUILD if guild_id is None else str(guild_id),
            event_key,
            float(magnitude),
            json.dumps(data or {}),
            float(now),
            float(now + duration),
        ),
    )
    await _commit()
    return cur.lastrowid


async def get_active_events(guild_id: int, now: float | None = None) -> list[dict]:
    """Events live in this guild right now (guild-scoped + global), soonest-ending
    first. Ended rows are filtered out by the query, never returned.

    Like get_active_effects this is a pure read. The prune it used to do here
    was a full table scan (the `(guild_id, ends_at)` index can't serve a bare
    `ends_at` predicate) plus a commit, on a path /fish cast hits twice per
    cast; `prune_events` now does it from start_event instead.
    """
    now = time.time() if now is None else now
    async with _conn().execute(
        "SELECT id, guild_id, event_key, magnitude, data, started_at, ends_at "
        "FROM economy_events WHERE guild_id IN (?, ?) AND ends_at > ? "
        "ORDER BY ends_at ASC",
        (str(guild_id), GLOBAL_EVENT_GUILD, float(now)),
    ) as cur:
        rows = await cur.fetchall()
    out = []
    for r in rows:
        try:
            data = json.loads(r["data"] or "{}")
        except ValueError:
            data = {}
        out.append(
            {
                "id": r["id"],
                "guild_id": int(r["guild_id"]),
                "event_key": r["event_key"],
                "magnitude": r["magnitude"],
                "data": data,
                "started_at": r["started_at"],
                "ends_at": r["ends_at"],
            }
        )
    return out


async def prune_events(now: float | None = None) -> int:
    """Delete every event that has ended. Returns rows removed.

    Index-served by `economy_events_ends`, and called from start_event rather
    than from the read path.
    """
    now = time.time() if now is None else now
    cur = await _conn().execute(
        "DELETE FROM economy_events WHERE ends_at <= ?", (float(now),)
    )
    await _commit()
    return cur.rowcount


async def end_event(event_id: int) -> None:
    await _conn().execute("DELETE FROM economy_events WHERE id=?", (int(event_id),))
    await _commit()


register_init(_ensure_items_tables)
