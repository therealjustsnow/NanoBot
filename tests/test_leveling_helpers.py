"""
Tests for the pure level-math helpers in cogs/leveling.py (no Discord deps).
"""

from cogs.leveling import (
    _COOLDOWN_MAX_AGE,
    _COOLDOWN_PRUNE_INTERVAL,
    Leveling,
    level_for_xp,
    level_progress,
    render_bar,
    xp_for_level,
    xp_to_advance,
)


def test_xp_to_advance_curve():
    # Mee6 curve: 5n^2 + 50n + 100
    assert xp_to_advance(0) == 100
    assert xp_to_advance(1) == 155
    assert xp_to_advance(2) == 220


def test_xp_for_level_is_cumulative():
    assert xp_for_level(0) == 0
    assert xp_for_level(1) == 100
    assert xp_for_level(2) == 100 + 155
    assert xp_for_level(3) == 100 + 155 + 220


def test_level_for_xp_boundaries():
    assert level_for_xp(0) == 0
    assert level_for_xp(99) == 0
    assert level_for_xp(100) == 1  # exactly enough for level 1
    assert level_for_xp(254) == 1
    assert level_for_xp(255) == 2  # 100 + 155


def test_level_for_xp_round_trips_with_xp_for_level():
    for lvl in range(0, 30):
        floor = xp_for_level(lvl)
        assert level_for_xp(floor) == lvl
        if lvl > 0:
            assert level_for_xp(floor - 1) == lvl - 1


def test_level_progress_decomposition():
    xp = xp_for_level(3) + 40  # 40 XP into level 3
    level, into, span = level_progress(xp)
    assert level == 3
    assert into == 40
    assert span == xp_to_advance(3)


def test_render_bar_fill():
    assert render_bar(0, 100, width=10) == "░" * 10
    assert render_bar(100, 100, width=10) == "█" * 10
    assert render_bar(50, 100, width=10) == "█" * 5 + "░" * 5


def test_render_bar_zero_span_is_full():
    # A zero/negative span shouldn't divide-by-zero; treat as complete.
    assert render_bar(0, 0, width=8) == "█" * 8


# ── in-memory XP cooldown pruning ────────────────────────────────────────────
# The XP cooldown map is keyed per *chatter*, not per guild: without a sweep it
# gains an entry for everyone who has ever spoken and never loses one, which is
# a leak that grows for as long as the process lives.


def _leveling_cog():
    """A Leveling instance without a bot — the prune is pure dict work."""
    return Leveling.__new__(Leveling)


def _armed(now: float, entries: dict):
    cog = _leveling_cog()
    cog._cooldowns = dict(entries)
    cog._last_prune = now - _COOLDOWN_PRUNE_INTERVAL - 1  # due for a sweep
    return cog


def test_prune_drops_stamps_too_old_to_suppress_anything():
    now = 1_000_000.0
    cog = _armed(
        now,
        {
            (1, 1): now - _COOLDOWN_MAX_AGE - 1,  # stale
            (1, 2): now - 30,  # fresh
        },
    )
    cog._prune_cooldowns(now)
    assert (1, 1) not in cog._cooldowns
    assert (1, 2) in cog._cooldowns


def test_prune_is_rate_limited_so_a_busy_guild_does_not_sweep_per_message():
    now = 1_000_000.0
    cog = _leveling_cog()
    cog._cooldowns = {(1, 1): now - _COOLDOWN_MAX_AGE - 1}
    cog._last_prune = now  # just swept
    cog._prune_cooldowns(now)
    assert (1, 1) in cog._cooldowns, "a sweep inside the interval must be a no-op"


def test_prune_advances_its_own_clock():
    now = 1_000_000.0
    cog = _armed(now, {})
    cog._prune_cooldowns(now)
    assert cog._last_prune == now


def test_prune_keeps_a_stamp_that_is_still_inside_a_normal_cooldown():
    """The whole point of the map: a member who spoke a minute ago must still
    be suppressed after a sweep."""
    now = 1_000_000.0
    cog = _armed(now, {(7, 7): now - 60})
    cog._prune_cooldowns(now)
    assert cog._cooldowns[(7, 7)] == now - 60


def test_prune_empties_a_map_of_nothing_but_stale_entries():
    now = 1_000_000.0
    old = now - _COOLDOWN_MAX_AGE - 1
    cog = _armed(now, {(g, u): old for g in range(5) for u in range(20)})
    cog._prune_cooldowns(now)
    assert cog._cooldowns == {}
