"""
utils/storage.py
Lightweight JSON key-value storage.
All bot data lives in the /data directory as .json files.
No database. No dependencies. Just files.
"""

import json
import os
from typing import Any

_DATA_DIR = "data"


# ── Internal ───────────────────────────────────────────────────────────────────
def _path(filename: str) -> str:
    os.makedirs(_DATA_DIR, exist_ok=True)
    return os.path.join(_DATA_DIR, filename)


# ── Core Read / Write ──────────────────────────────────────────────────────────
def read(filename: str) -> dict:
    """Read a JSON file. Returns empty dict if file doesn't exist."""
    p = _path(filename)
    if not os.path.exists(p):
        return {}
    with open(p, encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return {}


def write(filename: str, data: dict):
    """Overwrite a JSON file with the given data."""
    with open(_path(filename), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


# ── Guild-scoped helpers ───────────────────────────────────────────────────────
def get_guild(filename: str, guild_id: int) -> dict:
    """Return the data block for a specific guild."""
    return read(filename).get(str(guild_id), {})


def set_guild(filename: str, guild_id: int, data: dict):
    """Save a guild's data block."""
    all_data = read(filename)
    all_data[str(guild_id)] = data
    write(filename, all_data)


def update_guild(filename: str, guild_id: int, updates: dict):
    """Merge updates into a guild's data block."""
    current = get_guild(filename, guild_id)
    current.update(updates)
    set_guild(filename, guild_id, current)


# ── User-scoped helpers ────────────────────────────────────────────────────────
def get_user(filename: str, guild_id: int, user_id: int) -> Any | None:
    """Return data for a specific user in a guild."""
    return get_guild(filename, guild_id).get(str(user_id))


def set_user(filename: str, guild_id: int, user_id: int, data: Any):
    """Save data for a specific user in a guild."""
    all_data = read(filename)
    gid, uid = str(guild_id), str(user_id)
    if gid not in all_data:
        all_data[gid] = {}
    all_data[gid][uid] = data
    write(filename, all_data)


def delete_user(filename: str, guild_id: int, user_id: int) -> bool:
    """Delete a user's data. Returns True if deleted."""
    all_data = read(filename)
    gid, uid = str(guild_id), str(user_id)
    if gid in all_data and uid in all_data[gid]:
        del all_data[gid][uid]
        write(filename, all_data)
        return True
    return False
