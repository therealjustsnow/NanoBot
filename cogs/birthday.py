"""
cogs/birthday.py
Per-server birthday tracker.

Members register their birthday once; NanoBot announces it in a configured
channel on the day (with a festive GIF), and — if the birthday person happens
to be in a voice channel at announce time — hops in, plays "Happy Birthday"
once, and leaves. The song fires exactly once per birthday: a per-guild
"singing" guard plus the per-row last-announced date stamp stop it from
looping or re-joining.

The announcement is driven by a 15-minute background check (not a live event),
so it survives restarts. Each birthday fires once per year: the row's
`last_announced` is stamped with the local date before the announcement runs,
so a crash mid-announce can't double-fire and a later check the same day is a
no-op. If the bot was offline during the configured hour, the birthday still
fires whenever the next check lands that same local day — no cross-day catch-up.

──────────────────────────────────────────────────────
Commands  (group: /birthday, aliases: bday, birthdays)
──────────────────────────────────────────────────────
  /birthday set <date>          → register your birthday (e.g. "March 5", "5 Mar 1998", "03/05")
  /birthday remove              → delete your birthday
  /birthday view [member]       → show a member's birthday + countdown
  /birthday list                → upcoming birthdays in this server

  Manage Server only:
  /birthday channel <channel>   → set the announcement channel (turns the feature on)
  /birthday disable             → turn announcements off
  /birthday timezone <tz>       → set the IANA timezone used to decide "today" (default UTC)
  /birthday hour <0-23>         → local hour the announcement fires (default 9)
  /birthday message <text>      → customize the announcement (vars below; "default" resets)
  /birthday gifs <on|off>       → toggle the festive GIF
  /birthday voice <on|off>      → toggle joining voice to play the song
  /birthday ping <on|off>       → toggle pinging the birthday person
  /birthday config              → show the current settings
  /birthday test [member]       → preview the announcement now (ignores the once-a-year guard)

Message variables: {mention} {user} {username} {server} {age}

──────────────────────────────────────────────────────
Storage  (birthdays + birthday_config tables in nanobot.db)
──────────────────────────────────────────────────────
  birthdays        (guild_id, user_id) PK — month, day, year?, last_announced
  birthday_config  (guild_id) PK          — enabled, channel, timezone, hour, toggles, song
"""

import asyncio
import logging
import os
import random
import re
import subprocess
from datetime import date, datetime
from typing import Optional
from zoneinfo import available_timezones, ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands, tasks

from utils import db
from utils import helpers as h

log = logging.getLogger("NanoBot.birthday")

# Pink, festive — distinct from the brand blue.
BIRTHDAY_COLOR = 0xFF6FB5

_DEFAULT_MESSAGE = (
    "🎉 It's {mention}'s birthday today! Everybody wish them a happy birthday! 🎂🎈"
)

_VARS_HELP = (
    "`{mention}` — ping  ·  `{user}` — display name  ·  "
    "`{username}` — full username  ·  `{server}` — server name  ·  "
    "`{age}` — age turning (if year known)"
)

# A handful of festive GIFs, picked at random per announcement. If one ever
# 404s Discord just drops the image — the text still lands.
_BIRTHDAY_GIFS = [
    "https://media.giphy.com/media/Os4Mk2lDWNXMI/giphy.gif",
    "https://media.giphy.com/media/7Js6gSMQVgT5C/giphy.gif",
    "https://media.giphy.com/media/26u4cqiYI30juCOGY/giphy.gif",
    "https://media.giphy.com/media/g5R9dok94mrIvplmZd/giphy.gif",
    "https://media.giphy.com/media/l0MYt5jPR6QX5pnqM/giphy.gif",
    "https://media.giphy.com/media/IRsBljLW2bWPYTb1IY/giphy.gif",
]

# ── Date helpers (pure — imported directly by tests) ───────────────────────────

_MONTH_NAMES = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]  # fmt: skip

_MONTHS = {
    "january": 1, "jan": 1,
    "february": 2, "feb": 2,
    "march": 3, "mar": 3,
    "april": 4, "apr": 4,
    "may": 5,
    "june": 6, "jun": 6,
    "july": 7, "jul": 7,
    "august": 8, "aug": 8,
    "september": 9, "sep": 9, "sept": 9,
    "october": 10, "oct": 10,
    "november": 11, "nov": 11,
    "december": 12, "dec": 12,
}  # fmt: skip

# Max day per month (February allows 29 so leap-day birthdays register).
_MAX_DAY = (31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)


def _is_leap(year: int) -> bool:
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


def parse_birthday(s: str) -> tuple[int, int, int | None] | None:
    """Parse a birthday string into ``(month, day, year_or_None)`` or ``None``.

    Accepts month-name forms ("March 5", "5 Mar 1998", "March 5, 1998"),
    numeric month-first forms ("03/05", "3-5-1998") and ISO ("1998-03-05").
    Ordinal suffixes (5th) are tolerated. Years must be four digits.
    """
    if not s:
        return None
    s = s.strip()
    month = day = year = None

    low = s.lower().replace(",", " ")
    # Strip ordinal suffixes: "5th" → "5".
    tokens = [re.sub(r"^(\d+)(st|nd|rd|th)$", r"\1", t) for t in low.split()]

    name_idx = next((i for i, t in enumerate(tokens) if t in _MONTHS), None)
    if name_idx is not None:
        month = _MONTHS[tokens[name_idx]]
        nums = [int(t) for t in tokens if t.isdigit()]
        days = [n for n in nums if 1 <= n <= 31]
        years = [n for n in nums if 1000 <= n <= 9999]
        if not days:
            return None
        day = days[0]
        year = years[0] if years else None
    else:
        iso = re.fullmatch(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", s)
        if iso:
            year, month, day = int(iso[1]), int(iso[2]), int(iso[3])
        else:
            m = re.fullmatch(r"(\d{1,2})[-/.](\d{1,2})(?:[-/.](\d{4}))?", s)
            if not m:
                return None
            month, day = int(m[1]), int(m[2])
            year = int(m[3]) if m[3] else None

    if not (1 <= month <= 12):
        return None
    if not (1 <= day <= _MAX_DAY[month - 1]):
        return None
    if year is not None and not (1900 <= year <= 2100):
        return None
    return month, day, year


def fmt_birthday(month: int, day: int, year: int | None = None) -> str:
    """'March 5' or 'March 5, 1998'."""
    out = f"{_MONTH_NAMES[month - 1]} {day}"
    if year:
        out += f", {year}"
    return out


def next_birthday_date(month: int, day: int, today: date) -> date:
    """The next calendar date this birthday lands on (today counts as itself).

    A Feb 29 birthday falls back to Feb 28 in non-leap years.
    """

    def _make(year: int) -> date:
        try:
            return date(year, month, day)
        except ValueError:
            return date(year, month, 28)  # Feb 29 → Feb 28

    d = _make(today.year)
    if d < today:
        d = _make(today.year + 1)
    return d


def days_until_birthday(month: int, day: int, today: date) -> int:
    return (next_birthday_date(month, day, today) - today).days


def is_birthday_today(month: int, day: int, today: date) -> bool:
    if month == today.month and day == today.day:
        return True
    # Feb 29 birthday celebrated on Feb 28 when the year isn't a leap year.
    if month == 2 and day == 29 and today.month == 2 and today.day == 28:
        return not _is_leap(today.year)
    return False


def age_on(month: int, day: int, year: int | None, on_date: date) -> int | None:
    """Age the person reaches on/by ``on_date`` (None when birth year unknown)."""
    if year is None:
        return None
    age = on_date.year - year
    if (on_date.month, on_date.day) < (month, day):
        age -= 1
    return age


# ── "Happy Birthday" melody (synthesized, no asset/network needed) ─────────────
# C-major, public-domain melody rendered as plain sine tones by FFmpeg the first
# time it's needed, then cached on disk. (freq_hz, duration_seconds).
_HB_NOTES = [
    (392, 0.25), (392, 0.25), (440, 0.50), (392, 0.50), (523, 0.50), (494, 1.00),
    (392, 0.25), (392, 0.25), (440, 0.50), (392, 0.50), (587, 0.50), (523, 1.00),
    (392, 0.25), (392, 0.25), (784, 0.50), (659, 0.50), (523, 0.50), (494, 0.50), (440, 1.00),
    (698, 0.25), (698, 0.25), (659, 0.50), (523, 0.50), (587, 0.50), (523, 1.00),
]  # fmt: skip

_SONG_DIR = os.path.join("data", "birthday_cache")
_SONG_PATH = os.path.join(_SONG_DIR, "happy_birthday.wav")


def _ffmpeg_song_cmd(path: str) -> list[str]:
    """Build the FFmpeg command that renders _HB_NOTES to a stereo WAV at *path*."""
    inputs: list[str] = []
    chain: list[str] = []
    labels: list[str] = []
    for i, (freq, dur) in enumerate(_HB_NOTES):
        inputs += [
            "-f", "lavfi", "-t", f"{dur:.3f}",
            "-i", f"sine=frequency={freq}:sample_rate=48000",
        ]  # fmt: skip
        fade_out = max(dur - 0.07, 0.0)
        chain.append(
            f"[{i}]afade=t=in:st=0:d=0.02,"
            f"afade=t=out:st={fade_out:.3f}:d=0.07,volume=0.25[a{i}]"
        )
        labels.append(f"[a{i}]")
    filtergraph = (
        ";".join(chain)
        + ";"
        + "".join(labels)
        + f"concat=n={len(_HB_NOTES)}:v=0:a=1[out]"
    )
    return [
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        *inputs,
        "-filter_complex", filtergraph,
        "-map", "[out]", "-ac", "2", "-ar", "48000", "-y", path,
    ]  # fmt: skip


# ══════════════════════════════════════════════════════════════════════════════
class Birthday(commands.Cog):
    """Birthday registration + per-server announcements."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Guilds where the song is currently playing — blocks a second join while
        # one is in flight (belt-and-braces with the last_announced stamp).
        self._singing: set[int] = set()
        self._tz_cache: dict[str, ZoneInfo] = {}
        self._check_loop.start()

    def cog_unload(self):
        self._check_loop.cancel()

    # ── Timezone ────────────────────────────────────────────────────────────────

    def _tz(self, name: str | None) -> ZoneInfo:
        name = name or "UTC"
        tz = self._tz_cache.get(name)
        if tz is None:
            try:
                tz = ZoneInfo(name)
            except Exception:
                tz = ZoneInfo("UTC")
            self._tz_cache[name] = tz
        return tz

    # ── Background check ────────────────────────────────────────────────────────

    @tasks.loop(minutes=15)
    async def _check_loop(self):
        try:
            configs = await db.get_enabled_birthday_configs()
        except Exception:
            log.exception("Birthday check: failed to load configs")
            return
        for guild_id, cfg in configs.items():
            guild = self.bot.get_guild(guild_id)
            if guild is None:
                continue
            try:
                await self._check_guild(guild, cfg)
            except Exception:
                log.exception("Birthday check failed for guild %s", guild_id)

    @_check_loop.before_loop
    async def _before_check_loop(self):
        await self.bot.wait_until_ready()

    async def _check_guild(self, guild: discord.Guild, cfg: dict) -> None:
        now = datetime.now(self._tz(cfg["timezone"]))
        if now.hour < int(cfg["hour"]):
            return  # before the configured local hour — wait for a later tick
        channel = guild.get_channel(int(cfg["channel_id"]))
        if not isinstance(channel, discord.abc.Messageable):
            return

        today = now.date()
        today_str = today.isoformat()
        for bd in await db.get_guild_birthdays(guild.id):
            if bd["last_announced"] == today_str:
                continue
            if not is_birthday_today(bd["month"], bd["day"], today):
                continue
            member = guild.get_member(int(bd["user_id"]))
            if member is None:
                continue  # left the server — don't announce, don't stamp
            # Stamp BEFORE announcing so a failure can't double-fire next tick.
            await db.set_birthday_announced(guild.id, member.id, today_str)
            await self._announce(member, bd, cfg, channel, today)

    # ── Announcement ────────────────────────────────────────────────────────────

    def _fill(self, template: str, member: discord.Member, age: int | None) -> str:
        age_str = str(age) if age is not None else "another year older"
        return (
            template.replace("{mention}", member.mention)
            .replace("{user}", member.display_name)
            .replace("{username}", str(member))
            .replace("{server}", member.guild.name)
            .replace("{age}", age_str)
        )

    def _build_embed(
        self, member: discord.Member, bd: dict, cfg: dict, today: date
    ) -> discord.Embed:
        age = age_on(bd["month"], bd["day"], bd.get("year"), today)
        template = cfg["message"] or _DEFAULT_MESSAGE
        e = h.embed(
            "🎂 Happy Birthday!",
            self._fill(template, member, age),
            color=BIRTHDAY_COLOR,
        )
        if age is not None:
            e.add_field(name="Turns", value=f"**{age}** 🎈", inline=True)
        try:
            e.set_thumbnail(url=member.display_avatar.url)
        except Exception:
            pass
        if cfg["gif_enabled"]:
            e.set_image(url=random.choice(_BIRTHDAY_GIFS))
        return e

    async def _announce(
        self,
        member: discord.Member,
        bd: dict,
        cfg: dict,
        channel: discord.abc.Messageable,
        today: date,
    ) -> None:
        embed = self._build_embed(member, bd, cfg, today)
        content = member.mention if cfg["ping_enabled"] else None
        try:
            await channel.send(
                content=content,
                embed=embed,
                allowed_mentions=discord.AllowedMentions(users=True),
            )
        except discord.HTTPException as exc:
            log.warning("Birthday announce send failed in %s: %s", member.guild.id, exc)
        log.info("Birthday announced for %s in %s", h.user_log(member), member.guild.id)

        if cfg["vc_enabled"]:
            await self._maybe_sing(member, cfg)

    # ── Voice-channel song (plays once, then leaves) ───────────────────────────

    async def _ensure_song(self, cfg: dict) -> str | None:
        """Resolve the song source: a configured override, else the cached synth."""
        override = (cfg or {}).get("song")
        if override:
            if override.startswith(("http://", "https://")) or os.path.isfile(override):
                return override
            log.warning("Birthday song override not found, using default: %r", override)

        if os.path.isfile(_SONG_PATH) and os.path.getsize(_SONG_PATH) > 0:
            return _SONG_PATH

        os.makedirs(_SONG_DIR, exist_ok=True)
        cmd = _ffmpeg_song_cmd(_SONG_PATH)

        def _build() -> tuple[int, bytes]:
            try:
                proc = subprocess.run(cmd, capture_output=True, timeout=60)
                return proc.returncode, proc.stderr
            except FileNotFoundError:
                return 127, b"ffmpeg not found"
            except subprocess.TimeoutExpired:
                return 124, b"ffmpeg timed out"

        rc, err = await asyncio.get_event_loop().run_in_executor(None, _build)
        if rc != 0:
            log.warning("Birthday song render failed (rc=%s): %s", rc, err[:200])
            return None
        return _SONG_PATH

    async def _maybe_sing(self, member: discord.Member, cfg: dict) -> None:
        voice = member.voice
        if voice is None or voice.channel is None:
            return  # not in a voice channel — nothing to do
        guild = member.guild
        # Don't hijack an existing connection (music cog, or a sing already going).
        if guild.voice_client is not None or guild.id in self._singing:
            return
        channel = voice.channel
        perms = channel.permissions_for(guild.me)
        if not (perms.connect and perms.speak):
            return

        self._singing.add(guild.id)
        vc: discord.VoiceClient | None = None
        try:
            path = await self._ensure_song(cfg)
            if not path:
                return
            vc = await channel.connect(self_deaf=True, timeout=20)
            done = asyncio.Event()

            def _after(error: Exception | None) -> None:
                if error:
                    log.warning("Birthday song playback error: %s", error)
                self.bot.loop.call_soon_threadsafe(done.set)

            vc.play(discord.FFmpegPCMAudio(path), after=_after)
            # Song is ~13s; cap the wait so a stuck stream can't pin the bot in VC.
            try:
                await asyncio.wait_for(done.wait(), timeout=45)
            except asyncio.TimeoutError:
                log.warning("Birthday song didn't finish in time in %s", guild.id)
        except Exception as exc:
            log.warning("Birthday song failed in %s: %s", guild.id, exc)
        finally:
            if vc is not None:
                try:
                    await vc.disconnect(force=True)
                except Exception:
                    pass
            self._singing.discard(guild.id)

    # ══════════════════════════════════════════════════════════════════════════
    #  /birthday  group
    # ══════════════════════════════════════════════════════════════════════════

    @commands.hybrid_group(
        name="birthday",
        aliases=["bday", "birthdays"],
        invoke_without_command=True,
        description="Register your birthday and let NanoBot celebrate it.",
        extras={
            "category": "🎂 Birthdays",
            "short": "Register your birthday; NanoBot announces it on the day",
            "usage": "birthday [set|remove|view|list|...]",
            "desc": "Members register a birthday once; NanoBot announces it in the configured channel on the day with a festive GIF, and — if the birthday person is in voice — joins to play 'Happy Birthday' once.\nNo args: shows your own birthday.\nManage Server: channel, disable, timezone, hour, message, gifs, voice, ping, config, test.",
            "args": [
                ("set", "Register your birthday, e.g. 'March 5' or '03/05/1998'"),
                ("member", "Whose birthday to view (view/test)"),
            ],
            "perms": "None to register; Manage Server for setup subcommands",
            "example": "{prefix}birthday set March 5\n{prefix}birthday list\n{prefix}birthday channel #general",
        },
    )
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def birthday(self, ctx: commands.Context):
        """Default: show the caller's own registered birthday."""
        await self._view(ctx, ctx.author)

    # ── set ─────────────────────────────────────────────────────────────────────

    @birthday.command(
        name="set",
        aliases=["register", "add"],
        description="Register your birthday (e.g. 'March 5', '5 Mar 1998', '03/05').",
    )
    @app_commands.describe(
        date="Your birthday — 'March 5', '5 Mar 1998', '03/05/1998', or '1998-03-05'"
    )
    async def birthday_set(self, ctx: commands.Context, *, date: str):
        if ctx.guild is None:
            return await ctx.reply(
                embed=h.err(
                    "Birthdays are registered per server — use this in a server."
                ),
                ephemeral=True,
            )
        parsed = parse_birthday(date)
        if parsed is None:
            return await ctx.reply(
                embed=h.err(
                    "Couldn't read that date.\n"
                    "**Try:** `March 5` · `5 Mar 1998` · `03/05` · `1998-03-05`\n"
                    "Year is optional (add it to show your age)."
                ),
                ephemeral=True,
            )
        month, day, year = parsed
        if year is not None and year > datetime.now().year:
            return await ctx.reply(
                embed=h.err("That birth year is in the future. 👀"),
                ephemeral=True,
            )

        await db.set_birthday(ctx.guild.id, ctx.author.id, month, day, year)

        cfg = await db.get_birthday_config(ctx.guild.id)
        today = datetime.now(self._tz(cfg["timezone"])).date()
        until = days_until_birthday(month, day, today)
        when = (
            "🎉 **That's today — happy birthday!**"
            if until == 0
            else f"⏳ **{until}** day{'s' if until != 1 else ''} to go "
            f"({discord.utils.format_dt(datetime.combine(next_birthday_date(month, day, today), datetime.min.time(), tzinfo=self._tz(cfg['timezone'])), style='D')})"
        )
        lines = [f"🎂 Birthday saved: **{fmt_birthday(month, day, year)}**", when]
        if not cfg["enabled"]:
            lines.append(
                "\n_Note: birthday announcements aren't set up in this server yet._"
            )
        await ctx.reply(
            embed=h.ok("\n".join(lines), "🎂 Birthday Registered"), ephemeral=True
        )

    # ── remove ──────────────────────────────────────────────────────────────────

    @birthday.command(
        name="remove",
        aliases=["delete", "clear", "unset"],
        description="Delete your registered birthday.",
    )
    async def birthday_remove(self, ctx: commands.Context):
        if ctx.guild is None:
            return await ctx.reply(embed=h.err("Use this in a server."), ephemeral=True)
        removed = await db.remove_birthday(ctx.guild.id, ctx.author.id)
        if not removed:
            return await ctx.reply(
                embed=h.info(
                    "You don't have a birthday registered here.", "🎂 Birthday"
                ),
                ephemeral=True,
            )
        await ctx.reply(
            embed=h.ok("Your birthday has been removed.", "🗑️ Removed"), ephemeral=True
        )

    # ── view ────────────────────────────────────────────────────────────────────

    @birthday.command(
        name="view",
        aliases=["get", "show"],
        description="Show a member's birthday and countdown.",
    )
    @app_commands.describe(member="Whose birthday to view (defaults to you)")
    async def birthday_view(
        self, ctx: commands.Context, member: Optional[discord.Member] = None
    ):
        await self._view(ctx, member or ctx.author)

    async def _view(self, ctx: commands.Context, member: discord.Member):
        if ctx.guild is None:
            return await ctx.reply(embed=h.err("Use this in a server."), ephemeral=True)
        bd = await db.get_birthday(ctx.guild.id, member.id)
        if bd is None:
            who = (
                "You haven't"
                if member == ctx.author
                else f"{member.display_name} hasn't"
            )
            verb = "register your" if member == ctx.author else "registered a"
            hint = (
                "\nSet yours with `/birthday set <date>`."
                if member == ctx.author
                else ""
            )
            return await ctx.reply(
                embed=h.info(f"{who} {verb} birthday yet.{hint}", "🎂 Birthday"),
                ephemeral=True,
            )

        cfg = await db.get_birthday_config(ctx.guild.id)
        today = datetime.now(self._tz(cfg["timezone"])).date()
        until = days_until_birthday(bd["month"], bd["day"], today)
        age = age_on(bd["month"], bd["day"], bd.get("year"), today)

        e = h.embed(
            f"🎂 {member.display_name}'s Birthday",
            color=BIRTHDAY_COLOR,
        )
        e.set_thumbnail(url=member.display_avatar.url)
        e.add_field(
            name="Date",
            value=fmt_birthday(bd["month"], bd["day"], bd.get("year")),
            inline=True,
        )
        if until == 0:
            e.add_field(name="Countdown", value="🎉 **Today!**", inline=True)
        else:
            nxt = next_birthday_date(bd["month"], bd["day"], today)
            e.add_field(
                name="Countdown",
                value=f"**{until}** day{'s' if until != 1 else ''} "
                f"({nxt.strftime('%b %d')})",
                inline=True,
            )
        if age is not None:
            turning = age if until == 0 else age + 1
            e.add_field(name="Turning", value=f"**{turning}** 🎈", inline=True)
        await ctx.reply(embed=e, ephemeral=True)

    # ── list ────────────────────────────────────────────────────────────────────

    @birthday.command(
        name="list",
        aliases=["upcoming", "all"],
        description="Show upcoming birthdays in this server.",
    )
    async def birthday_list(self, ctx: commands.Context):
        if ctx.guild is None:
            return await ctx.reply(embed=h.err("Use this in a server."), ephemeral=True)
        rows = await db.get_guild_birthdays(ctx.guild.id)
        cfg = await db.get_birthday_config(ctx.guild.id)
        today = datetime.now(self._tz(cfg["timezone"])).date()

        entries = []
        for bd in rows:
            member = ctx.guild.get_member(int(bd["user_id"]))
            if member is None:
                continue  # member left — skip (row is harmless, kept for return)
            until = days_until_birthday(bd["month"], bd["day"], today)
            entries.append((until, member, bd))
        entries.sort(key=lambda x: x[0])

        if not entries:
            return await ctx.reply(
                embed=h.info(
                    "No birthdays registered here yet.\n"
                    "Be the first: `/birthday set <date>`",
                    "🎂 Birthdays",
                ),
                ephemeral=True,
            )

        lines = []
        for until, member, bd in entries[:25]:
            datestr = fmt_birthday(bd["month"], bd["day"])
            if until == 0:
                tail = "🎉 **today!**"
            elif until == 1:
                tail = "tomorrow"
            else:
                tail = f"in {until} days"
            lines.append(f"**{datestr}** — {member.mention} · {tail}")

        e = h.embed(
            f"🎂 Upcoming Birthdays ({len(entries)})",
            "\n".join(lines),
            color=BIRTHDAY_COLOR,
        )
        if len(entries) > 25:
            e.set_footer(text=f"NanoBot · showing 25 of {len(entries)}")
        await ctx.reply(embed=e, ephemeral=True)

    # ══════════════════════════════════════════════════════════════════════════
    #  Manage-Server setup subcommands
    # ══════════════════════════════════════════════════════════════════════════

    @birthday.command(
        name="channel",
        description="Set the birthday announcement channel (turns the feature on).",
    )
    @commands.has_permissions(manage_guild=True)
    @app_commands.describe(channel="Channel where birthday announcements are posted")
    async def birthday_channel(
        self, ctx: commands.Context, channel: discord.TextChannel
    ):
        await db.set_birthday_config(
            ctx.guild.id, enabled=True, channel_id=str(channel.id)
        )
        cfg = await db.get_birthday_config(ctx.guild.id)
        await ctx.reply(
            embed=h.ok(
                f"Birthday announcements are **on** in {channel.mention}.\n"
                f"Firing daily around **{cfg['hour']:02d}:00 {cfg['timezone']}**.\n"
                "Members register with `/birthday set <date>`.",
                "🎂 Birthdays Enabled",
            )
        )

    @birthday.command(
        name="disable",
        aliases=["off"],
        description="Turn birthday announcements off.",
    )
    @commands.has_permissions(manage_guild=True)
    async def birthday_disable(self, ctx: commands.Context):
        await db.set_birthday_config(ctx.guild.id, enabled=False)
        await ctx.reply(
            embed=h.ok(
                "Birthday announcements are **off**. Registered birthdays are kept.",
                "🎂 Birthdays Disabled",
            )
        )

    @birthday.command(
        name="timezone",
        aliases=["tz"],
        description="Set the IANA timezone used to decide when 'today' is.",
    )
    @commands.has_permissions(manage_guild=True)
    @app_commands.describe(
        timezone="IANA name, e.g. America/New_York, Europe/London, UTC"
    )
    async def birthday_timezone(self, ctx: commands.Context, timezone: str):
        timezone = timezone.strip()
        if timezone not in available_timezones():
            return await ctx.reply(
                embed=h.err(
                    f"`{timezone}` isn't a valid timezone.\n"
                    "Use an IANA name like `America/New_York`, `Europe/London`, or `UTC`.\n"
                    "Full list: <https://en.wikipedia.org/wiki/List_of_tz_database_time_zones>"
                ),
                ephemeral=True,
            )
        await db.set_birthday_config(ctx.guild.id, timezone=timezone)
        await ctx.reply(
            embed=h.ok(f"Timezone set to **{timezone}**.", "🕐 Timezone Set")
        )

    @birthday.command(
        name="hour",
        description="Set the local hour (0-23) announcements fire.",
    )
    @commands.has_permissions(manage_guild=True)
    @app_commands.describe(hour="Hour of the day, 0-23 (e.g. 9 = 9 AM local time)")
    async def birthday_hour(self, ctx: commands.Context, hour: int):
        if not (0 <= hour <= 23):
            return await ctx.reply(
                embed=h.err("Hour must be between **0** and **23**."), ephemeral=True
            )
        await db.set_birthday_config(ctx.guild.id, hour=hour)
        cfg = await db.get_birthday_config(ctx.guild.id)
        await ctx.reply(
            embed=h.ok(
                f"Announcements fire around **{hour:02d}:00 {cfg['timezone']}**.",
                "🕐 Hour Set",
            )
        )

    @birthday.command(
        name="message",
        description="Customize the announcement text ('default' resets it).",
    )
    @commands.has_permissions(manage_guild=True)
    @app_commands.describe(
        text=f"Template — variables: {_VARS_HELP}. Type 'default' to reset."
    )
    async def birthday_message(self, ctx: commands.Context, *, text: str):
        if text.strip().lower() in ("default", "reset", "none"):
            await db.set_birthday_config(ctx.guild.id, message=None)
            return await ctx.reply(
                embed=h.ok(
                    "Announcement message reset to the default.", "💬 Message Reset"
                )
            )
        if len(text) > 1500:
            return await ctx.reply(
                embed=h.err("Message must be 1500 characters or fewer."), ephemeral=True
            )
        await db.set_birthday_config(ctx.guild.id, message=text)
        await ctx.reply(
            embed=h.ok(
                f"Announcement message updated.\n**Variables:** {_VARS_HELP}",
                "💬 Message Set",
            )
        )

    async def _toggle(self, ctx: commands.Context, key: str, state: str, label: str):
        on = state.strip().lower() in ("on", "yes", "true", "enable", "enabled", "1")
        await db.set_birthday_config(ctx.guild.id, **{key: on})
        await ctx.reply(
            embed=h.ok(f"{label} is now **{'on' if on else 'off'}**.", "🎂 Updated")
        )

    @birthday.command(
        name="gifs", description="Toggle the festive GIF in announcements."
    )
    @commands.has_permissions(manage_guild=True)
    @app_commands.describe(state="on or off")
    async def birthday_gifs(self, ctx: commands.Context, state: str):
        await self._toggle(ctx, "gif_enabled", state, "Birthday GIFs")

    @birthday.command(
        name="voice",
        description="Toggle joining voice to play the birthday song.",
    )
    @commands.has_permissions(manage_guild=True)
    @app_commands.describe(state="on or off")
    async def birthday_voice(self, ctx: commands.Context, state: str):
        await self._toggle(ctx, "vc_enabled", state, "Voice-channel song")

    @birthday.command(name="ping", description="Toggle pinging the birthday person.")
    @commands.has_permissions(manage_guild=True)
    @app_commands.describe(state="on or off")
    async def birthday_ping(self, ctx: commands.Context, state: str):
        await self._toggle(ctx, "ping_enabled", state, "Birthday ping")

    @birthday.command(
        name="config", aliases=["settings"], description="Show birthday settings."
    )
    @commands.has_permissions(manage_guild=True)
    async def birthday_config(self, ctx: commands.Context):
        cfg = await db.get_birthday_config(ctx.guild.id)
        channel = (
            ctx.guild.get_channel(int(cfg["channel_id"])) if cfg["channel_id"] else None
        )
        count = len(await db.get_guild_birthdays(ctx.guild.id))
        e = h.embed("🎂 Birthday Settings", color=BIRTHDAY_COLOR)
        e.add_field(
            name="Status",
            value="🟢 Enabled" if cfg["enabled"] else "🔴 Disabled",
            inline=True,
        )
        e.add_field(
            name="Channel",
            value=channel.mention if channel else "_not set_",
            inline=True,
        )
        e.add_field(name="Registered", value=f"{count}", inline=True)
        e.add_field(name="Timezone", value=cfg["timezone"], inline=True)
        e.add_field(name="Hour", value=f"{cfg['hour']:02d}:00", inline=True)
        e.add_field(
            name="GIF / Voice / Ping",
            value=f"{'✅' if cfg['gif_enabled'] else '❌'} / "
            f"{'✅' if cfg['vc_enabled'] else '❌'} / "
            f"{'✅' if cfg['ping_enabled'] else '❌'}",
            inline=True,
        )
        e.add_field(
            name="Message",
            value=(cfg["message"] or _DEFAULT_MESSAGE)[:200],
            inline=False,
        )
        await ctx.reply(embed=e, ephemeral=True)

    @birthday.command(
        name="test",
        description="Preview the birthday announcement now (doesn't affect the schedule).",
    )
    @commands.has_permissions(manage_guild=True)
    @app_commands.describe(member="Whose announcement to preview (defaults to you)")
    async def birthday_test(
        self, ctx: commands.Context, member: Optional[discord.Member] = None
    ):
        member = member or ctx.author
        cfg = await db.get_birthday_config(ctx.guild.id)
        bd = await db.get_birthday(ctx.guild.id, member.id)
        # Fall back to a placeholder date so a preview works before registering.
        if bd is None:
            today = datetime.now(self._tz(cfg["timezone"])).date()
            bd = {"month": today.month, "day": today.day, "year": None}
        today = datetime.now(self._tz(cfg["timezone"])).date()
        embed = self._build_embed(member, bd, cfg, today)
        await ctx.reply(
            content="**Preview** (this is what the announcement looks like):",
            embed=embed,
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Birthday(bot))
