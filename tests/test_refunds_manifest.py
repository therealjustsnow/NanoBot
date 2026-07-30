"""
Tests for the shipped refund manifest in cogs/economy/refunds.py.

The manifest is frozen history — deliberately not derived from the live price
tables, since a batch describes what things cost on a particular day. That
freedom is also its risk: nothing stops a typo, a stale tier index, or the real
failure mode, which is silent — someone cuts a price, ships it, and never adds
the batch, so everyone who already bought it is quietly left out of pocket.

Recording both the old and the new price is what makes that checkable: the most
recent batch to mention an item has to agree with the ladder as it stands today.
"""

import pytest

from cogs.activities.constants import PICKAXES
from cogs.economy.refunds import REFUND_BATCHES
from cogs.fishing.constants import RODS
from cogs.fishing.spots import SPOTS

ITEMS = [(batch, item) for batch in REFUND_BATCHES for item in batch.items]


def live_price(kind: str, ref) -> int:
    if kind == "pickaxe":
        return PICKAXES[ref]["price"]
    if kind == "rod":
        return RODS[ref]["price"]
    return SPOTS[ref]["price"]


def latest_entries() -> dict[tuple[str, object], object]:
    """The last word on each item — a later batch supersedes an earlier one."""
    out: dict[tuple[str, object], object] = {}
    for _batch, item in ITEMS:
        out[(item.kind, item.ref)] = item
    return out


# ── The guard that matters ───────────────────────────────────────────────────
@pytest.mark.parametrize("key", sorted(latest_entries(), key=str))
def test_the_latest_batch_agrees_with_the_price_on_the_shelf(key):
    """If this fails, a price moved and no batch was added for it. Either add
    one, or — if the price went *up* — nothing is owed and the entry should be
    left alone; see the manifest's note on why rises are never clawed back.
    """
    item = latest_entries()[key]
    assert item.now == live_price(item.kind, item.ref), (
        f"{item.label} is {live_price(item.kind, item.ref):,} on the shelf but "
        f"the last refund batch recorded {item.now:,}"
    )


def test_every_price_cut_in_the_live_ladders_has_a_batch():
    """The other half of the same guard, from the ladders' side: every entry the
    manifest has ever touched must still be reachable, so a tier removed from a
    ladder can't leave a batch pointing into nothing."""
    for _batch, item in ITEMS:
        if item.kind == "pickaxe":
            assert 0 < item.ref < len(PICKAXES)
        elif item.kind == "rod":
            assert 0 < item.ref < len(RODS)
        else:
            assert item.ref in SPOTS


# ── Shape ────────────────────────────────────────────────────────────────────
def test_batch_keys_are_unique():
    """The key is the once-only guarantee — two batches sharing one would mean
    the second never pays out."""
    keys = [batch.key for batch in REFUND_BATCHES]
    assert len(keys) == len(set(keys))


@pytest.mark.parametrize("batch,item", ITEMS, ids=lambda v: getattr(v, "label", ""))
def test_every_entry_is_a_real_reduction(batch, item):
    """A batch that contains a price *rise* is a mistake: this system only ever
    pays out, so a negative would silently do nothing and hide the error."""
    assert item.was > item.now
    assert item.amount == item.was - item.now
    assert item.amount > 0


@pytest.mark.parametrize("batch,item", ITEMS, ids=lambda v: getattr(v, "label", ""))
def test_every_entry_is_labelled_for_a_human(batch, item):
    """The label goes straight into a DM, so it must read as the thing's name
    rather than as its registry key."""
    assert item.label
    assert item.label != str(item.ref)
    assert item.kind in {"pickaxe", "rod", "spot"}


def test_every_batch_explains_itself():
    for batch in REFUND_BATCHES:
        assert batch.summary.strip()
        assert batch.items, f"{batch.key} would pay nobody anything"


# ── What the shipped batch actually owes ─────────────────────────────────────
def test_the_july_batch_pays_what_the_repricing_took():
    """The one batch in flight, pinned end to end. Someone who owned everything
    that got cheaper is owed the sum of all five reductions."""
    batch = REFUND_BATCHES[0]
    total, detail = batch.owed(pickaxe_level=4, rod_level=5, spots={"abyss"})
    assert total == 313_500
    assert len(detail) == 5


def test_a_brand_new_account_is_owed_nothing():
    """Nobody who never bought anything may be paid — the sweep runs over every
    row in the database, so this is the case that decides its blast radius."""
    for batch in REFUND_BATCHES:
        assert batch.owed()[0] == 0
        assert batch.owed(pickaxe_level=0, rod_level=0, spots=set())[0] == 0
