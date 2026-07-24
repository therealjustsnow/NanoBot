"""utils/db.economy — economy + shop.

Part of the utils/db package (split from the former single-file utils/db.py).
Shared connection state + schema machinery live in utils/db/_core.py; this
module holds the economy + shop accessors and registers its table setup with _core so
db.init() creates them in the right order.
"""

import json
import time


from utils import db_crypto

from ._core import _conn, _ensure_columns, register_init, rows_for_users

# ══════════════════════════════════════════════════════════════════════════════
#  Economy (GLOBAL per-user coin balances + daily claim)
#
#  The wallet belongs to the *user*, not to a guild: coins earned in one server
#  spend in every other one. Only guild-owned settings (economy_config), the
#  guild's own shop, and its live co-op boards keep a guild_id — see
#  docs/global-economy.md for the full scope audit. Existing per-guild rows are
#  merged into one wallet per user by migration 1 (utils/db/globalize.py).
# ══════════════════════════════════════════════════════════════════════════════


async def _ensure_economy_tables():
    await _conn().execute("""
        CREATE TABLE IF NOT EXISTS economy (
            user_id     TEXT PRIMARY KEY,
            coins       INTEGER NOT NULL DEFAULT 0,
            last_daily  REAL NOT NULL DEFAULT 0,
            streak      INTEGER NOT NULL DEFAULT 0
        )
    """)
    await _conn().execute(
        "CREATE INDEX IF NOT EXISTS economy_coins ON economy (coins DESC)"
    )
    await _conn().execute("""
        CREATE TABLE IF NOT EXISTS economy_config (
            guild_id        TEXT PRIMARY KEY,
            daily_amount    INTEGER NOT NULL DEFAULT 100,
            streak_bonus    INTEGER NOT NULL DEFAULT 0,
            currency_name   TEXT NOT NULL DEFAULT 'NanoCoin',
            currency_emoji  TEXT NOT NULL DEFAULT '🪙'
        )
    """)
    await _conn().commit()
    # Lifetime co-op contribution stat (never decreases when coins are spent) and
    # the per-confirmed-co-op reward knob — added after the baseline tables.
    await _ensure_columns("economy", {"contribution": "INTEGER NOT NULL DEFAULT 0"})
    await _ensure_columns(
        "economy_config",
        {
            "coop_reward": "INTEGER NOT NULL DEFAULT 50",
            # Group-raid reward (per participant) + party-size bounds.
            "raid_reward": "INTEGER NOT NULL DEFAULT 100",
            "raid_min": "INTEGER NOT NULL DEFAULT 3",
            "raid_max": "INTEGER NOT NULL DEFAULT 20",
        },
    )
    await _conn().execute(
        "CREATE INDEX IF NOT EXISTS economy_contrib ON economy (contribution DESC)"
    )
    # Shop: redeemable rewards mods configure, and a purchase ledger that backs
    # per-user limits, cooldowns, stock counts, and custom-reward fulfilment.
    await _conn().execute("""
        CREATE TABLE IF NOT EXISTS shop_items (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id        TEXT NOT NULL,
            name            TEXT NOT NULL,
            description     TEXT NOT NULL DEFAULT '',
            price           INTEGER NOT NULL,
            kind            TEXT NOT NULL,
            role_id         TEXT,
            payload         TEXT NOT NULL DEFAULT '',
            stock           INTEGER NOT NULL DEFAULT -1,
            per_user_limit  INTEGER NOT NULL DEFAULT 0,
            cooldown        INTEGER NOT NULL DEFAULT 0,
            enabled         INTEGER NOT NULL DEFAULT 1,
            created_at      REAL NOT NULL DEFAULT 0,
            UNIQUE (guild_id, name)
        )
    """)
    await _conn().execute(
        "CREATE INDEX IF NOT EXISTS shop_items_guild ON shop_items (guild_id)"
    )
    await _conn().execute("""
        CREATE TABLE IF NOT EXISTS shop_purchases (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id      TEXT NOT NULL,
            item_id       INTEGER NOT NULL,
            user_id       TEXT NOT NULL,
            item_name     TEXT NOT NULL,
            kind          TEXT NOT NULL,
            price         INTEGER NOT NULL,
            bought_at     REAL NOT NULL,
            fulfilled     INTEGER NOT NULL DEFAULT 0,
            fulfilled_by  TEXT
        )
    """)
    await _conn().execute(
        "CREATE INDEX IF NOT EXISTS shop_purchases_user "
        "ON shop_purchases (guild_id, item_id, user_id)"
    )
    await _conn().execute(
        "CREATE INDEX IF NOT EXISTS shop_purchases_pending "
        "ON shop_purchases (guild_id, kind, fulfilled)"
    )
    # Open /raid join boards — persisted so a restart doesn't orphan the buttons
    # (clicking an orphaned in-memory view shows Discord's "This interaction
    # failed"). Rows are restored on startup and dropped on finish/cancel/expire.
    await _conn().execute("""
        CREATE TABLE IF NOT EXISTS economy_raids (
            raid_id      INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id     TEXT NOT NULL,
            channel_id   TEXT NOT NULL,
            message_id   TEXT,
            host_id      TEXT NOT NULL,
            activity     TEXT NOT NULL DEFAULT '',
            participants TEXT NOT NULL DEFAULT '[]',
            created_at   REAL NOT NULL DEFAULT 0
        )
    """)
    # Pending /squad co-op confirms — same restart-orphan problem as raids: if a
    # restart lands while teammates are still confirming, the button is dead and
    # the payout is lost. Persisted + restored on startup, dropped on confirm/
    # decline/expire. `confirmed` tracks who's pressed Confirm so restart resumes
    # mid-progress.
    await _conn().execute("""
        CREATE TABLE IF NOT EXISTS economy_squads (
            squad_id     INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id     TEXT NOT NULL,
            channel_id   TEXT NOT NULL,
            message_id   TEXT,
            author_id    TEXT NOT NULL,
            partner_ids  TEXT NOT NULL DEFAULT '[]',
            confirmed    TEXT NOT NULL DEFAULT '[]',
            activity     TEXT NOT NULL DEFAULT '',
            created_at   REAL NOT NULL DEFAULT 0
        )
    """)
    await _conn().commit()


# ── Balances (global — one wallet per user) ────────────────────────────────────
async def get_balance(user_id: int) -> int:
    async with _conn().execute(
        "SELECT coins FROM economy WHERE user_id=?", (str(user_id),)
    ) as cur:
        row = await cur.fetchone()
    return row["coins"] if row else 0


async def set_coins(user_id: int, amount: int) -> None:
    await _conn().execute(
        "INSERT INTO economy (user_id, coins) VALUES (?,?) "
        "ON CONFLICT(user_id) DO UPDATE SET coins=excluded.coins",
        (str(user_id), max(0, int(amount))),
    )
    await _conn().commit()


async def add_coins(user_id: int, amount: int) -> int:
    """Add (or subtract) coins atomically. Clamps at 0. Returns the new balance.

    The mutation is a single SQL statement so concurrent callers can't lose an
    update or race a stale read (the old read-modify-write could create or drop
    coins under concurrent /gamble, /pay, level-ups, etc.).
    """
    amount = int(amount)
    await _conn().execute(
        "INSERT INTO economy (user_id, coins) VALUES (?,MAX(0,?)) "
        "ON CONFLICT(user_id) DO UPDATE SET coins=MAX(0, coins + ?)",
        (str(user_id), amount, amount),
    )
    await _conn().commit()
    return await get_balance(user_id)


async def try_debit_coins(user_id: int, amount: int) -> bool:
    """Atomically subtract `amount` only if the balance covers it.

    Returns True on success, False if amount <= 0 or funds are insufficient. The
    conditional UPDATE makes the check-and-debit a single atomic step, so two
    concurrent debits (e.g. rapid /gamble in two different servers) can't both
    spend the same coins.
    """
    if amount <= 0:
        return False
    cur = await _conn().execute(
        "UPDATE economy SET coins = coins - ? WHERE user_id=? AND coins >= ?",
        (int(amount), str(user_id), int(amount)),
    )
    await _conn().commit()
    return cur.rowcount > 0


async def transfer_coins(from_id: int, to_id: int, amount: int) -> bool:
    """Move coins between two users. Returns False if amount <= 0 or low funds.

    The debit is an atomic conditional UPDATE, so concurrent transfers can't
    overdraw the sender.
    """
    if not await try_debit_coins(from_id, amount):
        return False
    await add_coins(to_id, amount)
    return True


async def get_econ_rank(user_id: int) -> tuple[int, int] | None:
    """Return (global rank, coins) for a user; None if they have no wallet row."""
    async with _conn().execute(
        "SELECT coins FROM economy WHERE user_id=?", (str(user_id),)
    ) as cur:
        row = await cur.fetchone()
    if row is None:
        return None
    coins = row["coins"]
    async with _conn().execute(
        "SELECT COUNT(*) FROM economy WHERE coins > ?", (coins,)
    ) as cur:
        ahead = (await cur.fetchone())[0]
    return ahead + 1, coins


async def get_econ_leaderboard(limit: int = 10, offset: int = 0) -> list[dict]:
    """Richest users across every server."""
    async with _conn().execute(
        "SELECT user_id, coins FROM economy WHERE coins > 0 "
        "ORDER BY coins DESC, user_id ASC LIMIT ? OFFSET ?",
        (int(limit), int(offset)),
    ) as cur:
        rows = await cur.fetchall()
    return [{"user_id": int(r["user_id"]), "coins": r["coins"]} for r in rows]


async def count_econ() -> int:
    async with _conn().execute("SELECT COUNT(*) FROM economy WHERE coins > 0") as cur:
        return (await cur.fetchone())[0]


async def get_econ_leaderboard_for(user_ids) -> list[dict]:
    """The same global wallets, filtered to a set of users (a server's members).

    Sorting/paging happens in the caller: the row count is bounded by the
    guild's member list, and chunking keeps the IN clause inside SQLite's
    bound-parameter limit.
    """
    rows = await rows_for_users(
        "SELECT user_id, coins FROM economy", user_ids, positive_col="coins"
    )
    return [{"user_id": int(r["user_id"]), "coins": r["coins"]} for r in rows]


async def reset_economy(user_id: int | None = None) -> int:
    """Wipe one user's wallet, or (user_id None) every wallet everywhere.

    Global data: a full reset is bot-owner-only at the command layer.
    """
    if user_id is None:
        cur = await _conn().execute("DELETE FROM economy")
    else:
        cur = await _conn().execute(
            "DELETE FROM economy WHERE user_id=?", (str(user_id),)
        )
    await _conn().commit()
    return cur.rowcount


# ── Daily claim state (global cooldown + streak) ───────────────────────────────
async def get_daily_state(user_id: int) -> tuple[float, int]:
    """Return (last_daily_epoch, streak) for a user."""
    async with _conn().execute(
        "SELECT last_daily, streak FROM economy WHERE user_id=?", (str(user_id),)
    ) as cur:
        row = await cur.fetchone()
    if row:
        return row["last_daily"], row["streak"]
    return 0.0, 0


async def set_daily_state(user_id: int, last_daily: float, streak: int) -> None:
    await _conn().execute(
        "INSERT INTO economy (user_id, last_daily, streak) VALUES (?,?,?) "
        "ON CONFLICT(user_id) DO UPDATE SET "
        "last_daily=excluded.last_daily, streak=excluded.streak",
        (str(user_id), float(last_daily), int(streak)),
    )
    await _conn().commit()


# ── Config ─────────────────────────────────────────────────────────────────────
async def get_econ_config(guild_id: int) -> dict:
    async with _conn().execute(
        "SELECT daily_amount, streak_bonus, currency_name, currency_emoji, "
        "coop_reward, raid_reward, raid_min, raid_max "
        "FROM economy_config WHERE guild_id=?",
        (str(guild_id),),
    ) as cur:
        row = await cur.fetchone()
    if row:
        return {
            "daily_amount": row["daily_amount"],
            "streak_bonus": row["streak_bonus"],
            "currency_name": row["currency_name"],
            "currency_emoji": row["currency_emoji"],
            "coop_reward": row["coop_reward"],
            "raid_reward": row["raid_reward"],
            "raid_min": row["raid_min"],
            "raid_max": row["raid_max"],
        }
    return {
        "daily_amount": 100,
        "streak_bonus": 0,
        "currency_name": "NanoCoin",
        "currency_emoji": "🪙",
        "coop_reward": 50,
        "raid_reward": 100,
        "raid_min": 3,
        "raid_max": 20,
    }


async def set_econ_config(guild_id: int, **kwargs) -> None:
    current = await get_econ_config(guild_id)
    current.update(kwargs)
    await _conn().execute(
        "INSERT INTO economy_config "
        "(guild_id, daily_amount, streak_bonus, currency_name, currency_emoji, "
        "coop_reward, raid_reward, raid_min, raid_max) "
        "VALUES (?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(guild_id) DO UPDATE SET daily_amount=excluded.daily_amount, "
        "streak_bonus=excluded.streak_bonus, currency_name=excluded.currency_name, "
        "currency_emoji=excluded.currency_emoji, coop_reward=excluded.coop_reward, "
        "raid_reward=excluded.raid_reward, raid_min=excluded.raid_min, "
        "raid_max=excluded.raid_max",
        (
            str(guild_id),
            int(current["daily_amount"]),
            int(current["streak_bonus"]),
            str(current["currency_name"]),
            str(current["currency_emoji"]),
            int(current["coop_reward"]),
            int(current["raid_reward"]),
            int(current["raid_min"]),
            int(current["raid_max"]),
        ),
    )
    await _conn().commit()


# ── Contribution (lifetime co-op stat, global) ─────────────────────────────────
async def add_contribution(user_id: int, amount: int) -> int:
    """Add to a user's lifetime contribution total. Returns the new total.

    Single atomic statement (mirrors add_coins) so concurrent co-op confirms
    can't lose an update. Contribution never decreases on spend.
    """
    amount = int(amount)
    await _conn().execute(
        "INSERT INTO economy (user_id, contribution) VALUES (?,MAX(0,?)) "
        "ON CONFLICT(user_id) DO UPDATE SET contribution=MAX(0, contribution + ?)",
        (str(user_id), amount, amount),
    )
    await _conn().commit()
    return await get_contribution(user_id)


async def get_contribution(user_id: int) -> int:
    async with _conn().execute(
        "SELECT contribution FROM economy WHERE user_id=?", (str(user_id),)
    ) as cur:
        row = await cur.fetchone()
    return row["contribution"] if row else 0


async def get_contrib_rank(user_id: int) -> tuple[int, int] | None:
    """Return (global rank, contribution); None if they have no points."""
    points = await get_contribution(user_id)
    if points <= 0:
        return None
    async with _conn().execute(
        "SELECT COUNT(*) FROM economy WHERE contribution > ?", (points,)
    ) as cur:
        ahead = (await cur.fetchone())[0]
    return ahead + 1, points


async def get_contrib_leaderboard(limit: int = 10, offset: int = 0) -> list[dict]:
    async with _conn().execute(
        "SELECT user_id, contribution FROM economy WHERE contribution > 0 "
        "ORDER BY contribution DESC, user_id ASC LIMIT ? OFFSET ?",
        (int(limit), int(offset)),
    ) as cur:
        rows = await cur.fetchall()
    return [
        {"user_id": int(r["user_id"]), "contribution": r["contribution"]} for r in rows
    ]


async def count_contrib() -> int:
    async with _conn().execute(
        "SELECT COUNT(*) FROM economy WHERE contribution > 0"
    ) as cur:
        return (await cur.fetchone())[0]


async def get_contrib_leaderboard_for(user_ids) -> list[dict]:
    """Contribution rows for a set of users (the server-scoped view)."""
    rows = await rows_for_users(
        "SELECT user_id, contribution FROM economy",
        user_ids,
        positive_col="contribution",
    )
    return [
        {"user_id": int(r["user_id"]), "contribution": r["contribution"]} for r in rows
    ]


# ══════════════════════════════════════════════════════════════════════════════
#  Shop (redeemable rewards + purchase ledger)
# ══════════════════════════════════════════════════════════════════════════════


def _shop_row(row) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "description": row["description"],
        "price": row["price"],
        "kind": row["kind"],
        "role_id": int(row["role_id"]) if row["role_id"] else None,
        "payload": row["payload"],
        "stock": row["stock"],
        "per_user_limit": row["per_user_limit"],
        "cooldown": row["cooldown"],
        "enabled": bool(row["enabled"]),
    }


async def add_shop_item(
    guild_id: int,
    name: str,
    price: int,
    kind: str,
    *,
    description: str = "",
    role_id: int | None = None,
    payload: str = "",
    stock: int = -1,
    per_user_limit: int = 0,
    cooldown: int = 0,
) -> int | None:
    """Create a shop item. Returns the new item id, or None if the name is taken."""
    try:
        cur = await _conn().execute(
            "INSERT INTO shop_items (guild_id, name, description, price, kind, "
            "role_id, payload, stock, per_user_limit, cooldown, enabled, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,1,?)",
            (
                str(guild_id),
                name,
                description,
                int(price),
                kind,
                str(role_id) if role_id else None,
                payload,
                int(stock),
                int(per_user_limit),
                int(cooldown),
                time.time(),
            ),
        )
    except db_crypto.INTEGRITY_ERRORS:
        return None
    await _conn().commit()
    return cur.lastrowid


async def edit_shop_item(guild_id: int, item_id: int, **fields) -> bool:
    """Update mutable fields of an item. Returns False if nothing matched."""
    allowed = {
        "name",
        "description",
        "price",
        "role_id",
        "payload",
        "stock",
        "per_user_limit",
        "cooldown",
        "enabled",
    }
    sets, params = [], []
    for key, val in fields.items():
        if key not in allowed:
            continue
        sets.append(f"{key}=?")
        if key == "role_id":
            params.append(str(val) if val else None)
        elif key == "enabled":
            params.append(1 if val else 0)
        else:
            params.append(val)
    if not sets:
        return False
    params += [str(guild_id), int(item_id)]
    try:
        cur = await _conn().execute(
            f"UPDATE shop_items SET {', '.join(sets)} WHERE guild_id=? AND id=?",
            params,
        )
    except db_crypto.INTEGRITY_ERRORS:
        return False
    await _conn().commit()
    return cur.rowcount > 0


async def remove_shop_item(guild_id: int, item_id: int) -> bool:
    cur = await _conn().execute(
        "DELETE FROM shop_items WHERE guild_id=? AND id=?",
        (str(guild_id), int(item_id)),
    )
    await _conn().commit()
    return cur.rowcount > 0


async def get_shop_item(guild_id: int, item_id: int) -> dict | None:
    async with _conn().execute(
        "SELECT * FROM shop_items WHERE guild_id=? AND id=?",
        (str(guild_id), int(item_id)),
    ) as cur:
        row = await cur.fetchone()
    return _shop_row(row) if row else None


async def get_shop_item_by_name(guild_id: int, name: str) -> dict | None:
    """Case-insensitive name lookup (shop names are unique per guild)."""
    async with _conn().execute(
        "SELECT * FROM shop_items WHERE guild_id=? AND name=? COLLATE NOCASE",
        (str(guild_id), name),
    ) as cur:
        row = await cur.fetchone()
    return _shop_row(row) if row else None


async def list_shop_items(
    guild_id: int, *, enabled_only: bool = False, limit: int = 100, offset: int = 0
) -> list[dict]:
    sql = "SELECT * FROM shop_items WHERE guild_id=?"
    if enabled_only:
        sql += " AND enabled=1"
    sql += " ORDER BY price ASC, id ASC LIMIT ? OFFSET ?"
    async with _conn().execute(sql, (str(guild_id), int(limit), int(offset))) as cur:
        rows = await cur.fetchall()
    return [_shop_row(r) for r in rows]


async def count_shop_items(guild_id: int, *, enabled_only: bool = False) -> int:
    sql = "SELECT COUNT(*) FROM shop_items WHERE guild_id=?"
    if enabled_only:
        sql += " AND enabled=1"
    async with _conn().execute(sql, (str(guild_id),)) as cur:
        return (await cur.fetchone())[0]


async def count_user_purchases(guild_id: int, item_id: int, user_id: int) -> int:
    async with _conn().execute(
        "SELECT COUNT(*) FROM shop_purchases "
        "WHERE guild_id=? AND item_id=? AND user_id=?",
        (str(guild_id), int(item_id), str(user_id)),
    ) as cur:
        return (await cur.fetchone())[0]


async def last_purchase_time(guild_id: int, item_id: int, user_id: int) -> float:
    async with _conn().execute(
        "SELECT MAX(bought_at) FROM shop_purchases "
        "WHERE guild_id=? AND item_id=? AND user_id=?",
        (str(guild_id), int(item_id), str(user_id)),
    ) as cur:
        row = await cur.fetchone()
    return row[0] or 0.0


async def purchase_item(guild_id: int, item_id: int, user_id: int) -> dict:
    """Atomically attempt a purchase, enforcing stock, per-user limit, cooldown,
    and funds.

    Returns a dict with "ok" plus a "reason" on failure
    ("missing"/"disabled"/"limit"/"cooldown"/"out_of_stock"/"funds"), a
    "retry_after" for cooldown failures, and on success "item" + "new_balance".
    Stock is decremented with an atomic conditional UPDATE and refunded if the
    coin debit then fails, so a sold-out race can never overspend or oversell.
    """
    item = await get_shop_item(guild_id, item_id)
    if not item:
        return {"ok": False, "reason": "missing"}
    if not item["enabled"]:
        return {"ok": False, "reason": "disabled"}

    if item["per_user_limit"] > 0:
        bought = await count_user_purchases(guild_id, item_id, user_id)
        if bought >= item["per_user_limit"]:
            return {"ok": False, "reason": "limit", "item": item}
    if item["cooldown"] > 0:
        last = await last_purchase_time(guild_id, item_id, user_id)
        elapsed = time.time() - last
        if last and elapsed < item["cooldown"]:
            return {
                "ok": False,
                "reason": "cooldown",
                "retry_after": int(item["cooldown"] - elapsed),
                "item": item,
            }

    # Reserve stock first (atomic), so two buyers can't claim the last unit.
    if item["stock"] != -1:
        cur = await _conn().execute(
            "UPDATE shop_items SET stock=stock-1 "
            "WHERE guild_id=? AND id=? AND stock>0",
            (str(guild_id), int(item_id)),
        )
        await _conn().commit()
        if cur.rowcount == 0:
            return {"ok": False, "reason": "out_of_stock", "item": item}

    # Charge the buyer; refund the reserved stock if they can't afford it.
    if not await try_debit_coins(user_id, item["price"]):
        if item["stock"] != -1:
            await _conn().execute(
                "UPDATE shop_items SET stock=stock+1 WHERE guild_id=? AND id=?",
                (str(guild_id), int(item_id)),
            )
            await _conn().commit()
        return {"ok": False, "reason": "funds", "item": item}

    await _conn().execute(
        "INSERT INTO shop_purchases (guild_id, item_id, user_id, item_name, kind, "
        "price, bought_at, fulfilled) VALUES (?,?,?,?,?,?,?,?)",
        (
            str(guild_id),
            int(item_id),
            str(user_id),
            item["name"],
            item["kind"],
            item["price"],
            time.time(),
            # Role rewards are granted immediately; custom rewards await a mod.
            1 if item["kind"] == "role" else 0,
        ),
    )
    await _conn().commit()
    new_balance = await get_balance(user_id)
    return {"ok": True, "item": item, "new_balance": new_balance}


async def restock_shop_item(guild_id: int, item_id: int, delta: int = 1) -> None:
    """Atomically put reserved stock back (refund path after a failed grant).

    A relative increment, never an absolute SET — a stale pre-purchase stock
    snapshot written back absolutely would silently erase a concurrent buyer's
    decrement and oversell a limited item.
    """
    await _conn().execute(
        "UPDATE shop_items SET stock=stock+? WHERE guild_id=? AND id=? AND stock>=0",
        (int(delta), str(guild_id), int(item_id)),
    )
    await _conn().commit()


async def list_pending_purchases(
    guild_id: int, limit: int = 25, offset: int = 0
) -> list[dict]:
    """Unfulfilled custom-reward purchases, oldest first (the mod fulfil queue)."""
    async with _conn().execute(
        "SELECT id, item_id, user_id, item_name, price, bought_at FROM shop_purchases "
        "WHERE guild_id=? AND kind='custom' AND fulfilled=0 "
        "ORDER BY bought_at ASC LIMIT ? OFFSET ?",
        (str(guild_id), int(limit), int(offset)),
    ) as cur:
        rows = await cur.fetchall()
    return [
        {
            "id": r["id"],
            "item_id": r["item_id"],
            "user_id": int(r["user_id"]),
            "item_name": r["item_name"],
            "price": r["price"],
            "bought_at": r["bought_at"],
        }
        for r in rows
    ]


async def count_pending_purchases(guild_id: int) -> int:
    async with _conn().execute(
        "SELECT COUNT(*) FROM shop_purchases "
        "WHERE guild_id=? AND kind='custom' AND fulfilled=0",
        (str(guild_id),),
    ) as cur:
        return (await cur.fetchone())[0]


async def fulfill_purchase(guild_id: int, purchase_id: int, mod_id: int) -> dict | None:
    """Mark a pending custom purchase fulfilled. Returns the purchase, or None."""
    async with _conn().execute(
        "SELECT id, user_id, item_name FROM shop_purchases "
        "WHERE guild_id=? AND id=? AND fulfilled=0",
        (str(guild_id), int(purchase_id)),
    ) as cur:
        row = await cur.fetchone()
    if not row:
        return None
    await _conn().execute(
        "UPDATE shop_purchases SET fulfilled=1, fulfilled_by=? WHERE id=?",
        (str(mod_id), int(purchase_id)),
    )
    await _conn().commit()
    return {
        "id": row["id"],
        "user_id": int(row["user_id"]),
        "item_name": row["item_name"],
    }


# ══════════════════════════════════════════════════════════════════════════════
#  Raids (open /raid join boards — persisted for restart-safe buttons)
# ══════════════════════════════════════════════════════════════════════════════


async def create_raid(
    guild_id: int,
    channel_id: int,
    host_id: int,
    activity: str,
    participants: list[int],
    created_at: float,
) -> int:
    """Insert an open raid and return its new id (used in the button custom_ids)."""
    cur = await _conn().execute(
        "INSERT INTO economy_raids "
        "(guild_id, channel_id, host_id, activity, participants, created_at) "
        "VALUES (?,?,?,?,?,?)",
        (
            str(guild_id),
            str(channel_id),
            str(host_id),
            activity,
            json.dumps([int(u) for u in participants]),
            float(created_at),
        ),
    )
    await _conn().commit()
    return cur.lastrowid


async def set_raid_message(raid_id: int, message_id: int) -> None:
    await _conn().execute(
        "UPDATE economy_raids SET message_id=? WHERE raid_id=?",
        (str(message_id), int(raid_id)),
    )
    await _conn().commit()


async def set_raid_participants(raid_id: int, participants: list[int]) -> None:
    await _conn().execute(
        "UPDATE economy_raids SET participants=? WHERE raid_id=?",
        (json.dumps([int(u) for u in participants]), int(raid_id)),
    )
    await _conn().commit()


async def delete_raid(raid_id: int) -> None:
    await _conn().execute("DELETE FROM economy_raids WHERE raid_id=?", (int(raid_id),))
    await _conn().commit()


async def get_open_raids() -> list[dict]:
    """All persisted raids (message_id may be NULL if a crash beat the update)."""
    async with _conn().execute(
        "SELECT raid_id, guild_id, channel_id, message_id, host_id, activity, "
        "participants, created_at FROM economy_raids"
    ) as cur:
        rows = await cur.fetchall()
    out = []
    for r in rows:
        try:
            participants = [int(u) for u in json.loads(r["participants"] or "[]")]
        except (ValueError, TypeError):
            participants = []
        out.append(
            {
                "raid_id": r["raid_id"],
                "guild_id": int(r["guild_id"]),
                "channel_id": int(r["channel_id"]),
                "message_id": int(r["message_id"]) if r["message_id"] else None,
                "host_id": int(r["host_id"]),
                "activity": r["activity"] or "",
                "participants": participants,
                "created_at": r["created_at"],
            }
        )
    return out


# ══════════════════════════════════════════════════════════════════════════════
#  Squads (pending /squad co-op confirms — persisted for restart-safe payout)
# ══════════════════════════════════════════════════════════════════════════════


async def create_squad(
    guild_id: int,
    channel_id: int,
    author_id: int,
    partner_ids: list[int],
    activity: str,
    created_at: float,
) -> int:
    """Insert a pending squad confirm and return its new id (used in custom_ids)."""
    cur = await _conn().execute(
        "INSERT INTO economy_squads "
        "(guild_id, channel_id, author_id, partner_ids, activity, created_at) "
        "VALUES (?,?,?,?,?,?)",
        (
            str(guild_id),
            str(channel_id),
            str(author_id),
            json.dumps([int(u) for u in partner_ids]),
            activity,
            float(created_at),
        ),
    )
    await _conn().commit()
    return cur.lastrowid


async def set_squad_message(squad_id: int, message_id: int) -> None:
    await _conn().execute(
        "UPDATE economy_squads SET message_id=? WHERE squad_id=?",
        (str(message_id), int(squad_id)),
    )
    await _conn().commit()


async def set_squad_confirmed(squad_id: int, confirmed: list[int]) -> None:
    await _conn().execute(
        "UPDATE economy_squads SET confirmed=? WHERE squad_id=?",
        (json.dumps([int(u) for u in confirmed]), int(squad_id)),
    )
    await _conn().commit()


async def delete_squad(squad_id: int) -> None:
    await _conn().execute(
        "DELETE FROM economy_squads WHERE squad_id=?", (int(squad_id),)
    )
    await _conn().commit()


async def get_open_squads() -> list[dict]:
    """All persisted pending squad confirms (message_id may be NULL after a crash)."""
    async with _conn().execute(
        "SELECT squad_id, guild_id, channel_id, message_id, author_id, partner_ids, "
        "confirmed, activity, created_at FROM economy_squads"
    ) as cur:
        rows = await cur.fetchall()
    out = []
    for r in rows:
        try:
            partner_ids = [int(u) for u in json.loads(r["partner_ids"] or "[]")]
        except (ValueError, TypeError):
            partner_ids = []
        try:
            confirmed = [int(u) for u in json.loads(r["confirmed"] or "[]")]
        except (ValueError, TypeError):
            confirmed = []
        out.append(
            {
                "squad_id": r["squad_id"],
                "guild_id": int(r["guild_id"]),
                "channel_id": int(r["channel_id"]),
                "message_id": int(r["message_id"]) if r["message_id"] else None,
                "author_id": int(r["author_id"]),
                "partner_ids": partner_ids,
                "confirmed": confirmed,
                "activity": r["activity"] or "",
                "created_at": r["created_at"],
            }
        )
    return out


register_init(_ensure_economy_tables)
