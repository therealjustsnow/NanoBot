"""utils/db.prefixes — per-guild prefix.

Part of the utils/db package (split from the former single-file utils/db.py).
Shared connection state + schema machinery live in utils/db/_core.py; this
module holds the per-guild prefix accessors and registers its table setup with _core so
db.init() creates them in the right order.
"""

from ._core import _commit, _conn

# ══════════════════════════════════════════════════════════════════════════════
#  Prefixes
# ══════════════════════════════════════════════════════════════════════════════


async def get_prefix(guild_id: int) -> str | None:
    async with _conn().execute(
        "SELECT prefix FROM prefixes WHERE guild_id=?", (str(guild_id),)
    ) as cur:
        row = await cur.fetchone()
    return row["prefix"] if row else None


async def set_prefix(guild_id: int, prefix: str) -> None:
    await _conn().execute(
        "INSERT INTO prefixes (guild_id, prefix) VALUES (?,?) "
        "ON CONFLICT(guild_id) DO UPDATE SET prefix=excluded.prefix",
        (str(guild_id), prefix),
    )
    await _commit()


async def get_all_prefixes() -> dict[str, str]:
    """Returns {guild_id_str: prefix} for all guilds."""
    async with _conn().execute("SELECT guild_id, prefix FROM prefixes") as cur:
        rows = await cur.fetchall()
    return {r["guild_id"]: r["prefix"] for r in rows}
