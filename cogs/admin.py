"""
cogs/admin.py
Owner-only bot management commands.

All commands here require the invoker to be the bot owner
(set via config.json → owner_id, or the Discord application owner).

Commands:
  reload  [cog|all]  — hot-reload one cog or every cog
  shutdown           — graceful shutdown (flushes logs, closes connection)
  restart            — graceful shutdown then re-exec the process
  setloglevel <lvl>  — change log level live and persist to config.json
  logs [lines]       — tail the log file right in Discord
"""

import asyncio
import json
import logging
import os
import sys
from typing import Optional

import discord
from discord.ext import commands

from utils import helpers as h

log = logging.getLogger("NanoBot.admin")

# All cogs that NanoBot manages (admin reloads itself too — safe with discord.py 2.x)
_ALL_COGS = (
    "cogs.moderation",
    "cogs.tags",
    "cogs.utility",
    "cogs.admin",
)

_VALID_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


# ══════════════════════════════════════════════════════════════════════════════
class Admin(commands.Cog):
    """Owner-only bot management."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ── Owner check ────────────────────────────────────────────────────────────
    async def cog_check(self, ctx: commands.Context) -> bool:
        """All commands in this cog require bot ownership."""
        if not await self.bot.is_owner(ctx.author):
            raise commands.NotOwner()
        return True

    # ══════════════════════════════════════════════════════════════════════════
    #  reload
    # ══════════════════════════════════════════════════════════════════════════
    @commands.command(
        name="reload",
        aliases=["rl"],
        help="Reload one cog by name, or all cogs at once.\n\nExamples:\n  !reload all\n  !reload cogs.moderation\n  !reload moderation",
    )
    async def reload(self, ctx: commands.Context, cog: Optional[str] = "all"):
        """
        !reload [cog|all]

        Accepted formats:
          all                → reload every cog
          cogs.moderation    → full dotted name
          moderation         → shorthand (cogs. prefix added automatically)
        """
        await ctx.defer()

        # ── Normalise name ─────────────────────────────────────────────────────
        target = cog.lower().strip() if cog else "all"

        if target == "all":
            targets = list(_ALL_COGS)
        else:
            # Accept "moderation" as shorthand for "cogs.moderation"
            if "." not in target:
                target = f"cogs.{target}"
            if target not in _ALL_COGS:
                return await ctx.reply(
                    embed=h.err(
                        f"Unknown cog: `{target}`\n"
                        f"Available: {', '.join(f'`{c}`' for c in _ALL_COGS)}"
                    ),
                    ephemeral=True,
                )
            targets = [target]

        # ── Reload each ────────────────────────────────────────────────────────
        results = []
        for ext in targets:
            try:
                await self.bot.reload_extension(ext)
                results.append(f"✅ `{ext}`")
                log.info(f"Reloaded: {ext}")
            except commands.ExtensionNotLoaded:
                # Wasn't loaded yet — try loading fresh
                try:
                    await self.bot.load_extension(ext)
                    results.append(f"✅ `{ext}` _(loaded fresh)_")
                    log.info(f"Loaded (was not loaded): {ext}")
                except Exception as exc:
                    results.append(f"❌ `{ext}`: {exc}")
                    log.error(f"Failed to load {ext}: {exc}", exc_info=exc)
            except Exception as exc:
                results.append(f"❌ `{ext}`: {exc}")
                log.error(f"Failed to reload {ext}: {exc}", exc_info=exc)

        had_errors = any(r.startswith("❌") for r in results)
        title  = "🔄 Reload Complete" if not had_errors else "🔄 Reload — Partial Failure"
        colour = h.GREEN if not had_errors else h.YELLOW

        e = h.embed(title=title, description="\n".join(results), color=colour)
        if not had_errors and len(targets) > 1:
            e.set_footer(text=f"All {len(targets)} cogs reloaded successfully  ·  NanoBot")
        else:
            e.set_footer(text="NanoBot Admin")

        await ctx.reply(embed=e)

    # ══════════════════════════════════════════════════════════════════════════
    #  shutdown
    # ══════════════════════════════════════════════════════════════════════════
    @commands.command(
        name="shutdown",
        aliases=["die", "stop"],
        help="Gracefully shut NanoBot down.",
    )
    async def shutdown(self, ctx: commands.Context):
        """Flush logs, send a goodbye embed, close the Discord connection cleanly."""
        log.warning(f"Shutdown initiated by {ctx.author} ({ctx.author.id})")

        await ctx.reply(
            embed=h.ok(
                "Closing connection and flushing logs.\nSee you on the other side. 👋",
                "⚡ NanoBot Shutting Down",
            )
        )

        # Give Discord a moment to deliver the message before closing
        await asyncio.sleep(0.5)
        await self.bot.close()

    # ══════════════════════════════════════════════════════════════════════════
    #  restart
    # ══════════════════════════════════════════════════════════════════════════
    @commands.command(
        name="restart",
        aliases=["reboot", "rs"],
        help="Gracefully restart NanoBot by re-executing the current process.",
    )
    async def restart(self, ctx: commands.Context):
        """
        Closes the bot cleanly, then re-executes the Python process with the
        same arguments (os.execv).  All cogs reload, schedules restore, and
        slash commands re-sync.

        Works with both `python main.py` and `python run.py`.
        """
        log.warning(f"Restart initiated by {ctx.author} ({ctx.author.id})")

        await ctx.reply(
            embed=h.ok(
                "Restarting… I'll be back in a few seconds. 🔄\n"
                "_If something goes wrong check `logs/nanobot.log`._",
                "🔄 NanoBot Restarting",
            )
        )

        await asyncio.sleep(0.5)

        # Close the Discord connection gracefully before re-exec
        await self.bot.close()

        # Re-execute: replaces this process with a fresh Python process running
        # the same script and arguments.  Logs, data files, and config are all
        # re-read from disk — so config changes take effect on restart too.
        os.execv(sys.executable, [sys.executable] + sys.argv)

    # ══════════════════════════════════════════════════════════════════════════
    #  setloglevel
    # ══════════════════════════════════════════════════════════════════════════
    @commands.command(
        name="setloglevel",
        aliases=["loglevel", "loglvl"],
        help=(
            "Change the log level live and save it to config.json.\n\n"
            f"Valid levels: {', '.join(_VALID_LEVELS)}\n\n"
            "Examples:\n"
            "  !setloglevel DEBUG    → verbose (see every gateway event)\n"
            "  !setloglevel INFO     → normal\n"
            "  !setloglevel WARNING  → quiet (only problems)"
        ),
    )
    async def setloglevel(self, ctx: commands.Context, level: str):
        level = level.upper().strip()

        if level not in _VALID_LEVELS:
            return await ctx.reply(
                embed=h.err(
                    f"`{level}` is not a valid log level.\n"
                    f"Choose from: {', '.join(f'`{l}`' for l in _VALID_LEVELS)}"
                ),
                ephemeral=True,
            )

        # Apply to the root logger (affects all NanoBot.* and discord.* loggers)
        numeric = getattr(logging, level)
        logging.getLogger().setLevel(numeric)
        log.info(f"Log level changed to {level} by {ctx.author} ({ctx.author.id})")

        # Persist to config.json
        cfg_path = "config.json"
        cfg = {}
        if os.path.exists(cfg_path):
            with open(cfg_path, encoding="utf-8") as f:
                try:
                    cfg = json.load(f)
                except json.JSONDecodeError:
                    pass

        cfg["log_level"] = level
        with open(cfg_path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)

        level_descriptions = {
            "DEBUG":    "verbose — every gateway event, HTTP call, and internal step",
            "INFO":     "normal — startup, commands, mod actions",
            "WARNING":  "quiet — only problems and warnings",
            "ERROR":    "minimal — errors only",
            "CRITICAL": "silent — only fatal errors",
        }

        await ctx.reply(
            embed=h.ok(
                f"Log level set to **{level}** — {level_descriptions[level]}.\n"
                f"Saved to `config.json`. Takes effect immediately.",
                "📋 Log Level Updated",
            )
        )

    # ══════════════════════════════════════════════════════════════════════════
    #  logs
    # ══════════════════════════════════════════════════════════════════════════
    @commands.command(
        name="logs",
        aliases=["log"],
        help=(
            "Tail the NanoBot log file right in Discord.\n\n"
            "Usage:  !logs [lines]\n"
            "Default: last 20 lines. Max: 50.\n\n"
            "Great for diagnosing issues without SSH access on mobile."
        ),
    )
    async def logs(self, ctx: commands.Context, lines: int = 20):
        lines = max(1, min(50, lines))  # Clamp 1–50

        log_path = "logs/nanobot.log"
        if not os.path.exists(log_path):
            return await ctx.reply(
                embed=h.warn(
                    "No log file found at `logs/nanobot.log`.\n"
                    "The file is created on first run.",
                    "📋 No Logs Yet",
                ),
                ephemeral=True,
            )

        # Read last N lines efficiently without loading the whole file
        try:
            with open(log_path, encoding="utf-8", errors="replace") as f:
                all_lines = f.readlines()
        except OSError as exc:
            return await ctx.reply(
                embed=h.err(f"Couldn't read log file: {exc}"),
                ephemeral=True,
            )

        tail       = all_lines[-lines:]
        total_lines = len(all_lines)
        content    = "".join(tail).strip()

        if not content:
            return await ctx.reply(
                embed=h.info("Log file exists but is empty.", "📋 Logs"),
                ephemeral=True,
            )

        # Discord code block cap is 2000 chars including the backticks / header
        max_chars = 1900
        if len(content) > max_chars:
            content = "…(truncated)\n" + content[-max_chars:]

        e = h.embed(
            title       = f"📋 Last {lines} log line(s)",
            description = f"```\n{content}\n```",
            color       = h.GREY,
        )
        e.set_footer(text=f"logs/nanobot.log  ·  {total_lines} total line(s)  ·  NanoBot")
        await ctx.reply(embed=e, ephemeral=True)


# ── Registration ───────────────────────────────────────────────────────────────
async def setup(bot: commands.Bot):
    await bot.add_cog(Admin(bot))
