"""
utils/helpers.py
Shared utilities:
  - Embed factory (consistently styled, mobile-optimized)
  - Duration parsing  ("30s", "5m", "2h", "1d" → seconds), incl. parse_duration_from_end
  - Duration formatting (seconds → "5m 30s")
  - Interval parsing / formatting (parse_interval, fmt_interval)
  - user_display() for consistent user references
"""

import asyncio
import re
from datetime import datetime, timezone

import discord

# ── Brand colours ──────────────────────────────────────────────────────────────
GREEN = 0x57F287  # success
RED = 0xED4245  # error
YELLOW = 0xFEE75C  # warning
BLUE = 0x5865F2  # info / neutral
GREY = 0x2B2D31  # default


# ── Embed Factory ──────────────────────────────────────────────────────────────
def embed(
    title: str = "",
    description: str = "",
    color: int = GREY,
    *,
    footer: str = "NanoBot",
) -> discord.Embed:
    """Create a base NanoBot embed with timestamp."""
    e = discord.Embed(title=title, description=description, color=color)
    e.set_footer(text=footer)
    e.timestamp = datetime.now(timezone.utc)
    return e


def ok(description: str, title: str = "✅ Done") -> discord.Embed:
    """Green success embed."""
    return embed(title, description, GREEN)


def err(description: str, title: str = "❌ Error") -> discord.Embed:
    """Red error embed."""
    return embed(title, description, RED)


def warn(description: str, title: str = "⚠️ Warning") -> discord.Embed:
    """Yellow warning embed."""
    return embed(title, description, YELLOW)


def info(description: str, title: str = "ℹ️ Info") -> discord.Embed:
    """Blue info embed."""
    return embed(title, description, BLUE)


# ── Duration Parsing ───────────────────────────────────────────────────────────
_UNITS_SHORT = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}

# ── File Size Parsing ──────────────────────────────────────────────────────────
_FILESIZE_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*(b|kb|mb|gb|tb)?$", re.IGNORECASE)
_FILESIZE_UNITS_MB: dict[str, float] = {
    "": 1.0,
    "b": 1.0 / (1024 * 1024),
    "kb": 1.0 / 1024,
    "mb": 1.0,
    "gb": 1024.0,
    "tb": 1024.0 * 1024,
}


def parse_filesize_mb(s: str | None) -> int | None:
    """
    Parse a file size string into whole MB. Bare integer treated as MB.
    Accepts B, KB, MB, GB, TB (case-insensitive, optional space before unit).
    Returns None for invalid/missing input.
    """
    if not s:
        return None
    m = _FILESIZE_RE.match(str(s).strip())
    if not m:
        return None
    value = float(m.group(1))
    unit = (m.group(2) or "").lower()
    return round(value * _FILESIZE_UNITS_MB[unit])


_UNITS_LONG = {
    "second": 1,
    "seconds": 1,
    "sec": 1,
    "secs": 1,
    "minute": 60,
    "minutes": 60,
    "min": 60,
    "mins": 60,
    "hour": 3600,
    "hours": 3600,
    "hr": 3600,
    "hrs": 3600,
    "day": 86400,
    "days": 86400,
    "week": 604800,
    "weeks": 604800,
    "wk": 604800,
    "wks": 604800,
}

# Matches: "8h", "30m", "1d", "2w", "60s", "60" (bare = seconds)
_PATTERN_SHORT = re.compile(r"^(\d+)\s*([smhdw]?)$", re.IGNORECASE)
# Matches: "8 hours", "30 minutes", "1 day", "2 weeks"
_PATTERN_LONG = re.compile(r"^(\d+)\s+(" + "|".join(_UNITS_LONG) + r")$", re.IGNORECASE)
# Matches duration at END of a string: "remind me 8h" or "do laundry in 2 hours"
_PATTERN_TAIL = re.compile(
    r"(?:\s+in\s+|\s+)(\d+)\s*("
    + "|".join(list(_UNITS_LONG) + list(_UNITS_SHORT))
    + r")\s*$",
    re.IGNORECASE,
)


def parse_duration(s: str | None) -> int | None:
    """
    Parse a standalone duration string into seconds.
    Supports shorthand (8h, 30m, 2d, 1w) and natural language (8 hours, 30 minutes).
    Returns None for invalid/missing input.
    """
    if not s:
        return None
    s = s.strip()
    m = _PATTERN_SHORT.match(s)
    if m:
        value = int(m.group(1))
        unit = (m.group(2) or "s").lower()
        return value * _UNITS_SHORT[unit]
    m = _PATTERN_LONG.match(s)
    if m:
        return int(m.group(1)) * _UNITS_LONG[m.group(2).lower()]
    return None


def parse_duration_from_end(text: str) -> tuple[str, int | None]:
    """
    Extract a duration from the END of a reminder string.
    Returns (cleaned_text, seconds) or (original_text, None).

    Examples:
        "go for a run in 2 hours"   → ("go for a run", 7200)
        "call mum 30m"              → ("call mum", 1800)
        "stand up in 45 minutes"    → ("stand up", 2700)
        "no duration here"          → ("no duration here", None)
    """
    m = _PATTERN_TAIL.search(text)
    if not m:
        return text, None
    raw_unit = m.group(2).lower()
    mult = _UNITS_LONG.get(raw_unit) or _UNITS_SHORT.get(raw_unit)
    if not mult:
        return text, None
    secs = int(m.group(1)) * mult
    cleaned = text[: m.start()].strip()
    return cleaned, secs


def fmt_duration(seconds: int) -> str:
    """
    Format a number of seconds into a human-readable string.

    Examples:
        45    → "45s"
        90    → "1m 30s"
        3600  → "1h"
        90061 → "1d 1h"
    """
    if seconds <= 0:
        return "0s"

    parts = []
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60), ("s", 1)):
        if seconds >= size:
            val = seconds // size
            seconds %= size
            parts.append(f"{val}{unit}")

    return " ".join(parts[:2])  # cap at 2 units for readability (e.g. "1d 2h")


# ── Duration pickers (mobile-first: tap a common one, or type your own) ───────
def duration_picker(suggestions: "list[tuple[str, str]]"):
    """Build an app-command autocomplete offering common durations.

    Unlike static `@app_commands.choices`, a suggestion list must never become
    a *restriction* — every one of these options parses arbitrary input, and
    "5 minutes isn't in the list" would be a regression. So whatever the caller
    has typed comes back as the first choice whenever `parse_duration` accepts
    it, echoed with the parsed length so a typo is visible before they send it.

    `suggestions` is [(label, value)] in the order they should appear; the
    values are what `parse_duration` will see.
    """

    async def _autocomplete(interaction, current: str):
        from discord import app_commands  # local: keeps this module import-light

        typed = (current or "").strip()
        choices: list = []
        listed = {value for _label, value in suggestions}
        if typed and typed not in listed:
            secs = parse_duration(typed)
            if secs and secs > 0:
                choices.append(
                    app_commands.Choice(
                        name=f"✏️ {typed} — {fmt_duration(int(secs))}"[:100],
                        value=typed,
                    )
                )
        q = typed.lower()
        for label, value in suggestions:
            if q and q not in label.lower() and q not in value.lower():
                continue
            choices.append(
                app_commands.Choice(name=f"{label} ({value})"[:100], value=value)
            )
        return choices[:25]

    return _autocomplete


# ── Interval Parsing (recurring reminders) ────────────────────────────────────
_INTERVAL_KEYWORDS: dict[str, int] = {
    "hourly": 3_600,
    "hour": 3_600,
    "daily": 86_400,
    "day": 86_400,
    "weekly": 7 * 86_400,
    "week": 7 * 86_400,
    "biweekly": 14 * 86_400,
    "fortnightly": 14 * 86_400,
    "monthly": 30 * 86_400,
    "month": 30 * 86_400,
    "yearly": 365 * 86_400,
    "annually": 365 * 86_400,
    "year": 365 * 86_400,
}


def parse_interval(s: str) -> int | None:
    """
    Parse a recurring interval string into seconds.

    Supports:
      - Keywords:          "daily", "weekly", "monthly", "hourly", "biweekly" …
      - Singular forms:    "day", "week", "month", "hour"
      - "every X" prefix:  "every 2 weeks", "every day", "every 3 hours"
      - Duration strings:  "2w", "1d", "3h", "30m"   (via parse_duration)

    Returns None for invalid or unrecognised input.
    """
    if not s:
        return None
    s = s.strip().lower()
    if s.startswith("every "):
        s = s[6:].strip()
    if s in _INTERVAL_KEYWORDS:
        return _INTERVAL_KEYWORDS[s]
    return parse_duration(s)


def fmt_interval(seconds: int) -> str:
    """
    Format an interval (in seconds) as a compact, human-readable string.
    Prefers clean unit names when the value divides evenly; falls back to
    fmt_duration for irregular values.

    Examples:
        3600      → "1 hour"
        7200      → "2 hours"
        86400     → "1 day"
        1209600   → "2 weeks"
        2592000   → "30 days"
        5400      → "1h 30m"   (fallback — doesn't divide evenly into hours)
    """
    if seconds <= 0:
        return "0s"

    for size, singular, plural in (
        (7 * 86_400, "week", "weeks"),
        (86_400, "day", "days"),
        (3_600, "hour", "hours"),
        (60, "minute", "minutes"),
    ):
        if seconds >= size and seconds % size == 0:
            val = seconds // size
            return f"{val} {singular if val == 1 else plural}"

    return fmt_duration(seconds)


# ── Misc Helpers ───────────────────────────────────────────────────────────────
def user_display(member: discord.Member | discord.User) -> str:
    """Return 'Display Name (@username) | ID' for consistent user references."""
    return f"**{member.display_name}** (`{member.name}` · `{member.id}`)"


# Permissions that effectively grant control over the server or its members.
# A self-assign role panel must never hand these out, and they mark a role as
# too sensitive to manage from below in the hierarchy.
DANGEROUS_ROLE_PERMS: tuple[str, ...] = (
    "administrator",
    "manage_guild",
    "manage_roles",
    "manage_channels",
    "manage_webhooks",
    "manage_messages",
    "manage_nicknames",
    "manage_emojis_and_stickers",
    "kick_members",
    "ban_members",
    "moderate_members",
    "mention_everyone",
)


def can_manage_role(actor: discord.Member, role: discord.Role) -> bool:
    """Whether `actor` is allowed to hand out / take `role`.

    The guild owner can manage anything. Otherwise the role must sit strictly
    below the actor's top role — Discord enforces this for actions a member
    performs directly, but NOT for actions a bot performs on their behalf, so we
    re-check it here to stop a Manage-Roles holder from using the bot to grant
    roles above their own rank.
    """
    if actor.guild.owner_id == actor.id:
        return True
    return actor.top_role > role


def dangerous_role_perms(role: discord.Role) -> list[str]:
    """Return the names of any server-/member-control permissions `role` grants."""
    perms = role.permissions
    return [name for name in DANGEROUS_ROLE_PERMS if getattr(perms, name, False)]


def mod_action_embed(
    title: str,
    target: discord.Member | discord.User,
    reason: str,
    *,
    moderator: discord.Member | discord.User | None = None,
    color: int = YELLOW,
) -> discord.Embed:
    """Consistently styled embed for mod actions (bot-automated or human).

    moderator=None  →  "NanoBot (automated)"
    moderator=member →  mention + display info
    """
    e = embed(title, f"{target.mention} {user_display(target)}", color)
    e.add_field(name="Reason", value=reason or "No reason given", inline=False)
    e.add_field(
        name="Moderator",
        value=(
            f"{moderator.mention} {user_display(moderator)}"
            if moderator is not None
            else "NanoBot (automated)"
        ),
        inline=False,
    )
    return e


def user_log(user) -> str:
    """Return 'Display Name (@username, id)' for plain-text log lines.

    Logs the readable display name first (what mods recognize) plus the stable
    @username and ID, since str(user) alone yields only the unreadable username.
    """
    return f"{user.display_name} (@{user.name}, {user.id})"


# ── Shared economy utilities ───────────────────────────────────────────────────
# ── Timed coin multipliers ───────────────────────────────────────────────────
# `coin_boost` is part of the shared effect vocabulary (docs/economy-design.md):
# a timed multiplier on coins *earned*, granted by voting and by consumables.
# It deliberately does not touch coins that merely move — a /pay transfer, a
# /rob theft or a casino payout — because multiplying those would mint the
# difference out of nothing rather than rewarding an activity.
COIN_BOOST_KEY = "coin_boost"


def coin_boost_multiplier(effects: dict) -> float:
    """The active coin multiplier from a member's effects. 1.0 when none.

    Takes the already-fetched effects dict rather than hitting the database, so
    a payout site that has one (every one of them does) pays nothing extra for
    honouring the boost.
    """
    magnitude = (effects or {}).get(COIN_BOOST_KEY, {}).get("magnitude") or 1.0
    return max(1.0, float(magnitude))


def apply_coin_boost(amount: int, effects: dict) -> int:
    """Scale a coin *reward* by any active boost, never downward."""
    return max(amount, round(amount * coin_boost_multiplier(effects)))


def fmt_coins(amount: int, name: str, emoji: str) -> str:
    """Render a coin amount, e.g. '🪙 1,234 NanoCoins'.

    The single source for currency formatting — cogs' _money helpers delegate
    here (cogs/economy/helpers.py re-exports it for back-compat).
    """
    label = name if abs(amount) == 1 else f"{name}s"
    return f"{emoji} **{amount:,}** {label}"


# Every economy leaderboard offers the same two views, so the option is
# declared once: the underlying rows are global either way, "server" just
# filters them to the guild's members.
SCOPE_CHOICES = [
    discord.app_commands.Choice(name="This server", value="server"),
    discord.app_commands.Choice(name="Global (every server)", value="global"),
]


def member_ids(guild) -> list[int]:
    """Human member ids of a guild — the filter that turns a global economy
    table into a server-scoped leaderboard."""
    return [m.id for m in guild.members if not m.bot]


def page_rows(rows: list, sort_key, page: int, per: int = 10):
    """Rank a fully-materialised leaderboard and cut one page from it.

    Global economy rows filtered to a guild's members come back unordered (the
    DB does the membership filter, the caller does the ranking), so sorting and
    paging live here instead of being duplicated in every board command.
    Returns (rows_on_page, page, pages, total).
    """
    ranked = sorted(rows, key=sort_key)
    total = len(ranked)
    pages = max(1, (total + per - 1) // per)
    page = max(1, min(page, pages))
    offset = (page - 1) * per
    return ranked[offset : offset + per], page, pages, total


def weighted_pick(table, roll: float):
    """Map a roll in [0, 1) onto a cumulative-weight table [(key, weight), …].

    The one implementation of the walk every drop table uses (fish rarity,
    ore, hunt/explore outcomes, slot symbols). Weights are expected to sum to
    ~1.0; the last key soaks up float round-off (a roll past the accumulated
    total). Identical to the per-cog loops it replaced: strict `roll < acc`,
    so a boundary roll falls into the *next* bucket.
    """
    acc = 0.0
    for key, weight in table:
        acc += weight
        if roll < acc:
            return key
    return table[-1][0]


def spawn_tracked(tasks: dict, key, coro) -> asyncio.Task:
    """Start a background task under `key`, cancelling whatever it replaces.

    Every restart-safe cog keeps a ``{key: Task}`` dict of live timers —
    reminders, recurring posts, auto-unbans, gatekeeper unmutes/kicks. Assigning
    straight into that dict silently orphans the task already sitting there: it
    keeps running, un-cancellable, and fires alongside its replacement. That is
    exactly what used to happen when `on_ready` re-fired after a gateway
    reconnect and every cog re-armed its schedules — one duplicate delivery per
    reconnect, forever.

    Cancelling first makes re-arming idempotent, so restore paths are safe to
    run more than once.
    """
    old = tasks.get(key)
    if old is not None and not old.done():
        old.cancel()
    task = asyncio.create_task(coro)
    tasks[key] = task
    return task


class KeyedLocks:
    """Per-key asyncio locks that free themselves when idle.

    Replaces the per-cog `self._locks: dict[(guild, user), asyncio.Lock]`
    pattern, which held every lock ever created for the process lifetime.
    Entries are refcounted: the count covers the holder *and* every waiter, so
    an entry is deleted only when the last interested task releases — a lock
    can never be dropped while locked or awaited, and two tasks can never
    hold different lock objects for the same key. Single-event-loop bot, so
    the count mutations (between awaits) need no extra synchronization.

    Usage (an async context manager, so existing `async with self._lock(g, u):`
    call sites work unchanged when `_lock` returns `self._locks.hold((g, u))`):

        async with locks.hold(key):
            ...
    """

    def __init__(self):
        self._entries: dict = {}  # key -> [asyncio.Lock, refcount]

    def hold(self, key):
        return _KeyedLockHold(self, key)

    def __len__(self):
        return len(self._entries)


class _KeyedLockHold:
    __slots__ = ("_locks", "_key", "_entry")

    def __init__(self, locks: KeyedLocks, key):
        self._locks = locks
        self._key = key
        self._entry = None

    async def __aenter__(self):
        entry = self._locks._entries.get(self._key)
        if entry is None:
            entry = self._locks._entries[self._key] = [asyncio.Lock(), 0]
        entry[1] += 1
        self._entry = entry
        try:
            await entry[0].acquire()
        except BaseException:
            # A cancelled acquire (asyncio.wait_for timeout, task cancellation
            # on unload) never reaches __aexit__ — release the refcount here
            # or the entry would be pinned forever.
            entry[1] -= 1
            if entry[1] == 0 and self._locks._entries.get(self._key) is entry:
                del self._locks._entries[self._key]
            raise

    async def __aexit__(self, exc_type, exc, tb):
        entry = self._entry
        entry[0].release()
        entry[1] -= 1
        if entry[1] == 0 and self._locks._entries.get(self._key) is entry:
            del self._locks._entries[self._key]
        return False
