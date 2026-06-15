"""
███╗   ██╗ █████╗ ███╗   ██╗ ██████╗ ██████╗  ██████╗ ████████╗
████╗  ██║██╔══██╗████╗  ██║██╔═══██╗██╔══██╗██╔═══██╗╚══██╔══╝
██╔██╗ ██║███████║██╔██╗ ██║██║   ██║██████╔╝██║   ██║   ██║
██║╚██╗██║██╔══██║██║╚██╗██║██║   ██║██╔══██╗██║   ██║   ██║
██║ ╚████║██║  ██║██║ ╚████║╚██████╔╝██████╔╝╚██████╔╝   ██║
╚═╝  ╚═══╝╚═╝  ╚═╝╚═╝  ╚═══╝ ╚═════╝ ╚═════╝  ╚═════╝    ╚═╝

Small. Fast. Built for Mobile Mods.
A lightweight Discord moderation bot — SQLite-backed, zero cloud dependency.
"""

import asyncio
import logging
import logging.handlers
import os
import random
import time
import traceback
import warnings
from collections import OrderedDict

import discord
from discord import app_commands
from discord.ext import commands, tasks

from utils import cache_db
from utils import config as cfg_mod
from utils import db
from utils import db_crypto
from utils import helpers as h
from utils import obs
from utils import sqlite_timing
from utils.health import health_routes, HEALTH_OWNER
from utils.webserver import HttpServer

# ── Config (read once at module level so logging init can use it) ──────────────
_CFG = cfg_mod.load()


# ── Logging ────────────────────────────────────────────────────────────────────
_VALID_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}

# Cap on the last-sender LRU (channel_id → Member). Bounds memory on bots that
# see traffic across very many channels over a long uptime.
_MAX_LAST_SENDERS = 10_000


def _setup_logging(cfg: dict) -> logging.Logger:
    os.makedirs("logs", exist_ok=True)

    level_str = str(cfg.get("log_level", "INFO")).upper()
    if level_str not in _VALID_LEVELS:
        level_str = "INFO"
    level = getattr(logging, level_str)

    # ── Shared formatter ──────────────────────────────────────────────────────
    # %(cid)s is the correlation id injected by obs.CorrelationFilter; it reads
    # "-" when no command is in flight.
    fmt = logging.Formatter(
        fmt="%(asctime)s [%(levelname)-8s] [%(cid)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    correlation_filter = obs.CorrelationFilter()

    # ── File handler (rotating) ───────────────────────────────────────────────
    # ~50 KB per file ≈ 500 lines. Small enough for quick n!logs reads,
    # 5 backups = ~300 KB total history across nanobot.log + .1 through .5.
    file_handler = logging.handlers.RotatingFileHandler(
        filename="logs/nanobot.log",
        maxBytes=50 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(fmt)
    file_handler.addFilter(correlation_filter)

    # ── Console handler (with colour if supported) ────────────────────────────
    # Build it ourselves instead of calling discord.utils.setup_logging, which
    # can stack duplicate handlers on restart and doesn't let us share the
    # formatter with the file handler.
    console_handler = logging.StreamHandler()
    if discord.utils.stream_supports_colour(console_handler.stream):
        console_handler.setFormatter(discord.utils._ColourFormatter())
    else:
        console_handler.setFormatter(fmt)
    console_handler.addFilter(correlation_filter)

    # ── Wire up the root logger ───────────────────────────────────────────────
    root = logging.getLogger()
    # Prevent handler stacking if this somehow runs twice
    root.handlers.clear()
    root.setLevel(level)
    root.addHandler(file_handler)
    root.addHandler(console_handler)

    http_level = logging.DEBUG if cfg.get("log_http") else logging.WARNING
    logging.getLogger("discord.http").setLevel(http_level)
    if cfg.get("log_http"):
        logging.getLogger("NanoBot").warning(
            "log_http is ON: discord.http DEBUG logs include the Authorization "
            "header (your bot token) and will be written to logs/nanobot.log "
            "(readable via the owner !logs command). Use only for short-lived "
            "local debugging — never leave it on in production."
        )

    # ── Structured JSONL event sink ───────────────────────────────────────────
    obs.setup_events_logger(bool(cfg.get("log_events_jsonl", True)))

    # ── Slow-query logging threshold ──────────────────────────────────────────
    sqlite_timing.set_threshold(cfg.get("db_slow_query_ms", 0))

    return logging.getLogger("NanoBot")


log = _setup_logging(_CFG)


# ── Prefix Resolution ──────────────────────────────────────────────────────────
def get_prefix(bot: "NanoBot", message: discord.Message):
    """Per-guild prefix + mention support."""
    if not message.guild:
        return commands.when_mentioned_or(bot.default_prefix)(bot, message)
    prefix = bot.prefixes.get(str(message.guild.id), bot.default_prefix)
    return commands.when_mentioned_or(prefix)(bot, message)


# ── All cogs — single source of truth shared with admin.py ────────────────────
# Keep this in sync with _ALL_COGS in cogs/admin/constants.py.
_ALL_COGS = (
    "cogs.moderation",
    "cogs.tags",
    "cogs.utility",
    "cogs.reminders",
    "cogs.recurring",
    "cogs.warnings",
    "cogs.welcome",
    "cogs.admin",
    "cogs.votes",
    "cogs.auditlog",
    "cogs.automod",
    "cogs.roles",
    "cogs.fun",
    "cogs.images",
    "cogs.eli5",
    "cogs.music",
    "cogs.leveling",
    "cogs.economy",
    "cogs.gatekeeper",
    "cogs.liverole",
    "cogs.debug",
)


# ── Slash command error response helper ───────────────────────────────────────
async def _slash_error_response(
    interaction: discord.Interaction,
    embed: discord.Embed,
) -> None:
    """
    Send an ephemeral error embed via a slash interaction.
    Handles both unresponded and already-deferred interactions safely.
    """
    try:
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)
    except discord.HTTPException:
        pass


# ── Bot ────────────────────────────────────────────────────────────────────────
class ObsTree(app_commands.CommandTree):
    """
    CommandTree that stamps a correlation id + start time on every app-command
    invocation. _call() is the single method discord.py routes every slash /
    context-menu invocation through, and the command callback runs inside it (in
    this task) — so setting the contextvar here propagates the id to the command
    body and any log lines it emits, which a listener (separate task) cannot do.

    NOTE: _call is private discord.py API. It has been stable across 2.x; the
    wrapper is kept thin and the startup cog/tree load exercises it, so a
    signature change would fail loudly rather than silently.
    """

    async def _call(self, interaction: discord.Interaction):
        cid = obs.new_cid()
        interaction.extras["_nb_cid"] = cid
        interaction.extras["_nb_t0"] = time.perf_counter()
        token = obs.set_cid(cid)
        try:
            cmd = (
                interaction.command.qualified_name
                if interaction.command
                else (interaction.data or {}).get("name", "?")
            )
            obs.log_event(
                "slash.start",
                cmd=cmd,
                user=interaction.user.id if interaction.user else None,
                guild=interaction.guild_id,
                channel=interaction.channel_id,
            )
            return await super()._call(interaction)
        finally:
            obs.reset_cid(token)


class NanoBot(commands.Bot):
    def __init__(self, cfg: dict):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        # Privileged intent: powers every presence-aware feature — the /user info
        # card status/activity, the /server online breakdown, and the liverole cog
        # (streaming auto-role + go-live notifications, both driven by
        # PRESENCE_UPDATE). Without it member.status is always "offline" and no
        # presence events fire. Must also be toggled on in the Discord Developer
        # Portal → Bot → Presence Intent.
        intents.presences = True

        super().__init__(
            command_prefix=get_prefix,
            intents=intents,
            help_command=None,
            description="NanoBot — Small. Fast. Built for Mobile Mods.",
            tree_cls=ObsTree,
            # Default-deny @everyone/@here and role pings on everything the bot
            # sends. Stops stored or echoed user content (tags, /echo, …) from
            # firing mass pings. Individual user mentions still resolve, and any
            # send that legitimately needs broader pings can pass its own
            # allowed_mentions to opt back in.
            allowed_mentions=discord.AllowedMentions(everyone=False, roles=False),
        )

        # Route app command failures (including transformer errors) to our
        # custom handler instead of discord.py's default "Ignoring exception"
        # log spam.
        self.tree.on_error = self.on_tree_error

        self.config: dict = dict(cfg)
        self._apply_config(cfg)
        self.prefixes: dict[str, str] = {}
        # channel_id → last non-bot author. Bounded LRU so a long-lived bot
        # across many channels can't leak Member references without limit.
        self.last_senders: OrderedDict[int, discord.Member] = OrderedDict()
        self.last_banned: dict[int, int] = {}  # guild_id → last banned user_id
        self.start_time = discord.utils.utcnow()
        self.commands_ran: int = 0  # incremented in on_command; resets on restart
        # Strong refs to fire-and-forget tasks so they aren't GC'd mid-flight.
        self._bg_tasks: set[asyncio.Task] = set()
        # Presence state. `_music_activity` is set by the music cog while a track
        # plays; when None the idle presence (manual override or auto-rotating
        # "Listening to /help | /<command>") is shown instead.
        self._music_activity: discord.BaseActivity | None = None
        # Guard so the startup config dump prints once, not on every reconnect.
        self._config_printed: bool = False
        # Shared HTTP server: the health probe and the vote webhook register
        # their routes here, sharing a port when only one is available.
        self.web: HttpServer = HttpServer()

    def _spawn_bg(self, coro, *, timeout: float | None = None) -> None:
        """Fire-and-forget a coroutine, keeping a strong ref so it isn't GC'd.

        Pass ``timeout`` (seconds) for short, self-contained work — a hung task
        is then cancelled by asyncio.wait_for instead of lingering in
        ``_bg_tasks`` forever. Leave it ``None`` (default) for genuinely
        long-running coroutines (e.g. the daily scrape) that must not be capped.
        """
        run = asyncio.wait_for(coro, timeout) if timeout is not None else coro
        try:
            task = asyncio.create_task(run)
        except RuntimeError:
            # No running event loop (called during/after shutdown). Close the
            # coroutine(s) explicitly so Python doesn't warn about them being
            # unawaited. If we wrapped in wait_for, close the inner coro too.
            run.close()
            if run is not coro:
                coro.close()
            return
        self._bg_tasks.add(task)
        task.add_done_callback(self._on_bg_done)

    def _on_bg_done(self, task: asyncio.Task) -> None:
        self._bg_tasks.discard(task)
        if task.cancelled():
            return
        exc = task.exception()
        if isinstance(exc, asyncio.TimeoutError):
            log.warning("Background task exceeded its timeout and was cancelled")
        elif exc is not None:
            log.error("Background task failed: %s", exc, exc_info=exc)

    async def close(self) -> None:
        # Restore the warning hook before the event loop begins tearing down so
        # late RuntimeWarnings (e.g. "coroutine '_run_event' was never awaited")
        # don't trigger _spawn_bg on a closing loop and cascade into more warnings.
        orig = getattr(self, "_orig_showwarning", None)
        if orig is not None:
            warnings.showwarning = orig
        # Tear down the shared HTTP server (if running) so its port is freed.
        try:
            await self.web.stop()
        except Exception as exc:
            log.debug("HTTP server shutdown error: %s", exc)
        # Cancel fire-and-forget background tasks on shutdown, then give them a
        # brief window to unwind so an in-flight task isn't torn off mid-write.
        # (Cog-owned tasks are cancelled by cog_unload on reload; on full
        # shutdown they stop with the event loop.)
        pending = list(self._bg_tasks)
        for task in pending:
            task.cancel()
        if pending:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*pending, return_exceptions=True), timeout=5
                )
            except asyncio.TimeoutError:
                log.warning(
                    "%d background task(s) didn't finish within shutdown grace period",
                    sum(not t.done() for t in pending),
                )
        await super().close()

    # ── Presence ──────────────────────────────────────────────────────────────
    # Single source of truth for the bot's activity. The music cog calls
    # set_music_activity() while a track plays; otherwise the idle presence is
    # shown — a manual override (!status) if set, else an auto-rotating
    # "Listening to /help | /<command>" that reshuffles hourly.
    def set_music_activity(self, activity: discord.BaseActivity | None) -> None:
        """Called by the music cog. None reverts to the idle presence."""
        self._music_activity = activity
        self._spawn_bg(self.apply_presence(), timeout=30)

    def _idle_activity(self) -> discord.Activity:
        if self.manual_status:
            name = self.manual_status
        else:
            cmd = self._random_command_name()
            name = f"/help | /{cmd}" if cmd else "/help"
        return discord.Activity(type=discord.ActivityType.listening, name=name[:128])

    def _random_command_name(self) -> str | None:
        """A random slash-command name (incl. subcommands) for the idle status."""
        try:
            names = [
                c.qualified_name
                for c in self.tree.walk_commands()
                if isinstance(c, app_commands.Command) and c.qualified_name != "help"
            ]
        except Exception:
            names = []
        return random.choice(names) if names else None

    async def apply_presence(self) -> None:
        """Push the current activity (music override > idle) to Discord."""
        activity = self._music_activity or self._idle_activity()
        try:
            await self.change_presence(activity=activity)
        except Exception as exc:
            log.debug("change_presence failed: %s", exc)

    @tasks.loop(hours=1)
    async def _presence_loop(self) -> None:
        # Only the idle presence rotates; while music plays apply_presence keeps
        # the override, so this is a no-op refresh until playback stops.
        await self.apply_presence()

    @_presence_loop.before_loop
    async def _before_presence_loop(self) -> None:
        await self.wait_until_ready()

    # ── Config application / reload ───────────────────────────────────────────
    def _apply_config(self, cfg: dict) -> None:
        """Copy the flat config dict onto the bot's runtime attributes."""
        self.default_prefix: str = cfg.get("default_prefix") or "!"
        self.groq_api_key: str | None = cfg.get("groq_api_key")
        raw_owner = cfg.get("owner_id")
        self.config_owner_id: int | None = (
            int(raw_owner) if raw_owner not in (None, "") else None
        )
        # Manual idle presence override (set via !status, persisted to config.ini).
        self.manual_status: str | None = (
            cfg.get("idle_status_message") or ""
        ).strip() or None

    def reload_config(self) -> dict:
        """
        Re-read config.ini from disk and refresh runtime attributes + log level.
        Returns the new flat config dict. Cogs can read ``bot.config`` for any
        key they care about.
        """
        new_cfg = cfg_mod.load()
        self.config = dict(new_cfg)
        self._apply_config(new_cfg)

        level_str = str(new_cfg.get("log_level") or "INFO").upper()
        if level_str in _VALID_LEVELS:
            logging.getLogger().setLevel(getattr(logging, level_str))
        http_level = logging.DEBUG if new_cfg.get("log_http") else logging.WARNING
        logging.getLogger("discord.http").setLevel(http_level)
        obs.setup_events_logger(bool(new_cfg.get("log_events_jsonl", True)))
        sqlite_timing.set_threshold(new_cfg.get("db_slow_query_ms", 0))

        self.dispatch("config_reloaded", self.config)
        return new_cfg

    # ── Startup ────────────────────────────────────────────────────────────────
    async def setup_hook(self):
        os.makedirs("data", exist_ok=True)
        db_key = db_crypto.resolve_key(self.config)
        if db_key:
            log.info("🔐 Database encryption at rest: enabled (SQLCipher)")
        await db.init(db_key)
        await cache_db.init(db_key)
        await self._load_prefixes()

        for cog in _ALL_COGS:
            try:
                await self.load_extension(cog)
                log.info(f"✅ Loaded {cog}")
            except Exception as exc:
                # Log but don't abort — optional cogs (eli5) may be absent
                log.warning(f"⚠️  Could not load {cog}: {exc}")

        await self._sync_commands_if_changed()
        await self._start_web_server()

    async def _start_web_server(self) -> None:
        """Register the /health route (if enabled) and bind the shared server.

        Cogs (e.g. votes) may already have registered their own routes during
        load; restart() binds everything — same-port registrations share one
        listener, so /health and /webhook/* can ride a single allocation.
        """
        try:
            port = int(self.config.get("health_check_port") or 0)
        except (TypeError, ValueError):
            port = 0
        if port > 0:
            host = str(self.config.get("health_check_host") or "0.0.0.0")
            self.web.register(HEALTH_OWNER, host, port, health_routes(self))
        await self.web.restart()

    async def _sync_commands_if_changed(self) -> None:
        """Sync the slash command tree only when it actually changed.

        A global ``tree.sync()`` on every boot is a Discord rate-limit footgun,
        and the bot can restart often (!restart / !upgrade). We hash the command
        signatures and skip the network sync when nothing changed. ``!sync``
        always forces a real sync regardless.
        """
        try:
            fingerprint = self._command_fingerprint()
        except Exception:
            log.warning("Couldn't fingerprint command tree — syncing unconditionally")
            fingerprint = None

        hash_path = os.path.join("data", ".slash_sync_hash")
        prev = None
        if fingerprint is not None:
            try:
                with open(hash_path, encoding="utf-8") as f:
                    prev = f.read().strip()
            except OSError:
                prev = None

        if fingerprint is not None and fingerprint == prev:
            log.info("⚡ Slash commands unchanged — skipping sync")
            return

        synced = await self.tree.sync()
        log.info(f"⚡ Synced {len(synced)} slash command(s)")
        if fingerprint is not None:
            try:
                with open(hash_path, "w", encoding="utf-8") as f:
                    f.write(fingerprint)
            except OSError as exc:
                log.warning(f"Couldn't persist slash-sync hash: {exc}")

    def _command_fingerprint(self) -> str:
        """Stable hash of every app command's name, description and parameters."""
        import hashlib

        parts: list[str] = []
        for cmd in self.tree.walk_commands():
            params = getattr(cmd, "parameters", []) or []
            sig = ",".join(
                f"{p.name}:{getattr(p.type, 'name', p.type)}:{p.required}"
                for p in params
            )
            parts.append(
                f"{cmd.qualified_name}|{getattr(cmd, 'description', '')}|{sig}"
            )
        blob = "\n".join(sorted(parts))
        return hashlib.sha256(blob.encode()).hexdigest()

    # ── Owner resolution ───────────────────────────────────────────────────────
    async def is_owner(self, user: discord.User) -> bool:
        """config.ini owner_id takes priority; falls back to application owner."""
        if self.config_owner_id is not None:
            return user.id == self.config_owner_id
        return await super().is_owner(user)

    # ── Prefix persistence ─────────────────────────────────────────────────────
    async def _load_prefixes(self):
        self.prefixes = await db.get_all_prefixes()

    async def save_prefix(self, guild_id: int, prefix: str):
        self.prefixes[str(guild_id)] = prefix
        await db.set_prefix(guild_id, prefix)

    # ── Error channel ──────────────────────────────────────────────────────────
    def _scrub_secrets(self, text: str) -> str:
        """Redact any configured secret value that appears in error text.

        Tracebacks posted to the error channel can include local variables or
        repr'd config, so strip the actual secret values (token, API keys,
        webhook secret) before they leave the host. Longest-first so a secret
        that contains another isn't partially missed.
        """
        secrets = [
            str(self.config[k]) for k in cfg_mod.SENSITIVE_KEYS if self.config.get(k)
        ]
        for secret in sorted(secrets, key=len, reverse=True):
            if len(secret) >= 6:  # skip trivially short values to avoid noise
                text = text.replace(secret, "***REDACTED***")
        return text

    async def _post_error(self, title: str, body: str) -> None:
        """Send a warning/error embed to the configured error channel."""
        raw = self.config.get("error_channel_id")
        if not raw:
            return
        try:
            ch = self.get_channel(int(raw))
            if ch is None:
                return
            body = self._scrub_secrets(body)
            embed = discord.Embed(
                title=title,
                description=f"```\n{body[:3900]}\n```",
                color=0xED4245,
                timestamp=discord.utils.utcnow(),
            )
            embed.set_footer(text="NanoBot error log")
            await ch.send(embed=embed)
        except Exception as exc:
            # Don't let the error-reporting path raise (it'd risk a feedback
            # loop via the asyncio exception handler). Trace at debug so a
            # broken error channel is still diagnosable from the logs.
            log.debug("_post_error failed to deliver to error channel: %s", exc)

    def _install_error_hooks(self) -> None:
        """Hook Python warnings and asyncio unhandled exceptions into _post_error."""
        bot_ref = self
        # Store original so close() can restore it before the loop tears down.
        self._orig_showwarning = warnings.showwarning
        orig_showwarning = self._orig_showwarning

        def _warn_hook(message, category, filename, lineno, file=None, line=None):
            orig_showwarning(message, category, filename, lineno, file, line)
            if bot_ref.is_closed():
                return
            text = warnings.formatwarning(message, category, filename, lineno, line)
            bot_ref._spawn_bg(
                bot_ref._post_error(f"⚠️ {category.__name__}", text), timeout=30
            )

        warnings.showwarning = _warn_hook

        def _asyncio_exc_handler(loop, context):
            msg = context.get("message", "Unhandled exception in event loop")
            exc = context.get("exception")
            if exc:
                tb = "".join(
                    traceback.format_exception(type(exc), exc, exc.__traceback__)
                )
                body = f"{msg}\n\n{tb}"
            else:
                body = msg
            log.error("asyncio unhandled: %s", msg, exc_info=exc)
            if not bot_ref.is_closed():
                bot_ref._spawn_bg(
                    bot_ref._post_error("🔴 asyncio exception", body), timeout=30
                )

        self.loop.set_exception_handler(_asyncio_exc_handler)

    # ── Events ─────────────────────────────────────────────────────────────────
    async def on_ready(self):
        log.info(f"🤖 Online as {self.user} (ID: {self.user.id})")
        log.info(f"📡 Connected to {len(self.guilds)} server(s)")
        log.info(
            f"🔑 Owner: {'config override' if self.config_owner_id else 'application owner'}"
        )
        await self.apply_presence()
        if not self._presence_loop.is_running():
            self._presence_loop.start()
        self.dispatch("restore_schedules")
        self._install_error_hooks()
        # Dump the active config LAST so it isn't buried under cog-load and
        # gateway chatter. Once only — on_ready can re-fire on reconnect.
        # Printed to stdout (not the logger) and UNMASKED: it's for the local
        # terminal, where only the host operator can see it. Routing it through
        # the logger would copy secrets into the rotating logs/nanobot.log file
        # (and expose them via the owner `!logs` command), so we don't.
        if not self._config_printed:
            self._config_printed = True
            print(
                "\n⚙️ Active config (config.ini):\n"
                + cfg_mod.summary(self.config, mask=False)
                + "\n",
                flush=True,
            )

    async def on_command(self, ctx: commands.Context):
        self.commands_ran += 1
        # Reuse the id/start: stamped in on_message for prefix invocations, or in
        # on_interaction for hybrid commands invoked via slash (which skip
        # on_message). Mint a fresh one only if neither ran.
        inter = ctx.interaction
        cid = (
            getattr(ctx, "_nb_cid", None)
            or (inter.extras.get("_nb_cid") if inter else None)
            or obs.new_cid()
        )
        ctx._nb_cid = cid
        if not hasattr(ctx, "_nb_t0"):
            ctx._nb_t0 = (
                inter.extras.get("_nb_t0")
                if inter and inter.extras.get("_nb_t0")
                else time.perf_counter()
            )
        if inter is not None:
            # This hybrid ran through the ext.commands lifecycle, so its success
            # is logged via on_command_completion — tell on_app_command_completion
            # not to double-log it.
            inter.extras["_nb_skip_app_completion"] = True
        obs.set_cid(cid)  # listener runs in its own task; no reset needed

        guild_info = f"{ctx.guild.name} ({ctx.guild.id})" if ctx.guild else "DM"
        log.info(
            f"CMD  {ctx.command}  |  "
            f"{h.user_log(ctx.author)}  |  "
            f"#{ctx.channel}  |  "
            f"{guild_info}"
        )
        obs.log_event(
            "command.start",
            cmd=str(ctx.command),
            user=ctx.author.id,
            guild=ctx.guild.id if ctx.guild else None,
            channel=getattr(ctx.channel, "id", None),
            via="slash" if ctx.interaction else "prefix",
        )

    async def on_command_completion(self, ctx: commands.Context):
        cid = getattr(ctx, "_nb_cid", "-")
        t0 = getattr(ctx, "_nb_t0", None)
        dur_ms = round((time.perf_counter() - t0) * 1000) if t0 is not None else None
        obs.set_cid(cid)
        if dur_ms is not None:
            log.info("DONE %s  |  %dms", ctx.command, dur_ms)
        obs.log_event(
            "command.ok",
            cmd=str(ctx.command),
            dur_ms=dur_ms,
            user=ctx.author.id,
            guild=ctx.guild.id if ctx.guild else None,
        )

    async def on_app_command_completion(
        self,
        interaction: discord.Interaction,
        command,
    ):
        # Hybrid commands invoked via slash also fire on_command_completion, so
        # skip them here to avoid double-logging their success.
        if interaction.extras.get("_nb_skip_app_completion"):
            return
        cid = interaction.extras.get("_nb_cid", "-")
        t0 = interaction.extras.get("_nb_t0")
        dur_ms = round((time.perf_counter() - t0) * 1000) if t0 else None
        obs.set_cid(cid)
        name = getattr(command, "qualified_name", str(command))
        if dur_ms is not None:
            log.info("DONE /%s  |  %dms", name, dur_ms)
        obs.log_event(
            "slash.ok",
            cmd=name,
            dur_ms=dur_ms,
            user=interaction.user.id if interaction.user else None,
            guild=interaction.guild_id,
        )

    async def on_guild_join(self, guild: discord.Guild):
        log.info(
            f"➕ Joined server: {guild.name} ({guild.id})  |  "
            f"{guild.member_count} members  |  "
            f"Owner: {guild.owner} ({guild.owner_id})"
        )

    async def on_guild_remove(self, guild: discord.Guild):
        log.info(f"➖ Left server: {guild.name} ({guild.id})")
        # Drop per-guild state so leaving servers doesn't accumulate forever.
        self.last_banned.pop(guild.id, None)
        channel_ids = {ch.id for ch in guild.channels}
        for cid in [c for c in self.last_senders if c in channel_ids]:
            self.last_senders.pop(cid, None)

    async def on_message(self, message: discord.Message):
        # During shutdown (!restart/!upgrade/reconnect) discord.py's close()
        # sets self.loop = MISSING. A message still in flight would then crash
        # inside invoke()'s event dispatch (loop.create_task on a gone loop).
        if self.is_closed():
            return

        try:
            if message.author.bot:
                return

            if message.guild:
                self.last_senders[message.channel.id] = message.author
                self.last_senders.move_to_end(message.channel.id)
                while len(self.last_senders) > _MAX_LAST_SENDERS:
                    self.last_senders.popitem(last=False)

            ctx = await self.get_context(message)

            if ctx.valid:
                # Stamp the correlation id here, in the same task that runs the
                # command body, so log lines emitted inside cogs inherit it.
                ctx._nb_cid = obs.new_cid()
                ctx._nb_t0 = time.perf_counter()
                token = obs.set_cid(ctx._nb_cid)
                try:
                    await self.invoke(ctx)
                finally:
                    obs.reset_cid(token)
                return

            if message.guild and ctx.prefix is not None:
                after = message.content[len(ctx.prefix) :].strip()
                if after:
                    await _try_tag_shortcut(message, self, after.lower())
        except Exception as exc:
            # The bot closing mid-dispatch is an expected shutdown race, not a bug.
            if self.is_closed():
                log.debug("Ignoring on_message error during shutdown: %s", exc)
                return
            log.error(
                "Unhandled error in on_message for guild=%s channel=%s author=%s: %s",
                message.guild.id if message.guild else "DM",
                getattr(message.channel, "id", "unknown"),
                message.author.id,
                exc,
                exc_info=exc,
            )

    # ── Slash command error handler ────────────────────────────────────────────
    async def on_tree_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        """
        Catches errors from pure slash commands and slash-invoked hybrid commands.
        on_command_error does NOT fire for these — this handler is required.
        """
        cmd_name = interaction.command.name if interaction.command else "that command"

        obs.set_cid(interaction.extras.get("_nb_cid", "-"))
        t0 = interaction.extras.get("_nb_t0")
        dur_ms = round((time.perf_counter() - t0) * 1000) if t0 else None
        obs.log_event(
            "slash.err",
            cmd=cmd_name,
            error=type(error).__name__,
            dur_ms=dur_ms,
            user=interaction.user.id if interaction.user else None,
            guild=interaction.guild_id,
        )

        if isinstance(error, app_commands.TransformerError):
            hint = ""
            if error.type == discord.AppCommandOptionType.channel:
                hint = "\nMake sure to select the channel from the picker rather than typing the name."
            log.debug(
                f"TransformerError in /{cmd_name}: {error} "
                f"(value={error.value!r}, type={error.type})"
            )
            e = discord.Embed(
                description=f"❌ Couldn't resolve that argument: `{error.value}`{hint}",
                color=0xED4245,
            )
            e.set_footer(text="NanoBot")
            return await _slash_error_response(interaction, e)

        if isinstance(error, app_commands.MissingPermissions):
            missing = ", ".join(
                p.replace("_", " ").title() for p in error.missing_permissions
            )
            e = discord.Embed(
                description=(
                    f"**{interaction.user.display_name}**, you don't have the permissions "
                    f"needed to use `{cmd_name}`.\nRequired: **{missing}**"
                ),
                color=0xED4245,
            )
            e.set_footer(text="NanoBot")
            return await _slash_error_response(interaction, e)

        if isinstance(error, app_commands.BotMissingPermissions):
            missing = ", ".join(
                p.replace("_", " ").title() for p in error.missing_permissions
            )
            e = discord.Embed(
                description=(
                    f"I'm missing permissions to run `{cmd_name}`.\n"
                    f"Please grant me: **{missing}**"
                ),
                color=0xED4245,
            )
            e.set_footer(text="NanoBot")
            return await _slash_error_response(interaction, e)

        if isinstance(error, app_commands.CommandOnCooldown):
            secs = round(error.retry_after)
            unit = "second" if secs == 1 else "seconds"
            e = discord.Embed(
                description=f"⏱️ Slow down! Try again in **{secs} {unit}**.",
                color=0xFEE75C,
            )
            e.set_footer(text="NanoBot")
            return await _slash_error_response(interaction, e)

        if isinstance(error, app_commands.CommandInvokeError):
            log.error(
                f"Unhandled slash error in /{cmd_name}: {error.original}",
                exc_info=error.original,
            )
            e = discord.Embed(
                description="Something went wrong running that command. Check `!logs` for details.",
                color=0xED4245,
            )
            e.set_footer(text="NanoBot")
            return await _slash_error_response(interaction, e)

        if isinstance(error, app_commands.CheckFailure):
            # A custom check (not the permission ones handled above) blocked
            # this slash command. Reply without logging a stack trace.
            e = discord.Embed(
                description="⛔ You can't use that command here.",
                color=0xED4245,
            )
            e.set_footer(text="NanoBot")
            return await _slash_error_response(interaction, e)

        # Catch-all for anything else
        log.error(f"Unhandled tree error in /{cmd_name}: {error}", exc_info=error)

    # ── Prefix/hybrid command error handler ────────────────────────────────────
    async def on_command_error(self, ctx: commands.Context, error):
        obs.set_cid(getattr(ctx, "_nb_cid", "-"))
        t0 = getattr(ctx, "_nb_t0", None)
        dur_ms = round((time.perf_counter() - t0) * 1000) if t0 is not None else None

        # Unwrap both CommandInvokeError and HybridCommandError — both carry
        # the real exception in .original but are different classes.
        if isinstance(
            error, (commands.CommandInvokeError, commands.HybridCommandError)
        ):
            error = error.original

        obs.log_event(
            "command.err",
            cmd=str(ctx.command),
            error=type(error).__name__,
            dur_ms=dur_ms,
            user=ctx.author.id,
            guild=ctx.guild.id if ctx.guild else None,
        )

        if isinstance(error, commands.MissingPermissions):
            cmd_name = ctx.command.name if ctx.command else "that command"
            missing = ", ".join(
                p.replace("_", " ").title() for p in error.missing_permissions
            )
            e = discord.Embed(
                description=(
                    f"**{ctx.author.display_name}**, you don't have the permissions needed "
                    f"to use `{cmd_name}`.\nRequired: **{missing}**"
                ),
                color=0xED4245,
            )
            e.set_footer(text="NanoBot")
            return await ctx.reply(embed=e, ephemeral=True)

        if isinstance(error, commands.BotMissingPermissions):
            cmd_name = ctx.command.name if ctx.command else "that"
            missing = ", ".join(
                p.replace("_", " ").title() for p in error.missing_permissions
            )
            e = discord.Embed(
                description=(
                    f"I'm missing permissions to run `{cmd_name}`.\n"
                    f"Please grant me: **{missing}**"
                ),
                color=0xED4245,
            )
            e.set_footer(text="NanoBot")
            return await ctx.reply(embed=e, ephemeral=True)

        if isinstance(error, commands.MissingRequiredArgument):
            cmd_name = ctx.command.name if ctx.command else "this command"
            e = discord.Embed(
                description=(
                    f"Missing argument: `{error.param.name}`\n"
                    f"Use `{ctx.prefix}help {cmd_name}` to see usage."
                ),
                color=0xFEE75C,
            )
            e.set_footer(text="NanoBot")
            return await ctx.reply(embed=e, ephemeral=True)

        if isinstance(error, (commands.BadArgument, app_commands.TransformerError)):
            hint = ""
            if isinstance(error, app_commands.TransformerError):
                if error.type == discord.AppCommandOptionType.channel:
                    hint = "\nMake sure to select the channel from the picker rather than typing the name."
                log.debug(
                    f"TransformerError in {ctx.command}: {error} "
                    f"(value={error.value!r}, type={error.type})"
                )
            e = discord.Embed(
                description=f"Invalid argument: {error}{hint}",
                color=0xFEE75C,
            )
            e.set_footer(text="NanoBot")
            return await ctx.reply(embed=e, ephemeral=True)

        if isinstance(error, commands.NotOwner):
            e = discord.Embed(
                description="⛔ That command is restricted to the **bot owner** only.",
                color=0xED4245,
            )
            e.set_footer(text="NanoBot")
            return await ctx.reply(embed=e, ephemeral=True)

        if isinstance(error, commands.CommandOnCooldown):
            secs = round(error.retry_after)
            unit = "second" if secs == 1 else "seconds"
            e = discord.Embed(
                description=f"⏱️ Slow down! Try again in **{secs} {unit}**.",
                color=0xFEE75C,
            )
            e.set_footer(text="NanoBot")
            return await ctx.reply(embed=e, ephemeral=True)

        if isinstance(error, commands.CommandNotFound):
            return

        if isinstance(error, commands.CheckFailure):
            # A custom check (not the permission/owner ones handled above)
            # blocked this. Tell the user without dumping a stack trace.
            e = discord.Embed(
                description="⛔ You can't use that command here.",
                color=0xED4245,
            )
            e.set_footer(text="NanoBot")
            return await ctx.reply(embed=e, ephemeral=True)

        log.error(f"Unhandled error in {ctx.command}: {error}", exc_info=error)


# ── Tag shortcut lookup ────────────────────────────────────────────────────────
async def _try_tag_shortcut(
    message: discord.Message,
    bot: commands.Bot,
    name: str,
) -> bool:
    try:
        tag = await db.get_tag(message.guild.id, name, message.author.id)
        if tag is None:
            return False

        text = tag.get("content") or ""
        img_url = tag.get("image_url")

        if len(text) > 1500:
            header = f"📌 **[{message.guild.name}]  {name}**"
            img_suffix = f"\n{img_url}" if img_url else ""
            await message.reply(f"{header}\n\n{text}{img_suffix}")
        else:
            e = discord.Embed(
                title=f"📌 [{message.guild.name}]  {name}",
                description=text or None,
                color=0x5865F2,
            )
            if img_url:
                e.set_image(url=img_url)
            e.set_footer(text="NanoBot Tags")
            await message.reply(embed=e)
        log.debug(
            f"Tag shortcut fired: '{name}' for {message.author} in {message.guild}"
        )
        return True

    except Exception as exc:
        log.error(f"Tag shortcut error for '{name}': {exc}", exc_info=exc)
        return False


# ── Entry Point ────────────────────────────────────────────────────────────────
async def main():
    cfg = _CFG

    token = os.getenv("DISCORD_TOKEN") or cfg.get("token")
    if not token:
        log.error(
            "❌ No token found. Set DISCORD_TOKEN env var or add it to config.ini"
        )
        return

    # Pass the resolved token so an env-var-only setup (no token key in
    # config.ini) doesn't trigger a false fatal "Missing or placeholder" error.
    cfg_to_validate = {**cfg, "token": token}
    all_issues = cfg_mod.validate(cfg_to_validate)
    for issue in all_issues:
        if issue.fatal:
            log.critical(f"Config error [{issue.field}]: {issue.message}")
        else:
            log.warning(f"Config warning [{issue.field}]: {issue.message}")
    fatal_issues = [i for i in all_issues if i.fatal]
    if fatal_issues:
        log.critical("Aborting — fix config.ini and restart.")
        return

    bot = NanoBot(cfg)
    log.info("🚀 Starting NanoBot...")
    try:
        async with bot:
            await bot.start(token)
    finally:
        await db.close()
        await cache_db.close()


if __name__ == "__main__":
    asyncio.run(main())
