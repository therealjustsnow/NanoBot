"""utils/db.tags — guild/personal text snippet.

Part of the utils/db package (split from the former single-file utils/db.py).
Shared connection state + schema machinery live in utils/db/_core.py; this
module holds the guild/personal text snippet accessors and registers its table setup with _core so
db.init() creates them in the right order.
"""

from ._core import _commit, _conn

# ══════════════════════════════════════════════════════════════════════════════
#  Tags
# ══════════════════════════════════════════════════════════════════════════════


async def get_tag(guild_id: int, name: str, user_id: int) -> dict | None:
    """Personal tag first, then global. Returns dict or None."""
    async with _conn().execute(
        "SELECT content, image_url FROM tags "
        "WHERE guild_id=? AND name=? AND scope=? LIMIT 1",
        (str(guild_id), name, str(user_id)),
    ) as cur:
        row = await cur.fetchone()
    if row:
        return {"content": row["content"], "image_url": row["image_url"]}

    async with _conn().execute(
        "SELECT content, image_url FROM tags "
        "WHERE guild_id=? AND name=? AND scope='global' LIMIT 1",
        (str(guild_id), name),
    ) as cur:
        row = await cur.fetchone()
    if row:
        return {"content": row["content"], "image_url": row["image_url"]}
    return None


async def get_personal_tags(guild_id: int, user_id: int) -> dict:
    """All personal tags for a user in a guild. Returns {name: {content, image_url}}."""
    async with _conn().execute(
        "SELECT name, content, image_url FROM tags WHERE guild_id=? AND scope=?",
        (str(guild_id), str(user_id)),
    ) as cur:
        rows = await cur.fetchall()
    return {
        r["name"]: {"content": r["content"], "image_url": r["image_url"]} for r in rows
    }


async def get_global_tags(guild_id: int) -> dict:
    """All global tags for a guild. Returns {name: {content, image_url, by_id, by_name}}."""
    async with _conn().execute(
        "SELECT name, content, image_url, by_id, by_name FROM tags "
        "WHERE guild_id=? AND scope='global'",
        (str(guild_id),),
    ) as cur:
        rows = await cur.fetchall()
    return {
        r["name"]: {
            "content": r["content"],
            "image_url": r["image_url"],
            "by_id": r["by_id"],
            "by_name": r["by_name"],
        }
        for r in rows
    }


async def tag_exists(guild_id: int, scope: str, name: str) -> bool:
    async with _conn().execute(
        "SELECT 1 FROM tags WHERE guild_id=? AND scope=? AND name=? LIMIT 1",
        (str(guild_id), scope, name),
    ) as cur:
        return await cur.fetchone() is not None


async def set_tag(
    guild_id: int,
    scope: str,  # "global" or str(user_id)
    name: str,
    content: str | None,
    image_url: str | None,
    by_id: str | None = None,
    by_name: str | None = None,
) -> None:
    """Insert or replace a tag."""
    await _conn().execute(
        """INSERT INTO tags (guild_id, scope, name, content, image_url, by_id, by_name)
           VALUES (?,?,?,?,?,?,?)
           ON CONFLICT(guild_id, scope, name) DO UPDATE SET
               content=excluded.content,
               image_url=excluded.image_url,
               by_id=excluded.by_id,
               by_name=excluded.by_name""",
        (str(guild_id), scope, name, content, image_url, by_id, by_name),
    )
    await _commit()


async def update_tag_image(
    guild_id: int, scope: str, name: str, image_url: str | None
) -> None:
    await _conn().execute(
        "UPDATE tags SET image_url=? WHERE guild_id=? AND scope=? AND name=?",
        (image_url, str(guild_id), scope, name),
    )
    await _commit()


async def update_tag_content(
    guild_id: int, scope: str, name: str, content: str
) -> None:
    await _conn().execute(
        "UPDATE tags SET content=? WHERE guild_id=? AND scope=? AND name=?",
        (content, str(guild_id), scope, name),
    )
    await _commit()


async def delete_tag(guild_id: int, scope: str, name: str) -> bool:
    """Returns True if a row was deleted."""
    cur = await _conn().execute(
        "DELETE FROM tags WHERE guild_id=? AND scope=? AND name=?",
        (str(guild_id), scope, name),
    )
    await _commit()
    return cur.rowcount > 0


async def find_tag_scope(guild_id: int, name: str, user_id: int) -> str | None:
    """
    Return the scope string if the user can edit/delete the tag, else None.
    Personal takes priority. Requires manage_messages for global (checked by caller).
    """
    async with _conn().execute(
        "SELECT scope FROM tags WHERE guild_id=? AND name=? AND scope=? LIMIT 1",
        (str(guild_id), name, str(user_id)),
    ) as cur:
        row = await cur.fetchone()
    if row:
        return row["scope"]
    async with _conn().execute(
        "SELECT scope FROM tags WHERE guild_id=? AND name=? AND scope='global' LIMIT 1",
        (str(guild_id), name),
    ) as cur:
        row = await cur.fetchone()
    return row["scope"] if row else None
