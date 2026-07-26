"""utils/db.gatekeeper — gatekeeper config/pending.

Part of the utils/db package (split from the former single-file utils/db.py).
Shared connection state + schema machinery live in utils/db/_core.py; this
module holds the gatekeeper config/pending accessors and registers its table setup with _core so
db.init() creates them in the right order.
"""

import time
from typing import Any

import aiosqlite


from ._core import _commit, _conn, _ensure_columns, register_init

# ══════════════════════════════════════════════════════════════════════════════
#  Gatekeeper (new-account mute + captcha verification)
# ══════════════════════════════════════════════════════════════════════════════
# `gatekeeper_config` holds per-guild settings. `gatekeeper_pending` tracks every
# member currently held behind the mute role: `unmute_at` is set only for
# account-age mutes (auto-lifts once the account is old enough); `kick_at` is set
# whenever verification is on (the member is kicked if still unverified by then).
# Verifying or leaving removes the row.

# Default timings: mute accounts younger than 30d, auto-unmute at 35d (5 weeks),
# kick unverified members after 7d.
_GK_DEFAULTS = {
    "enabled": False,
    "mute_role_id": None,
    "quarantine_channel_id": None,
    "log_channel_id": None,
    "min_account_age": 2592000,  # 30 days
    "unmute_age": 3024000,  # 35 days (5 weeks)
    "mute_new_accounts": True,
    "mute_default_avatar": True,  # Discord's auto-assigned logo avatars
    "mute_stock_avatar": True,  # pickable stock avatars matched against the catalog
    "verify_enabled": True,
    "verify_message": None,
    "kick_timeout": 604800,  # 7 days
    "stock_threshold": 8,  # max dHash Hamming distance for a stock-avatar match
    # How the account-age and bad-avatar checks combine, per guild:
    #   "or"  → mute if young OR bad-avatar (original behaviour)
    #   "and" → mute only if young AND (no-avatar or stock-avatar)
    "match_mode": "or",
    # When the account-age check is one of the mute reasons, auto-unmute once the
    # account crosses unmute_age. Off → those members must verify to get out.
    "age_unmute_enabled": True,
}

# Which columns are stored as 0/1 integers but exposed as Python bools.
_GK_BOOL_COLS = (
    "enabled",
    "mute_new_accounts",
    "mute_default_avatar",
    "mute_stock_avatar",
    "verify_enabled",
    "age_unmute_enabled",
)
# Ordered list of all config columns (used for the upsert in set_gatekeeper_config).
_GK_COLS = (
    "enabled",
    "mute_role_id",
    "quarantine_channel_id",
    "log_channel_id",
    "min_account_age",
    "unmute_age",
    "mute_new_accounts",
    "mute_default_avatar",
    "mute_stock_avatar",
    "verify_enabled",
    "verify_message",
    "kick_timeout",
    "stock_threshold",
    "match_mode",
    "age_unmute_enabled",
)


async def _ensure_gatekeeper_tables() -> None:
    await _conn().executescript("""
        CREATE TABLE IF NOT EXISTS gatekeeper_config (
            guild_id              TEXT PRIMARY KEY,
            enabled               INTEGER NOT NULL DEFAULT 0,
            mute_role_id          TEXT,
            quarantine_channel_id TEXT,
            log_channel_id        TEXT,
            min_account_age       INTEGER NOT NULL DEFAULT 2592000,
            unmute_age            INTEGER NOT NULL DEFAULT 3024000,
            mute_new_accounts     INTEGER NOT NULL DEFAULT 1,
            mute_default_avatar   INTEGER NOT NULL DEFAULT 1,
            mute_stock_avatar     INTEGER NOT NULL DEFAULT 1,
            verify_enabled        INTEGER NOT NULL DEFAULT 1,
            verify_message        TEXT,
            kick_timeout          INTEGER NOT NULL DEFAULT 604800,
            stock_threshold       INTEGER NOT NULL DEFAULT 8,
            match_mode            TEXT NOT NULL DEFAULT 'or',
            age_unmute_enabled    INTEGER NOT NULL DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS gatekeeper_pending (
            key         TEXT PRIMARY KEY,   -- "guild_id:user_id"
            guild_id    TEXT NOT NULL,
            user_id     TEXT NOT NULL,
            reason      TEXT NOT NULL,
            unmute_at   REAL,
            kick_at     REAL,
            created_at  REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS gk_pending_guild ON gatekeeper_pending (guild_id);
    """)
    await _commit()
    # Added after the table's first cut — backfill for early-branch databases.
    await _ensure_columns(
        "gatekeeper_config",
        {
            "stock_threshold": "INTEGER NOT NULL DEFAULT 8",
            "match_mode": "TEXT NOT NULL DEFAULT 'or'",
            "age_unmute_enabled": "INTEGER NOT NULL DEFAULT 1",
        },
    )


def _gatekeeper_row(row: aiosqlite.Row) -> dict:
    out: dict = {}
    for col in _GK_COLS:
        val = row[col]
        out[col] = bool(val) if col in _GK_BOOL_COLS else val
    return out


async def get_gatekeeper_config(guild_id: int) -> dict:
    """Return the gatekeeper config for a guild, defaults merged in when unset."""
    async with _conn().execute(
        f"SELECT {', '.join(_GK_COLS)} FROM gatekeeper_config WHERE guild_id=? LIMIT 1",
        (str(guild_id),),
    ) as cur:
        row = await cur.fetchone()
    return _gatekeeper_row(row) if row else dict(_GK_DEFAULTS)


async def set_gatekeeper_config(guild_id: int, **kwargs: Any) -> None:
    """Merge kwargs into the guild's config row (creating it from defaults)."""
    current = await get_gatekeeper_config(guild_id)
    current.update(kwargs)
    values = [str(guild_id)]
    for col in _GK_COLS:
        val = current[col]
        if col in _GK_BOOL_COLS:
            values.append(1 if val else 0)
        else:
            values.append(val)
    placeholders = ", ".join(["?"] * (len(_GK_COLS) + 1))
    updates = ", ".join(f"{c}=excluded.{c}" for c in _GK_COLS)
    await _conn().execute(
        f"INSERT INTO gatekeeper_config (guild_id, {', '.join(_GK_COLS)}) "
        f"VALUES ({placeholders}) "
        f"ON CONFLICT(guild_id) DO UPDATE SET {updates}",
        values,
    )
    await _commit()


async def set_gatekeeper_pending(
    key: str,
    guild_id: int,
    user_id: int,
    reason: str,
    unmute_at: float | None,
    kick_at: float | None,
) -> None:
    """Insert or replace a pending gatekeeper hold for a member."""
    await _conn().execute(
        "INSERT INTO gatekeeper_pending "
        "(key, guild_id, user_id, reason, unmute_at, kick_at, created_at) "
        "VALUES (?,?,?,?,?,?,?) "
        "ON CONFLICT(key) DO UPDATE SET reason=excluded.reason, "
        "unmute_at=excluded.unmute_at, kick_at=excluded.kick_at",
        (
            key,
            str(guild_id),
            str(user_id),
            reason,
            unmute_at,
            kick_at,
            time.time(),
        ),
    )
    await _commit()


async def get_gatekeeper_pending(key: str) -> dict | None:
    async with _conn().execute(
        "SELECT key, guild_id, user_id, reason, unmute_at, kick_at, created_at "
        "FROM gatekeeper_pending WHERE key=? LIMIT 1",
        (key,),
    ) as cur:
        row = await cur.fetchone()
    return dict(row) if row else None


async def get_all_gatekeeper_pending() -> dict:
    async with _conn().execute(
        "SELECT key, guild_id, user_id, reason, unmute_at, kick_at, created_at "
        "FROM gatekeeper_pending"
    ) as cur:
        rows = await cur.fetchall()
    return {r["key"]: dict(r) for r in rows}


async def remove_gatekeeper_pending(key: str) -> None:
    await _conn().execute("DELETE FROM gatekeeper_pending WHERE key=?", (key,))
    await _commit()


register_init(_ensure_gatekeeper_tables)
