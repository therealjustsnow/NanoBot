"""utils/memdiag.py — live memory diagnostics for a long-running bot.

Exists because "memory grows over days" is not a question static reading can
answer. A leak in a bot this size is one of three things, and they need
completely different fixes:

1. **A Python object graph that keeps growing** — an in-memory registry nobody
   prunes, a task dict that never pops, a cache with no eviction. tracemalloc
   finds these, and only tracemalloc: it attributes live allocations back to the
   source line that made them.
2. **A library holding memory the interpreter can't see** — Pillow buffers,
   yt-dlp extractor state, aiohttp connectors, SQLite page cache. These barely
   register in tracemalloc (the allocation happens in C) but show up as the gap
   between RSS and traced memory.
3. **Allocator fragmentation, not a leak at all** — glibc gives each thread its
   own malloc arena, and this bot runs several: aiosqlite's connection thread,
   the default executor's pool (card renders, yt-dlp, git, regex timeouts). Freed
   memory returns to the arena and never to the OS, so RSS climbs and plateaus
   while Python's own heap stays flat. `MALLOC_ARENA_MAX=2` is the fix, not a
   code change — and the *only* way to tell it apart from (1) is to measure
   RSS and traced memory side by side, which is why `overview()` reports both
   and names the likely category rather than making you eyeball two numbers.

Everything here is stdlib-only (no psutil) and safe to call on a live process.
tracemalloc is *off* until someone asks for it: it adds 15-30% CPU overhead and
memory of its own, so it is a deliberate "I am hunting a leak this week" switch
rather than something the bot always pays for.

The intended workflow, and the reason `diff()` exists at all: a snapshot taken
now shows what is *allocated*, which is dominated by legitimately-large
long-lived structures (the member cache, the item catalogue) and tells you
almost nothing. What identifies a leak is what *grew* between two points hours
apart. So: `!mem trace` (arms it, stores a baseline), wait, `!mem diff`.
"""

from __future__ import annotations

import gc
import logging
import os
import sys
import tracemalloc
from collections import Counter
from typing import Any, Iterable

log = logging.getLogger("NanoBot.memdiag")

# Kept module-level rather than on the cog so a `!reload debug` doesn't throw
# away a baseline someone has been accumulating for six hours.
_baseline: tracemalloc.Snapshot | None = None
_baseline_at: float | None = None

# Frames per allocation traceback. 1 attributes to the allocating line, which is
# usually a container's `append` inside a helper and so names the helper rather
# than the caller that actually leaked. 8 costs more memory but makes the culprit
# obvious without a second pass.
TRACE_FRAMES = 8

# Containers smaller than this are noise in the registry sweep — every cog has a
# handful of 2-entry config dicts and listing them buries the one with 400k.
_REGISTRY_MIN = 32

_PAGE_SIZE = os.sysconf("SC_PAGE_SIZE") if hasattr(os, "sysconf") else 4096


# ── process-level numbers ────────────────────────────────────────────────────
def rss_bytes() -> int | None:
    """Resident set size — what the OS thinks the process is using.

    None when it can't be read (non-Linux, or /proc not mounted). Deliberately
    not psutil: one more dependency to install on every host running this bot,
    for a number that is two lines of stdlib on the platform it ships to.
    """
    try:
        with open("/proc/self/statm", "r") as fh:
            fields = fh.read().split()
        return int(fields[1]) * _PAGE_SIZE
    except (OSError, IndexError, ValueError):
        pass
    # macOS/BSD fallback. ru_maxrss is the *peak*, not the current value, so it
    # can only ever over-report — flagged by the caller as approximate.
    try:
        import resource

        maxrss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        # Linux reports KiB, macOS bytes. We only reach here off Linux.
        return int(maxrss)
    except Exception:
        return None


def traced_bytes() -> tuple[int, int]:
    """(current, peak) bytes of Python allocations tracemalloc is tracking."""
    if not tracemalloc.is_tracing():
        return (0, 0)
    return tracemalloc.get_traced_memory()


def fmt_bytes(n: float | None) -> str:
    if n is None:
        return "n/a"
    sign = "-" if n < 0 else ""
    n = abs(float(n))
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{sign}{n:.1f} {unit}" if unit != "B" else f"{sign}{n:.0f} B"
        n /= 1024
    return f"{sign}{n:.1f} GB"


# ── tracemalloc control ──────────────────────────────────────────────────────
def is_tracing() -> bool:
    return tracemalloc.is_tracing()


def start(frames: int = TRACE_FRAMES) -> None:
    """Arm tracing and take the baseline in one step.

    Both together on purpose: a baseline taken at some later moment measures
    growth from *then*, and the whole value of the tool is that the baseline is
    the earliest point you have.
    """
    if not tracemalloc.is_tracing():
        tracemalloc.start(frames)
    set_baseline()


def stop() -> None:
    global _baseline, _baseline_at
    if tracemalloc.is_tracing():
        tracemalloc.stop()
    _baseline = None
    _baseline_at = None


def set_baseline() -> None:
    global _baseline, _baseline_at
    if not tracemalloc.is_tracing():
        return
    import time

    # Filter out tracemalloc's own bookkeeping and this module, so the diff
    # doesn't report the measuring apparatus as the leak.
    _baseline = tracemalloc.take_snapshot().filter_traces(_FILTERS)
    _baseline_at = time.time()


def baseline_age() -> float | None:
    if _baseline_at is None:
        return None
    import time

    return time.time() - _baseline_at


_FILTERS = (
    tracemalloc.Filter(False, tracemalloc.__file__),
    tracemalloc.Filter(False, __file__),
    tracemalloc.Filter(False, "<frozen importlib._bootstrap>"),
    tracemalloc.Filter(False, "<frozen importlib._bootstrap_external>"),
)


def _snapshot() -> tracemalloc.Snapshot:
    return tracemalloc.take_snapshot().filter_traces(_FILTERS)


def _format_stat(stat: tracemalloc.Statistic | tracemalloc.StatisticDiff) -> str:
    frame = stat.traceback[0]
    # Repo-relative paths: absolute ones eat the embed's width and every line
    # shares the same prefix anyway.
    path = frame.filename
    cwd = os.getcwd()
    if path.startswith(cwd):
        path = path[len(cwd) :].lstrip("/")
    size = getattr(stat, "size_diff", None)
    if size is None:
        # A plain snapshot statistic: absolute totals, so a signed count would
        # read as growth that was never measured.
        return f"{fmt_bytes(stat.size):>10}  {stat.count:6d} obj  {path}:{frame.lineno}"
    count = stat.count_diff
    return f"{fmt_bytes(size):>10}  {count:+6d} obj  {path}:{frame.lineno}"


def top(limit: int = 15) -> list[str]:
    """Largest live allocations right now, by source line."""
    if not tracemalloc.is_tracing():
        return []
    stats = _snapshot().statistics("lineno")
    return [_format_stat(s) for s in stats[:limit]]


def diff(limit: int = 15) -> list[str]:
    """What grew since the baseline — the line that actually finds a leak.

    Sorted by size *delta*, so a 400 MB structure that hasn't moved since the
    baseline sorts below a 30 MB one that has doubled. Shrinking lines are kept
    out: they are noise when the question is what is growing.
    """
    if not tracemalloc.is_tracing() or _baseline is None:
        return []
    stats = _snapshot().compare_to(_baseline, "lineno")
    grew = [s for s in stats if s.size_diff > 0]
    return [_format_stat(s) for s in grew[:limit]]


def traceback_for(index: int = 0) -> list[str]:
    """Full call stack behind the Nth-largest *growth* since the baseline.

    `diff()` names the line that allocated; this names who asked it to, which is
    the difference between "a list grew in helpers.py" and knowing which cog is
    filling it.
    """
    if not tracemalloc.is_tracing() or _baseline is None:
        return []
    stats = _snapshot().compare_to(_baseline, "traceback")
    grew = [s for s in stats if s.size_diff > 0]
    if index >= len(grew):
        return []
    stat = grew[index]
    header = f"{fmt_bytes(stat.size_diff)} in {stat.count_diff:+d} objects:"
    return [header] + [line.strip() for line in stat.traceback.format()]


# ── object-graph views (work without tracemalloc) ────────────────────────────
def gc_histogram(limit: int = 20) -> list[str]:
    """Live object counts by type.

    The cheap first look, and it works even when tracing was never armed — so
    it is the one thing that can be run the moment a leak is noticed, before
    restarting anything. A type whose count is absurd (200k Message, 90k Task)
    names the subsystem even though it can't name the line.
    """
    counts: Counter[str] = Counter()
    for obj in gc.get_objects():
        try:
            counts[type(obj).__qualname__] += 1
        except Exception:
            continue
    return [f"{count:>9,d}  {name}" for name, count in counts.most_common(limit)]


def gc_state() -> dict[str, Any]:
    """Collector health. `garbage` is the one that matters: anything in there is
    unreachable but uncollectable, which is a genuine permanent leak."""
    counts = gc.get_count()
    stats = gc.get_stats()
    return {
        "enabled": gc.isenabled(),
        "counts": counts,
        "collected": sum(s.get("collected", 0) for s in stats),
        "uncollectable": sum(s.get("uncollectable", 0) for s in stats),
        "garbage": len(gc.garbage),
    }


def _container_len(value: Any) -> int | None:
    if isinstance(value, (str, bytes, bytearray)):
        return None
    if isinstance(value, (dict, list, set, frozenset, tuple, Counter)):
        try:
            return len(value)
        except Exception:
            return None
    # deque, OrderedDict, defaultdict and anything else sized-but-not-builtin.
    if hasattr(value, "__len__") and not callable(value):
        try:
            return len(value)
        except Exception:
            return None
    return None


def registry_sizes(bot: Any, minimum: int = _REGISTRY_MIN) -> list[str]:
    """Every in-memory container this bot holds, biggest first.

    Two sweeps, because leaks live in both places: cog instance attributes
    (`self._cooldowns`, `self._ban_cache`) and module-level globals in the
    project's own modules (`_spam_tracker`, `_feeds`, `_autogen_locks`).

    Generic rather than a hand-maintained list on purpose — a registry added
    next month is exactly the one that will leak, and a list nobody remembers to
    update would not include it.
    """
    rows: list[tuple[int, str]] = []

    for name, cog in getattr(bot, "cogs", {}).items():
        for attr, value in vars(cog).items():
            size = _container_len(value)
            if size is not None and size >= minimum:
                rows.append((size, f"{name}.{attr}"))

    for mod_name, module in list(sys.modules.items()):
        if not (mod_name.startswith("cogs.") or mod_name.startswith("utils.")):
            continue
        if module is None:
            continue
        for attr, value in list(vars(module).items()):
            if attr.startswith("__"):
                continue
            size = _container_len(value)
            if size is not None and size >= minimum:
                rows.append((size, f"{mod_name}:{attr}"))

    rows.sort(reverse=True)
    return [f"{size:>9,d}  {label}" for size, label in rows]


def discord_caches(bot: Any) -> list[str]:
    """Sizes of discord.py's own caches.

    Worth its own section because these are the biggest structures in a bot
    this size and none of them belong to us: the member cache scales with every
    guild joined, and the persistent-view store is the one that genuinely leaks
    if a cog registers a view per message and never stops it.
    """
    rows: list[str] = []
    state = getattr(bot, "_connection", None)

    def add(label: str, value: Iterable | None) -> None:
        if value is None:
            return
        try:
            rows.append(f"{len(value):>9,d}  {label}")
        except Exception:
            pass

    add("guilds", getattr(bot, "guilds", None))
    add("users (global cache)", getattr(bot, "users", None))
    add("private channels", getattr(bot, "private_channels", None))
    if state is not None:
        add("messages (bounded by max_messages)", getattr(state, "_messages", None))
        add("emojis", getattr(state, "_emojis", None))
        add("stickers", getattr(state, "_stickers", None))
        store = getattr(state, "_view_store", None)
        if store is not None:
            add("persistent view items", getattr(store, "_views", None))
            add("synced message views", getattr(store, "_synced_message_views", None))
            add("modals", getattr(store, "_modals", None))
            add("dynamic items", getattr(store, "_dynamic_items", None))

    members = 0
    for guild in getattr(bot, "guilds", []) or []:
        try:
            members += len(guild._members)
        except Exception:
            continue
    rows.append(f"{members:>9,d}  members (summed across guilds)")

    rows.sort(key=lambda r: int(r.split()[0].replace(",", "")), reverse=True)
    return rows


def thread_count() -> int:
    import threading

    return threading.active_count()


# ── the summary that decides which of the three problems you have ────────────
def overview(bot: Any) -> dict[str, Any]:
    rss = rss_bytes()
    current, peak = traced_bytes()
    verdict = _verdict(rss, current)
    return {
        "rss": rss,
        "traced_current": current,
        "traced_peak": peak,
        "tracing": tracemalloc.is_tracing(),
        "baseline_age": baseline_age(),
        "threads": thread_count(),
        "gc": gc_state(),
        "verdict": verdict,
    }


def _verdict(rss: int | None, traced: int) -> str:
    """Name the likely category rather than leaving two numbers to interpret.

    The ratio is the whole diagnostic. Python's heap being a small fraction of
    RSS is normal at these sizes (the interpreter, loaded extensions and the
    allocator's own overhead are real), so the threshold is set where the gap
    stops being explicable that way.
    """
    if not tracemalloc.is_tracing():
        return (
            "Tracing is off — run `!mem trace`, leave it a few hours, then "
            "`!mem diff`. Growth between two points is what identifies a leak; "
            "a single snapshot mostly shows legitimately-large structures."
        )
    if rss is None:
        return "RSS unavailable on this platform — compare `!mem diff` over time."
    if traced <= 0:
        return "No traced allocations yet."
    ratio = rss / traced
    if ratio < 3:
        return (
            "Python's own heap accounts for most of RSS — a growing object "
            "graph. `!mem diff` will name the line."
        )
    if ratio < 6:
        return (
            "Mixed: Python's heap is a minority of RSS. Check `!mem diff` for "
            "the Python half, and whether a C-level holder (Pillow, yt-dlp, "
            "SQLite page cache) explains the rest."
        )
    return (
        "Most of RSS is *outside* Python's heap. That is either a C-level "
        "holder (Pillow buffers, yt-dlp, aiohttp connectors) or glibc arena "
        "fragmentation from the executor threads — if `!mem diff` stays flat "
        "while RSS climbs, it is fragmentation: set MALLOC_ARENA_MAX=2 in the "
        "environment rather than changing code."
    )
