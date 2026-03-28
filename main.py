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
from discord import app_commands
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
_VALID_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


def _setup_logging(cfg: dict) -> logging.Logger:
    os.makedirs("logs", exist_ok=True)

    level_str = str(cfg.get("log_level", "INFO")).upper()
    if level_str not in _VALID_LEVELS:
        level_str = "INFO"
    level = getattr(logging, level_str)

    file_handler = logging.handlers.RotatingFileHandler(
        filename="logs/nanobot.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s [%(levelname)-8s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )

    discord.utils.setup_logging(level=level, root=True)
    logging.getLogger().addHandler(file_handler)

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


# ── All cogs — single source of truth shared with admin.py ────────────────────
# Keep this in sync with _ALL_COGS in cogs/admin.py.
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
class NanoBot(commands.Bot):
    def __init__(self, cfg: dict):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True

        super().__init__(
            command_prefix=get_prefix,
            intents=intents,
            help_command=None,
            description="NanoBot — Small. Fast. Built for Mobile Mods.",
        )

        self.default_prefix: str = cfg.get("default_prefix", "!")
        self.prefixes: dict[str, str] = {}
        self.last_senders: dict[int, discord.Member] = {}
        self.start_time = discord.utils.utcnow()
        self.groq_api_key: str | None = cfg.get("groq_api_key")
        self.commands_ran: int = 0  # incremented in on_command; resets on restart

        raw_owner = cfg.get("owner_id")
        self.config_owner_id: int | None = int(raw_owner) if raw_owner else None

    # ── Startup ────────────────────────────────────────────────────────────────
    async def setup_hook(self):
        os.makedirs("data", exist_ok=True)
        await db.init()
        await self._load_prefixes()

        for cog in _ALL_COGS:
            try:
                await self.load_extension(cog)
                log.info(f"✅ Loaded {cog}")
            except Exception as exc:
                # Log but don't abort — optional cogs (eli5) may be absent
                log.warning(f"⚠️  Could not load {cog}: {exc}")

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
        self.prefixes = await db.get_all_prefixes()

    async def save_prefix(self, guild_id: int, prefix: str):
        self.prefixes[str(guild_id)] = prefix
        await db.set_prefix(guild_id, prefix)

    # ── Events ─────────────────────────────────────────────────────────────────
    async def on_ready(self):
        log.info(f"🤖 Online as {self.user} (ID: {self.user.id})")
        log.info(f"📡 Connected to {len(self.guilds)} server(s)")
        log.info(
            f"🔑 Owner: {'config override' if self.config_owner_id else 'application owner'}"
        )
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name="over the server 👁️",
            )
        )
        self.dispatch("restore_schedules")

    async def on_command(self, ctx: commands.Context):
        self.commands_ran += 1
        guild_info = f"{ctx.guild.name} ({ctx.guild.id})" if ctx.guild else "DM"
        log.info(
            f"CMD  {ctx.command}  |  "
            f"{ctx.author} ({ctx.author.id})  |  "
            f"#{ctx.channel}  |  "
            f"{guild_info}"
        )

    async def on_guild_join(self, guild: discord.Guild):
        log.info(
            f"➕ Joined server: {guild.name} ({guild.id})  |  "
            f"{guild.member_count} members  |  "
            f"Owner: {guild.owner} ({guild.owner_id})"
        )

    async def on_guild_remove(self, guild: discord.Guild):
        log.info(f"➖ Left server: {guild.name} ({guild.id})")

    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return

        self.last_senders[message.channel.id] = message.author

        ctx = await self.get_context(message)

        if ctx.valid:
            await self.invoke(ctx)
            return

        if ctx.prefix is not None:
            after = message.content[len(ctx.prefix) :].strip()
            if after:
                await _try_tag_shortcut(message, self, after.lower())

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

        # Catch-all for anything else
        log.error(f"Unhandled tree error in /{cmd_name}: {error}", exc_info=error)

    # ── Prefix/hybrid command error handler ────────────────────────────────────
    async def on_command_error(self, ctx: commands.Context, error):
        # Unwrap both CommandInvokeError and HybridCommandError — both carry
        # the real exception in .original but are different classes.
        if isinstance(
            error, (commands.CommandInvokeError, commands.HybridCommandError)
        ):
            error = error.original

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
            "❌ No token found. Set DISCORD_TOKEN env var or add it to config.json"
        )
        return

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
