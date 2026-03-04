"""
███╗   ██╗ █████╗ ███╗   ██╗ ██████╗ ██████╗  ██████╗ ████████╗
████╗  ██║██╔══██╗████╗  ██║██╔═══██╗██╔══██╗██╔═══██╗╚══██╔══╝
██╔██╗ ██║███████║██╔██╗ ██║██║   ██║██████╔╝██║   ██║   ██║
██║╚██╗██║██╔══██║██║╚██╗██║██║   ██║██╔══██╗██║   ██║   ██║
██║ ╚████║██║  ██║██║ ╚████║╚██████╔╝██████╔╝╚██████╔╝   ██║
╚═╝  ╚═══╝╚═╝  ╚═╝╚═╝  ╚═══╝ ╚═════╝ ╚═════╝  ╚═════╝    ╚═╝

Small. Fast. Built for Mobile Mods.
A lightweight Discord moderation bot — no database, just JSON.
"""

import asyncio
import json
import logging
import logging.handlers
import os

import discord
from discord.ext import commands

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

        # Owner ID: config override takes priority over the application owner
        raw_owner = cfg.get("owner_id")
        self.config_owner_id: int | None = int(raw_owner) if raw_owner else None

    # ── Startup ────────────────────────────────────────────────────────────────
    async def setup_hook(self):
        os.makedirs("data", exist_ok=True)
        self._load_prefixes()

        cogs = (
            "cogs.moderation",
            "cogs.tags",
            "cogs.utility",
            "cogs.admin",       # owner-only: reload / shutdown / restart
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
    def _load_prefixes(self):
        path = "data/prefixes.json"
        if os.path.exists(path):
            with open(path) as f:
                self.prefixes = json.load(f)

    def save_prefixes(self):
        os.makedirs("data", exist_ok=True)
        with open("data/prefixes.json", "w") as f:
            json.dump(self.prefixes, f, indent=2)

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

    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return

        self.last_senders[message.channel.id] = message.author

        # ── Tag shortcut: n!tagname ────────────────────────────────────────────
        # Resolve the guild prefix, check if the word after it is NOT a real
        # command, and try it as a tag BEFORE handing off to process_commands.
        # Doing this here (not in CommandNotFound) means failures are never
        # silently swallowed inside the error handler chain.
        prefix  = self.prefixes.get(str(message.guild.id), self.default_prefix)
        content = message.content.strip()

        if content.lower().startswith(prefix.lower()):
            after  = content[len(prefix):].strip()
            parts  = after.split()
            name   = parts[0].lower() if parts else ""

            # Real commands always win — only fall through to tag lookup if
            # the word isn't a registered command name or alias.
            if name and name not in self.all_commands:
                handled = await _try_tag_shortcut(message, self, name)
                if handled:
                    return   # tag sent — skip process_commands

        await self.process_commands(message)

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
    from utils import storage as _storage
    try:
        data = _storage.read("tags.json")
        gid  = str(message.guild.id)
        uid  = str(message.author.id)

        # Personal tags first, then global
        raw = (
            data.get(gid, {}).get("personal", {}).get(uid, {}).get(name)
            or data.get(gid, {}).get("global", {}).get(name)
        )
        if raw is None:
            return False

        # Normalise legacy plain-string tags
        tag = {"content": raw, "image_url": None} if isinstance(raw, str) else raw

        e = discord.Embed(
            title       = f"📌 [{message.guild.name}]  {name}",
            description = tag.get("content", ""),
            color       = 0x5865F2,
        )
        if tag.get("image_url"):
            e.set_image(url=tag["image_url"])
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

    bot = NanoBot(cfg)
    log.info("🚀 Starting NanoBot...")
    async with bot:
        await bot.start(token)


if __name__ == "__main__":
    asyncio.run(main())
