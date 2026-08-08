"""
cogs/debug.py
Owner-only shell and Python REPL commands for remote administration.

Commands:
  sh      [--disable-timeout] <command>  — run a shell command, get stdout/stderr back
  shkill                                 — kill the active --disable-timeout shell process
  py      [--disable-timeout] <code>     — evaluate Python in the bot process (supports await)
  mem                                    — memory overview: RSS, Python heap, GC, verdict
  mem trace                              — arm tracemalloc and store a baseline
  mem diff [n]                           — what grew since the baseline (finds the leak)
  mem where [i]                          — full call stack behind the Nth growth entry
  mem top [n]                            — largest live allocations right now
  mem objects [n]                        — live object counts by type (no tracing needed)
  mem registries [n]                     — every in-memory container the bot holds
  mem caches                             — discord.py's own cache sizes
  mem stop                               — disarm tracing and drop the baseline

When output is too long for the embed it's truncated to the tail there and the
full, untruncated log is attached as a .txt file.
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
from utils import memdiag

log = logging.getLogger("NanoBot.debug")

_SHELL_TIMEOUT = 60  # seconds before a shell/py process is killed (default)
_OUTPUT_CAP = 900  # chars per section before truncation
_FILE_CAP = 1_000_000  # max chars written to an attached file (keeps the tail)
_DISABLE_TIMEOUT_FLAG = "--disable-timeout"


def _trim(text: str) -> str:
    if len(text) <= _OUTPUT_CAP:
        return text
    return "…(truncated)\n" + text[-_OUTPUT_CAP:]


def _overflows(*texts: str) -> bool:
    """True when any section is long enough that the embed truncated it."""
    return any(len(t) > _OUTPUT_CAP for t in texts)


def _as_file(sections: list[tuple[str, str]], filename: str) -> discord.File:
    """Pack the full (untruncated) sections into a single .txt attachment.

    Each section is headered so stdout/stderr (or output/return/error) stay
    distinguishable. The body keeps the tail if it somehow exceeds _FILE_CAP.
    """
    body = "\n\n".join(f"===== {name} =====\n{text}" for name, text in sections if text)
    if not body:
        body = "(no output)"
    if len(body) > _FILE_CAP:
        body = "…(truncated)\n" + body[-_FILE_CAP:]
    return discord.File(io.BytesIO(body.encode("utf-8", "replace")), filename=filename)


class Debug(commands.Cog):
    """Owner-only debug/REPL commands."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._active_proc: asyncio.subprocess.Process | None = None

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
            "Owner-only. 60-second timeout by default. Both streams shown.\n"
            "Long output is truncated in the embed and attached in full as a file.\n\n"
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

        if disable_timeout:
            self._active_proc = proc

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
        finally:
            if disable_timeout and self._active_proc is proc:
                self._active_proc = None

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

        attachment = None
        if _overflows(stdout, stderr):
            attachment = _as_file(
                [("stdout", stdout), ("stderr", stderr)], "sh-output.txt"
            )
            parts.append("📄 Output was truncated above — full log attached.")

        label = command if len(command) <= 60 else command[:57] + "…"
        e = h.embed(
            title=f"{'✅' if rc == 0 else '❌'} sh: {label}",
            description="\n".join(parts),
            color=h.GREEN if rc == 0 else h.RED,
        )
        e.set_footer(text=f"exit {rc}  ·  NanoBot Debug")
        await ctx.reply(embed=e, **({"file": attachment} if attachment else {}))

    # ══════════════════════════════════════════════════════════════════════════
    #  shkill
    # ══════════════════════════════════════════════════════════════════════════
    @commands.command(
        name="shkill",
        help=(
            "Kill the currently running n!sh --disable-timeout process, if any.\n\n"
            "Owner-only. Use this if you accidentally started a shell command without\n"
            "a timeout and need to abort it.\n\n"
            "Example:\n"
            "  !shkill"
        ),
    )
    async def shkill(self, ctx: commands.Context):
        proc = self._active_proc
        if proc is None:
            return await ctx.reply(
                embed=h.warn("No active no-timeout shell process to kill.")
            )

        if proc.returncode is not None:
            self._active_proc = None
            return await ctx.reply(embed=h.warn("Process already finished."))

        proc.kill()
        log.warning(
            "shkill: %s (%s) killed active proc pid=%s",
            ctx.author,
            ctx.author.id,
            proc.pid,
        )
        await ctx.reply(embed=h.ok(f"Killed process (pid {proc.pid})."))

    # ══════════════════════════════════════════════════════════════════════════
    #  py
    # ══════════════════════════════════════════════════════════════════════════
    @commands.command(
        name="py",
        aliases=["eval", "python"],
        help=(
            "Evaluate Python code inside the running bot process.\n\n"
            "Owner-only. 60-second timeout by default. Top-level `await` works.\n"
            "Code block backticks are stripped. Long output is truncated in the "
            "embed and attached in full as a file.\n\n"
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

        r = repr(result) if result is not None else ""

        parts = []
        if printed:
            parts.append(f"**output**\n```\n{_trim(printed)}\n```")
        if r:
            parts.append(f"**return**\n```py\n{_trim(r)}\n```")
        if error:
            parts.append(f"**error**\n```py\n{_trim(error)}\n```")
        if not parts:
            parts.append("_(no output)_")

        attachment = None
        if _overflows(printed, r, error or ""):
            attachment = _as_file(
                [("output", printed), ("return", r), ("error", error or "")],
                "py-output.txt",
            )
            parts.append("📄 Output was truncated above — full log attached.")

        e = h.embed(
            title="❌ Python Error" if error else "✅ Python",
            description="\n".join(parts),
            color=h.RED if error else h.GREEN,
        )
        e.set_footer(text="NanoBot Debug")
        await ctx.reply(embed=e, **({"file": attachment} if attachment else {}))

    # ══════════════════════════════════════════════════════════════════════════
    #  mem
    # ══════════════════════════════════════════════════════════════════════════
    async def _mem_reply(
        self, ctx: commands.Context, title: str, lines: list[str], note: str = ""
    ) -> None:
        """Render a diagnostic listing, attaching the full version when it spills.

        Listings here are long by nature — the point of `registries` is to show
        everything, and truncating it at the embed cap would hide the entry at
        the bottom that is the actual leak.
        """
        body = "\n".join(lines) if lines else "(nothing to report)"
        block = f"```\n{_trim(body)}\n```"
        desc = f"{note}\n{block}" if note else block
        attachment = None
        if _overflows(body):
            attachment = _as_file([(title, body)], "memdiag.txt")
        e = h.embed(title=f"🧠 {title}", description=desc, color=h.BLUE)
        e.set_footer(text="NanoBot Debug")
        await ctx.reply(embed=e, **({"file": attachment} if attachment else {}))

    @commands.group(
        name="mem",
        aliases=["memory"],
        invoke_without_command=True,
        help=(
            "Live memory diagnostics for hunting a leak.\n\n"
            "Owner-only. Bare `!mem` is a read-only overview and is always safe "
            "to run. Tracing is off until you arm it — it costs 15-30% CPU, so "
            "it is a deliberate switch, not something the bot always pays for.\n\n"
            "Workflow that actually finds a leak:\n"
            "  1. !mem trace     — arm it and store a baseline\n"
            "  2. (wait hours)   — a leak is only visible as growth over time\n"
            "  3. !mem diff      — the lines that grew, biggest first\n"
            "  4. !mem where 0   — the full call stack behind the top one\n\n"
            "Subcommands: trace, diff, where, top, objects, registries, caches, stop"
        ),
    )
    async def mem(self, ctx: commands.Context):
        data = memdiag.overview(self.bot)
        gc_state = data["gc"]
        rss = data["rss"]
        traced = data["traced_current"]

        lines = [
            f"RSS (what the OS sees)   {memdiag.fmt_bytes(rss)}",
            f"Python heap (traced)     {memdiag.fmt_bytes(traced)}"
            + ("" if data["tracing"] else "   [tracing off]"),
            f"Python heap peak         {memdiag.fmt_bytes(data['traced_peak'])}",
            f"Threads                  {data['threads']}",
            f"GC tracked (gen 0/1/2)   {'/'.join(str(c) for c in gc_state['counts'])}",
            f"GC uncollectable         {gc_state['uncollectable']}",
            f"gc.garbage               {gc_state['garbage']}",
        ]
        if rss and traced:
            lines.append(f"RSS / heap ratio         {rss / traced:.1f}x")
        age = data["baseline_age"]
        if age is not None:
            lines.append(f"Baseline age             {age / 3600:.1f} h")

        await self._mem_reply(ctx, "Memory Overview", lines, note=data["verdict"])

    @mem.command(
        name="trace", help="Arm tracemalloc and store a baseline to diff against."
    )
    async def mem_trace(self, ctx: commands.Context):
        already = memdiag.is_tracing()
        memdiag.start()
        verb = "Baseline reset" if already else "Tracing armed, baseline stored"
        await ctx.reply(
            embed=h.ok(
                f"{verb}.\n\nLeave it running — a leak is only visible as growth "
                "between two points. Come back in a few hours and run `!mem diff`.",
                title="🧠 Memory Tracing",
            )
        )

    @mem.command(name="stop", help="Disarm tracemalloc and drop the baseline.")
    async def mem_stop(self, ctx: commands.Context):
        memdiag.stop()
        await ctx.reply(embed=h.ok("Tracing disarmed, baseline dropped."))

    @mem.command(
        name="diff",
        help="What grew since the baseline, biggest growth first. The leak-finder.",
    )
    async def mem_diff(self, ctx: commands.Context, limit: int = 15):
        if not memdiag.is_tracing():
            return await ctx.reply(
                embed=h.warn("Tracing is off. Run `!mem trace` first, then wait.")
            )
        lines = memdiag.diff(max(1, min(limit, 60)))
        age = memdiag.baseline_age()
        note = (
            f"Growth over the last {age / 3600:.1f}h. "
            "Columns: size delta, object delta, source line."
            if age
            else ""
        )
        if not lines:
            note = "Nothing grew since the baseline — if RSS is still climbing, the growth is outside Python's heap (see `!mem` verdict)."
        await self._mem_reply(ctx, "Growth Since Baseline", lines, note=note)

    @mem.command(
        name="where",
        help="Full call stack behind the Nth entry in `!mem diff` (default 0, the biggest).",
    )
    async def mem_where(self, ctx: commands.Context, index: int = 0):
        if not memdiag.is_tracing():
            return await ctx.reply(
                embed=h.warn("Tracing is off. Run `!mem trace` first, then wait.")
            )
        lines = memdiag.traceback_for(max(0, index))
        if not lines:
            return await ctx.reply(
                embed=h.warn(
                    f"No growth entry at index {index}. Try `!mem diff` first."
                )
            )
        await self._mem_reply(ctx, f"Allocation Stack #{index}", lines)

    @mem.command(name="top", help="Largest live allocations right now, by source line.")
    async def mem_top(self, ctx: commands.Context, limit: int = 15):
        if not memdiag.is_tracing():
            return await ctx.reply(
                embed=h.warn("Tracing is off. Run `!mem trace` first.")
            )
        lines = memdiag.top(max(1, min(limit, 60)))
        await self._mem_reply(
            ctx,
            "Largest Live Allocations",
            lines,
            note="Snapshot of what is allocated. For finding a *leak* use `!mem diff` — this is dominated by legitimately-large structures.",
        )

    @mem.command(
        name="objects",
        aliases=["gc"],
        help="Live object counts by type. Works without tracing — run this first.",
    )
    async def mem_objects(self, ctx: commands.Context, limit: int = 25):
        lines = await asyncio.to_thread(memdiag.gc_histogram, max(1, min(limit, 60)))
        await self._mem_reply(
            ctx,
            "Live Objects By Type",
            lines,
            note="An absurd count names the subsystem even when tracing was never armed.",
        )

    @mem.command(
        name="registries",
        aliases=["containers"],
        help="Every in-memory container the bot holds (cog attributes + module globals).",
    )
    async def mem_registries(self, ctx: commands.Context, limit: int = 30):
        lines = memdiag.registry_sizes(self.bot)[: max(1, min(limit, 80))]
        await self._mem_reply(
            ctx,
            "In-Memory Registries",
            lines,
            note="Anything here that grows without bound between two runs is a leak.",
        )

    @mem.command(name="caches", help="discord.py's own cache sizes.")
    async def mem_caches(self, ctx: commands.Context):
        lines = memdiag.discord_caches(self.bot)
        await self._mem_reply(
            ctx,
            "discord.py Caches",
            lines,
            note="Member/user caches scale with guilds joined and are expected to be large. The view store is the one that leaks if a persistent view is registered per message and never stopped.",
        )


# ── Registration ───────────────────────────────────────────────────────────────
async def setup(bot: commands.Bot):
    await bot.add_cog(Debug(bot))
