"""
Tests for the price-refund sweep and its DM run (cogs/economy/refunds.py + the
Economy cog's sweep/deliver pair).

The sweep mints coins into global wallets from a manifest, which makes it the
most dangerous piece of code in the economy: every other faucet pays a bounded
amount for an action someone took, and this one pays a lump sum to everybody at
once for something they did in the past. So the tests are mostly about what it
must *not* do — pay twice, pay a member who bought at the new price, or let a
failed DM cost anyone their money.

The cog is built with __new__ rather than loaded, the way tests/test_music_player
drives GuildPlayer: the sweep touches no gateway state, and a duck-typed bot is
enough to observe the DMs.
"""

import aiosqlite
import pytest

import utils.db as db
from cogs.economy.cog import Economy
from cogs.economy.refunds import RefundBatch, RefundItem


@pytest.fixture(autouse=True)
async def database(monkeypatch):
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA journal_mode=WAL")
    monkeypatch.setattr(db, "_db", conn)
    await db._ensure_refund_tables()
    await db._ensure_economy_tables()
    await db._ensure_activities_tables()
    await db._ensure_fishing_tables()
    yield conn
    await conn.close()


BATCH = RefundBatch(
    key="test-reprice",
    summary="Prices came down.",
    items=[
        RefundItem("pickaxe", 2, 4_000, 3_000, "⛏️ Iron Pickaxe"),
        RefundItem("pickaxe", 4, 100_000, 24_000, "⛏️ Obsidian Pickaxe"),
        RefundItem("rod", 5, 450_000, 380_000, "🔱 Mythic Trident"),
        RefundItem("spot", "abyss", 600_000, 450_000, "🌊 Abyssal Trench charter"),
    ],
)


class FakeUser:
    def __init__(self, user_id: int, *, fails: bool = False):
        self.id = user_id
        self.fails = fails
        self.embeds = []

    async def send(self, *, embed=None):
        if self.fails:
            import discord

            raise discord.HTTPException(_Response(), "cannot send to this user")
        self.embeds.append(embed)


class _Response:
    status = 403
    reason = "Forbidden"


class FakeBot:
    def __init__(self, users: dict[int, FakeUser] | None = None):
        self.users = users or {}

    def get_user(self, user_id: int):
        return self.users.get(user_id)

    async def fetch_user(self, user_id: int):
        user = self.users.get(user_id)
        if user is None:
            import discord

            raise discord.HTTPException(_Response(), "unknown user")
        return user


def make_cog(monkeypatch, bot=None, batches=(BATCH,)):
    cog = Economy.__new__(Economy)
    cog.bot = bot or FakeBot()
    monkeypatch.setattr("cogs.economy.cog.REFUND_BATCHES", list(batches))
    # The DM pacing is real seconds; the tests don't need to live through them.
    monkeypatch.setattr("cogs.economy.cog.REFUND_DM_INTERVAL", 0)
    return cog


async def give_pickaxe(user_id: int, level: int):
    await db.set_pickaxe_level(user_id, level, expected=0)


# ── What each member is owed ─────────────────────────────────────────────────
def test_a_ladder_entry_pays_everyone_at_or_above_that_tier():
    """Owning tier 5 means having bought 1 through 5, so the refund is the sum
    of every reduction at or below it — that is the only way to reconstruct a
    receipt nobody kept."""
    assert BATCH.owed(pickaxe_level=4)[0] == 77_000
    assert BATCH.owed(pickaxe_level=2)[0] == 1_000
    assert BATCH.owed(pickaxe_level=1)[0] == 0


def test_a_charter_pays_only_the_member_holding_it():
    """A charter is bought on its own, not as a ladder, so ownership is the set
    membership rather than a threshold."""
    assert BATCH.owed(spots={"abyss"})[0] == 150_000
    assert BATCH.owed(spots={"reef", "deep"})[0] == 0


def test_the_breakdown_names_things_rather_than_keys():
    _total, detail = BATCH.owed(pickaxe_level=4, spots={"abyss"})
    assert any("Obsidian Pickaxe" in line for line in detail)
    assert not any("abyss" in line for line in detail)


def test_a_member_owed_across_several_ladders_gets_one_total():
    total, detail = BATCH.owed(pickaxe_level=4, rod_level=5, spots={"abyss"})
    assert total == 297_000
    assert len(detail) == 4


# ── The sweep ────────────────────────────────────────────────────────────────
async def test_the_sweep_credits_every_owner_once(monkeypatch):
    cog = make_cog(monkeypatch)
    await give_pickaxe(1, 4)
    await give_pickaxe(2, 2)

    await cog.sweep_price_refunds()

    assert await db.get_balance(1) == 77_000
    assert await db.get_balance(2) == 1_000


async def test_a_second_sweep_pays_nothing(monkeypatch):
    """The batch marker alone would do this; the ledger is the belt to its
    braces, and the next test removes the marker to prove it."""
    cog = make_cog(monkeypatch)
    await give_pickaxe(1, 4)

    await cog.sweep_price_refunds()
    await cog.sweep_price_refunds()

    assert await db.get_balance(1) == 77_000


async def test_the_ledger_holds_even_if_the_batch_marker_is_lost(monkeypatch):
    """A crash between paying the members and closing the batch re-runs the
    whole sweep. Nobody may be paid twice for it."""
    cog = make_cog(monkeypatch)
    await give_pickaxe(1, 4)
    await cog.sweep_price_refunds()

    await db._conn().execute("DELETE FROM price_refund_batches")
    await db._commit()
    await cog.sweep_price_refunds()

    assert await db.get_balance(1) == 77_000


async def test_someone_who_buys_after_the_sweep_is_owed_nothing(monkeypatch):
    """The point of closing a batch. A member who buys the tier tomorrow pays
    the new price — refunding them would be handing out the discount twice."""
    cog = make_cog(monkeypatch)
    await cog.sweep_price_refunds()

    await give_pickaxe(9, 4)
    await cog.sweep_price_refunds()

    assert await db.get_balance(9) == 0


async def test_a_member_below_every_reduced_tier_is_left_alone(monkeypatch):
    cog = make_cog(monkeypatch)
    await give_pickaxe(1, 1)
    await cog.sweep_price_refunds()
    assert await db.get_balance(1) == 0
    assert await db.get_refunds(1) == []


async def test_rods_and_charters_are_swept_too(monkeypatch):
    cog = make_cog(monkeypatch)
    await db.set_rod_level(1, 5, expected=0)
    await db.unlock_spot(2, "abyss", 0.0)

    await cog.sweep_price_refunds()

    assert await db.get_balance(1) == 70_000
    assert await db.get_balance(2) == 150_000


async def test_one_member_owed_on_three_ladders_is_paid_once(monkeypatch):
    """The merge across ownership tables — three queries, one ledger row, one
    credit."""
    cog = make_cog(monkeypatch)
    await give_pickaxe(1, 4)
    await db.set_rod_level(1, 5, expected=0)
    await db.unlock_spot(1, "abyss", 0.0)

    await cog.sweep_price_refunds()

    assert await db.get_balance(1) == 297_000
    assert len(await db.get_refunds(1)) == 1


async def test_the_sweep_reports_what_it_paid(monkeypatch):
    cog = make_cog(monkeypatch)
    await give_pickaxe(1, 4)
    await give_pickaxe(2, 2)

    assert await cog.sweep_price_refunds() == {"test-reprice": (2, 78_000)}
    totals = await db.refund_batch_totals("test-reprice")
    assert (totals["users"], totals["coins"]) == (2, 78_000)


async def test_a_later_batch_runs_without_disturbing_an_earlier_one(monkeypatch):
    """Batches are the extension point: adding one must not re-open the last."""
    later = RefundBatch(
        key="test-reprice-2",
        summary="More prices came down.",
        items=[RefundItem("pickaxe", 4, 24_000, 19_000, "⛏️ Obsidian Pickaxe")],
    )
    await give_pickaxe(1, 4)

    cog = make_cog(monkeypatch)
    await cog.sweep_price_refunds()
    assert await db.get_balance(1) == 77_000

    cog = make_cog(monkeypatch, batches=(BATCH, later))
    await cog.sweep_price_refunds()
    assert await db.get_balance(1) == 82_000


# ── Delivery ─────────────────────────────────────────────────────────────────
async def test_the_dm_carries_the_amount_and_the_breakdown(monkeypatch):
    user = FakeUser(1)
    cog = make_cog(monkeypatch, bot=FakeBot({1: user}))
    await give_pickaxe(1, 4)

    await cog.sweep_price_refunds()
    assert await cog.deliver_refund_notices() == 1

    (embed,) = user.embeds
    assert "77,000" in embed.description
    assert "Obsidian Pickaxe" in embed.fields[0].value


async def test_a_delivered_notice_is_not_sent_again(monkeypatch):
    user = FakeUser(1)
    cog = make_cog(monkeypatch, bot=FakeBot({1: user}))
    await give_pickaxe(1, 4)
    await cog.sweep_price_refunds()

    await cog.deliver_refund_notices()
    await cog.deliver_refund_notices()

    assert len(user.embeds) == 1


async def test_closed_dms_cost_the_member_nothing(monkeypatch):
    """The credit and the announcement are separate columns precisely so this
    case keeps its coins and stays queued for a later start."""
    user = FakeUser(1, fails=True)
    cog = make_cog(monkeypatch, bot=FakeBot({1: user}))
    await give_pickaxe(1, 4)
    await cog.sweep_price_refunds()

    assert await cog.deliver_refund_notices() == 0
    assert await db.get_balance(1) == 77_000
    assert len(await db.pending_refund_notices()) == 1


async def test_an_unreachable_account_does_not_block_the_queue(monkeypatch):
    """A member who left every server the bot is in must not stop the person
    behind them in the queue from being told."""
    reachable = FakeUser(2)
    cog = make_cog(monkeypatch, bot=FakeBot({2: reachable}))
    await give_pickaxe(1, 4)  # no FakeUser — fetch_user raises
    await give_pickaxe(2, 4)

    assert await cog.deliver_refund_notices() == 0  # nothing credited yet
    await cog.sweep_price_refunds()
    assert await cog.deliver_refund_notices() == 1
    assert len(reachable.embeds) == 1


async def test_the_whole_run_survives_a_failure(monkeypatch):
    """_run_refunds is fired off the restore path, so an exception in it must
    not escape into the cog's startup."""
    cog = make_cog(monkeypatch)

    async def boom():
        raise RuntimeError("database on fire")

    monkeypatch.setattr(cog, "sweep_price_refunds", boom)
    await cog._run_refunds()  # must not raise
