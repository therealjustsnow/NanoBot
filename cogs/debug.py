"""
cogs/debug.py
Owner-only shell and Python REPL commands for remote administration.

Commands:
  sh  [--disable-timeout] <command>  — run a shell command, get stdout/stderr back
  py  [--disable-timeout] <code>     — evaluate Python in the bot process (supports await)
"""

import asyncio
import contextlib
import io
import logging
import textwrap
import traceback

import discord
from discord.ext import commands

from utils import helpers as h

log = logging.getLogger("NanoBot.debug")

_SHELL_TIMEOUT = 60  # seconds before a shell/py process is killed (default)
_OUTPUT_CAP = 900  # chars per section before truncation
_DISABLE_TIMEOUT_FLAG = "--disable-timeout"


def _trim(text: str) -> str:
    if len(text) <= _OUTPUT_CAP:
        return text
    return "…(truncated)\n" + text[-_OUTPUT_CAP:]


class Debug(commands.Cog):
    """Owner-only debug/REPL commands."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_check(self, ctx: commands.Context) -> bool:
        if not await self.bot.is_owner(ctx.author):
            raise commands.NotOwner()
        return True

    # ══════════════════════════════════════════════════════════════════════════
    #  sh
    # ══════════════════════════════════════════════════════════════════════════
    @commands.command(
        name="sh",
        aliases=["shell", "exec"],
        help=(
            "Run a shell command and see stdout + stderr.\n\n"
            "Owner-only. 60-second timeout by default. Both streams shown.\n\n"
            "Flags:\n"
            "  --disable-timeout  — no timeout (use for long downloads etc.)\n\n"
            "Examples:\n"
            "  !sh ls -la\n"
            "  !sh pip install yt-dlp\n"
            "  !sh --disable-timeout yt-dlp <url>"
        ),
    )
    async def sh(self, ctx: commands.Context, *, command: str):
        disable_timeout = False
        if command.startswith(_DISABLE_TIMEOUT_FLAG):
            disable_timeout = True
            command = command[len(_DISABLE_TIMEOUT_FLAG) :].lstrip()

        timeout = None if disable_timeout else _SHELL_TIMEOUT

        await ctx.defer()
        log.warning(
            "sh: %s (%s) timeout=%s → %s",
            ctx.author,
            ctx.author.id,
            "disabled" if disable_timeout else f"{timeout}s",
            command,
        )

        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except Exception as exc:
            log.error("sh: spawn failed: %s", exc, exc_info=exc)
            return await ctx.reply(embed=h.err(f"Failed to start process: {exc}"))

        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            return await ctx.reply(
                embed=h.err(
                    f"Killed after {_SHELL_TIMEOUT}s.\n```\n{command[:200]}\n```",
                    "⏱️ Timed Out",
                )
            )

        stdout = stdout_b.decode(errors="replace").strip()
        stderr = stderr_b.decode(errors="replace").strip()
        rc = proc.returncode

        parts = []
        if stdout:
            parts.append(f"**stdout**\n```\n{_trim(stdout)}\n```")
        if stderr:
            parts.append(f"**stderr**\n```\n{_trim(stderr)}\n```")
        if not parts:
            parts.append("_(no output)_")

        label = command if len(command) <= 60 else command[:57] + "…"
        e = h.embed(
            title=f"{'✅' if rc == 0 else '❌'} sh: {label}",
            description="\n".join(parts),
            color=h.GREEN if rc == 0 else h.RED,
        )
        e.set_footer(text=f"exit {rc}  ·  NanoBot Debug")
        await ctx.reply(embed=e)

    # ══════════════════════════════════════════════════════════════════════════
    #  py
    # ══════════════════════════════════════════════════════════════════════════
    @commands.command(
        name="py",
        aliases=["eval", "python"],
        help=(
            "Evaluate Python code inside the running bot process.\n\n"
            "Owner-only. 60-second timeout by default. Top-level `await` works.\n"
            "Code block backticks are stripped.\n\n"
            "Flags:\n"
            "  --disable-timeout  — no timeout (use for long-running async code)\n\n"
            "Locals: bot, ctx, guild, channel, author, discord, asyncio\n\n"
            "Examples:\n"
            "  !py len(bot.guilds)\n"
            "  !py [g.name for g in bot.guilds]\n"
            "  !py --disable-timeout await bot.fetch_user(123456789)"
        ),
    )
    async def py(self, ctx: commands.Context, *, code: str):
        disable_timeout = False
        if code.lstrip().startswith(_DISABLE_TIMEOUT_FLAG):
            disable_timeout = True
            code = code.lstrip()[len(_DISABLE_TIMEOUT_FLAG) :].lstrip()

        timeout = None if disable_timeout else _SHELL_TIMEOUT

        # Strip fenced code block markers if the user pastes with backticks
        code = code.strip()
        if code.startswith("```") and code.endswith("```"):
            code = code[3:-3].strip()
            if code.startswith("python\n") or code.startswith("py\n"):
                code = code.split("\n", 1)[1]

        log.warning(
            "py: %s (%s) timeout=%s → %s",
            ctx.author,
            ctx.author.id,
            "disabled" if disable_timeout else f"{timeout}s",
            code[:120],
        )

        env = {
            "bot": self.bot,
            "ctx": ctx,
            "guild": ctx.guild,
            "channel": ctx.channel,
            "author": ctx.author,
            "discord": discord,
            "asyncio": asyncio,
        }

        # Wrap in async def so bare `await` works at top level
        wrapped = f"async def _exec():\n{textwrap.indent(code, '    ')}"

        stdout_buf = io.StringIO()
        result = None
        error = None

        try:
            exec(compile(wrapped, "<discord>", "exec"), env)  # noqa: S102
            with contextlib.redirect_stdout(stdout_buf):
                result = await asyncio.wait_for(env["_exec"](), timeout=timeout)
        except asyncio.TimeoutError:
            error = f"Timed out after {_SHELL_TIMEOUT}s."
        except Exception:
            error = traceback.format_exc()

        printed = stdout_buf.getvalue().strip()

        parts = []
        if printed:
            parts.append(f"**output**\n```\n{_trim(printed)}\n```")
        if result is not None:
            r = repr(result)
            parts.append(f"**return**\n```py\n{_trim(r)}\n```")
        if error:
            parts.append(f"**error**\n```py\n{_trim(error)}\n```")
        if not parts:
            parts.append("_(no output)_")

        e = h.embed(
            title="❌ Python Error" if error else "✅ Python",
            description="\n".join(parts),
            color=h.RED if error else h.GREEN,
        )
        e.set_footer(text="NanoBot Debug")
        await ctx.reply(embed=e)


# ── Registration ───────────────────────────────────────────────────────────────
async def setup(bot: commands.Bot):
    await bot.add_cog(Debug(bot))
