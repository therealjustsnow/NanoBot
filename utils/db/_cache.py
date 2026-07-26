"""utils/db/_cache.py — in-process cache for per-guild config rows.

Config rows are read constantly and written almost never: every chat message
reads `level_config` (plus the ignored-channel set), and every economy command
opens with a `get_*_config`. Those reads all resolve to one small, stable row
per guild, so they belong in memory rather than in a round trip to the aiosqlite
thread.

The contract is deliberately narrow:

* only whole config rows keyed by guild id go in here — never a member's
  progress, never anything a command mutates as part of gameplay;
* every setter invalidates its own key, so a cached read can never be staler
  than the last write this process made;
* `clear()` runs in `db.init()`, which keeps test fixtures (a fresh in-memory
  database per test) from inheriting another test's rows.

Single-process assumption, same as the rest of the data layer: nothing else
writes `data/nanobot.db` while the bot is running.
"""

from typing import Any

_cache: dict[tuple[str, str], Any] = {}


def get(table: str, guild_id) -> Any | None:
    """Cached value for (table, guild_id), or None when nothing is cached.

    Containers are copied on the way out: callers treat a config dict as theirs
    to poke at, and a shallow copy is still orders of magnitude cheaper than the
    query it replaces.
    """
    value = _cache.get((table, str(guild_id)))
    if isinstance(value, (dict, list, set)):
        return value.copy()
    return value


def put(table: str, guild_id, value: Any) -> Any:
    """Cache `value` and hand the caller's own copy back.

    Containers are copied on the way *in* as well, so the cached row and the one
    the caller walks away with are different objects — a setter that reads its
    config, mutates the dict and writes it back can't poison the cache.
    """
    stored = value.copy() if isinstance(value, (dict, list, set)) else value
    _cache[(table, str(guild_id))] = stored
    return value


def invalidate(table: str, guild_id) -> None:
    """Drop one guild's cached row for `table`. Call from every setter."""
    _cache.pop((table, str(guild_id)), None)


def clear() -> None:
    """Drop everything — used by db.init() so a new connection starts clean."""
    _cache.clear()
