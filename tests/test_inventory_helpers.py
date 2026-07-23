"""
Tests for the pure inventory helpers + the shared item catalogue.
"""

import pytest

from cogs.inventory import CHEST_COINS_MAX, CHEST_COINS_MIN, chest_payout
from utils import items


# ── chest_payout ───────────────────────────────────────────────────────────────
def test_chest_payout_bounds():
    assert chest_payout(0.0) == CHEST_COINS_MIN
    assert chest_payout(1.0) == CHEST_COINS_MAX
    assert chest_payout(-5.0) == CHEST_COINS_MIN
    assert chest_payout(5.0) == CHEST_COINS_MAX


def test_chest_payout_monotonic():
    assert chest_payout(0.2) <= chest_payout(0.8)


# ── item catalogue ─────────────────────────────────────────────────────────────
def test_catalogue_entries_are_well_formed():
    assert items.ITEMS, "catalogue should not be empty"
    for key, d in items.ITEMS.items():
        assert d.key == key
        assert d.name and d.emoji
        assert d.category in items.CATEGORY_ORDER
        assert d.value >= 0 and d.price >= 0
        if d.effect:
            assert "key" in d.effect
            # Timed or charge-based, never both.
            assert not (d.effect.get("duration") and d.effect.get("uses"))


def test_find_by_key_and_name():
    d = items.find("lucky_charm")
    assert d is not None
    assert items.find("Lucky Charm") is d
    assert items.find("LUCKY CHARM") is d
    assert items.find("no-such-item") is None


def test_display_falls_back_to_raw_key():
    assert items.display("no-such-item") == "no-such-item"
    assert items.display("treasure_key").endswith("Treasure Key")


def test_reregister_identical_ok_conflict_raises():
    d = items.ITEMS["treasure_key"]
    items.register(d)  # idempotent double-import
    clash = items.ItemDef(key="treasure_key", name="Other", emoji="❓", category="misc")
    with pytest.raises(ValueError):
        items.register(clash)
