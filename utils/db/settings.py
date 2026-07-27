"""utils/db.settings — bot-wide (owner-owned) settings.

Part of the utils/db package. One tiny key/value table for the handful of knobs
that are neither a *server's* decision nor a *member's* progress, but the bot
operator's: things that would be meaningless per guild because what they govern
is global.

The activity cooldowns are the reason this exists. Cooldown claims are keyed by
user id alone (see utils/db/activities.py), so a member who runs /work in one
server can't run it in another — which also means a per-guild *length* never
described anything real: whichever server set the shortest one silently set the
pace for every server that member is in, and the coins minted at that pace spend
everywhere. The lengths therefore live here, set once by the bot owner
(`!cooldown` in cogs/admin), and a guild keeps only what is genuinely its own
call: whether an activity runs there at all.

Only *overrides* are stored — an activity with no row uses the default from
`cogs.activities.constants`. The defaults stay in the cog because that is where
the balance maths that produced them lives; this module deliberately knows
nothing about which activities exist.
"""

from . import _cache
from ._core import _commit, _conn, register_init

# The bot-wide row has no guild, so it caches under a fixed sentinel key. Same
# contract as every other entry in _cache: config only, and the setter below
# invalidates it.
_CACHE_KEY = 0

# Key prefix for a per-activity cooldown override, e.g. "cooldown:mine".
_COOLDOWN_PREFIX = "cooldown:"


async def _ensure_settings_tables():
    await _conn().execute("""
        CREATE TABLE IF NOT EXISTS bot_settings (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)
    await _commit()


register_init(_ensure_settings_tables)


async def get_bot_settings() -> dict[str, str]:
    """Every bot-wide setting as {key: value}. Cached; the table is tiny."""
    cached = _cache.get("bot_settings", _CACHE_KEY)
    if cached is not None:
        return cached
    async with _conn().execute("SELECT key, value FROM bot_settings") as cur:
        rows = await cur.fetchall()
    return _cache.put("bot_settings", _CACHE_KEY, {r["key"]: r["value"] for r in rows})


async def get_bot_setting(key: str, default=None):
    return (await get_bot_settings()).get(key, default)


async def set_bot_setting(key: str, value) -> None:
    await _conn().execute(
        "INSERT INTO bot_settings (key, value) VALUES (?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (str(key), str(value)),
    )
    await _commit()
    _cache.invalidate("bot_settings", _CACHE_KEY)


async def clear_bot_setting(key: str) -> bool:
    """Drop a setting so it falls back to its default. True if one was set."""
    cur = await _conn().execute("DELETE FROM bot_settings WHERE key=?", (str(key),))
    await _commit()
    _cache.invalidate("bot_settings", _CACHE_KEY)
    return cur.rowcount > 0


# ── Activity cooldowns ────────────────────────────────────────────────────────
async def get_activity_cooldowns() -> dict[str, int]:
    """Only the activities the owner has overridden → their length in seconds.

    An activity missing from the result has no override and takes the cog's
    default. A row that somehow isn't a number is ignored rather than returned,
    so a hand-edited database can't turn into "no cooldown".
    """
    out: dict[str, int] = {}
    for key, value in (await get_bot_settings()).items():
        if not key.startswith(_COOLDOWN_PREFIX):
            continue
        try:
            seconds = int(value)
        except (TypeError, ValueError):
            continue
        if seconds > 0:
            out[key[len(_COOLDOWN_PREFIX) :]] = seconds
    return out


async def set_activity_cooldown(activity: str, seconds: int) -> None:
    await set_bot_setting(f"{_COOLDOWN_PREFIX}{activity}", int(seconds))


async def clear_activity_cooldown(activity: str) -> bool:
    """Return an activity to its default length. True if an override existed."""
    return await clear_bot_setting(f"{_COOLDOWN_PREFIX}{activity}")
