"""
Tests for the progression accessors in utils/db/ — in-memory SQLite.
"""

import aiosqlite
import pytest

import utils.db as db


@pytest.fixture(autouse=True)
async def database(monkeypatch):
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA journal_mode=WAL")
    monkeypatch.setattr(db, "_db", conn)
    await db._ensure_progression_tables()
    yield conn
    await conn.close()


G = 777
A, B = 1, 2


# ── Achievements ─────────────────────────────────────────────────────────────────
async def test_no_achievements_earned_by_default():
    assert await db.get_earned_achievements(G, A) == {}


async def test_award_achievement_first_time_succeeds():
    awarded = await db.try_award_achievement(G, A, "fish_caught_10", earned_at=1000.0)
    assert awarded is True
    earned = await db.get_earned_achievements(G, A)
    assert earned == {"fish_caught_10": 1000.0}


async def test_award_achievement_is_one_time_only():
    """Re-evaluating (calling the award again) after it's already earned must
    NOT re-insert / re-signal a fresh award — this is the guard against
    double-paying a reward on re-evaluation."""
    first = await db.try_award_achievement(G, A, "fish_caught_10", earned_at=1000.0)
    second = await db.try_award_achievement(G, A, "fish_caught_10", earned_at=2000.0)
    assert first is True
    assert second is False
    earned = await db.get_earned_achievements(G, A)
    # The original earned_at timestamp is untouched by the second (ignored) call.
    assert earned == {"fish_caught_10": 1000.0}


async def test_achievements_are_scoped_per_guild_and_user():
    await db.try_award_achievement(G, A, "fish_caught_10")
    assert await db.get_earned_achievements(G, B) == {}
    assert await db.get_earned_achievements(999, A) == {}


# ── Weekly (period) objectives ────────────────────────────────────────────────────
async def test_get_or_create_objective_creates_with_given_baseline():
    row = await db.get_or_create_objective(G, A, "2026-W30", "weekly_fish_catch", 20, 5)
    assert row == {
        "obj_key": "weekly_fish_catch",
        "target": 20,
        "baseline": 5,
        "claimed": False,
    }


async def test_get_or_create_objective_is_idempotent_baseline_sticks():
    """A second call with a different baseline/target must NOT overwrite the
    row already created this period — the baseline is snapshotted once."""
    first = await db.get_or_create_objective(
        G, A, "2026-W30", "weekly_fish_catch", 20, 5
    )
    second = await db.get_or_create_objective(
        G, A, "2026-W30", "weekly_fish_catch", 999, 999
    )
    assert second == first


async def test_get_period_objectives_lists_rows_for_that_period():
    await db.get_or_create_objective(G, A, "2026-W30", "weekly_fish_catch", 20, 5)
    await db.get_or_create_objective(G, A, "2026-W30", "weekly_work", 5, 0)
    await db.get_or_create_objective(G, A, "2026-W31", "weekly_work", 5, 0)
    rows = await db.get_period_objectives(G, A, "2026-W30")
    assert {r["obj_key"] for r in rows} == {"weekly_fish_catch", "weekly_work"}


async def test_try_claim_objective_fails_when_not_yet_complete():
    await db.get_or_create_objective(G, A, "2026-W30", "weekly_fish_catch", 20, 5)
    # current=10, baseline=5 -> delta 5 < target 20
    claimed = await db.try_claim_objective(G, A, "2026-W30", "weekly_fish_catch", 10)
    assert claimed is False
    rows = await db.get_period_objectives(G, A, "2026-W30")
    assert rows[0]["claimed"] is False


async def test_try_claim_objective_succeeds_when_complete():
    await db.get_or_create_objective(G, A, "2026-W30", "weekly_fish_catch", 20, 5)
    # current=25, baseline=5 -> delta 20 >= target 20
    claimed = await db.try_claim_objective(G, A, "2026-W30", "weekly_fish_catch", 25)
    assert claimed is True
    rows = await db.get_period_objectives(G, A, "2026-W30")
    assert rows[0]["claimed"] is True


async def test_try_claim_objective_is_one_time_only():
    await db.get_or_create_objective(G, A, "2026-W30", "weekly_fish_catch", 20, 5)
    first = await db.try_claim_objective(G, A, "2026-W30", "weekly_fish_catch", 25)
    second = await db.try_claim_objective(G, A, "2026-W30", "weekly_fish_catch", 25)
    assert first is True
    assert second is False


async def test_try_claim_objective_missing_row_is_a_noop():
    claimed = await db.try_claim_objective(G, A, "2026-W30", "nonexistent", 100)
    assert claimed is False


# ── Prestige / profile ────────────────────────────────────────────────────────────
async def test_get_progression_defaults():
    assert await db.get_progression(G, A) == {"prestige": 0, "selected_title": ""}


async def test_try_advance_prestige_from_zero_succeeds():
    advanced = await db.try_advance_prestige(G, A, 1, expected=0)
    assert advanced is True
    assert (await db.get_progression(G, A))["prestige"] == 1


async def test_try_advance_prestige_cas_blocks_stale_expected():
    """The CAS pattern: a second confirm that read the OLD rank must fail once
    someone else already advanced it — the caller is responsible for
    refunding the coins it already debited."""
    await db.try_advance_prestige(G, A, 1, expected=0)
    # A racing confirm that also computed expected=0 (stale read) must lose.
    second = await db.try_advance_prestige(G, A, 1, expected=0)
    assert second is False
    assert (await db.get_progression(G, A))["prestige"] == 1


async def test_try_advance_prestige_sequential_ranks():
    assert await db.try_advance_prestige(G, A, 1, expected=0) is True
    assert await db.try_advance_prestige(G, A, 2, expected=1) is True
    assert (await db.get_progression(G, A))["prestige"] == 2


async def test_prestige_is_scoped_per_guild_and_user():
    await db.try_advance_prestige(G, A, 1, expected=0)
    assert (await db.get_progression(G, B))["prestige"] == 0
    assert (await db.get_progression(999, A))["prestige"] == 0


async def test_set_selected_title_roundtrip():
    await db.set_selected_title(G, A, "Master Angler")
    assert (await db.get_progression(G, A))["selected_title"] == "Master Angler"
    await db.set_selected_title(G, A, "")
    assert (await db.get_progression(G, A))["selected_title"] == ""


async def test_set_selected_title_before_any_prestige_row_exists():
    # No progression row yet for B — set_selected_title must still work (upsert).
    await db.set_selected_title(G, B, "Tycoon")
    row = await db.get_progression(G, B)
    assert row == {"prestige": 0, "selected_title": "Tycoon"}
