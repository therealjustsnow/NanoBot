"""
tests/test_globalxp.py
The account-wide level system (utils/globalxp.py + utils/db/identity.py).

The contract these guard is the one the design turns on: global XP is awarded
per *normalized action* on a *hard-coded* curve, so no server can make it move
faster or slower than any other.
"""

import aiosqlite
import pytest

import utils.db as db
from utils import globalxp

USER, OTHER = 7001, 7002


@pytest.fixture
async def identity_db(monkeypatch):
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    monkeypatch.setattr(db, "_db", conn)
    await db._ensure_identity_tables()
    yield conn
    await conn.close()


# ── The curve (pure) ──────────────────────────────────────────────────────────
def test_curve_starts_at_zero_and_is_monotonic():
    assert globalxp.cum_xp_for_level(0) == 0
    levels = [globalxp.cum_xp_for_level(n) for n in range(0, 60)]
    assert levels == sorted(levels)
    assert len(set(levels)) == len(levels)  # every level costs something


def test_level_from_xp_is_the_inverse_of_the_curve():
    for level in range(0, 120):
        need = globalxp.cum_xp_for_level(level)
        assert globalxp.level_from_xp(need) == level
        # One XP short is still the previous level.
        if level:
            assert globalxp.level_from_xp(need - 1) == level - 1


def test_level_progress_reports_position_inside_the_level():
    xp = globalxp.cum_xp_for_level(10) + 40
    level, into, need = globalxp.level_progress(xp)
    assert level == 10
    assert into == 40
    assert need == globalxp.cum_xp_for_level(11) - globalxp.cum_xp_for_level(10)


def test_negative_or_zero_xp_is_level_zero():
    assert globalxp.level_from_xp(0) == 0
    assert globalxp.level_from_xp(-500) == 0


def test_the_curve_is_hard_coded_not_configurable():
    """There is deliberately no per-guild knob: the module exposes constants and
    pure functions only, and nothing reads a config table."""
    source = (globalxp.__doc__ or "") + str(globalxp.XP_AWARDS)
    assert "hard-coded" in source
    assert not hasattr(globalxp, "set_config")
    assert all(isinstance(v, int) and v > 0 for v in globalxp.XP_AWARDS.values())


# ── Awards ────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_award_grants_the_actions_flat_value(identity_db):
    res = await globalxp.award(USER, "daily")
    assert res["awarded"] == globalxp.XP_AWARDS["daily"]
    assert res["xp"] == globalxp.XP_AWARDS["daily"]
    assert await db.get_global_xp(USER) == globalxp.XP_AWARDS["daily"]


@pytest.mark.asyncio
async def test_award_reports_a_level_up_exactly_once(identity_db):
    """The level-up flag is derived from the XP *before* the award, so two
    awards can't both claim the same crossing."""
    need = globalxp.cum_xp_for_level(1)
    await db.add_global_xp(USER, need - 1)
    first = await globalxp.award(USER, "achievement")
    assert first["levelled_up"] is True
    assert first["level"] == globalxp.level_from_xp(await db.get_global_xp(USER))
    second = await globalxp.award(USER, "message")
    assert second["levelled_up"] is False


@pytest.mark.asyncio
async def test_unknown_action_awards_nothing(identity_db):
    res = await globalxp.award(USER, "not_a_real_action")
    assert res["awarded"] == 0
    assert await db.get_global_xp(USER) == 0


@pytest.mark.asyncio
async def test_message_xp_is_rate_limited_per_account(identity_db):
    """A member chatting in two servers inside the window earns once — the
    cooldown belongs to the account, not the guild."""
    first = await globalxp.award_message(USER, now=1_000.0)
    assert first["awarded"] == globalxp.XP_AWARDS["message"]
    again = await globalxp.award_message(USER, now=1_000.0 + 5)
    assert again["awarded"] == 0
    later = await globalxp.award_message(USER, now=1_000.0 + globalxp.MESSAGE_COOLDOWN)
    assert later["awarded"] == globalxp.XP_AWARDS["message"]


@pytest.mark.asyncio
async def test_xp_is_per_user_not_per_guild(identity_db):
    await globalxp.award(USER, "daily")
    assert await db.get_global_xp(OTHER) == 0
    rank = await db.get_global_xp_rank(USER)
    assert rank == (1, globalxp.XP_AWARDS["daily"])
    assert await db.get_global_xp_rank(OTHER) is None


@pytest.mark.asyncio
async def test_leaderboard_ranks_accounts(identity_db):
    await db.add_global_xp(USER, 500)
    await db.add_global_xp(OTHER, 900)
    board = await db.get_global_xp_leaderboard()
    assert [r["user_id"] for r in board] == [OTHER, USER]
