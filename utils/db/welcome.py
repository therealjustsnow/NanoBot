"""utils/db.welcome — welcome/leave message.

Part of the utils/db package (split from the former single-file utils/db.py).
Shared connection state + schema machinery live in utils/db/_core.py; this
module holds the welcome/leave message accessors and registers its table setup with _core so
db.init() creates them in the right order.
"""

from ._core import _conn, _ensure_columns, register_init

# ══════════════════════════════════════════════════════════════════════════════
#  Welcome / Leave
# ══════════════════════════════════════════════════════════════════════════════


async def _ensure_welcome_tables():
    await _conn().execute("""
        CREATE TABLE IF NOT EXISTS welcome_config (
            guild_id    TEXT PRIMARY KEY,
            enabled     INTEGER NOT NULL DEFAULT 0,
            channel_id  TEXT,
            title       TEXT,
            content     TEXT,
            image_url   TEXT,
            dm          INTEGER NOT NULL DEFAULT 0,
            footer_text TEXT,
            thumbnail   TEXT,
            color       TEXT,
            image_text  TEXT
        )
    """)
    await _conn().execute("""
        CREATE TABLE IF NOT EXISTS leave_config (
            guild_id    TEXT PRIMARY KEY,
            enabled     INTEGER NOT NULL DEFAULT 0,
            channel_id  TEXT,
            title       TEXT,
            content     TEXT,
            image_url   TEXT,
            dm          INTEGER NOT NULL DEFAULT 0,
            footer_text TEXT,
            thumbnail   TEXT,
            color       TEXT,
            image_text  TEXT
        )
    """)
    await _conn().commit()

    # Migration: add new columns to existing tables that pre-date this change.
    new_columns = {
        "footer_text": "TEXT",
        "thumbnail": "TEXT",
        "color": "TEXT",
        "image_text": "TEXT",
    }
    for table in ("welcome_config", "leave_config"):
        await _ensure_columns(table, new_columns)


async def _get_event_config(table: str, guild_id: int) -> dict | None:
    async with _conn().execute(
        f"SELECT enabled, channel_id, title, content, image_url, dm, "
        f"footer_text, thumbnail, color, image_text FROM {table} WHERE guild_id=?",
        (str(guild_id),),
    ) as cur:
        row = await cur.fetchone()
    if not row:
        return None
    return {
        "enabled": bool(row["enabled"]),
        "channel_id": row["channel_id"],
        "title": row["title"],
        "content": row["content"],
        "image_url": row["image_url"],
        "dm": bool(row["dm"]),
        "footer_text": row["footer_text"],
        "thumbnail": row["thumbnail"],
        "color": row["color"],
        "image_text": row["image_text"],
    }


async def _set_event_config(table: str, guild_id: int, **kwargs) -> None:
    await _conn().execute(
        f"INSERT INTO {table} "
        "(guild_id, enabled, channel_id, title, content, image_url, dm, "
        "footer_text, thumbnail, color, image_text) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(guild_id) DO UPDATE SET "
        "enabled=excluded.enabled, channel_id=excluded.channel_id, "
        "title=excluded.title, content=excluded.content, "
        "image_url=excluded.image_url, dm=excluded.dm, "
        "footer_text=excluded.footer_text, thumbnail=excluded.thumbnail, "
        "color=excluded.color, image_text=excluded.image_text",
        (
            str(guild_id),
            1 if kwargs.get("enabled", False) else 0,
            kwargs.get("channel_id"),
            kwargs.get("title"),
            kwargs.get("content"),
            kwargs.get("image_url"),
            1 if kwargs.get("dm", False) else 0,
            kwargs.get("footer_text"),
            kwargs.get("thumbnail"),
            kwargs.get("color"),
            kwargs.get("image_text"),
        ),
    )
    await _conn().commit()


async def get_welcome_config(guild_id: int) -> dict | None:
    return await _get_event_config("welcome_config", guild_id)


async def set_welcome_config(guild_id: int, **kwargs) -> None:
    await _set_event_config("welcome_config", guild_id, **kwargs)


async def get_leave_config(guild_id: int) -> dict | None:
    return await _get_event_config("leave_config", guild_id)


async def set_leave_config(guild_id: int, **kwargs) -> None:
    await _set_event_config("leave_config", guild_id, **kwargs)


register_init(_ensure_welcome_tables)
