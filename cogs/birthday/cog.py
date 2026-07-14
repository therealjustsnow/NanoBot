"""
cogs/birthday.py
Per-server birthday tracker.

Members register their birthday once; NanoBot announces it in a configured
channel on the day (always with a festive, reachability-checked GIF), and plays
"Happy Birthday" in voice for the birthday person. Members sharing a birthday
are celebrated in ONE combined post (joined mentions, a Turns field each)
rather than back-to-back posts. The song fires any time the birthday person is
in a voice channel that local day: on joining (via on_voice_state_update) AND
via a 15-minute sweep that catches people already sitting in voice before the
bot started or before the announce hour — but at most once per person per
local day: an in-memory "sung today" set (recorded only on a successful play)
plus a per-guild "singing" guard stop it from looping or re-joining. If the
bot is already connected in the guild, it reuses that connection when it's
idle and already in the birthday person's channel; otherwise (busy elsewhere,
e.g. the music cog, or connected to a different channel) it skips singing
rather than hijacking it.

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
  /birthday timezone <tz>       → set the timezone (start typing to search via autocomplete; auto-guessed from voice region at setup)
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
import subprocess
from datetime import date, datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo, available_timezones

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks

from utils import db
from utils import helpers as h
from utils.converters import SafeTextChannel

from .constants import (
    BIRTHDAY_COLOR,
    _BIRTHDAY_GIFS,
    _DEFAULT_MESSAGE,
    _SONG_DIR,
    _SONG_PATH,
    _TZ_CHOICES,
    _VARS_HELP,
)
from .helpers import (
    age_on,
    days_until_birthday,
    fmt_birthday,
    guess_timezone_from_regions,
    is_birthday_today,
    next_birthday_date,
    parse_birthday,
    _ffmpeg_song_cmd,
)

log = logging.getLogger("NanoBot.birthday")


class Birthday(commands.Cog):
    """Birthday registration + per-server announcements."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Guilds where the song is currently playing — blocks a second join while
        # one is in flight (belt-and-braces with the last_announced stamp).
        self._singing: set[int] = set()
        self._tz_cache: dict[str, ZoneInfo] = {}
        # (guild_id, user_id, local_date) tuples already serenaded in voice today.
        # In-memory (resets on restart) — stops the voice listener re-singing on
        # every re-join. Pruned daily in _check_loop.
        self._vc_sung: set[tuple[int, int, str]] = set()
        self._session: aiohttp.ClientSession | None = None
        self._good_gifs: list[str] = []
        self._gifs_checked = False
        self._gif_lock = asyncio.Lock()

    async def cog_load(self):
        self._session = aiohttp.ClientSession()
        self._check_loop.start()

    async def cog_unload(self):
        self._check_loop.cancel()
        if self._session and not self._session.closed:
            await self._session.close()

    # ── GIFs (validated so an announcement always shows a working one) ──────────

    async def _validate_gifs(self) -> None:
        """HEAD-check the gif list, keeping only the ones that resolve to images."""
        if not self._session or self._session.closed:
            return
        good: list[str] = []
        for url in _BIRTHDAY_GIFS:
            try:
                async with self._session.head(
                    url,
                    timeout=aiohttp.ClientTimeout(total=5),
                    allow_redirects=True,
                ) as resp:
                    ctype = resp.headers.get("Content-Type", "")
                    if resp.status == 200 and ctype.startswith("image"):
                        good.append(url)
            except Exception:
                continue  # network/HEAD-unsupported — just skip this one
        self._good_gifs = good
        log.info(
            "Birthday: %d/%d gifs verified reachable", len(good), len(_BIRTHDAY_GIFS)
        )

    async def _pick_gif(self) -> str:
        """Return a verified-working gif URL (falls back to the raw list if the
        check couldn't reach any — Discord still drops a dead image gracefully)."""
        async with self._gif_lock:
            if not self._gifs_checked:
                await self._validate_gifs()
                self._gifs_checked = True
        return random.choice(self._good_gifs or list(_BIRTHDAY_GIFS))

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
        self._prune_vc_sung()
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

    def _prune_vc_sung(self) -> None:
        """Drop voice-sung markers older than yesterday so the set can't grow
        without bound (dates are local strings; UTC-yesterday is a safe floor)."""
        if not self._vc_sung:
            return
        floor = (date.today() - timedelta(days=1)).isoformat()
        self._vc_sung = {k for k in self._vc_sung if k[2] >= floor}

    @_check_loop.before_loop
    async def _before_check_loop(self):
        await self.bot.wait_until_ready()

    # ── Voice listener: sing whenever the birthday person joins a channel ──────

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        # Only react to *joining* a channel (ignore leaves and in-channel state
        # changes like mute/deafen, which would otherwise re-trigger endlessly).
        if member.bot or after.channel is None:
            return
        if before.channel is not None and before.channel.id == after.channel.id:
            return

        cfg = await db.get_birthday_config(member.guild.id)
        if not cfg["enabled"] or not cfg["vc_enabled"]:
            return
        bd = await db.get_birthday(member.guild.id, member.id)
        if bd is None:
            return
        today = datetime.now(self._tz(cfg["timezone"])).date()
        if not is_birthday_today(bd["month"], bd["day"], today):
            return
        self._spawn_sing(member, cfg, today)

    async def _check_guild(self, guild: discord.Guild, cfg: dict) -> None:
        now = datetime.now(self._tz(cfg["timezone"]))
        today = now.date()
        today_str = today.isoformat()

        celebrants: list[tuple[discord.Member, dict]] = []
        for bd in await db.get_guild_birthdays(guild.id):
            if not is_birthday_today(bd["month"], bd["day"], today):
                continue
            member = guild.get_member(int(bd["user_id"]))
            if member is None:
                continue  # left the server — don't announce, don't stamp
            celebrants.append((member, bd))
        if not celebrants:
            return

        # Voice-song sweep, any hour of the local day: catches birthday folks
        # who were already sitting in voice before the bot started (no join
        # event to react to) or before the announce hour. _sing_for's
        # once-per-day guard keeps this 15-minute tick from re-singing.
        if cfg["vc_enabled"]:
            for member, _bd in celebrants:
                if member.voice is not None and member.voice.channel is not None:
                    self._spawn_sing(member, cfg, today)

        if now.hour < int(cfg["hour"]):
            return  # before the configured local hour — wait for a later tick
        channel = guild.get_channel(int(cfg["channel_id"]))
        if not isinstance(channel, discord.abc.Messageable):
            return

        pending = [(m, bd) for m, bd in celebrants if bd["last_announced"] != today_str]
        if not pending:
            return
        # Stamp BEFORE announcing so a failure can't double-fire next tick.
        for member, _bd in pending:
            await db.set_birthday_announced(guild.id, member.id, today_str)
        # One combined post covers everyone sharing the day.
        await self._announce(pending, cfg, channel, today)

    # ── Announcement ────────────────────────────────────────────────────────────

    @staticmethod
    def _join_names(names: list[str]) -> str:
        """'a' · 'a and b' · 'a, b and c'."""
        if len(names) <= 1:
            return names[0] if names else ""
        return ", ".join(names[:-1]) + f" and {names[-1]}"

    def _fill(
        self,
        template: str,
        *,
        mention: str,
        user: str,
        username: str,
        server: str,
        age: int | None,
    ) -> str:
        age_str = str(age) if age is not None else "another year older"
        return (
            template.replace("{mention}", mention)
            .replace("{user}", user)
            .replace("{username}", username)
            .replace("{server}", server)
            .replace("{age}", age_str)
        )

    def _turning_age(self, bd: dict, today: date) -> int | None:
        # The announcement celebrates the birthday itself, so report the age the
        # member turns on that day — not their age as of `today`. On the real
        # announce `today` is the birthday (next occurrence == today); for a
        # `/birthday test` preview before the day this still shows the upcoming age.
        bday = next_birthday_date(bd["month"], bd["day"], today)
        return age_on(bd["month"], bd["day"], bd.get("year"), bday)

    def _build_embed(
        self,
        celebrants: list[tuple[discord.Member, dict]],
        cfg: dict,
        today: date,
        gif_url: str | None = None,
    ) -> discord.Embed:
        """One embed for everyone sharing the day — a single celebrant keeps the
        classic card; multiple get joined mentions plus a Turns field each."""
        template = cfg["message"] or _DEFAULT_MESSAGE
        guild = celebrants[0][0].guild

        if len(celebrants) == 1:
            member, bd = celebrants[0]
            age = self._turning_age(bd, today)
            e = h.embed(
                "🎂 Happy Birthday!",
                self._fill(
                    template,
                    mention=member.mention,
                    user=member.display_name,
                    username=str(member),
                    server=guild.name,
                    age=age,
                ),
                color=BIRTHDAY_COLOR,
            )
            if age is not None:
                e.add_field(name="Turns", value=f"**{age}** 🎈", inline=True)
            try:
                e.set_thumbnail(url=member.display_avatar.url)
            except Exception:
                pass
        else:
            e = h.embed(
                "🎂 Happy Birthdays!",
                self._fill(
                    template,
                    mention=self._join_names([m.mention for m, _ in celebrants]),
                    user=self._join_names([m.display_name for m, _ in celebrants]),
                    username=self._join_names([str(m) for m, _ in celebrants]),
                    server=guild.name,
                    # A shared age would be wrong for a shared day — the
                    # per-member Turns fields below carry the numbers.
                    age=None,
                ),
                color=BIRTHDAY_COLOR,
            )
            for member, bd in celebrants[:25]:  # Discord's embed-field cap
                age = self._turning_age(bd, today)
                if age is not None:
                    e.add_field(
                        name=member.display_name,
                        value=f"turns **{age}** 🎈",
                        inline=True,
                    )

        if cfg["gif_enabled"] and gif_url:
            e.set_image(url=gif_url)
        return e

    async def _announce(
        self,
        celebrants: list[tuple[discord.Member, dict]],
        cfg: dict,
        channel: discord.abc.Messageable,
        today: date,
    ) -> None:
        guild_id = celebrants[0][0].guild.id
        gif_url = await self._pick_gif() if cfg["gif_enabled"] else None
        embed = self._build_embed(celebrants, cfg, today, gif_url)
        content = (
            " ".join(m.mention for m, _ in celebrants) if cfg["ping_enabled"] else None
        )
        try:
            await channel.send(
                content=content,
                embed=embed,
                allowed_mentions=discord.AllowedMentions(users=True),
            )
        except discord.HTTPException as exc:
            log.warning("Birthday announce send failed in %s: %s", guild_id, exc)
        log.info(
            "Birthday announced for %s in %s",
            ", ".join(h.user_log(m) for m, _ in celebrants),
            guild_id,
        )
        # No song spawn here — _check_guild's voice sweep already covered
        # everyone in voice this same tick.

    def _spawn_sing(self, member: discord.Member, cfg: dict, today: date) -> None:
        """Fire-and-forget _sing_for so a busy voice client we're waiting on
        can't stall the 15-minute check loop or the voice-state listener."""
        self.bot.loop.create_task(self._sing_for(member, cfg, today))

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

    async def _sing_for(self, member: discord.Member, cfg: dict, today: date) -> None:
        """Sing once per member per local day, recording it only if it played
        (so a skip while music is busy still lets a later re-join get the song)."""
        key = (member.guild.id, member.id, today.isoformat())
        if key in self._vc_sung:
            return
        if await self._maybe_sing(member, cfg):
            self._vc_sung.add(key)

    # How long to wait for a busy same-channel voice client to free up before
    # giving up on the song entirely, and how often to check.
    _QUEUE_WAIT_TIMEOUT = 180
    _QUEUE_POLL_INTERVAL = 3

    async def _wait_for_idle(self, vc: discord.VoiceClient) -> bool:
        """Poll a voice client until it's idle, disconnects, or we time out.
        Returns True if it's idle and still connected, False otherwise."""
        elapsed = 0.0
        while elapsed < self._QUEUE_WAIT_TIMEOUT:
            if not vc.is_connected():
                return False
            if not (vc.is_playing() or vc.is_paused()):
                return True
            await asyncio.sleep(self._QUEUE_POLL_INTERVAL)
            elapsed += self._QUEUE_POLL_INTERVAL
        return False

    async def _maybe_sing(self, member: discord.Member, cfg: dict) -> bool:
        """Join the member's voice channel (or reuse an existing connection
        already in that channel, queuing behind whatever it's playing), play
        the song once, then leave if we were the one who joined. Returns True
        only if the song actually started playing."""
        voice = member.voice
        if voice is None or voice.channel is None:
            return False  # not in a voice channel — nothing to do
        guild = member.guild
        # Claim the per-guild singing slot BEFORE any await: the busy-wait
        # below yields for up to _QUEUE_WAIT_TIMEOUT seconds, and two members
        # sharing a birthday in the same VC could otherwise both slip past
        # this check and race vc.play(). The loser just retries on the next
        # 15-minute sweep (it never got marked sung).
        if guild.id in self._singing:
            return False  # a sing is already in flight for this guild
        self._singing.add(guild.id)
        channel = voice.channel

        existing = guild.voice_client
        joined_here = existing is None
        vc: discord.VoiceClient | None = existing
        played = False
        try:
            if existing is not None:
                if existing.channel is None or existing.channel.id != channel.id:
                    log.info(
                        "Birthday song skipped in %s: bot connected to a different "
                        "channel than %s",
                        guild.id,
                        member.display_name,
                    )
                    return False
                if existing.is_playing() or existing.is_paused():
                    log.info(
                        "Birthday song queued in %s: voice client busy, waiting up "
                        "to %ss for it to free up",
                        guild.id,
                        self._QUEUE_WAIT_TIMEOUT,
                    )
                    if not await self._wait_for_idle(existing):
                        log.info(
                            "Birthday song skipped in %s: voice client still busy "
                            "(or disconnected) after waiting",
                            guild.id,
                        )
                        return False
                    # Re-check the member is still there and it's still their day —
                    # we may have waited up to _QUEUE_WAIT_TIMEOUT seconds.
                    voice = member.voice
                    if (
                        voice is None
                        or voice.channel is None
                        or voice.channel.id != channel.id
                    ):
                        log.info(
                            "Birthday song skipped in %s: %s left the channel "
                            "while queued",
                            guild.id,
                            member.display_name,
                        )
                        return False
                # Existing connection is idle and in the right channel — reuse it.
            else:
                perms = channel.permissions_for(guild.me)
                if not (perms.connect and perms.speak):
                    log.info(
                        "Birthday song skipped in %s: missing connect/speak perms "
                        "in %s",
                        guild.id,
                        channel.id,
                    )
                    return False

            path = await self._ensure_song(cfg)
            if not path:
                log.warning(
                    "Birthday song skipped in %s: song file unavailable", guild.id
                )
                return False
            if vc is None:
                vc = await channel.connect(self_deaf=True, timeout=20)
            done = asyncio.Event()

            def _after(error: Exception | None) -> None:
                if error:
                    log.warning("Birthday song playback error: %s", error)
                self.bot.loop.call_soon_threadsafe(done.set)

            vc.play(discord.FFmpegPCMAudio(path), after=_after)
            played = True
            # Song is ~13s; cap the wait so a stuck stream can't pin the bot in VC.
            try:
                await asyncio.wait_for(done.wait(), timeout=45)
            except asyncio.TimeoutError:
                log.warning("Birthday song didn't finish in time in %s", guild.id)
        except Exception as exc:
            log.warning("Birthday song failed in %s: %s", guild.id, exc)
        finally:
            # Only disconnect if we're the ones who joined — leave a
            # pre-existing (reused) connection exactly as we found it.
            if joined_here and vc is not None:
                try:
                    await vc.disconnect(force=True)
                except Exception:
                    pass
            self._singing.discard(guild.id)
        return played

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
    async def birthday_channel(self, ctx: commands.Context, channel: SafeTextChannel):
        cfg = await db.get_birthday_config(ctx.guild.id)
        updates = {"enabled": True, "channel_id": str(channel.id)}

        # First-time setup with the default UTC still in place: try to pre-fill a
        # timezone from the guild's voice-channel regions. Best-effort guess only.
        guessed = None
        if cfg["timezone"] == "UTC":
            regions = [
                vc.rtc_region for vc in ctx.guild.voice_channels if vc.rtc_region
            ]
            guessed = guess_timezone_from_regions(regions)
            if guessed:
                updates["timezone"] = guessed

        await db.set_birthday_config(ctx.guild.id, **updates)
        cfg = await db.get_birthday_config(ctx.guild.id)

        lines = [
            f"Birthday announcements are **on** in {channel.mention}.",
            f"Firing daily around **{cfg['hour']:02d}:00 {cfg['timezone']}**.",
        ]
        if guessed:
            lines.append(
                f"_Auto-detected timezone **{guessed}** from your voice region — "
                "change it with `/birthday timezone`._"
            )
        else:
            lines.append(
                "_Set your timezone with `/birthday timezone` (defaults to UTC)._"
            )
        lines.append("Members register with `/birthday set <date>`.")
        await ctx.reply(embed=h.ok("\n".join(lines), "🎂 Birthdays Enabled"))

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
        description="Set the timezone used to decide 'today' (start typing to search).",
    )
    @commands.has_permissions(manage_guild=True)
    @app_commands.describe(
        timezone="Start typing a city or region to search, e.g. 'New York', 'London', 'UTC'."
    )
    async def birthday_timezone(self, ctx: commands.Context, *, timezone: str):
        timezone = timezone.strip()
        if timezone not in available_timezones():
            return await ctx.reply(
                embed=h.err(
                    f"`{timezone}` isn't a valid timezone.\n"
                    "Start typing in the **timezone** option to search — names handle "
                    "daylight saving automatically (Iowa is **America/Chicago**, not a "
                    "fixed offset).\n"
                    "Full list: <https://en.wikipedia.org/wiki/List_of_tz_database_time_zones>"
                ),
                ephemeral=True,
            )
        await db.set_birthday_config(ctx.guild.id, timezone=timezone)
        self._tz_cache.pop(timezone, None)
        await ctx.reply(
            embed=h.ok(f"Timezone set to **{timezone}**.", "🕐 Timezone Set")
        )

    @birthday_timezone.autocomplete("timezone")
    async def _timezone_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """Suggest timezones as the user types.

        Empty query shows the curated common zones (friendly labels); typing
        filters those plus the full IANA database by substring. Capped at 25.
        """
        q = current.strip().lower()
        out: list[app_commands.Choice[str]] = []
        seen: set[str] = set()

        # Curated common zones first — friendly labels for quick picking.
        for label, iana, emoji in _TZ_CHOICES:
            if not q or q in label.lower() or q in iana.lower():
                out.append(
                    app_commands.Choice(name=f"{emoji} {label}"[:100], value=iana)
                )
                seen.add(iana)
                if len(out) >= 25:
                    return out

        # Then any other IANA zone matching the query (so every zone is reachable).
        if q:
            for iana in sorted(available_timezones()):
                if iana in seen:
                    continue
                if q in iana.lower():
                    out.append(app_commands.Choice(name=iana[:100], value=iana))
                    if len(out) >= 25:
                        break
        return out[:25]

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
        gif_url = await self._pick_gif() if cfg["gif_enabled"] else None
        embed = self._build_embed([(member, bd)], cfg, today, gif_url)
        await ctx.reply(
            content="**Preview** (this is what the announcement looks like):",
            embed=embed,
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Birthday(bot))
