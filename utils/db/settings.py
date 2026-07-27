"""utils/db.settings — bot-wide (owner-owned) settings.

Part of the utils/db package. One tiny key/value table for the handful of knobs
that are neither a *server's* decision nor a *member's* progress, but the bot
operator's: things that would be meaningless per guild because what they govern
is global.

Two families live here, and both are here for the same reason — the thing they
control crosses server boundaries, so a per-guild value could only ever describe
*someone else's* server too.

**Cooldown lengths** (`cooldown:<activity>`). A cooldown claim is keyed by user
id alone (see utils/db/activities.py), so a member who runs /work in one server
can't run it in another — which also means a per-guild *length* never described
anything real: whichever server set the shortest one silently set the pace for
every server that member is in.

**Reward amounts** (`reward:<key>`). The faucets — /daily, its streak bonus,
/squad, /raid, and the level-up coin payout — mint into one global wallet, so a
server raising any of them pays its members coins that spend everywhere else
too. A guild keeps the knobs that only affect itself: what its currency is
called, its raid party size, and above all its **shop prices**, which are a
*sink* — they destroy coins in exchange for that guild's own roles and perks,
so nobody outside it is affected by what they cost.

Only *overrides* are stored — a key with no row uses the default owned by the
cog that consumes it (`cogs.activities.constants`, `cogs.economy.constants`).
The defaults stay there because that is where the balance maths that produced
them lives; this module deliberately knows nothing about which activities or
rewards exist.
"""

from . import _cache
from ._core import _commit, _conn, register_init

# The bot-wide row has no guild, so it caches under a fixed sentinel key. Same
# contract as every other entry in _cache: config only, and the setter below
# invalidates it.
_CACHE_KEY = 0

# Key prefix for a per-activity cooldown override, e.g. "cooldown:mine".
_COOLDOWN_PREFIX = "cooldown:"

# Key prefix for a coin-faucet amount, e.g. "reward:daily".
_REWARD_PREFIX = "reward:"


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


async def _int_overrides(prefix: str, *, allow_zero: bool = False) -> dict[str, int]:
    """Every `prefix`-namespaced setting that reads as a whole number.

    A key missing from the result has no override and takes the consuming cog's
    default. A value that isn't a number is dropped rather than handed back, so
    a hand-edited database can't turn a cooldown into "no cooldown" or a reward
    into a crash.
    """
    out: dict[str, int] = {}
    for key, value in (await get_bot_settings()).items():
        if not key.startswith(prefix):
            continue
        try:
            number = int(value)
        except (TypeError, ValueError):
            continue
        if number > 0 or (allow_zero and number == 0):
            out[key[len(prefix) :]] = number
    return out


# ── Activity cooldowns ────────────────────────────────────────────────────────
async def get_activity_cooldowns() -> dict[str, int]:
    """Overridden activities → their cooldown length in seconds."""
    return await _int_overrides(_COOLDOWN_PREFIX)


async def set_activity_cooldown(activity: str, seconds: int) -> None:
    await set_bot_setting(f"{_COOLDOWN_PREFIX}{activity}", int(seconds))


async def clear_activity_cooldown(activity: str) -> bool:
    """Return an activity to its default length. True if an override existed."""
    return await clear_bot_setting(f"{_COOLDOWN_PREFIX}{activity}")


# ── Coin faucets ──────────────────────────────────────────────────────────────
async def get_reward_amounts() -> dict[str, int]:
    """Overridden faucets → their coin amount.

    Zero is a meaningful value here, unlike a cooldown: it's how the owner turns
    /squad, /raid or the level-up payout off, so it has to survive the read.
    """
    return await _int_overrides(_REWARD_PREFIX, allow_zero=True)


async def set_reward_amount(key: str, coins: int) -> None:
    await set_bot_setting(f"{_REWARD_PREFIX}{key}", int(coins))


async def clear_reward_amount(key: str) -> bool:
    """Return a faucet to its default amount. True if an override existed."""
    return await clear_bot_setting(f"{_REWARD_PREFIX}{key}")
