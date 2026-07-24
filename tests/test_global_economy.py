"""
tests/test_global_economy.py
The cross-server contract: a member's economy belongs to them, not to a guild.

Everything here is written from the user's point of view — "if I do X in one
server, does it still count in another?" — so it guards the *intent* of the
global refactor rather than any one accessor's signature. The DB-level merge
rules live in tests/test_globalize_migration.py.
"""

import aiosqlite
import pytest

import utils.db as db

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def db_ready(monkeypatch):
    """In-memory DB with every economy-domain table created."""
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    monkeypatch.setattr(db, "_db", conn)
    for setup in (
        db._ensure_economy_tables,
        db._ensure_items_tables,
        db._ensure_fishing_tables,
        db._ensure_casino_tables,
        db._ensure_activities_tables,
        db._ensure_progression_tables,
    ):
        await setup()
    yield conn
    await conn.close()


SERVER_A, SERVER_B = 8001, 8002  # two guilds the same user is in
USER, OTHER = 4001, 4002


# ── Wallet, inventory, and progress follow the user ────────────────────────────
async def test_coins_earned_anywhere_spend_everywhere(db_ready):
    """The headline promise: one wallet, every server."""
    await db.add_coins(USER, 500)  # earned in server A
    assert await db.get_balance(USER) == 500  # …and visible in server B
    assert await db.try_debit_coins(USER, 500) is True  # spendable there too
    assert await db.get_balance(USER) == 0


async def test_daily_is_one_claim_per_day_not_one_per_server(db_ready):
    """Claiming in server A must block the claim in server B — the cooldown is
    a property of the user, which is also what closes the old farm-it-in-every-
    server hole."""
    await db.set_daily_state(USER, 1_000.0, 1)
    last, streak = await db.get_daily_state(USER)
    assert (last, streak) == (1_000.0, 1)


async def test_items_and_effects_are_carried_between_servers(db_ready):
    await db.add_item(USER, "iron_ore", 5)
    await db.grant_effect(USER, "luck", 0.2, uses=3)
    assert await db.get_item_qty(USER, "iron_ore") == 5
    effects = await db.get_active_effects(USER)
    assert effects["luck"]["uses_left"] == 3


async def test_fishing_progress_is_one_profile(db_ready):
    await db.try_claim_cast(USER, 1_000.0, 240)
    await db.record_catch(USER, "cod", 3.0, 30)
    await db.add_fishing_xp(USER, 40)
    await db.set_rod_level(USER, 1, expected=0)

    fisher = await db.get_fisher(USER)
    assert fisher["caught"] == 1 and fisher["xp"] == 40 and fisher["rod_level"] == 1
    # The cast cooldown claimed a moment ago blocks a cast from anywhere.
    assert await db.try_claim_cast(USER, 1_050.0, 240) > 0


async def test_activity_cooldown_cannot_be_dodged_by_switching_servers(db_ready):
    assert await db.try_claim_activity(USER, "work", 1_000.0, 3600) == 0
    assert await db.try_claim_activity(USER, "work", 1_100.0, 3600) > 0


async def test_achievement_pays_out_once_for_the_user(db_ready):
    assert await db.try_award_achievement(USER, "fish_caught_10") is True
    # A second evaluation — in any server — must not land, so the reward the
    # cog grants on a successful insert can't be paid twice.
    assert await db.try_award_achievement(USER, "fish_caught_10") is False


# ── Server-scoped views of global data ────────────────────────────────────────
async def test_server_leaderboard_is_the_global_table_filtered_to_members(db_ready):
    await db.add_coins(USER, 900)
    await db.add_coins(OTHER, 400)

    everyone = await db.get_econ_leaderboard()
    assert [r["user_id"] for r in everyone] == [USER, OTHER]

    # Server B only has OTHER in it: the same wallets, filtered.
    here = await db.get_econ_leaderboard_for([OTHER])
    assert [r["user_id"] for r in here] == [OTHER]
    assert here[0]["coins"] == 400  # the member's true (global) balance


async def test_scoped_leaderboards_exist_for_every_economy_board(db_ready):
    """Each board offers both views, and the server view never invents rows."""
    await db.add_coins(USER, 100)
    await db.add_contribution(USER, 50)
    await db.add_fishing_earned(USER, 70)
    await db.record_casino_game(USER, 100, 250)

    for rows in (
        await db.get_econ_leaderboard_for([USER]),
        await db.get_contrib_leaderboard_for([USER]),
        await db.get_fishing_leaderboard_for([USER]),
        await db.get_casino_leaderboard_for([USER]),
    ):
        assert [r["user_id"] for r in rows] == [USER]
    for rows in (
        await db.get_econ_leaderboard_for([OTHER]),
        await db.get_contrib_leaderboard_for([OTHER]),
        await db.get_fishing_leaderboard_for([OTHER]),
        await db.get_casino_leaderboard_for([OTHER]),
    ):
        assert rows == []


async def test_member_filter_chunks_past_sqlites_parameter_limit(db_ready):
    """A big guild must not blow the bound-parameter cap — ids are chunked."""
    await db.add_coins(USER, 10)
    ids = list(range(1, 2500)) + [USER]
    rows = await db.get_econ_leaderboard_for(ids)
    assert [r["user_id"] for r in rows] == [USER]


# ── Guild-owned data stays guild-owned ────────────────────────────────────────
async def test_settings_shop_and_jackpot_remain_per_server(db_ready):
    await db.set_econ_config(SERVER_A, currency_name="Doubloon", daily_amount=250)
    assert (await db.get_econ_config(SERVER_A))["currency_name"] == "Doubloon"
    assert (await db.get_econ_config(SERVER_B))["currency_name"] == "NanoCoin"

    await db.set_fishing_config(SERVER_A, enabled=False)
    assert (await db.get_fishing_config(SERVER_A))["enabled"] is False
    assert (await db.get_fishing_config(SERVER_B))["enabled"] is True

    item_id = await db.add_shop_item(SERVER_A, "Colour Role", 100, kind="custom")
    assert await db.get_shop_item(SERVER_B, item_id) is None

    await db.add_to_jackpot(SERVER_A, 500)
    assert (await db.get_casino_config(SERVER_A))["jackpot_pool"] == 500
    assert (await db.get_casino_config(SERVER_B))["jackpot_pool"] == 0

    await db.set_activities_config(SERVER_A, rob_enabled=False)
    assert (await db.get_activities_config(SERVER_A))["rob_enabled"] is False
    assert (await db.get_activities_config(SERVER_B))["rob_enabled"] is True


async def test_shop_purchase_charges_the_global_wallet(db_ready):
    """The shop is the guild's, the money is the user's."""
    item_id = await db.add_shop_item(SERVER_A, "Sticker", 300, kind="custom")
    await db.add_coins(USER, 300)  # earned anywhere
    res = await db.purchase_item(SERVER_A, item_id, USER)
    assert res["ok"] is True
    assert res["new_balance"] == 0
    assert await db.get_balance(USER) == 0
