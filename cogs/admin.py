"""
cogs/admin.py
Owner-only bot management commands.

All commands here require the invoker to be the bot owner
(set via config.ini → [bot] owner_id, or the Discord application owner).

Commands:
  reload  [cog|all]  — hot-reload one cog or every cog
  unload  <cog>      — unload a single cog without restarting
  update             — git pull + reload all cogs (no slash command sync)
  upgrade            — git pull + pip install + spawn new process + close
  sync   [guild_id]  — push slash commands to Discord (global or one guild)
  shutdown           — graceful shutdown (flushes logs, closes connection)
  restart            — graceful shutdown then re-exec the process
  setloglevel <lvl>  — change log level live and persist to config.ini
  logs [lines]       — tail the log file right in Discord
  scrape             — manually trigger the daily content cache scrape
  cachestats         — show cache DB statistics (FML, WYR, images)
  fmlpurge           — wipe all cached FML stories (forces re-scrape)
  reloadconfig       — re-read config.ini without restarting
  config             — DM-only: show/get/set config values
"""

import asyncio
import logging
import os
import subprocess
import sys
from typing import Optional

import discord
from discord.ext import commands

from utils import config as cfg_mod
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
    "cogs.images",
    "cogs.fun",
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
    #  unload
    # ══════════════════════════════════════════════════════════════════════════
    @commands.command(
        name="unload",
        aliases=["ul"],
        help=(
            "Unload a single cog by name.\n\n"
            "The cog's commands become unavailable until you !reload it.\n"
            "Cannot unload admin (that would lock you out).\n\n"
            "Examples:\n"
            "  !unload fun\n"
            "  !unload cogs.images"
        ),
    )
    async def unload(self, ctx: commands.Context, cog: str):
        """
        !unload <cog>

        Accepted formats:
          cogs.moderation    — full dotted name
          moderation         — shorthand (cogs. prefix added automatically)
        """
        target = cog.lower().strip()

        if "." not in target:
            target = f"cogs.{target}"

        # Block unloading admin — you'd lose the ability to reload anything
        if target == "cogs.admin":
            return await ctx.reply(
                embed=h.err(
                    "Cannot unload `cogs.admin` — that would lock you out.\n"
                    "Use `!reload cogs.admin` instead if you need to refresh it."
                ),
                ephemeral=True,
            )

        if target not in _ALL_COGS:
            return await ctx.reply(
                embed=h.err(
                    f"Unknown cog: `{target}`\n"
                    f"Available: {', '.join(f'`{c}`' for c in _ALL_COGS)}"
                ),
                ephemeral=True,
            )

        try:
            await self.bot.unload_extension(target)
            log.info(f"Unloaded: {target} (by {ctx.author})")
            await ctx.reply(
                embed=h.ok(
                    f"Unloaded `{target}`.\n"
                    f"Use `!reload {target}` to bring it back.",
                    "📦 Cog Unloaded",
                )
            )
        except commands.ExtensionNotLoaded:
            await ctx.reply(
                embed=h.warn(
                    f"`{target}` is already unloaded.",
                    "📦 Already Unloaded",
                ),
                ephemeral=True,
            )
        except Exception as exc:
            log.error(f"Failed to unload {target}: {exc}", exc_info=exc)
            await ctx.reply(
                embed=h.err(f"Failed to unload `{target}`: {exc}"),
                ephemeral=True,
            )

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
    #  upgrade
    # ══════════════════════════════════════════════════════════════════════════
    @commands.command(
        name="upgrade",
        aliases=["deploy", "ud"],
        help=(
            "Full upgrade: git pull → pip install -r requirements.txt → restart.\n\n"
            "Use this to deploy new code and dependency changes from mobile.\n"
            "Stops at git pull failure — won't install or restart if pull fails.\n"
            "If pip fails, reports the error but restarts anyway (existing code still works).\n\n"
            "See also: !update (pull + reload cogs, no restart), !restart (restart only)."
        ),
    )
    async def upgrade(self, ctx: commands.Context):
        """
        !upgrade  /  !deploy

        1. Runs `git pull` and reports the output.
        2. If pull fails, stops — no install, no restart.
        3. Runs `pip install -r requirements.txt --quiet` in a background thread
           (can be slow; uses asyncio.to_thread to avoid blocking the event loop).
        4. Sends a result embed showing both steps, then spawns a new process
           and closes this one — identical to !restart.
        """
        await ctx.defer()

        log.warning(f"Upgrade initiated by {ctx.author} ({ctx.author.id})")

        # ── Step 1: git pull ───────────────────────────────────────────────────
        try:
            git_result = subprocess.run(
                ["git", "pull"],
                capture_output=True,
                text=True,
                timeout=30,
            )
            git_stdout = git_result.stdout.strip()
            git_stderr = git_result.stderr.strip()
            git_ok = git_result.returncode == 0
        except FileNotFoundError:
            return await ctx.reply(
                embed=h.err(
                    "`git` not found. Make sure git is installed and NanoBot was cloned from a repo."
                ),
                ephemeral=True,
            )
        except subprocess.TimeoutExpired:
            return await ctx.reply(
                embed=h.err("Git pull timed out after 30 seconds."),
                ephemeral=True,
            )

        git_output = git_stdout or git_stderr or "_(no output)_"
        if len(git_output) > 900:
            git_output = git_output[:900] + "\n…(truncated)"

        if not git_ok:
            e = h.embed(title="📥 Upgrade — Git Pull Failed", color=h.RED)
            e.description = f"```\n{git_output}\n```"
            e.set_footer(text="pip install and restart were skipped  ·  NanoBot Admin")
            log.error(
                f"upgrade: git pull failed (rc={git_result.returncode}): {git_output}"
            )
            return await ctx.reply(embed=e)

        log.info(f"upgrade: git pull OK by {ctx.author}: {git_stdout[:200]}")

        # ── Step 2: pip install (run in thread — can take 30s+) ───────────────
        pip_ok = False
        pip_output = ""
        try:

            def _run_pip():
                return subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "pip",
                        "install",
                        "-r",
                        "requirements.txt",
                        "--quiet",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=120,
                )

            pip_result = await asyncio.to_thread(_run_pip)
            pip_ok = pip_result.returncode == 0
            pip_output = (pip_result.stdout + pip_result.stderr).strip()
        except subprocess.TimeoutExpired:
            pip_output = "pip install timed out after 120 seconds"
            log.error("upgrade: pip install timed out")
        except Exception as exc:
            pip_output = f"pip install error: {exc}"
            log.error(f"upgrade: pip install raised: {exc}", exc_info=exc)

        if len(pip_output) > 800:
            pip_output = pip_output[:800] + "\n…(truncated)"

        if pip_ok:
            log.info("upgrade: pip install OK")
        else:
            log.warning(f"upgrade: pip install failed — {pip_output[:200]}")

        # ── Step 3: build embed, spawn new process, close ─────────────────────
        colour = h.GREEN if pip_ok else h.YELLOW
        e = h.embed(title="🚀 Upgrade Complete — Restarting", color=colour)
        e.add_field(name="📥 Git Pull", value=f"```\n{git_output}\n```", inline=False)
        pip_display = pip_output or "_(nothing to install / all up to date)_"
        e.add_field(
            name="📦 Pip Install", value=f"```\n{pip_display}\n```", inline=False
        )

        if pip_ok:
            e.set_footer(
                text="Spawning new process… back in a few seconds  ·  NanoBot Admin"
            )
        else:
            e.set_footer(
                text="pip install had errors but restarting anyway  ·  NanoBot Admin"
            )

        await ctx.reply(embed=e)
        await asyncio.sleep(0.5)

        subprocess.Popen([sys.executable] + sys.argv)
        log.info(f"upgrade: spawned new process — shutting down (by {ctx.author})")
        await self.bot.close()

    # ══════════════════════════════════════════════════════════════════════════
    #  sync
    # ══════════════════════════════════════════════════════════════════════════
    @commands.command(
        name="sync",
        help=(
            "Sync slash commands with Discord.\n\n"
            "Usage:\n"
            "  !sync              → global sync (up to 1 hr to propagate)\n"
            "  !sync <guild>      → instant sync to one guild by ID\n"
            "  !sync clear <guild> → remove guild-specific commands (fixes duplicates)\n\n"
            "Run this after adding or removing any slash commands.\n"
            "You do NOT need to run this after a normal !update."
        ),
    )
    async def sync(
        self,
        ctx: commands.Context,
        target: Optional[str] = None,
        guild_id: Optional[int] = None,
    ):
        """
        !sync [guild_id | clear <guild_id>]

        Global sync:  !sync
          Pushes all app commands to Discord globally.  Changes can take up to
          an hour to appear for all users.

        Guild sync:   !sync 123456789012345678
          Instantly syncs global commands to a single guild for testing.

        Clear guild:  !sync clear 123456789012345678
          Removes all guild-specific command overrides from a guild.
          Use this to fix duplicate commands caused by prior copy_global_to usage.
        """
        await ctx.defer()

        # ── !sync clear <guild_id> — remove guild-specific overrides ─────────
        if target is not None and target.lower() == "clear":
            if guild_id is None:
                return await ctx.reply(
                    embed=h.err(
                        "Usage: `!sync clear <guild_id>`\n"
                        "This removes guild-specific command overrides to fix duplicates."
                    ),
                    ephemeral=True,
                )

            guild = discord.Object(id=guild_id)
            try:
                self.bot.tree.clear_commands(guild=guild)
                await self.bot.tree.sync(guild=guild)
            except discord.Forbidden:
                return await ctx.reply(
                    embed=h.err(
                        f"Missing `applications.commands` scope in guild `{guild_id}`.\n"
                        "Re-invite the bot with that scope enabled."
                    ),
                    ephemeral=True,
                )
            except discord.HTTPException as exc:
                log.error(f"Guild clear failed for {guild_id}: {exc}", exc_info=exc)
                return await ctx.reply(
                    embed=h.err(f"Discord returned an error: {exc}"),
                    ephemeral=True,
                )

            log.info(f"Cleared guild commands for {guild_id} by {ctx.author}")
            await ctx.reply(
                embed=h.ok(
                    f"Cleared guild-specific commands from `{guild_id}`.\n"
                    "Only global commands will appear now (no more duplicates).",
                    "🧹 Guild Commands Cleared",
                )
            )
            return

        # ── !sync <guild_id> — instant guild sync ───────────────────────────
        # If target looks like a guild ID (all digits), treat it as guild sync
        if target is not None:
            try:
                parsed_guild_id = int(target)
            except ValueError:
                return await ctx.reply(
                    embed=h.err(
                        f"Unknown argument: `{target}`\n"
                        "Usage: `!sync`, `!sync <guild_id>`, or `!sync clear <guild_id>`"
                    ),
                    ephemeral=True,
                )

            guild = discord.Object(id=parsed_guild_id)
            try:
                # Clear any existing guild overrides first to avoid duplicates,
                # then copy global commands and sync
                self.bot.tree.clear_commands(guild=guild)
                self.bot.tree.copy_global_to(guild=guild)
                synced = await self.bot.tree.sync(guild=guild)
            except discord.Forbidden:
                return await ctx.reply(
                    embed=h.err(
                        f"Missing `applications.commands` scope in guild `{parsed_guild_id}`.\n"
                        "Re-invite the bot with that scope enabled."
                    ),
                    ephemeral=True,
                )
            except discord.HTTPException as exc:
                log.error(
                    f"Guild sync failed for {parsed_guild_id}: {exc}", exc_info=exc
                )
                return await ctx.reply(
                    embed=h.err(f"Discord returned an error: {exc}"),
                    ephemeral=True,
                )

            log.info(
                f"Guild sync to {parsed_guild_id}: {len(synced)} command(s) by {ctx.author}"
            )
            await ctx.reply(
                embed=h.ok(
                    f"Synced **{len(synced)}** slash command(s) to guild `{parsed_guild_id}`.\n"
                    "Changes are live immediately.\n\n"
                    "_Run `!sync clear {0}` after you're done testing to remove guild overrides._".format(
                        parsed_guild_id
                    ),
                    "⚡ Guild Sync Complete",
                )
            )
            return

        # ── !sync — global sync ─────────────────────────────────────────────
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
            "Change the log level live and save it to config.ini.\n\n"
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

        # Persist to config.ini and refresh bot.config.
        cfg_mod.set_value("log_level", level)
        if hasattr(self.bot, "reload_config"):
            self.bot.reload_config()

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
                f"Saved to `config.ini`. Takes effect immediately.",
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

    # ══════════════════════════════════════════════════════════════════════════
    #  scrape
    # ══════════════════════════════════════════════════════════════════════════
    @commands.command(
        name="scrape",
        help=(
            "Manually trigger the daily content scrape.\n\n"
            "Runs the same scrape loop that fires every 24h:\n"
            "FML stories, WYR questions, nekos.best GIFs/images, Nekosia thighs.\n\n"
            "Safe to run anytime — duplicates are skipped automatically."
        ),
    )
    async def scrape(self, ctx: commands.Context):
        fun_cog = self.bot.get_cog("Fun")
        if not fun_cog:
            return await ctx.reply(
                embed=h.err("Fun cog is not loaded."), ephemeral=True
            )

        if fun_cog._scrape_lock.locked():
            return await ctx.reply(
                embed=h.warn(
                    "Scrape already in progress. Check `!logs` or `!cachestats` for status.",
                    "\u23f3 Scrape Already Running",
                ),
                ephemeral=True,
            )

        await ctx.reply(
            embed=h.info(
                "Scrape started -- this takes a few minutes.\n"
                "Check `!logs` or `!cachestats` when it's done.",
                "\U0001f504 Scrape Running",
            )
        )
        asyncio.create_task(fun_cog._run_scrape())

    # ══════════════════════════════════════════════════════════════════════════
    #  cachestats
    # ══════════════════════════════════════════════════════════════════════════
    @commands.command(
        name="cachestats",
        aliases=["cs"],
        help=(
            "Show content cache statistics.\n\n"
            "Displays counts for FML stories, WYR questions, and\n"
            "cached image URLs broken down by source and endpoint."
        ),
    )
    async def cachestats(self, ctx: commands.Context):
        from utils import cache_db

        fml = await cache_db.count_fml()
        wyr = await cache_db.count_wyr()
        img_stats = await cache_db.get_image_stats()
        last_scrape = await cache_db.get_meta("last_scrape")

        # Build image summary
        total_imgs = 0
        img_lines = []
        for source, endpoints in sorted(img_stats.items()):
            source_total = sum(endpoints.values())
            total_imgs += source_total
            img_lines.append(
                f"**{source}**: {source_total:,} ({len(endpoints)} endpoints)"
            )

        desc_parts = [
            f"**FML stories:** {fml:,}",
            f"**WYR questions:** {wyr:,}",
            f"**Images/GIFs:** {total_imgs:,}",
        ]
        if img_lines:
            desc_parts.append("")
            desc_parts.extend(img_lines)

        if last_scrape:
            desc_parts.append(f"\nLast scrape: <t:{int(float(last_scrape))}:R>")

        e = h.embed(
            title="\U0001f4ca Cache Stats",
            description="\n".join(desc_parts),
            color=h.BLUE,
        )
        e.set_footer(text="data/cache.db \u00b7 NanoBot Admin")
        await ctx.reply(embed=e)

    # ══════════════════════════════════════════════════════════════════════════
    #  fmlpurge
    # ══════════════════════════════════════════════════════════════════════════
    @commands.command(
        name="fmlpurge",
        help=(
            "Wipe all cached FML stories.\n\n"
            "Use after a scraper bugfix to drop poisoned entries. "
            "The next `!scrape` repopulates the table."
        ),
    )
    async def fmlpurge(self, ctx: commands.Context):
        from utils import cache_db

        removed = await cache_db.purge_fml()
        await ctx.reply(
            embed=h.ok(
                f"Removed **{removed:,}** cached FML stories.\n"
                "Run `!scrape` to repopulate.",
                "\U0001f9f9 FML Cache Purged",
            )
        )

    # ══════════════════════════════════════════════════════════════════════════
    #  reloadconfig
    # ══════════════════════════════════════════════════════════════════════════
    @commands.command(
        name="reloadconfig",
        aliases=["rlc", "rlconfig"],
        help=(
            "Re-read config.ini from disk without restarting the bot.\n\n"
            "Refreshes: log level, owner_id, default_prefix, Groq key, and every\n"
            "[scraper] knob used by !scrape. Webhook-related values in the votes\n"
            "cog are captured at init — use `!reload votes` after changing those."
        ),
    )
    async def reloadconfig(self, ctx: commands.Context):
        if not hasattr(self.bot, "reload_config"):
            return await ctx.reply(
                embed=h.err(
                    "This bot instance doesn't expose `reload_config()` — "
                    "running an old main.py? Restart with `!restart`."
                ),
                ephemeral=True,
            )

        try:
            new_cfg = self.bot.reload_config()
        except Exception as exc:
            log.error(f"reloadconfig failed: {exc}", exc_info=exc)
            return await ctx.reply(
                embed=h.err(f"Failed to reload config.ini: {exc}"),
                ephemeral=True,
            )

        issues = cfg_mod.validate(new_cfg)
        fatals = [i for i in issues if i.fatal]
        warns = [i for i in issues if not i.fatal]

        lines = [f"Reloaded **{len(new_cfg)}** key(s) from `config.ini`."]
        if fatals:
            lines.append("\n**Fatal issues** (take effect on next restart):")
            lines.extend(f"• `{i.field}` — {i.message}" for i in fatals)
        if warns:
            lines.append("\n**Warnings:**")
            lines.extend(f"• `{i.field}` — {i.message}" for i in warns)
        if not fatals and not warns:
            lines.append("All values validated cleanly.")

        log.info(
            f"reloadconfig by {ctx.author} ({ctx.author.id}) — "
            f"{len(fatals)} fatal, {len(warns)} warning(s)"
        )
        await ctx.reply(
            embed=h.ok(
                "\n".join(lines),
                "🔁 Config Reloaded",
            )
        )

    # ══════════════════════════════════════════════════════════════════════════
    #  config  (DM-only)
    # ══════════════════════════════════════════════════════════════════════════
    @commands.command(
        name="config",
        aliases=["cfg"],
        help=(
            "View or edit config.ini from Discord. **DM-only** for safety —\n"
            "prevents leaking secrets in a public channel.\n\n"
            "Usage:\n"
            "  !config show                     — list every key (secrets masked)\n"
            "  !config get <section>.<key>      — read one value\n"
            "  !config set <section>.<key> <v>  — write one value\n"
            "  !config unset <section>.<key>    — clear a value\n\n"
            "Key may be given as `key` or `section.key`. After a change the\n"
            "bot auto-refreshes its live settings (log level, scraper knobs,\n"
            "etc.) — you do not need to run !reloadconfig separately."
        ),
    )
    async def config_cmd(
        self,
        ctx: commands.Context,
        action: Optional[str] = None,
        key: Optional[str] = None,
        *,
        value: Optional[str] = None,
    ):
        # ── DM-only gate ──────────────────────────────────────────────────────
        if ctx.guild is not None:
            try:
                await ctx.message.delete()
            except (discord.Forbidden, discord.HTTPException):
                pass
            try:
                await ctx.author.send(
                    embed=h.warn(
                        "`!config` is DM-only — run it here instead.\n"
                        "This prevents the bot token and other secrets from "
                        "being echoed in a channel.",
                        "🔒 DM-Only",
                    )
                )
            except discord.Forbidden:
                await ctx.reply(
                    embed=h.err(
                        "`!config` is DM-only and I can't DM you. "
                        "Enable DMs from server members and try again."
                    ),
                    ephemeral=True,
                )
            return

        action = (action or "show").lower().strip()

        # ── show ──────────────────────────────────────────────────────────────
        if action == "show":
            return await self._config_show(ctx)

        # ── get / set / unset need a key ──────────────────────────────────────
        if not key:
            return await ctx.reply(
                embed=h.err(
                    "Missing key. Usage:\n"
                    "`!config get <section>.<key>` or "
                    "`!config set <section>.<key> <value>`"
                )
            )

        resolved = self._resolve_key(key)
        if resolved is None:
            return await ctx.reply(
                embed=h.err(
                    f"Unknown config key: `{key}`.\n"
                    "Run `!config show` to see every valid key."
                )
            )
        section, bare_key = resolved

        if action == "get":
            return await self._config_get(ctx, section, bare_key)
        if action == "set":
            if value is None:
                return await ctx.reply(
                    embed=h.err(
                        f"Missing value. Usage: `!config set {section}.{bare_key} <value>`"
                    )
                )
            return await self._config_set(ctx, section, bare_key, value)
        if action == "unset":
            return await self._config_set(ctx, section, bare_key, "")

        await ctx.reply(
            embed=h.err(
                f"Unknown action `{action}`. Use `show`, `get`, `set`, or `unset`."
            )
        )

    # ── config helpers ────────────────────────────────────────────────────────
    @staticmethod
    def _resolve_key(raw: str) -> Optional[tuple[str, str]]:
        """Accept either 'section.key' or bare 'key'. Returns (section, key) or None."""
        raw = raw.strip().lower()
        if "." in raw:
            section, _, bare = raw.partition(".")
            if (
                section in cfg_mod.SECTION_ORDER
                and cfg_mod.SECTION_MAP.get(bare) == section
            ):
                return section, bare
            return None
        if raw in cfg_mod.SECTION_MAP:
            return cfg_mod.SECTION_MAP[raw], raw
        return None

    @staticmethod
    def _display(key: str, val) -> str:
        if val is None or val == "":
            return "_(unset)_"
        if key in cfg_mod.SENSITIVE_KEYS:
            s = str(val)
            return f"`{s[:4]}…{s[-2:]}`" if len(s) > 8 else "`***`"
        # Truncate long strings (like groq_wyr_system) for readability.
        s = str(val)
        if len(s) > 120:
            return f"`{s[:117]}…`"
        return f"`{s}`"

    async def _config_show(self, ctx: commands.Context):
        cfg = cfg_mod.load()
        lines: list[str] = []
        for section in cfg_mod.SECTION_ORDER:
            keys = [k for k, sec in cfg_mod.SECTION_MAP.items() if sec == section]
            if not keys:
                continue
            lines.append(f"**[{section}]**")
            for k in keys:
                val = cfg.get(k, cfg_mod.DEFAULTS.get(k))
                lines.append(f"  `{k}` = {self._display(k, val)}")
            lines.append("")
        e = h.embed(
            title="⚙️ Config (config.ini)",
            description="\n".join(lines).rstrip(),
            color=h.BLUE,
        )
        e.set_footer(
            text="Secrets are masked · `!config set <key> <value>` to change · NanoBot"
        )
        await ctx.reply(embed=e)

    async def _config_get(self, ctx: commands.Context, section: str, key: str):
        cfg = cfg_mod.load()
        val = cfg.get(key, cfg_mod.DEFAULTS.get(key))
        desc = f"**[{section}]** `{key}` = {self._display(key, val)}"
        if key in cfg_mod.SENSITIVE_KEYS:
            desc += "\n_(masked — secret)_"
        await ctx.reply(
            embed=h.embed(
                title="⚙️ Config Value",
                description=desc,
                color=h.BLUE,
            )
        )

    async def _config_set(
        self, ctx: commands.Context, section: str, key: str, raw_value: str
    ):
        # Coerce the string through the same pipeline used by config.load()
        coerced = cfg_mod._coerce(key, raw_value)

        # Block obviously bad values before touching disk.
        cfg = cfg_mod.load()
        cfg[key] = coerced
        issues = [i for i in cfg_mod.validate(cfg) if i.field == key and i.fatal]
        if issues:
            return await ctx.reply(
                embed=h.err(
                    f"Rejected — `{key}` failed validation: {issues[0].message}"
                )
            )

        try:
            cfg_mod.set_value(key, coerced)
        except Exception as exc:
            log.error(f"config set {key} failed: {exc}", exc_info=exc)
            return await ctx.reply(embed=h.err(f"Could not write config.ini: {exc}"))

        if hasattr(self.bot, "reload_config"):
            self.bot.reload_config()

        log.info(
            f"config set: [{section}] {key} changed by {ctx.author} ({ctx.author.id})"
        )
        display = self._display(key, coerced)
        await ctx.reply(
            embed=h.ok(
                f"**[{section}]** `{key}` = {display}\n"
                "Saved to `config.ini` and live now.",
                "⚙️ Config Updated",
            )
        )

    # ══════════════════════════════════════════════════════════════════════════
    #  servers
    # ══════════════════════════════════════════════════════════════════════════
    @commands.command(
        name="servers",
        aliases=["guilds", "serverlist"],
        help=(
            "List every server NanoBot is currently in.\n\n"
            "Shows: name, ID, member count, owner.\n"
            "Sorted by member count descending.\n"
            "Paginates automatically at 10 servers per embed."
        ),
    )
    async def servers(self, ctx: commands.Context, page: int = 1):
        guilds = sorted(
            self.bot.guilds, key=lambda g: g.member_count or 0, reverse=True
        )
        total_guilds = len(guilds)
        total_members = sum(g.member_count or 0 for g in guilds)

        # Build lines — compact for mobile readability
        lines = []
        for i, g in enumerate(guilds, start=1):
            if g.owner_id:
                owner = g.get_member(g.owner_id)
                if owner is None:
                    try:
                        owner = await self.bot.fetch_user(g.owner_id)
                    except Exception:
                        owner = None
                owner_str = str(owner) if owner else f"ID: {g.owner_id}"
            else:
                owner_str = "Unknown"
            lines.append(
                f"`{i}.` **{g.name}**\n"
                f"    🆔 `{g.id}` · 👥 {g.member_count:,} · 👑 {owner_str}"
            )

        # One page at a time — no more embed floods
        page_size = 10
        pages = [lines[i : i + page_size] for i in range(0, len(lines), page_size)]
        total_pages = len(pages)
        page = max(1, min(page, total_pages))

        e = h.embed(
            title=f"🌐 Servers ({total_guilds})",
            description="\n".join(pages[page - 1]),
            color=h.BLUE,
        )
        footer = (
            f"Page {page}/{total_pages}  ·  "
            f"{total_guilds} server(s)  ·  {total_members:,} total members  ·  NanoBot"
        )
        if total_pages > 1:
            footer += f"  ·  !servers {page + 1 if page < total_pages else 1} for next"
        e.set_footer(text=footer)
        await ctx.send(embed=e)

        log.info(f"servers: page {page}/{total_pages} for {ctx.author}")


# ── Registration ───────────────────────────────────────────────────────────────
async def setup(bot: commands.Bot):
    await bot.add_cog(Admin(bot))
