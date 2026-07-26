"""utils/db.roles — self-assign role panel.

Part of the utils/db package (split from the former single-file utils/db.py).
Shared connection state + schema machinery live in utils/db/_core.py; this
module holds the self-assign role panel accessors and registers its table setup with _core so
db.init() creates them in the right order.
"""

import json

import aiosqlite


from ._core import _commit, _conn, log, register_init

# ══════════════════════════════════════════════════════════════════════════════
#  Role Panels
# ══════════════════════════════════════════════════════════════════════════════


async def _ensure_role_panels_tables() -> None:
    await _conn().executescript("""
        CREATE TABLE IF NOT EXISTS role_panels (
            id          TEXT PRIMARY KEY,
            guild_id    TEXT NOT NULL,
            title       TEXT NOT NULL,
            description TEXT,
            mode        TEXT NOT NULL DEFAULT 'toggle',
            channel_id  TEXT,
            message_id  TEXT
        );
        CREATE INDEX IF NOT EXISTS role_panels_guild ON role_panels (guild_id);

        CREATE TABLE IF NOT EXISTS role_panel_entries (
            panel_id    TEXT NOT NULL REFERENCES role_panels(id) ON DELETE CASCADE,
            role_id     INTEGER NOT NULL,
            label       TEXT NOT NULL,
            emoji       TEXT,
            style       TEXT NOT NULL DEFAULT 'secondary',
            position    INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (panel_id, role_id)
        );
        CREATE INDEX IF NOT EXISTS rpe_panel ON role_panel_entries (panel_id);
    """)
    await _commit()


async def _migrate_role_panel_entries() -> None:
    """
    One-time migration: older versions stored entries as a JSON blob in a
    'entries' column on the role_panels table. This reads that column (if it
    still exists) and populates role_panel_entries, then drops the old column
    via a table rebuild.

    Safe to run on every startup — exits immediately if no migration is needed.
    """
    # Check whether the old 'entries' column exists on role_panels
    async with _conn().execute("PRAGMA table_info(role_panels)") as cur:
        columns = {row["name"] for row in await cur.fetchall()}

    if "entries" not in columns:
        return  # Already migrated or fresh install — nothing to do

    log.info(
        "DB migration: migrating role_panel entries from JSON column to relational table"
    )

    async with _conn().execute(
        "SELECT id, entries FROM role_panels WHERE entries IS NOT NULL AND entries != ''"
    ) as cur:
        rows = await cur.fetchall()

    migrated_panels = 0
    migrated_entries = 0

    for row in rows:
        panel_id = row["id"]
        try:
            entries = json.loads(row["entries"])
        except (json.JSONDecodeError, TypeError):
            log.warning(
                f"DB migration: could not parse entries for panel {panel_id!r} — skipping"
            )
            continue

        for i, entry in enumerate(entries):
            role_id = entry.get("role_id")
            if not role_id:
                continue
            await _conn().execute(
                "INSERT OR IGNORE INTO role_panel_entries "
                "(panel_id, role_id, label, emoji, style, position) VALUES (?,?,?,?,?,?)",
                (
                    panel_id,
                    int(role_id),
                    entry.get("label") or "Role",
                    entry.get("emoji"),
                    entry.get("style", "secondary"),
                    i,
                ),
            )
            migrated_entries += 1
        migrated_panels += 1

    await _commit()

    # Rebuild role_panels without the 'entries' column.
    # SQLite < 3.35 doesn't support DROP COLUMN, so we do a table swap.
    await _conn().executescript("""
        CREATE TABLE IF NOT EXISTS role_panels_new (
            id          TEXT PRIMARY KEY,
            guild_id    TEXT NOT NULL,
            title       TEXT NOT NULL,
            description TEXT,
            mode        TEXT NOT NULL DEFAULT 'toggle',
            channel_id  TEXT,
            message_id  TEXT
        );
        INSERT OR IGNORE INTO role_panels_new
            (id, guild_id, title, description, mode, channel_id, message_id)
        SELECT id, guild_id, title, description, mode, channel_id, message_id
        FROM role_panels;
        DROP TABLE role_panels;
        ALTER TABLE role_panels_new RENAME TO role_panels;
        CREATE INDEX IF NOT EXISTS role_panels_guild ON role_panels (guild_id);
    """)
    await _commit()

    log.info(
        f"DB migration complete: migrated {migrated_entries} entries across "
        f"{migrated_panels} panel(s). Old 'entries' column removed."
    )


def _panel_row(panel: aiosqlite.Row, entries: list[aiosqlite.Row]) -> dict:
    return {
        "id": panel["id"],
        "guild_id": panel["guild_id"],
        "title": panel["title"],
        "description": panel["description"],
        "mode": panel["mode"],
        "channel_id": panel["channel_id"],
        "message_id": panel["message_id"],
        "entries": [
            {
                "role_id": e["role_id"],
                "label": e["label"],
                "emoji": e["emoji"],
                "style": e["style"],
            }
            for e in sorted(entries, key=lambda x: x["position"])
        ],
    }


async def _fetch_entries(panel_id: str) -> list[aiosqlite.Row]:
    async with _conn().execute(
        "SELECT role_id, label, emoji, style, position "
        "FROM role_panel_entries WHERE panel_id=? ORDER BY position ASC",
        (panel_id,),
    ) as cur:
        return await cur.fetchall()


async def create_role_panel(
    panel_id: str,
    guild_id: int,
    title: str,
    description: str | None,
    mode: str,
) -> None:
    """Insert a new panel with no entries."""
    await _conn().execute(
        "INSERT INTO role_panels (id, guild_id, title, description, mode) "
        "VALUES (?,?,?,?,?)",
        (panel_id, str(guild_id), title, description, mode),
    )
    await _commit()


async def get_role_panel(panel_id: str) -> dict | None:
    """Return a single panel (with entries) or None."""
    async with _conn().execute(
        "SELECT id, guild_id, title, description, mode, channel_id, message_id "
        "FROM role_panels WHERE id=? LIMIT 1",
        (panel_id,),
    ) as cur:
        row = await cur.fetchone()
    if not row:
        return None
    entries = await _fetch_entries(panel_id)
    return _panel_row(row, entries)


async def get_role_panels_for_guild(guild_id: int) -> list[dict]:
    """All panels for a guild, ordered by rowid (creation order)."""
    async with _conn().execute(
        "SELECT id, guild_id, title, description, mode, channel_id, message_id "
        "FROM role_panels WHERE guild_id=? ORDER BY rowid ASC",
        (str(guild_id),),
    ) as cur:
        rows = await cur.fetchall()
    panels = []
    for row in rows:
        entries = await _fetch_entries(row["id"])
        panels.append(_panel_row(row, entries))
    return panels


async def get_all_role_panels() -> list[dict]:
    """All panels across every guild — used on startup to restore persistent views."""
    async with _conn().execute(
        "SELECT id, guild_id, title, description, mode, channel_id, message_id "
        "FROM role_panels ORDER BY rowid ASC"
    ) as cur:
        rows = await cur.fetchall()
    panels = []
    for row in rows:
        entries = await _fetch_entries(row["id"])
        panels.append(_panel_row(row, entries))
    return panels


async def edit_role_panel(
    panel_id: str,
    title: str,
    description: str | None,
    mode: str,
) -> None:
    """Update title, description, and mode on an existing panel."""
    await _conn().execute(
        "UPDATE role_panels SET title=?, description=?, mode=? WHERE id=?",
        (title, description, mode, panel_id),
    )
    await _commit()


async def update_role_panel_message(
    panel_id: str, channel_id: int, message_id: int
) -> None:
    """Record where the panel message was posted."""
    await _conn().execute(
        "UPDATE role_panels SET channel_id=?, message_id=? WHERE id=?",
        (str(channel_id), str(message_id), panel_id),
    )
    await _commit()


async def delete_role_panel(panel_id: str) -> None:
    """Delete a panel and all its entries (CASCADE handles entries)."""
    await _conn().execute("DELETE FROM role_panels WHERE id=?", (panel_id,))
    await _commit()


async def add_role_to_panel(panel_id: str, entry: dict) -> None:
    """
    Append a role entry to a panel.
    entry must have: role_id, label, emoji (or None), style.
    Position is set to max(existing) + 1 so ordering is stable.
    """
    async with _conn().execute(
        "SELECT COALESCE(MAX(position), -1) FROM role_panel_entries WHERE panel_id=?",
        (panel_id,),
    ) as cur:
        row = await cur.fetchone()
    next_pos = (row[0] + 1) if row else 0

    await _conn().execute(
        "INSERT INTO role_panel_entries (panel_id, role_id, label, emoji, style, position) "
        "VALUES (?,?,?,?,?,?) "
        "ON CONFLICT(panel_id, role_id) DO UPDATE SET "
        "label=excluded.label, emoji=excluded.emoji, style=excluded.style",
        (
            panel_id,
            entry["role_id"],
            entry.get("label") or "Role",
            entry.get("emoji"),
            entry.get("style", "secondary"),
            next_pos,
        ),
    )
    await _commit()


async def remove_role_from_panel(panel_id: str, role_id: int) -> None:
    """Remove a single role entry from a panel."""
    await _conn().execute(
        "DELETE FROM role_panel_entries WHERE panel_id=? AND role_id=?",
        (panel_id, role_id),
    )
    await _commit()


async def _init_roles():
    await _ensure_role_panels_tables()
    await _migrate_role_panel_entries()


register_init(_init_roles)
