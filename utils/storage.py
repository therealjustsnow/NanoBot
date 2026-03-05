"""
utils/storage.py
Lightweight JSON key-value storage with async locking and atomic writes.

All bot data lives in the /data directory as .json files.
No database. No dependencies. Just files.

Safe for concurrent async access:
  - awrite() holds a per-file asyncio.Lock so only one write runs at a time
  - Writes go to <file>.tmp first, then os.replace() swaps atomically
  - A crash mid-write leaves a .tmp file, not a corrupt .json

Use awrite() in all async (cog) code.
Use write() only at startup/shutdown when no event loop is running yet.
"""

import asyncio
import json
import logging
import os
from typing import Any

log = logging.getLogger("NanoBot.storage")

_DATA_DIR = "data"

# Per-filename asyncio locks — created lazily so they belong to the running loop
_locks: dict[str, asyncio.Lock] = {}


def _get_lock(filename: str) -> asyncio.Lock:
    if filename not in _locks:
        _locks[filename] = asyncio.Lock()
    return _locks[filename]


# ── Path helpers ───────────────────────────────────────────────────────────────
def _path(filename: str) -> str:
    os.makedirs(_DATA_DIR, exist_ok=True)
    return os.path.join(_DATA_DIR, filename)


# ── Core Read ──────────────────────────────────────────────────────────────────
def read(filename: str) -> dict:
    """Read a JSON file from disk. Returns empty dict if missing or corrupt."""
    p = _path(filename)
    if not os.path.exists(p):
        return {}
    with open(p, encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError as exc:
            log.error(f"Corrupt JSON in {filename}: {exc} — returning empty dict")
            return {}


# ── Sync Write (startup / shutdown only) ──────────────────────────────────────
def write(filename: str, data: dict) -> None:
    """
    Synchronous atomic write.
    Only use this at startup or shutdown when no event loop is running.
    In async (cog) code use awrite() instead.
    """
    p   = _path(filename)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, p)


# ── Async Write (all cog code) ─────────────────────────────────────────────────
async def awrite(filename: str, data: dict) -> None:
    """
    Async atomic write with per-file locking.

    Concurrent calls for the same file queue up and execute one at a time.
    Concurrent calls for different files run in parallel.
    The file is never left in a partial state — write goes to .tmp first,
    then os.replace() swaps it atomically.
    """
    async with _get_lock(filename):
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, write, filename, data)


# ── Guild-scoped helpers ───────────────────────────────────────────────────────
def get_guild(filename: str, guild_id: int) -> dict:
    """Return the data block for a specific guild."""
    return read(filename).get(str(guild_id), {})


async def set_guild(filename: str, guild_id: int, data: dict) -> None:
    """Save a guild's data block."""
    all_data = read(filename)
    all_data[str(guild_id)] = data
    await awrite(filename, all_data)


async def update_guild(filename: str, guild_id: int, updates: dict) -> None:
    """Merge updates into a guild's data block."""
    async with _get_lock(filename):
        current = read(filename)
        gid = str(guild_id)
        current.setdefault(gid, {}).update(updates)
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, write, filename, current)


# ── User-scoped helpers ────────────────────────────────────────────────────────
def get_user(filename: str, guild_id: int, user_id: int) -> Any | None:
    """Return data for a specific user in a guild."""
    return get_guild(filename, guild_id).get(str(user_id))


async def set_user(filename: str, guild_id: int, user_id: int, data: Any) -> None:
    """Save data for a specific user in a guild."""
    async with _get_lock(filename):
        all_data = read(filename)
        gid, uid = str(guild_id), str(user_id)
        all_data.setdefault(gid, {})[uid] = data
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, write, filename, all_data)


async def delete_user(filename: str, guild_id: int, user_id: int) -> bool:
    """Delete a user's data. Returns True if something was deleted."""
    async with _get_lock(filename):
        all_data = read(filename)
        gid, uid = str(guild_id), str(user_id)
        if gid in all_data and uid in all_data[gid]:
            del all_data[gid][uid]
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, write, filename, all_data)
            return True
    return False
