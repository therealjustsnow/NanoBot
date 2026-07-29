"""
Tests for the activities accessors in utils/db/ — in-memory SQLite.
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
    await db._ensure_activities_tables()
    # /rob and /mine upgrade touch coins/items through the shared tables.
    await db._ensure_economy_tables()
    await db._ensure_items_tables()
    yield conn
    await conn.close()


G = 555
A, B = 1, 2


# ── Config ─────────────────────────────────────────────────────────────────────
async def test_config_defaults():
    cfg = await db.get_activities_config(G)
    assert cfg == {
        "work_enabled": True,
        "mine_enabled": True,
        "hunt_enabled": True,
        "explore_enabled": True,
        "rob_enabled": True,
    }


async def test_config_roundtrip_partial_update():
    await db.set_activities_config(G, rob_enabled=False)
    cfg = await db.get_activities_config(G)
    assert cfg["rob_enabled"] is False
    assert cfg["work_enabled"] is True  # untouched key keeps its default

    await db.set_activities_config(G, work_enabled=False)
    cfg = await db.get_activities_config(G)
    assert cfg["work_enabled"] is False
    assert cfg["rob_enabled"] is False  # earlier partial update stuck


async def test_cooldown_lengths_are_bot_wide_not_per_guild(database):
    """A cooldown claim is keyed by user, so its length can't be a server's to
    set — it lives in bot_settings, with no guild id anywhere near it."""
    await db._ensure_settings_tables()

    assert await db.get_activity_cooldowns() == {}  # nothing set: all defaults
    await db.set_activity_cooldown("mine", 900)
    assert await db.get_activity_cooldowns() == {"mine": 900}

    # A guild config row can't carry one.
    assert "mine_cooldown" not in await db.get_activities_config(G)
    assert "mine_cooldown" not in await db.get_activities_config(G + 1)

    # Junk and non-positive rows are ignored rather than handed back as a length.
    await db.set_bot_setting("cooldown:work", "soon")
    await db.set_bot_setting("cooldown:hunt", "0")
    assert await db.get_activity_cooldowns() == {"mine": 900}

    assert await db.clear_activity_cooldown("mine") is True
    assert await db.clear_activity_cooldown("mine") is False
    assert await db.get_activity_cooldowns() == {}


# ── Stats defaults ───────────────────────────────────────────────────────────────
async def test_stats_defaults():
    stats = await db.get_activity_stats(A)
    assert stats == {
        "last_work": 0.0,
        "work_shifts": 0,
        "last_mine": 0.0,
        "mine_count": 0,
        "pickaxe_level": 0,
        "last_hunt": 0.0,
        "hunt_count": 0,
        "last_explore": 0.0,
        "explore_count": 0,
        "last_rob": 0.0,
        "rob_count": 0,
        "last_coop": 0.0,
        "coop_count": 0,
        "last_raid": 0.0,
        "raid_count": 0,
        "streak_days": 0,
        "last_day": 0,
    }


# ── try_claim_activity: atomicity ────────────────────────────────────────────────
async def test_unknown_activity_rejected():
    with pytest.raises(ValueError):
        await db.try_claim_activity(A, "fishing", 1000.0, 60)


async def test_first_claim_succeeds_and_counts():
    assert await db.try_claim_activity(A, "work", 1000.0, 3600) == 0
    stats = await db.get_activity_stats(A)
    assert stats["work_shifts"] == 1
    assert stats["last_work"] == 1000.0


async def test_claim_blocked_inside_cooldown():
    await db.try_claim_activity(A, "mine", 1000.0, 1800)
    retry = await db.try_claim_activity(A, "mine", 1500.0, 1800)
    assert retry == 1300
    stats = await db.get_activity_stats(A)
    # The failed claim didn't touch the row.
    assert stats["mine_count"] == 1
    assert stats["last_mine"] == 1000.0


async def test_claim_allowed_after_cooldown():
    await db.try_claim_activity(A, "hunt", 1000.0, 2700)
    assert await db.try_claim_activity(A, "hunt", 3700.0, 2700) == 0
    stats = await db.get_activity_stats(A)
    assert stats["hunt_count"] == 2


async def test_claim_retry_is_at_least_one_second():
    await db.try_claim_activity(A, "explore", 1000.0, 10800)
    retry = await db.try_claim_activity(A, "explore", 10799.5, 10800)
    assert retry >= 1


async def test_different_activities_have_independent_cooldowns():
    assert await db.try_claim_activity(A, "work", 1000.0, 3600) == 0
    # Claiming /work doesn't block /mine for the same user.
    assert await db.try_claim_activity(A, "mine", 1000.0, 1800) == 0
    stats = await db.get_activity_stats(A)
    assert stats["work_shifts"] == 1
    assert stats["mine_count"] == 1


async def test_different_users_have_independent_cooldowns():
    await db.try_claim_activity(A, "rob", 1000.0, 14400)
    assert await db.try_claim_activity(B, "rob", 1000.0, 14400) == 0


async def test_cooldown_follows_the_user_into_every_server():
    """Cooldowns are per user, not per (guild, user): claiming in one server
    blocks the same activity everywhere, which is what stops /work being
    farmed once per guild."""
    assert await db.try_claim_activity(A, "rob", 1000.0, 14400) == 0
    retry = await db.try_claim_activity(A, "rob", 1000.0, 14400)
    assert retry > 0


# ── Pickaxe upgrades (race guard + refund contract) ──────────────────────────────
async def test_pickaxe_upgrade_from_fresh_row():
    assert await db.set_pickaxe_level(A, 1, expected=0) is True
    assert (await db.get_activity_stats(A))["pickaxe_level"] == 1


async def test_pickaxe_upgrade_race_second_buyer_loses():
    assert await db.set_pickaxe_level(A, 1, expected=0) is True
    # A racing second upgrade still expects level 0 — must fail so the caller
    # knows to refund the coins it already debited.
    assert await db.set_pickaxe_level(A, 1, expected=0) is False
    assert (await db.get_activity_stats(A))["pickaxe_level"] == 1
    assert await db.set_pickaxe_level(A, 2, expected=1) is True


async def test_pickaxe_upgrade_does_not_touch_other_users():
    await db.set_pickaxe_level(A, 1, expected=0)
    assert (await db.get_activity_stats(B))["pickaxe_level"] == 0


# ── cross-server farming guard ────────────────────────────────────────────────
async def test_cooldown_claims_are_global_not_per_guild():
    """The whole point of keying activities_stats by user_id alone: hopping to
    another server must not hand back a fresh cooldown."""
    now = 1000.0
    assert await db.try_claim_activity(A, "work", now, 3600) == 0
    # Same user, one second later — the guild isn't part of the claim at all,
    # so there is no second server to try.
    assert await db.try_claim_activity(A, "work", now + 1, 3600) > 0

    # Another member is unaffected.
    assert await db.try_claim_activity(B, "work", now + 1, 3600) == 0

    # And the claim really is one row per user, not one per guild+user.
    async with db._conn().execute(
        "SELECT COUNT(*) AS n FROM activities_stats WHERE user_id=?", (str(A),)
    ) as cur:
        assert (await cur.fetchone())["n"] == 1


# ── Charge banking ───────────────────────────────────────────────────────────
async def test_one_charge_is_the_old_behaviour():
    """The default must be arithmetically identical to the plain 'last = now'
    claim it replaced, or /rob, /squad and /raid change meaning silently."""
    now = 100_000.0
    assert await db.try_claim_activity(A, "rob", now, 3600) == 0
    assert (await db.get_activity_stats(A))["last_rob"] == now
    assert await db.try_claim_activity(A, "rob", now + 1, 3600) == 3599
    # A long absence still lands `last` exactly on `now` — no banking.
    assert await db.try_claim_activity(A, "rob", now + 99_999, 3600) == 0
    assert (await db.get_activity_stats(A))["last_rob"] == now + 99_999


async def test_a_fresh_claim_leaves_the_rest_of_the_bucket_full():
    """A member's first ever dig must not empty a bucket they never filled."""
    now = 100_000.0
    assert await db.try_claim_activity(A, "mine", now, 600, max_charges=4) == 0
    # Three charges left, all spendable in the same second.
    for _ in range(3):
        assert await db.try_claim_activity(A, "mine", now, 600, max_charges=4) == 0
    assert await db.try_claim_activity(A, "mine", now, 600, max_charges=4) == 600
    assert (await db.get_activity_stats(A))["mine_count"] == 4


async def test_charges_refill_one_interval_at_a_time():
    now = 100_000.0
    for _ in range(3):
        await db.try_claim_activity(A, "work", now, 1200, max_charges=3)
    assert await db.try_claim_activity(A, "work", now, 1200, max_charges=3) == 1200

    # Half an interval on: still nothing.
    assert await db.try_claim_activity(A, "work", now + 600, 1200, max_charges=3) == 600
    # One full interval: exactly one charge back, not two.
    assert await db.try_claim_activity(A, "work", now + 1200, 1200, max_charges=3) == 0
    assert await db.try_claim_activity(A, "work", now + 1200, 1200, max_charges=3) > 0


async def test_the_bucket_never_banks_past_its_cap():
    """The point of the floor: a month away is worth three digs, not a month
    of them. Income per hour is unchanged by banking — only the shape is."""
    now = 100_000.0
    await db.try_claim_activity(A, "mine", now, 600, max_charges=3)
    later = now + 30 * 86_400
    for _ in range(3):
        assert await db.try_claim_activity(A, "mine", later, 600, max_charges=3) == 0
    assert await db.try_claim_activity(A, "mine", later, 600, max_charges=3) == 600


async def test_a_shared_row_still_starts_this_activity_full():
    """activities_stats is one row for every activity, so a member who has only
    ever worked has last_mine=0 — that zero is a fresh bucket, not a claim."""
    await db.try_claim_activity(A, "work", 1000.0, 3600)
    assert await db.try_claim_activity(A, "mine", 1000.0, 600, max_charges=3) == 0
    assert await db.try_claim_activity(A, "mine", 1000.0, 600, max_charges=3) == 0


async def test_lowering_the_cap_cannot_hand_back_a_charge():
    """The owner shortening an interval must not retroactively refund runs: the
    stored value is a timestamp, so a smaller cap only ever raises the floor."""
    now = 100_000.0
    for _ in range(4):
        await db.try_claim_activity(A, "mine", now, 600, max_charges=4)
    assert await db.try_claim_activity(A, "mine", now, 600, max_charges=1) == 600


# ── Adventure daily streak ───────────────────────────────────────────────────
async def test_streak_claims_once_a_day():
    await db.try_claim_activity(A, "work", 1000.0, 3600)
    assert await db.try_claim_adventure_streak(A, 20_000, 1) is True
    stats = await db.get_activity_stats(A)
    assert stats["streak_days"] == 1 and stats["last_day"] == 20_000

    # A second activity the same day can't claim it again.
    assert await db.try_claim_adventure_streak(A, 20_000, 2) is False
    assert (await db.get_activity_stats(A))["streak_days"] == 1

    assert await db.try_claim_adventure_streak(A, 20_001, 2) is True
    assert (await db.get_activity_stats(A))["streak_days"] == 2


async def test_streak_is_per_user_not_per_guild():
    await db.try_claim_activity(A, "work", 1000.0, 3600)
    await db.try_claim_activity(B, "work", 1000.0, 3600)
    assert await db.try_claim_adventure_streak(A, 20_000, 5) is True
    assert (await db.get_activity_stats(B))["streak_days"] == 0


async def test_a_short_cooldown_elsewhere_cannot_shorten_a_long_one():
    """The claim stores only the timestamp, so a later check with a longer
    cooldown still blocks: a permissive server can't 'clear' a strict one."""
    now = 2000.0
    assert await db.try_claim_activity(A, "mine", now, 900) == 0
    # 15 minutes on, ready under a 900s server but not under a 1800s one.
    assert await db.try_claim_activity(A, "mine", now + 901, 1800) > 0
    assert await db.try_claim_activity(A, "mine", now + 1801, 1800) == 0
