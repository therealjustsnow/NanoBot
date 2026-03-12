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
import json
import logging
import logging.handlers
import os

import discord
from discord.ext import commands

from utils import db

# ── Config (read once at module level so logging init can use it) ──────────────
def _load_config() -> dict:
    cfg = {}
    if os.path.exists("config.json"):
        with open("config.json", encoding="utf-8") as f:
            try:
                cfg = json.load(f)
            except json.JSONDecodeError:
                pass
    return cfg

_CFG = _load_config()


# ── Logging ────────────────────────────────────────────────────────────────────
# discord.utils.setup_logging() is the recommended setup in discord.py 2.x.
# It wires all discord.* sub-loggers (gateway, client, http, shard…) with ANSI
# colour on supported terminals. We bolt a RotatingFileHandler on top for
# persistence.  Both level and HTTP verbosity are configurable via config.json.

_VALID_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}

def _setup_logging(cfg: dict) -> logging.Logger:
    os.makedirs("logs", exist_ok=True)

    # ── Resolve log level from config ─────────────────────────────────────────
    level_str = str(cfg.get("log_level", "INFO")).upper()
    if level_str not in _VALID_LEVELS:
        level_str = "INFO"
    level = getattr(logging, level_str)

    # ── Rotating file handler (plain text, no ANSI) ───────────────────────────
    file_handler = logging.handlers.RotatingFileHandler(
        filename    = "logs/nanobot.log",
        maxBytes    = 5 * 1024 * 1024,   # 5 MB per file
        backupCount = 3,                  # keep 3 backups → ≤ 15 MB total
        encoding    = "utf-8",
    )
    file_handler.setFormatter(logging.Formatter(
        fmt     = "%(asctime)s [%(levelname)-8s] %(name)s: %(message)s",
        datefmt = "%Y-%m-%d %H:%M:%S",
    ))

    # ── discord.utils.setup_logging — console + discord.* tree ────────────────
    discord.utils.setup_logging(level=level, root=True)
    logging.getLogger().addHandler(file_handler)

    # ── discord.http verbosity (very chatty at INFO — default to WARNING) ─────
    http_level = logging.DEBUG if cfg.get("log_http") else logging.WARNING
    logging.getLogger("discord.http").setLevel(http_level)

    return logging.getLogger("NanoBot")

log = _setup_logging(_CFG)


# ── Prefix Resolution ──────────────────────────────────────────────────────────
def get_prefix(bot: "NanoBot", message: discord.Message):
    """Per-guild prefix + mention support."""
    if not message.guild:
        return commands.when_mentioned_or(bot.default_prefix)(bot, message)
    prefix = bot.prefixes.get(str(message.guild.id), bot.default_prefix)
    return commands.when_mentioned_or(prefix)(bot, message)


# ── Bot ────────────────────────────────────────────────────────────────────────
class NanoBot(commands.Bot):
    def __init__(self, cfg: dict):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members          = True

        super().__init__(
            command_prefix=get_prefix,
            intents=intents,
            help_command=None,
            description="NanoBot — Small. Fast. Built for Mobile Mods.",
        )

        self.default_prefix: str        = cfg.get("default_prefix", "!")
        self.prefixes: dict[str, str]   = {}
        self.last_senders: dict[int, discord.Member] = {}
        self.start_time = discord.utils.utcnow()

        # Owner ID: config override takes priority over the application owner
        raw_owner = cfg.get("owner_id")
        self.config_owner_id: int | None = int(raw_owner) if raw_owner else None

    # ── Startup ────────────────────────────────────────────────────────────────
    async def setup_hook(self):
        os.makedirs("data", exist_ok=True)
        await db.init()
        await self._load_prefixes()

        cogs = (
            "cogs.moderation",
            "cogs.tags",
            "cogs.utility",
            "cogs.reminders",
            "cogs.warnings",    # per-server warning system
            "cogs.welcome",     # per-server welcome / leave messages
            "cogs.admin",       # owner-only: reload / shutdown / restart
            "cogs.votes",
            "cogs.auditlog",    # per-server audit log feed
            "cogs.automod",     # per-server auto-moderation rules
        )
        for cog in cogs:
            await self.load_extension(cog)
            log.info(f"✅ Loaded {cog}")

        synced = await self.tree.sync()
        log.info(f"⚡ Synced {len(synced)} slash command(s)")

    # ── Owner resolution ───────────────────────────────────────────────────────
    async def is_owner(self, user: discord.User) -> bool:
        """config.json owner_id takes priority; falls back to application owner."""
        if self.config_owner_id:
            return user.id == self.config_owner_id
        return await super().is_owner(user)

    # ── Prefix persistence ─────────────────────────────────────────────────────
    async def _load_prefixes(self):
        """Load per-guild prefixes from SQLite into the in-memory dict."""
        self.prefixes = await db.get_all_prefixes()

    async def save_prefix(self, guild_id: int, prefix: str):
        """Persist a single guild prefix to SQLite and update in-memory dict."""
        self.prefixes[str(guild_id)] = prefix
        await db.set_prefix(guild_id, prefix)

    # ── Events ─────────────────────────────────────────────────────────────────
    async def on_ready(self):
        log.info(f"🤖 Online as {self.user} (ID: {self.user.id})")
        log.info(f"📡 Connected to {len(self.guilds)} server(s)")
        log.info(f"🔑 Owner: {'config override' if self.config_owner_id else 'application owner'}")
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name="over the server 👁️",
            )
        )
        self.dispatch("restore_schedules")

    async def on_command(self, ctx: commands.Context):
        """Log every successful command invocation."""
        guild_info = f"{ctx.guild.name} ({ctx.guild.id})" if ctx.guild else "DM"
        log.info(
            f"CMD  {ctx.command}  |  "
            f"{ctx.author} ({ctx.author.id})  |  "
            f"#{ctx.channel}  |  "
            f"{guild_info}"
        )

    async def on_guild_join(self, guild: discord.Guild):
        """Log when the bot is added to a new server."""
        log.info(
            f"➕ Joined server: {guild.name} ({guild.id})  |  "
            f"{guild.member_count} members  |  "
            f"Owner: {guild.owner} ({guild.owner_id})"
        )

    async def on_guild_remove(self, guild: discord.Guild):
        """Log when the bot is removed from a server."""
        log.info(
            f"➖ Left server: {guild.name} ({guild.id})"
        )

    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return

        self.last_senders[message.channel.id] = message.author

        # ── Tag shortcut: n!tagname ────────────────────────────────────────────
        # Use get_context() so discord.py does ALL prefix resolution for us
        # (text prefix, mention prefix, per-guild prefix — all handled correctly).
        # ctx.valid = True  → real command found, invoke it normally.
        # ctx.valid = False + ctx.prefix is not None → prefix matched but no
        #   command → try the word as a tag name.
        ctx = await self.get_context(message)

        if ctx.valid:
            await self.invoke(ctx)
            return

        if ctx.prefix is not None:
            # A prefix was matched but no command found — try the entire
            # remaining text as a tag name (supports multi-word tag names).
            after = message.content[len(ctx.prefix):].strip()
            if after:
                await _try_tag_shortcut(message, self, after.lower())
            # Whether tag found or not, we're done — no unknown-command noise

    # ── Error Handler ──────────────────────────────────────────────────────────
    async def on_command_error(self, ctx: commands.Context, error):
        if isinstance(error, commands.CommandInvokeError):
            error = error.original

        if isinstance(error, commands.MissingPermissions):
            cmd_name = ctx.command.name if ctx.command else "that command"
            missing  = ", ".join(p.replace("_", " ").title() for p in error.missing_permissions)
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
            missing  = ", ".join(p.replace("_", " ").title() for p in error.missing_permissions)
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

        if isinstance(error, commands.BadArgument):
            e = discord.Embed(description=f"Invalid argument: {error}", color=0xFEE75C)
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
            return   # tag shortcuts are handled in on_message, not here

        log.error(f"Unhandled error in {ctx.command}: {error}", exc_info=error)


# ── Tag shortcut lookup ────────────────────────────────────────────────────────
async def _try_tag_shortcut(
    message: discord.Message,
    bot:     commands.Bot,
    name:    str,
) -> bool:
    """
    Look up a tag by name for the message author and send it to the channel.
    Returns True if a tag was found and sent, False otherwise.
    Called from on_message BEFORE process_commands so errors surface clearly.
    """
    try:
        tag = await db.get_tag(message.guild.id, name, message.author.id)
        if tag is None:
            return False

        text    = tag.get("content") or ""
        img_url = tag.get("image_url")

        if len(text) > 1500:
            header = f"📌 **[{message.guild.name}]  {name}**"
            img_suffix = f"\n{img_url}" if img_url else ""
            await message.reply(f"{header}\n\n{text}{img_suffix}")
        else:
            e = discord.Embed(
                title       = f"📌 [{message.guild.name}]  {name}",
                description = text or None,
                color       = 0x5865F2,
            )
            if img_url:
                e.set_image(url=img_url)
            e.set_footer(text="NanoBot Tags")
            await message.reply(embed=e)
        log.debug(f"Tag shortcut fired: '{name}' for {message.author} in {message.guild}")
        return True

    except Exception as exc:
        log.error(f"Tag shortcut error for '{name}': {exc}", exc_info=exc)
        return False


# ── Entry Point ────────────────────────────────────────────────────────────────
async def main():
    cfg = _CFG  # already loaded at module level

    token = os.getenv("DISCORD_TOKEN") or cfg.get("token")
    if not token:
        log.error("❌ No token found. Set DISCORD_TOKEN env var or add it to config.json")
        return

    # Validate config — log warnings for non-fatal issues, abort on fatal ones
    from utils.config import validate as _validate_cfg
    for issue in _validate_cfg(cfg):
        if issue.fatal:
            log.critical(f"Config error [{issue.field}]: {issue.message}")
        else:
            log.warning(f"Config warning [{issue.field}]: {issue.message}")
    fatal_issues = [i for i in _validate_cfg(cfg) if i.fatal]
    if fatal_issues:
        log.critical("Aborting — fix config.json and restart.")
        return

    bot = NanoBot(cfg)
    log.info("🚀 Starting NanoBot...")
    try:
        async with bot:
            await bot.start(token)
    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
