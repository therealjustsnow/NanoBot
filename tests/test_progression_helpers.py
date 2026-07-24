"""
Tests for the pure progression helpers/registries in cogs/progression/ (no
Discord, no DB).
"""

import re

from cogs.progression.constants import PRESTIGE_MAX, PRESTIGE_WEEKLY_BONUS_PER_RANK
from cogs.progression.definitions import ACHIEVEMENTS, WEEKLY_POOL
from cogs.progression.helpers import (
    can_prestige,
    earned_titles,
    objective_complete,
    objective_progress,
    period_key,
    pick_weekly_objectives,
    prestige_bonus_multiplier,
    prestige_requirement,
    prestige_title,
    render_bar,
    total_points,
)
from cogs.progression.stats import STAT_PROVIDERS


# ── Registry well-formedness: achievements ──────────────────────────────────────
def test_achievement_keys_are_unique():
    keys = [a.key for a in ACHIEVEMENTS]
    assert len(keys) == len(set(keys))


def test_at_least_thirty_achievements():
    assert len(ACHIEVEMENTS) >= 30


def test_achievement_stats_are_valid_provider_keys():
    for a in ACHIEVEMENTS:
        assert a.stat in STAT_PROVIDERS, f"{a.key}: unknown stat {a.stat!r}"


def test_achievement_thresholds_and_points_are_positive():
    for a in ACHIEVEMENTS:
        assert a.threshold > 0, a.key
        assert a.points > 0, a.key


def test_achievement_rewards_only_use_known_keys():
    for a in ACHIEVEMENTS:
        assert set(a.reward.keys()) <= {"coins", "item", "qty", "title"}, a.key
        if "coins" in a.reward:
            assert a.reward["coins"] > 0, a.key
        if "item" in a.reward:
            assert isinstance(a.reward["item"], str) and a.reward["item"]
        if "title" in a.reward:
            assert isinstance(a.reward["title"], str) and a.reward["title"]


def test_achievement_categories_cover_all_four_pillars():
    categories = {a.category for a in ACHIEVEMENTS}
    assert any("Fishing" in c for c in categories)
    assert any("Wealth" in c for c in categories)
    assert any("Casino" in c for c in categories)
    assert any("Activities" in c for c in categories)


def test_a_tiered_achievement_progression_increases_with_threshold():
    """Same-stat achievements should have increasing points as the threshold rises
    (higher bar = bigger prize)."""
    by_stat: dict[str, list] = {}
    for a in ACHIEVEMENTS:
        by_stat.setdefault(a.stat, []).append(a)
    for stat, defs in by_stat.items():
        defs = sorted(defs, key=lambda a: a.threshold)
        points = [a.points for a in defs]
        assert points == sorted(points), f"{stat}: points don't increase with threshold"


# ── Registry well-formedness: weekly objective pool ─────────────────────────────
def test_weekly_pool_keys_are_unique():
    keys = [w.key for w in WEEKLY_POOL]
    assert len(keys) == len(set(keys))


def test_weekly_pool_has_enough_variety_for_three_distinct_picks():
    assert len(WEEKLY_POOL) >= 3


def test_weekly_pool_stats_are_valid_provider_keys():
    for w in WEEKLY_POOL:
        assert w.stat in STAT_PROVIDERS, f"{w.key}: unknown stat {w.stat!r}"


def test_weekly_pool_targets_coins_points_are_positive():
    for w in WEEKLY_POOL:
        assert w.target > 0, w.key
        assert w.coins > 0, w.key
        assert w.points > 0, w.key


# ── Period keys ──────────────────────────────────────────────────────────────────
def test_period_key_format():
    assert re.fullmatch(r"\d{4}-W\d{2}", period_key(1_800_000_000))


def test_period_key_deterministic_for_same_timestamp():
    assert period_key(1_800_000_000) == period_key(1_800_000_000)


def test_period_key_changes_across_a_week():
    a = period_key(1_800_000_000)
    b = period_key(1_800_000_000 + 8 * 86400)
    assert a != b


# ── Deterministic weekly picks ───────────────────────────────────────────────────
def test_pick_weekly_objectives_is_deterministic():
    a = pick_weekly_objectives(2, "2026-W30", WEEKLY_POOL, 3)
    b = pick_weekly_objectives(2, "2026-W30", WEEKLY_POOL, 3)
    assert [d.key for d in a] == [d.key for d in b]


def test_pick_weekly_objectives_returns_requested_count_and_distinct_entries():
    picks = pick_weekly_objectives(2, "2026-W30", WEEKLY_POOL, 3)
    assert len(picks) == 3
    assert len({d.key for d in picks}) == 3
    assert all(p in WEEKLY_POOL for p in picks)


def test_pick_weekly_objectives_varies_by_user():
    a = pick_weekly_objectives(2, "2026-W30", WEEKLY_POOL, 3)
    b = pick_weekly_objectives(999, "2026-W30", WEEKLY_POOL, 3)
    # Not guaranteed different, but the two users' seeds differ, so at least
    # one of many pool-sized draws should show variation across a handful of
    # different "users" (guards against an accidental constant seed).
    variants = {
        tuple(d.key for d in pick_weekly_objectives(u, "2026-W30", WEEKLY_POOL, 3))
        for u in range(20)
    }
    assert len(variants) > 1


def test_pick_weekly_objectives_varies_by_period():
    a = pick_weekly_objectives(2, "2026-W30", WEEKLY_POOL, 3)
    b = pick_weekly_objectives(2, "2026-W31", WEEKLY_POOL, 3)
    variants = {
        tuple(
            d.key for d in pick_weekly_objectives(2, f"2026-W{w:02d}", WEEKLY_POOL, 3)
        )
        for w in range(1, 20)
    }
    assert len(variants) > 1


# ── Objective progress math ──────────────────────────────────────────────────────
def test_objective_progress_clamps_to_target():
    assert objective_progress(current_value=50, baseline=10, target=20) == 20


def test_objective_progress_clamps_negative_delta_to_zero():
    # A non-monotonic stat (e.g. balance) can net-decrease during the period.
    assert objective_progress(current_value=5, baseline=10, target=20) == 0


def test_objective_progress_normal_case():
    assert objective_progress(current_value=15, baseline=10, target=20) == 5


def test_objective_complete_true_at_or_above_target():
    assert objective_complete(current_value=30, baseline=10, target=20) is True
    assert objective_complete(current_value=29, baseline=10, target=20) is False


# ── Achievement scoring / titles ──────────────────────────────────────────────────
def test_total_points_sums_only_known_earned_keys():
    achievements = {a.key: a for a in ACHIEVEMENTS[:3]}
    earned = [ACHIEVEMENTS[0].key, ACHIEVEMENTS[1].key, "unknown_key"]
    expected = ACHIEVEMENTS[0].points + ACHIEVEMENTS[1].points
    assert total_points(earned, achievements) == expected


def test_earned_titles_sorted_by_points_descending_and_deduped():
    titled = [a for a in ACHIEVEMENTS if a.reward.get("title")]
    assert len(titled) >= 2
    achievements = {a.key: a for a in titled}
    earned = [a.key for a in titled]
    titles = earned_titles(earned, achievements)
    points = [pts for _t, pts in titles]
    assert points == sorted(points, reverse=True)
    assert len(titles) == len({t for t, _p in titles})


def test_earned_titles_empty_when_nothing_earned():
    achievements = {a.key: a for a in ACHIEVEMENTS}
    assert earned_titles([], achievements) == []


# ── Prestige ─────────────────────────────────────────────────────────────────────
def test_prestige_requirement_scales_with_rank():
    p0, c0 = prestige_requirement(0)
    p1, c1 = prestige_requirement(1)
    assert p1 > p0
    assert c1 > c0


def test_prestige_bonus_multiplier_zero_at_rank_zero():
    assert prestige_bonus_multiplier(0) == 1.0


def test_prestige_bonus_multiplier_five_percent_per_rank():
    assert prestige_bonus_multiplier(1) == 1.0 + PRESTIGE_WEEKLY_BONUS_PER_RANK
    assert prestige_bonus_multiplier(3) == 1.0 + PRESTIGE_WEEKLY_BONUS_PER_RANK * 3


def test_prestige_title_blank_at_rank_zero():
    assert prestige_title(0) == ""


def test_prestige_title_named_at_higher_ranks():
    assert "1" in prestige_title(1) or "I" in prestige_title(1)


def test_can_prestige_blocks_at_max_rank():
    ok, _reason = can_prestige(PRESTIGE_MAX, points=10**9, balance=10**9)
    assert ok is False


def test_can_prestige_blocks_on_insufficient_points():
    req_points, cost = prestige_requirement(0)
    ok, reason = can_prestige(0, points=req_points - 1, balance=cost)
    assert ok is False
    assert "points" in reason.lower()


def test_can_prestige_blocks_on_insufficient_coins():
    req_points, cost = prestige_requirement(0)
    ok, reason = can_prestige(0, points=req_points, balance=cost - 1)
    assert ok is False
    assert "coins" in reason.lower()


def test_can_prestige_allows_when_both_requirements_met():
    req_points, cost = prestige_requirement(0)
    ok, reason = can_prestige(0, points=req_points, balance=cost)
    assert ok is True
    assert reason == ""


# ── Display ──────────────────────────────────────────────────────────────────────
def test_render_bar_full_and_empty():
    assert render_bar(0, 10, width=10) == "░" * 10
    assert render_bar(10, 10, width=10) == "█" * 10


def test_render_bar_zero_span_is_full():
    assert render_bar(5, 0, width=8) == "█" * 8


def test_render_bar_partial():
    bar = render_bar(3, 12, width=12)
    assert bar.count("█") == 3
    assert bar.count("░") == 9
