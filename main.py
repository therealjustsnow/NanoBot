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
import os

import discord
from discord.ext import commands

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("NanoBot")


# ── Prefix Resolution ──────────────────────────────────────────────────────────
def get_prefix(bot: "NanoBot", message: discord.Message):
    """Per-guild prefix + mention support."""
    if not message.guild:
        return commands.when_mentioned_or(bot.default_prefix)(bot, message)
    prefix = bot.prefixes.get(str(message.guild.id), bot.default_prefix)
    return commands.when_mentioned_or(prefix)(bot, message)


# ── Bot ────────────────────────────────────────────────────────────────────────
class NanoBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True

        super().__init__(
            command_prefix=get_prefix,
            intents=intents,
            help_command=None,
            description="NanoBot — Small. Fast. Built for Mobile Mods.",
        )

        self.default_prefix: str = "!"

        # Loaded from data/prefixes.json
        self.prefixes: dict[str, str] = {}

        # In-memory: tracks last message sender per channel {channel_id: Member}
        self.last_senders: dict[int, discord.Member] = {}

    # ── Startup ────────────────────────────────────────────────────────────────
    async def setup_hook(self):
        os.makedirs("data", exist_ok=True)
        self._load_prefixes()

        cogs = ("cogs.moderation", "cogs.tags", "cogs.utility")
        for cog in cogs:
            await self.load_extension(cog)
            log.info(f"✅ Loaded {cog}")

        # Sync slash commands globally
        synced = await self.tree.sync()
        log.info(f"⚡ Synced {len(synced)} slash command(s)")

    # ── Prefix Persistence ─────────────────────────────────────────────────────
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
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name="over the server 👁️",
            )
        )
        # Signal cogs to restore any scheduled tasks (timed unbans, slowmode, etc.)
        self.dispatch("restore_schedules")

    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return
        # Track who last spoke in each channel (used by commands that omit a user arg)
        self.last_senders[message.channel.id] = message.author
        await self.process_commands(message)

    async def on_command_error(self, ctx: commands.Context, error):
        if isinstance(error, commands.MissingPermissions):
            await ctx.send("❌ You don't have permission to use that command.", delete_after=5)
        elif isinstance(error, commands.BotMissingPermissions):
            await ctx.send("❌ I'm missing the permissions to do that.", delete_after=5)
        elif isinstance(error, commands.CommandNotFound):
            pass  # Silently ignore unknown commands
        elif isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(f"❌ Missing argument: `{error.param.name}`", delete_after=8)
        else:
            log.error(f"Unhandled error in {ctx.command}: {error}", exc_info=error)


# ── Entry Point ────────────────────────────────────────────────────────────────
async def main():
    bot = NanoBot()

    # Token: env var first, then config.json
    token = os.getenv("DISCORD_TOKEN")
    if not token and os.path.exists("config.json"):
        with open("config.json") as f:
            cfg = json.load(f)
        token = cfg.get("token")
        bot.default_prefix = cfg.get("default_prefix", "!")

    if not token:
        log.error("❌ No token found. Set DISCORD_TOKEN env var or add it to config.json")
        return

    log.info("🚀 Starting NanoBot...")
    async with bot:
        await bot.start(token)


if __name__ == "__main__":
    asyncio.run(main())
