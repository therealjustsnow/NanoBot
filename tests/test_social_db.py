"""
Tests for the social accessors in utils/db/social.py — in-memory SQLite.

Rep and cookies are account-level tallies: global, worth no coins, and so
neither a faucet nor a sink. What still has to be exact is the once-a-day rep
claim (a conditional UPDATE, like every other claim in the bot) and the
two-sided cookie write.
"""

import aiosqlite
import pytest

import utils.db as db
from utils.db.social import REP_COOLDOWN


@pytest.fixture(autouse=True)
async def database(monkeypatch):
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA journal_mode=WAL")
    monkeypatch.setattr(db, "_db", conn)
    await db._ensure_social_tables()
    yield conn
    await conn.close()


A, B, C = 1, 2, 3


async def test_defaults_for_an_account_that_has_never_played():
    assert await db.get_social(A) == {
        "rep": 0,
        "last_rep": 0.0,
        "cookies_sent": 0,
        "cookies_received": 0,
    }


# ── Rep ───────────────────────────────────────────────────────────────────────
async def test_rep_claim_is_once_a_day_and_atomic():
    now = 1_000_000.0
    assert await db.try_claim_rep(A, now) == 0

    # Second attempt the same instant loses — this is the double-tap guard.
    retry = await db.try_claim_rep(A, now)
    assert retry == REP_COOLDOWN

    # An hour later it's still blocked, and reports the real time left.
    assert await db.try_claim_rep(A, now + 3600) == REP_COOLDOWN - 3600
    # A day later it opens again.
    assert await db.try_claim_rep(A, now + REP_COOLDOWN) == 0


async def test_one_members_claim_does_not_block_anothers():
    now = 1_000_000.0
    assert await db.try_claim_rep(A, now) == 0
    assert await db.try_claim_rep(B, now) == 0


async def test_refund_reopens_the_claim():
    """A claim taken for an award that then failed must not cost a day."""
    now = 1_000_000.0
    assert await db.try_claim_rep(A, now) == 0
    await db.refund_rep(A)
    assert await db.try_claim_rep(A, now) == 0


async def test_rep_accumulates_on_the_receiver():
    assert await db.add_rep(B) == 1
    assert await db.add_rep(B) == 2
    assert await db.add_rep(B, 5) == 7
    assert (await db.get_social(B))["rep"] == 7
    assert (await db.get_social(A))["rep"] == 0  # the giver gains nothing

    # Never goes below zero, even if a correction overshoots.
    assert await db.add_rep(B, -100) == 0


async def test_giving_rep_does_not_touch_the_givers_own_total():
    now = 1_000_000.0
    await db.try_claim_rep(A, now)
    await db.add_rep(B)
    giver = await db.get_social(A)
    assert giver["rep"] == 0 and giver["last_rep"] == now


async def test_rep_rank_counts_everyone_ahead():
    await db.add_rep(A, 10)
    await db.add_rep(B, 5)
    await db.add_rep(C, 20)
    assert await db.get_rep_rank(C) == (1, 20)
    assert await db.get_rep_rank(A) == (2, 10)
    assert await db.get_rep_rank(B) == (3, 5)
    # Nobody with zero rep has a position at all.
    assert await db.get_rep_rank(999) is None


# ── Cookies ───────────────────────────────────────────────────────────────────
async def test_a_cookie_moves_both_halves():
    totals = await db.give_cookie(A, B)
    assert totals == {"sent": 1, "received": 1}
    assert await db.get_social(A) == {
        "rep": 0,
        "last_rep": 0.0,
        "cookies_sent": 1,
        "cookies_received": 0,
    }
    assert (await db.get_social(B))["cookies_received"] == 1
    assert (await db.get_social(B))["cookies_sent"] == 0


async def test_cookies_accumulate_in_both_directions():
    for _ in range(3):
        await db.give_cookie(A, B)
    await db.give_cookie(B, A)
    assert (await db.get_social(A))["cookies_sent"] == 3
    assert (await db.get_social(A))["cookies_received"] == 1
    assert (await db.get_social(B))["cookies_sent"] == 1
    assert (await db.get_social(B))["cookies_received"] == 3


async def test_cookie_rank_and_leaderboards():
    for _ in range(5):
        await db.give_cookie(A, B)
    await db.give_cookie(A, C)
    assert await db.get_cookie_rank(B) == (1, 5)
    assert await db.get_cookie_rank(C) == (2, 1)
    assert await db.get_cookie_rank(A) is None  # sent plenty, received none

    board = await db.social_leaderboard("cookies_received")
    assert [(r["user_id"], r["value"]) for r in board] == [(B, 5), (C, 1)]
    sent = await db.social_leaderboard("cookies_sent")
    assert [(r["user_id"], r["value"]) for r in sent] == [(A, 6)]


async def test_rep_and_cookies_share_a_row_without_colliding():
    """One table, two features — a cookie must not disturb a rep claim."""
    now = 1_000_000.0
    await db.try_claim_rep(A, now)
    await db.add_rep(A, 4)
    await db.give_cookie(A, B)
    await db.give_cookie(B, A)

    assert await db.get_social(A) == {
        "rep": 4,
        "last_rep": now,
        "cookies_sent": 1,
        "cookies_received": 1,
    }
    # The rep claim is still spent — the cookie writes didn't reset it.
    assert await db.try_claim_rep(A, now) == REP_COOLDOWN


async def test_leaderboard_column_is_whitelisted():
    """The column is substituted into SQL, so it can never come from input."""
    with pytest.raises(ValueError):
        await db.social_leaderboard("rep; DROP TABLE user_social")
