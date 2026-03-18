"""
cogs/admin.py
Owner-only bot management commands.

All commands here require the invoker to be the bot owner
(set via config.json → owner_id, or the Discord application owner).

Commands:
  reload  [cog|all]  — hot-reload one cog or every cog
  update             — git pull + reload all cogs (no slash command sync)
  sync   [guild_id]  — push slash commands to Discord (global or one guild)
  shutdown           — graceful shutdown (flushes logs, closes connection)
  restart            — graceful shutdown then re-exec the process
  setloglevel <lvl>  — change log level live and persist to config.json
  logs [lines]       — tail the log file right in Discord
"""

import asyncio
import json
import logging
import os
import subprocess
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
    "cogs.reminders",
    "cogs.recurring",
    "cogs.warnings",
    "cogs.welcome",
    "cogs.admin",
    "cogs.votes",
    "cogs.auditlog",
    "cogs.automod",
    "cogs.roles",
    "cogs.eli5",
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
    extras={
        'category': '🔧 Owner / Admin',
        'short': 'Hot-reload a cog or all cogs (owner only)',
        'usage': 'reload [cog|all]',
        'desc': 'Reloads without restarting. Accepts all, the full dotted name, or just the short name.',
        'args': [
            ('cog', 'Cog to reload, or all (default: all)'),
        ],
        'perms': 'Bot Owner',
        'example': '!reload all\n!reload moderation',
    },
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
        title = (
            "🔄 Reload Complete" if not had_errors else "🔄 Reload — Partial Failure"
        )
        colour = h.GREEN if not had_errors else h.YELLOW

        e = h.embed(title=title, description="\n".join(results), color=colour)
        if not had_errors and len(targets) > 1:
            e.set_footer(
                text=f"All {len(targets)} cogs reloaded successfully  ·  NanoBot"
            )
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
    extras={
        'category': '🔧 Owner / Admin',
        'short': 'Gracefully shut down (owner only)',
        'usage': 'shutdown',
        'desc': 'Flushes all logs, sends a goodbye message, and closes the Discord connection.',
        'args': [],
        'perms': 'Bot Owner',
        'example': '!shutdown',
    },
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
    extras={
        'category': '🔧 Owner / Admin',
        'short': 'Gracefully restart the bot process (owner only)',
        'usage': 'restart',
        'desc': 'Closes cleanly, then re-executes the Python process with the same arguments.',
        'args': [],
        'perms': 'Bot Owner',
        'example': '!restart',
    },
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

        # Spawn a fresh process BEFORE closing so it can start initialising
        # while this one finishes its shutdown.  subprocess.Popen works
        # correctly on all platforms (os.execv silently fails on Windows).
        subprocess.Popen([sys.executable] + sys.argv)
        log.info("Spawned new process — shutting down this one")

        await self.bot.close()

    # ══════════════════════════════════════════════════════════════════════════
    #  update
    # ══════════════════════════════════════════════════════════════════════════
    @commands.command(
        name="update",
        aliases=["pull"],
        help=(
            "Pull the latest code from git and reload all cogs.\n\n"
            "Runs: git pull → reloads every cog.\n"
            "Does NOT sync slash commands — use !sync for that.\n"
            "Does NOT restart the process — use !restart for that."
        ),
    extras={
        'category': '🔧 Owner / Admin',
        'short': 'Git pull + reload all cogs (owner only)',
        'usage': 'update',
        'desc': 'Runs git pull and reports the output, then reloads all cogs. Does NOT sync slash commands — use !sync for that. Does NOT restart the process — use !restart for that.',
        'args': [],
        'perms': 'Bot Owner',
        'example': '!update',
    },
    )
    async def update(self, ctx: commands.Context):
        """
        !update  /  !pull

        1. Runs `git pull` and reports the output.
        2. If the pull succeeds, reloads all cogs.
        3. Reports per-cog reload results.

        Slash commands are NOT synced here — run !sync separately if you
        added or removed any slash commands.
        """
        await ctx.defer()

        # ── Step 1: git pull ───────────────────────────────────────────────────
        try:
            result = subprocess.run(
                ["git", "pull"],
                capture_output=True,
                text=True,
                timeout=30,
            )
            stdout = result.stdout.strip()
            stderr = result.stderr.strip()
            git_ok = result.returncode == 0
        except FileNotFoundError:
            return await ctx.reply(
                embed=h.err(
                    "`git` not found. Make sure git is installed and NanoBot was cloned from a repo.\n"
                    "If you're running from a zip/download, use `!reload all` instead."
                ),
                ephemeral=True,
            )
        except subprocess.TimeoutExpired:
            return await ctx.reply(
                embed=h.err("Git pull timed out after 30 seconds."),
                ephemeral=True,
            )

        git_output = stdout or stderr or "_(no output)_"
        # Trim for embed safety
        if len(git_output) > 900:
            git_output = git_output[:900] + "\n…(truncated)"

        if not git_ok:
            e = h.embed(title="📥 Update — Git Pull Failed", color=h.RED)
            e.description = f"```\n{git_output}\n```"
            e.set_footer(text="Cogs were NOT reloaded  ·  NanoBot Admin")
            log.error(f"git pull failed (rc={result.returncode}): {git_output}")
            return await ctx.reply(embed=e)

        log.info(f"git pull OK by {ctx.author}: {stdout[:200]}")

        # ── Step 2: reload all cogs ────────────────────────────────────────────
        reload_results = []
        for ext in _ALL_COGS:
            try:
                await self.bot.reload_extension(ext)
                reload_results.append(f"✅ `{ext}`")
                log.info(f"update: reloaded {ext}")
            except commands.ExtensionNotLoaded:
                try:
                    await self.bot.load_extension(ext)
                    reload_results.append(f"✅ `{ext}` _(loaded fresh)_")
                except Exception as exc:
                    reload_results.append(f"❌ `{ext}`: {exc}")
                    log.error(f"update: failed to load {ext}: {exc}", exc_info=exc)
            except Exception as exc:
                reload_results.append(f"❌ `{ext}`: {exc}")
                log.error(f"update: failed to reload {ext}: {exc}", exc_info=exc)

        had_errors = any(r.startswith("❌") for r in reload_results)

        e = h.embed(
            title=(
                "📥 Update Complete" if not had_errors else "📥 Update — Reload Errors"
            ),
            color=h.GREEN if not had_errors else h.YELLOW,
        )
        e.add_field(name="📦 Git Pull", value=f"```\n{git_output}\n```", inline=False)
        e.add_field(
            name="🔄 Cog Reloads", value="\n".join(reload_results), inline=False
        )

        already_latest = "already up to date" in stdout.lower()
        if already_latest:
            e.set_footer(
                text="Already up to date — cogs reloaded anyway  ·  NanoBot Admin"
            )
        elif not had_errors:
            e.set_footer(
                text=f"Pulled + reloaded {len(_ALL_COGS)} cog(s) · Run !sync if slash commands changed  ·  NanoBot Admin"
            )
        else:
            e.set_footer(
                text="Some cogs failed to reload — check logs  ·  NanoBot Admin"
            )

        log.info(
            f"update complete: git_ok={git_ok}, reload_errors={had_errors}, by {ctx.author}"
        )
        await ctx.reply(embed=e)

    # ══════════════════════════════════════════════════════════════════════════
    #  sync
    # ══════════════════════════════════════════════════════════════════════════
    @commands.command(
        name="sync",
        help=(
            "Sync slash commands with Discord.\n\n"
            "Usage:\n"
            "  !sync           → global sync (up to 1 hr to propagate)\n"
            "  !sync <guild>   → instant sync to one guild by ID\n\n"
            "Run this after adding or removing any slash commands.\n"
            "You do NOT need to run this after a normal !update."
        ),
    extras={
        'category': '🔧 Owner / Admin',
        'short': 'Push slash commands to Discord (owner only)',
        'usage': 'sync [guild_id]',
        'desc': 'No guild_id: global sync (up to 1 hour to propagate). With guild_id: instant sync to that specific guild — use this during development.',
        'args': [
            ('guild_id', 'Guild ID for instant sync (omit for global sync)'),
        ],
        'perms': 'Bot Owner',
        'example': '!sync\n!sync 123456789012345678',
    },
    )
    async def sync(self, ctx: commands.Context, guild_id: Optional[int] = None):
        """
        !sync [guild_id]

        Global sync:  !sync
          Pushes all app commands to Discord globally.  Changes can take up to
          an hour to appear for all users — use guild sync during development.

        Guild sync:   !sync 123456789012345678
          Instantly pushes commands to a single guild.  Useful when iterating
          on new slash commands without waiting for global propagation.
        """
        await ctx.defer()

        if guild_id is not None:
            guild = discord.Object(id=guild_id)
            try:
                # Copy global command tree to the guild, then sync
                self.bot.tree.copy_global_to(guild=guild)
                synced = await self.bot.tree.sync(guild=guild)
            except discord.Forbidden:
                return await ctx.reply(
                    embed=h.err(
                        f"Missing `applications.commands` scope in guild `{guild_id}`.\n"
                        "Re-invite the bot with that scope enabled."
                    ),
                    ephemeral=True,
                )
            except discord.HTTPException as exc:
                log.error(f"Guild sync failed for {guild_id}: {exc}", exc_info=exc)
                return await ctx.reply(
                    embed=h.err(f"Discord returned an error: {exc}"),
                    ephemeral=True,
                )

            log.info(
                f"Guild sync to {guild_id}: {len(synced)} command(s) by {ctx.author}"
            )
            await ctx.reply(
                embed=h.ok(
                    f"Synced **{len(synced)}** slash command(s) to guild `{guild_id}`.\n"
                    "Changes are live immediately.",
                    "⚡ Guild Sync Complete",
                )
            )

        else:
            try:
                synced = await self.bot.tree.sync()
            except discord.HTTPException as exc:
                log.error(f"Global sync failed: {exc}", exc_info=exc)
                return await ctx.reply(
                    embed=h.err(f"Discord returned an error during global sync: {exc}"),
                    ephemeral=True,
                )

            log.info(f"Global sync: {len(synced)} command(s) by {ctx.author}")
            await ctx.reply(
                embed=h.ok(
                    f"Synced **{len(synced)}** slash command(s) globally.\n"
                    "⏱️ Global changes can take **up to 1 hour** to propagate.\n"
                    "_Tip: use `!sync <guild_id>` for instant updates while developing._",
                    "🌐 Global Sync Complete",
                )
            )

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
    extras={
        'category': '🔧 Owner / Admin',
        'short': 'Change log verbosity live (owner only)',
        'usage': 'setloglevel <level>',
        'desc': 'Changes logging level immediately and saves to config.json.',
        'args': [
            ('level', 'DEBUG / INFO / WARNING / ERROR / CRITICAL'),
        ],
        'perms': 'Bot Owner',
        'example': '!setloglevel DEBUG',
    },
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
            "DEBUG": "verbose — every gateway event, HTTP call, and internal step",
            "INFO": "normal — startup, commands, mod actions",
            "WARNING": "quiet — only problems and warnings",
            "ERROR": "minimal — errors only",
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
    extras={
        'category': '🔧 Owner / Admin',
        'short': 'Tail the log file in Discord (owner only)',
        'usage': 'logs [lines]',
        'desc': 'Fetches the last N lines of logs/nanobot.log as an ephemeral embed.',
        'args': [
            ('lines', 'How many lines to show (1–50, default 20)'),
        ],
        'perms': 'Bot Owner',
        'example': '!logs 30',
    },
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

        tail = all_lines[-lines:]
        total_lines = len(all_lines)
        content = "".join(tail).strip()

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
            title=f"📋 Last {lines} log line(s)",
            description=f"```\n{content}\n```",
            color=h.GREY,
        )
        e.set_footer(
            text=f"logs/nanobot.log  ·  {total_lines} total line(s)  ·  NanoBot"
        )
        await ctx.reply(embed=e, ephemeral=True)


# ── Registration ───────────────────────────────────────────────────────────────
async def setup(bot: commands.Bot):
    await bot.add_cog(Admin(bot))
