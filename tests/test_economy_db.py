"""
Tests for the economy accessors in utils/db/ — in-memory SQLite.
"""

import asyncio

import aiosqlite
import pytest

import utils.db as db


@pytest.fixture(autouse=True)
async def database(monkeypatch):
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA journal_mode=WAL")
    monkeypatch.setattr(db, "_db", conn)
    await db._ensure_economy_tables()
    yield conn
    await conn.close()


G = 555
A, B, C = 1, 2, 3


async def test_add_and_clamp():
    assert await db.add_coins(A, 100) == 100
    assert await db.add_coins(A, 50) == 150
    assert await db.add_coins(A, -1000) == 0


async def test_set_is_absolute():
    await db.add_coins(A, 500)
    await db.set_coins(A, 20)
    assert await db.get_balance(A) == 20


async def test_transfer_success():
    await db.add_coins(A, 100)
    assert await db.transfer_coins(A, B, 40) is True
    assert await db.get_balance(A) == 60
    assert await db.get_balance(B) == 40


async def test_transfer_insufficient_funds():
    await db.add_coins(A, 30)
    assert await db.transfer_coins(A, B, 40) is False
    assert await db.get_balance(A) == 30
    assert await db.get_balance(B) == 0


async def test_transfer_rejects_nonpositive():
    await db.add_coins(A, 30)
    assert await db.transfer_coins(A, B, 0) is False
    assert await db.transfer_coins(A, B, -5) is False


async def test_try_debit_success_and_balance():
    await db.add_coins(A, 100)
    assert await db.try_debit_coins(A, 40) is True
    assert await db.get_balance(A) == 60


async def test_try_debit_insufficient_leaves_balance():
    await db.add_coins(A, 30)
    assert await db.try_debit_coins(A, 40) is False
    assert await db.get_balance(A) == 30


async def test_try_debit_no_account_is_false():
    assert await db.try_debit_coins(A, 10) is False


async def test_try_debit_rejects_nonpositive():
    await db.add_coins(A, 50)
    assert await db.try_debit_coins(A, 0) is False
    assert await db.try_debit_coins(A, -5) is False
    assert await db.get_balance(A) == 50


async def test_try_debit_exact_balance_allowed():
    await db.add_coins(A, 40)
    assert await db.try_debit_coins(A, 40) is True
    assert await db.get_balance(A) == 0
    # A second debit of the now-empty account fails.
    assert await db.try_debit_coins(A, 1) is False


async def test_concurrent_debits_cannot_overdraw():
    import asyncio

    await db.add_coins(A, 100)
    # Fire five 40-coin debits at once against a 100 balance: at most two can
    # succeed (the atomic conditional UPDATE serializes them), never overdrawing.
    results = await asyncio.gather(*(db.try_debit_coins(A, 40) for _ in range(5)))
    assert sum(results) == 2
    assert await db.get_balance(A) == 20


async def test_rank_and_leaderboard():
    await db.add_coins(A, 100)
    await db.add_coins(B, 300)
    await db.add_coins(C, 200)
    assert await db.get_econ_rank(B) == (1, 300)
    assert await db.get_econ_rank(A) == (3, 100)
    top = await db.get_econ_leaderboard(limit=10)
    assert [r["user_id"] for r in top] == [B, C, A]


async def test_rank_none_without_account():
    assert await db.get_econ_rank(99) is None


async def test_leaderboard_excludes_zero():
    await db.add_coins(A, 100)
    await db.set_coins(B, 0)
    assert await db.count_econ() == 1


async def test_reset_one_and_all():
    await db.add_coins(A, 100)
    await db.add_coins(B, 100)
    assert await db.reset_economy(A) == 1
    assert await db.get_balance(A) == 0
    assert await db.reset_economy() == 1  # only B left
    assert await db.count_econ() == 0


async def test_daily_state_round_trip():
    assert await db.get_daily_state(A) == (0.0, 0)
    await db.set_daily_state(A, 12345.0, 3)
    assert await db.get_daily_state(A) == (12345.0, 3)
    # set_daily_state must not clobber an existing balance
    await db.set_coins(B, 500)
    await db.set_daily_state(B, 999.0, 1)
    assert await db.get_balance(B) == 500


async def test_config_defaults_and_partial_update():
    cfg = await db.get_econ_config(G)
    assert cfg["daily_amount"] == 100
    assert cfg["currency_name"] == "NanoCoin"
    assert cfg["currency_emoji"] == "🪙"
    assert cfg["streak_bonus"] == 0

    await db.set_econ_config(G, currency_name="Gold", streak_bonus=25)
    cfg = await db.get_econ_config(G)
    assert cfg["currency_name"] == "Gold"
    assert cfg["streak_bonus"] == 25
    assert cfg["daily_amount"] == 100  # untouched


async def test_coop_and_raid_config_defaults_and_update():
    cfg = await db.get_econ_config(G)
    assert cfg["coop_reward"] == 50
    assert cfg["raid_reward"] == 100
    assert cfg["raid_min"] == 3
    assert cfg["raid_max"] == 20

    await db.set_econ_config(
        G, coop_reward=75, raid_reward=500, raid_min=5, raid_max=40
    )
    cfg = await db.get_econ_config(G)
    assert cfg["coop_reward"] == 75
    assert cfg["raid_reward"] == 500
    assert cfg["raid_min"] == 5
    assert cfg["raid_max"] == 40
    assert cfg["daily_amount"] == 100  # untouched


# ── Contribution (lifetime co-op stat) ───────────────────────────────────────────
async def test_contribution_accumulates_independent_of_coins():
    assert await db.get_contribution(A) == 0
    assert await db.add_contribution(A, 50) == 50
    assert await db.add_contribution(A, 50) == 100
    # Spending coins must not touch contribution.
    await db.add_coins(A, 100)
    await db.add_coins(A, -100)
    assert await db.get_contribution(A) == 100


async def test_contribution_rank_and_leaderboard():
    await db.add_contribution(A, 100)
    await db.add_contribution(B, 300)
    await db.add_contribution(C, 200)
    assert await db.get_contrib_rank(B) == (1, 300)
    assert await db.get_contrib_rank(A) == (3, 100)
    assert await db.count_contrib() == 3
    top = await db.get_contrib_leaderboard(limit=10)
    assert [r["user_id"] for r in top] == [B, C, A]


async def test_contribution_rank_none_without_points():
    await db.add_coins(A, 500)  # has coins, no contribution
    assert await db.get_contrib_rank(A) is None
    assert await db.count_contrib() == 0


# ── Shop ─────────────────────────────────────────────────────────────────────────
async def test_shop_add_get_and_unique_name():
    iid = await db.add_shop_item(G, "VIP Role", 500, "role", role_id=42)
    assert iid is not None
    item = await db.get_shop_item(G, iid)
    assert item["name"] == "VIP Role"
    assert item["price"] == 500
    assert item["role_id"] == 42
    assert item["enabled"] is True
    # Duplicate name (case-insensitive lookup, exact-insert clash) → None.
    assert await db.add_shop_item(G, "VIP Role", 100, "role", role_id=7) is None
    assert (await db.get_shop_item_by_name(G, "vip role"))["id"] == iid


async def test_shop_edit_and_remove():
    iid = await db.add_shop_item(G, "Loot", 100, "custom", payload="gold")
    assert await db.edit_shop_item(G, iid, price=250, enabled=False) is True
    item = await db.get_shop_item(G, iid)
    assert item["price"] == 250
    assert item["enabled"] is False
    assert await db.edit_shop_item(G, iid, bogus=1) is False  # no valid fields
    assert await db.remove_shop_item(G, iid) is True
    assert await db.get_shop_item(G, iid) is None


async def test_shop_list_enabled_filter():
    a = await db.add_shop_item(G, "Cheap", 10, "custom", payload="x")
    await db.add_shop_item(G, "Hidden", 20, "custom", payload="y")
    await db.edit_shop_item(G, await _name_id(G, "Hidden"), enabled=False)
    all_items = await db.list_shop_items(G)
    assert len(all_items) == 2
    visible = await db.list_shop_items(G, enabled_only=True)
    assert [i["id"] for i in visible] == [a]
    assert await db.count_shop_items(G, enabled_only=True) == 1


async def _name_id(guild, name):
    return (await db.get_shop_item_by_name(guild, name))["id"]


async def test_purchase_success_debits_and_records():
    await db.add_coins(A, 1000)
    iid = await db.add_shop_item(G, "Color", 300, "role", role_id=9)
    res = await db.purchase_item(G, iid, A)
    assert res["ok"] is True
    assert res["new_balance"] == 700
    assert await db.count_user_purchases(G, iid, A) == 1


async def test_purchase_insufficient_funds():
    await db.add_coins(A, 100)
    iid = await db.add_shop_item(G, "Pricey", 300, "custom", payload="z")
    res = await db.purchase_item(G, iid, A)
    assert res["ok"] is False
    assert res["reason"] == "funds"
    assert await db.get_balance(A) == 100  # not charged


async def test_purchase_stock_decrements_and_sells_out():
    await db.add_coins(A, 1000)
    await db.add_coins(B, 1000)
    iid = await db.add_shop_item(G, "Limited", 100, "custom", payload="z", stock=1)
    assert (await db.purchase_item(G, iid, A))["ok"] is True
    assert (await db.get_shop_item(G, iid))["stock"] == 0
    res = await db.purchase_item(G, iid, B)
    assert res["ok"] is False
    assert res["reason"] == "out_of_stock"
    assert await db.get_balance(B) == 1000  # not charged when sold out


async def test_purchase_stock_refunded_when_funds_short():
    await db.add_coins(A, 50)
    iid = await db.add_shop_item(G, "Spendy", 100, "custom", payload="z", stock=2)
    res = await db.purchase_item(G, iid, A)
    assert res["ok"] is False and res["reason"] == "funds"
    # Reserved stock must be returned when the debit fails.
    assert (await db.get_shop_item(G, iid))["stock"] == 2


async def test_purchase_per_user_limit():
    await db.add_coins(A, 1000)
    iid = await db.add_shop_item(
        G, "OnePer", 100, "custom", payload="z", per_user_limit=1
    )
    assert (await db.purchase_item(G, iid, A))["ok"] is True
    res = await db.purchase_item(G, iid, A)
    assert res["ok"] is False and res["reason"] == "limit"


async def test_purchase_cooldown():
    await db.add_coins(A, 1000)
    iid = await db.add_shop_item(
        G, "Cooldown", 100, "custom", payload="z", cooldown=999
    )
    assert (await db.purchase_item(G, iid, A))["ok"] is True
    res = await db.purchase_item(G, iid, A)
    assert res["ok"] is False and res["reason"] == "cooldown"
    assert res["retry_after"] > 0


async def test_purchase_disabled_item():
    await db.add_coins(A, 1000)
    iid = await db.add_shop_item(G, "Off", 100, "custom", payload="z")
    await db.edit_shop_item(G, iid, enabled=False)
    res = await db.purchase_item(G, iid, A)
    assert res["ok"] is False and res["reason"] == "disabled"


async def test_custom_purchase_pending_then_fulfilled():
    await db.add_coins(A, 1000)
    iid = await db.add_shop_item(G, "Manual", 100, "custom", payload="loot")
    await db.purchase_item(G, iid, A)
    assert await db.count_pending_purchases(G) == 1
    pending = await db.list_pending_purchases(G)
    pid = pending[0]["id"]
    res = await db.fulfill_purchase(G, pid, mod_id=999)
    assert res["item_name"] == "Manual"
    assert await db.count_pending_purchases(G) == 0
    # Re-fulfilling the same purchase is a no-op.
    assert await db.fulfill_purchase(G, pid, mod_id=999) is None


async def test_role_purchase_not_in_pending_queue():
    await db.add_coins(A, 1000)
    iid = await db.add_shop_item(G, "AutoRole", 100, "role", role_id=5)
    await db.purchase_item(G, iid, A)
    # Role rewards are auto-fulfilled, so they never enter the mod queue.
    assert await db.count_pending_purchases(G) == 0


# ── Raids (persisted /raid boards) ──────────────────────────────────────────────
async def test_create_and_get_raid_roundtrip():
    rid = await db.create_raid(G, 111, A, "dungeon", [A], created_at=1000.0)
    assert isinstance(rid, int)
    await db.set_raid_message(rid, 222)
    raids = await db.get_open_raids()
    assert len(raids) == 1
    r = raids[0]
    assert r["raid_id"] == rid
    assert r["guild_id"] == G
    assert r["channel_id"] == 111
    assert r["message_id"] == 222
    assert r["host_id"] == A
    assert r["activity"] == "dungeon"
    assert r["participants"] == [A]
    assert r["created_at"] == 1000.0


async def test_raid_participants_update():
    rid = await db.create_raid(G, 111, A, "", [A], created_at=1.0)
    await db.set_raid_participants(rid, [A, B, C])
    r = (await db.get_open_raids())[0]
    assert r["participants"] == [A, B, C]


async def test_delete_raid():
    rid = await db.create_raid(G, 111, A, "", [A], created_at=1.0)
    await db.delete_raid(rid)
    assert await db.get_open_raids() == []


async def test_raid_message_id_null_before_set():
    await db.create_raid(G, 111, A, "", [A], created_at=1.0)
    r = (await db.get_open_raids())[0]
    assert r["message_id"] is None


# ── Squads (persisted /squad co-op confirms) ────────────────────────────────────
async def test_create_and_get_squad_roundtrip():
    sid = await db.create_squad(G, 111, A, [B, C], "dungeon", created_at=1000.0)
    assert isinstance(sid, int)
    await db.set_squad_message(sid, 222)
    squads = await db.get_open_squads()
    assert len(squads) == 1
    s = squads[0]
    assert s["squad_id"] == sid
    assert s["guild_id"] == G
    assert s["channel_id"] == 111
    assert s["message_id"] == 222
    assert s["author_id"] == A
    assert s["partner_ids"] == [B, C]
    assert s["confirmed"] == []
    assert s["activity"] == "dungeon"
    assert s["created_at"] == 1000.0


async def test_squad_confirmed_update():
    sid = await db.create_squad(G, 111, A, [B, C], "", created_at=1.0)
    await db.set_squad_confirmed(sid, [B])
    s = (await db.get_open_squads())[0]
    assert s["confirmed"] == [B]


async def test_delete_squad():
    sid = await db.create_squad(G, 111, A, [B], "", created_at=1.0)
    await db.delete_squad(sid)
    assert await db.get_open_squads() == []


async def test_squad_message_id_null_before_set():
    await db.create_squad(G, 111, A, [B], "", created_at=1.0)
    s = (await db.get_open_squads())[0]
    assert s["message_id"] is None


# ── Purchase atomicity ────────────────────────────────────────────────────────
async def test_concurrent_buys_cannot_both_beat_a_per_user_limit():
    """The limit lives in the INSERT's WHERE clause, so two buys racing on the
    same await points can't both read "bought = 0" and both succeed."""
    item_id = await db.add_shop_item(G, "Badge", 10, kind="custom", per_user_limit=1)
    await db.add_coins(A, 1000)

    results = await asyncio.gather(
        db.purchase_item(G, item_id, A), db.purchase_item(G, item_id, A)
    )
    assert sorted(bool(r["ok"]) for r in results) == [False, True]
    assert [r["reason"] for r in results if not r["ok"]] == ["limit"]
    assert await db.count_user_purchases(G, item_id, A) == 1
    # The loser's coins came back: exactly one purchase was paid for.
    assert await db.get_balance(A) == 990


async def test_purchase_cooldown_is_enforced_in_the_claim():
    item_id = await db.add_shop_item(G, "Daily", 10, kind="custom", cooldown=3600)
    await db.add_coins(A, 1000)

    assert (await db.purchase_item(G, item_id, A))["ok"] is True
    second = await db.purchase_item(G, item_id, A)
    assert second["ok"] is False
    assert second["reason"] == "cooldown"
    assert second["retry_after"] > 0
    assert await db.get_balance(A) == 990


async def test_rejected_claim_restores_stock_and_coins():
    item_id = await db.add_shop_item(
        G, "Rare", 10, kind="custom", per_user_limit=1, stock=5
    )
    await db.add_coins(A, 1000)
    await db.purchase_item(G, item_id, A)
    blocked = await db.purchase_item(G, item_id, A)

    assert blocked["reason"] == "limit"
    assert (await db.get_shop_item(G, item_id))["stock"] == 4  # only the paid one
    assert await db.get_balance(A) == 990


async def test_transfer_coins_is_all_or_nothing():
    await db.add_coins(A, 100)
    assert await db.transfer_coins(A, B, 60) is True
    assert (await db.get_balance(A), await db.get_balance(B)) == (40, 60)
    # Short funds: nothing moves at all.
    assert await db.transfer_coins(A, B, 500) is False
    assert (await db.get_balance(A), await db.get_balance(B)) == (40, 60)
    assert await db.transfer_coins(A, B, 0) is False
