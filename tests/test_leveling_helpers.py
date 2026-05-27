"""
Tests for the pure level-math helpers in cogs/leveling.py (no Discord deps).
"""

from cogs.leveling import (
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
