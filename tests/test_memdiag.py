"""tests/test_memdiag.py — the live memory diagnostics in utils/memdiag.py.

A diagnostic that lies is worse than no diagnostic: it sends whoever is chasing
a leak at 3am after the wrong subsystem. So the tests here are less about the
formatting than about the two claims the tool makes — that `diff()` reports
*growth* rather than size, and that `registry_sizes` actually finds a container
the bot is holding, wherever it lives.

Everything is stdlib-only and Discord-free, matching the module.
"""

import gc
import tracemalloc

import pytest

from utils import memdiag


@pytest.fixture(autouse=True)
def _tracing_off():
    """Never leave tracing armed — it costs every later test 15-30% CPU."""
    yield
    memdiag.stop()


# ── formatting ───────────────────────────────────────────────────────────────
def test_fmt_bytes_scales_and_handles_none():
    assert memdiag.fmt_bytes(None) == "n/a"
    assert memdiag.fmt_bytes(512) == "512 B"
    assert memdiag.fmt_bytes(2048) == "2.0 KB"
    assert memdiag.fmt_bytes(5 * 1024**2) == "5.0 MB"
    assert memdiag.fmt_bytes(3 * 1024**3) == "3.0 GB"


def test_fmt_bytes_keeps_the_sign_on_a_shrink():
    """A negative delta has to read as one — `diff` filters these out, but
    `overview`'s ratio line and any future caller must not show -12 MB as 12."""
    assert memdiag.fmt_bytes(-2048).startswith("-")


# ── process numbers ──────────────────────────────────────────────────────────
def test_rss_is_a_plausible_number_or_none():
    rss = memdiag.rss_bytes()
    if rss is None:  # non-Linux, no /proc
        return
    # A Python process running pytest is never under a megabyte, and this test
    # would rather fail loudly than have the overview quietly report garbage.
    assert rss > 1024**2


def test_traced_bytes_is_zero_until_armed():
    assert not memdiag.is_tracing()
    assert memdiag.traced_bytes() == (0, 0)


def test_start_arms_tracing_and_stop_disarms_it():
    memdiag.start()
    assert memdiag.is_tracing()
    assert tracemalloc.is_tracing()
    current, _peak = memdiag.traced_bytes()
    assert current > 0
    memdiag.stop()
    assert not memdiag.is_tracing()


def test_start_also_stores_a_baseline():
    """The two are one action on purpose: a baseline set later measures growth
    from later, which is the opposite of what the tool is for."""
    memdiag.start()
    age = memdiag.baseline_age()
    assert age is not None and age >= 0


def test_stop_drops_the_baseline():
    memdiag.start()
    memdiag.stop()
    assert memdiag.baseline_age() is None
    assert memdiag.diff() == []


# ── the leak-finder ──────────────────────────────────────────────────────────
def test_diff_reports_growth_since_the_baseline():
    memdiag.start()
    hoard = [{"leaked": i} for i in range(20_000)]
    lines = memdiag.diff(10)
    assert lines, "allocating 20k dicts must show up as growth"
    # The allocating line is in this file, which is what makes the output
    # actionable rather than a pointer into CPython internals.
    assert any(__file__.rsplit("/", 1)[-1] in line for line in lines)
    assert len(hoard) == 20_000  # keep it alive until after the snapshot


def test_diff_is_empty_without_tracing():
    assert not memdiag.is_tracing()
    assert memdiag.diff() == []
    assert memdiag.top() == []
    assert memdiag.traceback_for() == []


def test_diff_only_reports_things_that_grew():
    """Shrinking lines are noise when the question is what is leaking."""
    memdiag.start()
    hoard = [object() for _ in range(10_000)]
    del hoard
    gc.collect()
    memdiag.set_baseline()
    grown = [{"x": i} for i in range(10_000)]
    lines = memdiag.diff(20)
    # Every rendered delta is positive: no line starts with a minus.
    assert all(not line.strip().startswith("-") for line in lines)
    assert len(grown) == 10_000


def test_top_reports_absolute_sizes_without_a_growth_sign():
    """A snapshot statistic is a total, not a delta — rendering `+149739 obj`
    would claim a measurement nobody took."""
    memdiag.start()
    hoard = [{"x": i} for i in range(5_000)]
    lines = memdiag.top(5)
    assert lines
    assert all("+" not in line.split("obj")[0] for line in lines)
    assert len(hoard) == 5_000


def test_traceback_for_names_the_caller_not_just_the_line():
    memdiag.start()

    def _inner_allocator():
        return [{"deep": i} for i in range(20_000)]

    hoard = _inner_allocator()
    frames = memdiag.traceback_for(0)
    assert frames, "the biggest growth must have a stack behind it"
    assert "objects:" in frames[0]
    assert len(frames) > 1, "a stack of one frame is what `diff` already gave you"
    assert len(hoard) == 20_000


def test_traceback_for_out_of_range_is_empty_not_an_error():
    memdiag.start()
    assert memdiag.traceback_for(10_000) == []


# ── object-graph views ───────────────────────────────────────────────────────
def test_gc_histogram_works_without_tracing():
    """The one view that can be run the moment a leak is noticed, before
    anything is restarted and the evidence is gone."""
    assert not memdiag.is_tracing()
    lines = memdiag.gc_histogram(5)
    assert len(lines) == 5
    assert any("function" in line or "dict" in line for line in lines)


def test_gc_state_reports_the_fields_the_overview_prints():
    state = memdiag.gc_state()
    assert set(state) >= {"enabled", "counts", "uncollectable", "garbage"}
    assert len(state["counts"]) == 3


# ── registry sweep ───────────────────────────────────────────────────────────
class _FakeCog:
    def __init__(self):
        self._leaky = {i: i for i in range(500)}
        self._tiny = {"a": 1}
        self._name = "not a container"
        self._blob = b"x" * 10_000


class _FakeBot:
    def __init__(self):
        self.cogs = {"Fake": _FakeCog()}
        self.guilds = []
        self.users = []
        self.private_channels = []
        self._connection = None


def test_registry_sizes_finds_a_cog_attribute():
    rows = memdiag.registry_sizes(_FakeBot())
    assert any("Fake._leaky" in row for row in rows)


def test_registry_sizes_skips_noise():
    """Small containers bury the one that matters, and a str/bytes is not a
    registry however long it is."""
    rows = memdiag.registry_sizes(_FakeBot())
    joined = "\n".join(rows)
    assert "Fake._tiny" not in joined
    assert "Fake._name" not in joined
    assert "Fake._blob" not in joined


def test_registry_sizes_is_sorted_biggest_first():
    rows = memdiag.registry_sizes(_FakeBot(), minimum=1)
    sizes = [int(row.split()[0].replace(",", "")) for row in rows]
    assert sizes == sorted(sizes, reverse=True)


def test_registry_sizes_sweeps_project_module_globals():
    """Half the leaks in this codebase live in module-level registries
    (_spam_tracker, _feeds, _autogen_locks), not on a cog."""
    import utils.items

    rows = memdiag.registry_sizes(_FakeBot(), minimum=1)
    assert any(row.endswith("utils.items:ITEMS") for row in rows)


def test_registry_sizes_survives_an_attribute_that_raises_on_len():
    class _Hostile:
        def __len__(self):
            raise RuntimeError("no")

    class _Cog:
        def __init__(self):
            self.bad = _Hostile()

    bot = _FakeBot()
    bot.cogs = {"Bad": _Cog()}
    memdiag.registry_sizes(bot)  # must not raise


# ── discord.py caches ────────────────────────────────────────────────────────
def test_discord_caches_handles_a_bot_with_no_connection():
    rows = memdiag.discord_caches(_FakeBot())
    assert any("members" in row for row in rows)


def test_discord_caches_reads_the_view_store_when_present():
    """The view store is the discord.py cache that genuinely leaks — a
    persistent view registered per message and never stopped stays forever."""

    class _Store:
        _views = {i: i for i in range(7)}
        _synced_message_views = {}
        _modals = {}

    class _State:
        _messages = [None] * 3
        _view_store = _Store()

    bot = _FakeBot()
    bot._connection = _State()
    joined = "\n".join(memdiag.discord_caches(bot))
    assert "persistent view items" in joined
    assert "messages" in joined


# ── the verdict ──────────────────────────────────────────────────────────────
def test_overview_tells_you_to_arm_tracing_when_it_is_off():
    data = memdiag.overview(_FakeBot())
    assert not data["tracing"]
    assert "!mem trace" in data["verdict"]


def test_overview_reports_the_fields_the_command_prints():
    memdiag.start()
    data = memdiag.overview(_FakeBot())
    assert set(data) >= {
        "rss",
        "traced_current",
        "traced_peak",
        "tracing",
        "threads",
        "gc",
        "verdict",
    }
    assert data["threads"] >= 1


@pytest.mark.parametrize(
    "rss, traced, expected",
    [
        (300 * 1024**2, 200 * 1024**2, "object graph"),
        (300 * 1024**2, 70 * 1024**2, "Mixed"),
        (300 * 1024**2, 10 * 1024**2, "outside"),
    ],
)
def test_verdict_names_the_category_from_the_ratio(rss, traced, expected):
    """The RSS-to-heap ratio is the whole diagnostic: it separates a Python
    leak from a C-level holder from allocator fragmentation, and each has a
    completely different fix."""
    memdiag.start()
    assert expected in memdiag._verdict(rss, traced)


def test_fragmentation_verdict_points_at_the_env_var_not_a_code_change():
    memdiag.start()
    verdict = memdiag._verdict(400 * 1024**2, 8 * 1024**2)
    assert "MALLOC_ARENA_MAX" in verdict
