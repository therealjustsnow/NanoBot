"""utils/db.notes — moderator note.

Part of the utils/db package (split from the former single-file utils/db.py).
Shared connection state + schema machinery live in utils/db/_core.py; this
module holds the moderator note accessors and registers its table setup with _core so
db.init() creates them in the right order.
"""

from ._core import _commit, _conn

# ══════════════════════════════════════════════════════════════════════════════
#  Notes
# ══════════════════════════════════════════════════════════════════════════════


async def add_note(
    guild_id: int,
    user_id: int,
    content: str,
    by_id: str,
    by_name: str,
    created_at: str,
) -> int:
    """Add a note. Returns total note count for that user in that guild."""
    await _conn().execute(
        "INSERT INTO notes (guild_id, user_id, content, by_id, by_name, created_at) "
        "VALUES (?,?,?,?,?,?)",
        (str(guild_id), str(user_id), content, by_id, by_name, created_at),
    )
    await _commit()
    async with _conn().execute(
        "SELECT COUNT(*) FROM notes WHERE guild_id=? AND user_id=?",
        (str(guild_id), str(user_id)),
    ) as cur:
        row = await cur.fetchone()
    return row[0]


async def get_notes(guild_id: int, user_id: int) -> list[dict]:
    """All notes for a user, oldest first."""
    async with _conn().execute(
        "SELECT content, by_id, by_name, created_at FROM notes "
        "WHERE guild_id=? AND user_id=? ORDER BY id ASC",
        (str(guild_id), str(user_id)),
    ) as cur:
        rows = await cur.fetchall()
    return [
        {
            "note": r["content"],
            "by_id": r["by_id"],
            "by_name": r["by_name"],
            "at": r["created_at"],
        }
        for r in rows
    ]


async def get_note_count(guild_id: int, user_id: int) -> int:
    async with _conn().execute(
        "SELECT COUNT(*) FROM notes WHERE guild_id=? AND user_id=?",
        (str(guild_id), str(user_id)),
    ) as cur:
        row = await cur.fetchone()
    return row[0]


async def clear_notes(guild_id: int, user_id: int) -> int:
    """Delete all notes for a user. Returns count deleted."""
    cur = await _conn().execute(
        "DELETE FROM notes WHERE guild_id=? AND user_id=?",
        (str(guild_id), str(user_id)),
    )
    await _commit()
    return cur.rowcount
