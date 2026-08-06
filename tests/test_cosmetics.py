"""
tests/test_cosmetics.py
The cosmetics catalogue + the profile-card renderer.

Two things worth guarding here. First, the catalogue is meant to be extended by
*data* — a registry entry or a line of JSON — so these check the contract that
makes that safe (valid slots, evaluable unlock rules, glyphs the bundled font
can actually draw). Second, the renderer must never be the reason a command
fails: it has to produce an image for a brand-new account, a maxed one, and a
member whose avatar didn't download.
"""

import io
import json
import os

import aiosqlite
import pytest
from PIL import Image, ImageFont

import utils.db as db
from cogs.identity.helpers import (
    announce_channel_id,
    equip_result,
    newly_unlocked,
    unlock_context,
)
from utils import cosmetics, profile_card

USER = 9001


@pytest.fixture
async def identity_db(monkeypatch):
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    monkeypatch.setattr(db, "_db", conn)
    await db._ensure_identity_tables()
    yield conn
    await conn.close()


# ── Catalogue integrity ───────────────────────────────────────────────────────
def test_every_cosmetic_is_well_formed():
    assert cosmetics.COSMETICS, "the built-in catalogue should not be empty"
    for key, d in cosmetics.COSMETICS.items():
        assert d.key == key
        assert d.slot in cosmetics.SLOTS
        assert d.name and d.description is not None
        assert d.palette, f"{key} has no palette to generate art from"
        assert (d.unlock or {}).get("kind"), f"{key} has no unlock rule"


def test_every_slot_has_at_least_one_cosmetic_and_a_sane_limit():
    for key, slot in cosmetics.SLOTS.items():
        assert slot.max_equipped >= 1
        assert cosmetics.in_slot(key), f"slot {key} has nothing in it"


def test_badge_glyphs_render_in_the_bundled_font():
    """A glyph the font lacks draws as a tofu box — catch that here rather than
    on someone's profile card."""
    path = next((p for p in profile_card._FONT_CANDIDATES if os.path.exists(p)), None)
    if path is None:  # pragma: no cover - bare container without fonts
        pytest.skip("no TrueType font available")
    font = ImageFont.truetype(path, 40)
    missing = bytes(font.getmask("￿"))
    bad = [
        d.key
        for d in cosmetics.in_slot("badge")
        if bytes(font.getmask(d.glyph)) == missing
    ]
    assert not bad, f"badge glyphs not covered by the font: {bad}"


def test_default_loadout_only_references_real_cosmetics():
    for slot, keys in cosmetics.DEFAULT_LOADOUT.items():
        assert slot in cosmetics.SLOTS
        for key in keys:
            d = cosmetics.get(key)
            assert d is not None and d.slot == slot
            # Defaults must be wearable without unlocking anything.
            assert (d.unlock or {}).get("kind") == "default"


def test_find_matches_key_or_display_name():
    assert cosmetics.find("badge_developer").key == "badge_developer"
    assert cosmetics.find("Developer").key == "badge_developer"
    assert cosmetics.find("  gilded frame ").key == "border_gold"
    assert cosmetics.find("nope") is None


def test_registering_a_conflicting_key_is_rejected():
    d = cosmetics.COSMETICS["badge_developer"]
    cosmetics.register(d)  # identical re-register is a no-op
    with pytest.raises(ValueError):
        cosmetics.register(
            cosmetics.CosmeticDef(key=d.key, name="Something else", slot="badge")
        )


def test_unknown_slot_is_rejected():
    with pytest.raises(ValueError):
        cosmetics.register(
            cosmetics.CosmeticDef(key="x_test", name="X", slot="not_a_slot")
        )


# ── Unlock rules ──────────────────────────────────────────────────────────────
def test_unlock_rules_evaluate_against_a_context():
    ctx = unlock_context(
        global_level=25,
        prestige=1,
        achievements={"fish_caught_10"},
        stats={"fish_caught": 300, "casino_games": 5},
    )
    assert cosmetics.is_unlocked(cosmetics.get("banner_default"), ctx) is True
    assert cosmetics.is_unlocked(cosmetics.get("banner_ember"), ctx) is True  # level 25
    assert cosmetics.is_unlocked(cosmetics.get("banner_aurora"), ctx) is False  # 50
    assert cosmetics.is_unlocked(cosmetics.get("banner_royal"), ctx) is True  # prestige
    assert cosmetics.is_unlocked(cosmetics.get("banner_ocean"), ctx) is True  # 250 fish
    assert cosmetics.is_unlocked(cosmetics.get("badge_high_roller"), ctx) is False
    # Manual/event grants are never auto-unlocked.
    assert cosmetics.is_unlocked(cosmetics.get("badge_developer"), ctx) is False


def test_newly_unlocked_skips_what_you_already_own():
    ctx = unlock_context(global_level=60, prestige=0, achievements=(), stats={})
    fresh = {d.key for d in newly_unlocked([], ctx)}
    assert "banner_ember" in fresh and "badge_veteran" in fresh
    owned = {d.key for d in newly_unlocked(fresh, ctx)}
    assert owned == set()


def test_describe_unlock_reads_as_a_sentence():
    assert "level" in cosmetics.describe_unlock(cosmetics.get("banner_ember")).lower()
    assert (
        "prestige" in cosmetics.describe_unlock(cosmetics.get("banner_royal")).lower()
    )
    assert (
        "staff" in cosmetics.describe_unlock(cosmetics.get("badge_developer")).lower()
    )


# ── Equipping (pure) ──────────────────────────────────────────────────────────
def test_single_slots_swap_and_multi_slots_fill_then_report_full():
    keys, outcome = equip_result("banner", ["banner_default"], "banner_ember")
    assert (keys, outcome) == (["banner_ember"], "equipped")

    keys, outcome = equip_result("badge", ["badge_veteran"], "badge_angler")
    assert (keys, outcome) == (["badge_veteran", "badge_angler"], "equipped")

    keys, outcome = equip_result("badge", ["badge_veteran"], "badge_veteran")
    assert outcome == "already"

    full = [f"badge_{i}" for i in range(cosmetics.SLOTS["badge"].max_equipped)]
    keys, outcome = equip_result("badge", full, "badge_angler")
    assert outcome == "full" and keys == full


# ── Global level-up routing (pure) ────────────────────────────────────────────
def _cfg(**kw):
    base = {
        "announce": True,
        "announce_channel": None,
        "global_announce": True,
        "global_announce_channel": None,
    }
    base.update(kw)
    return base


def test_global_levelup_goes_where_they_are_talking_by_default():
    assert announce_channel_id(_cfg(), set(), 42) == 42


def test_global_levelup_channel_overrides_everything_else():
    cfg = _cfg(global_announce_channel=99, announce_channel=77)
    assert announce_channel_id(cfg, {99}, 42) == 99  # even an XP-ignored one


def test_global_levelup_inherits_the_server_announce_channel():
    """A server that already routed level-ups shouldn't have to say it twice."""
    assert announce_channel_id(_cfg(announce_channel=77), set(), 42) == 77


def test_server_announce_off_does_not_silence_global_levelups():
    """Only the *channel* is inherited — the two systems keep their own switch."""
    cfg = _cfg(announce=False, announce_channel=77)
    assert announce_channel_id(cfg, set(), 42) == 77


def test_global_levelup_skips_a_channel_that_earns_no_xp():
    """The venting-channel case: excluded from XP means excluded from this."""
    assert announce_channel_id(_cfg(), {42}, 42) is None


def test_global_levelup_can_be_switched_off_for_a_server():
    cfg = _cfg(global_announce=False, global_announce_channel=99)
    assert announce_channel_id(cfg, set(), 42) is None


def test_global_levelup_with_nowhere_to_post_falls_through():
    assert announce_channel_id(_cfg(), set(), None) is None


def test_global_levelup_defaults_hold_for_a_config_without_the_keys():
    """An old cached/config dict must not turn announcements off."""
    assert announce_channel_id({}, set(), 42) == 42


def test_global_xp_disabled_skips_the_guild_even_with_a_pinned_channel():
    """The whole-system opt-out wins over everything else `/level
    globalannounce` can configure — a pinned channel included."""
    cfg = _cfg(global_xp_enabled=False, global_announce_channel=99)
    assert announce_channel_id(cfg, set(), 42) is None


def test_global_xp_enabled_defaults_true_for_a_config_without_the_key():
    """An old cached/config dict predates this key and must not opt a guild
    out by accident."""
    assert announce_channel_id({}, set(), 42) == 42


# ── JSON extension ────────────────────────────────────────────────────────────
def test_cosmetics_can_be_added_from_a_json_file(tmp_path):
    """The 'no code change' path: a seasonal badge is one JSON entry."""
    path = tmp_path / "cosmetics.json"
    path.write_text(
        json.dumps(
            [
                {
                    "key": "badge_test_seasonal",
                    "name": "Test Seasonal",
                    "slot": "badge",
                    "glyph": "✿",
                    "palette": ["#123456", "#654321"],
                    "unlock": {"kind": "global_level", "value": 3},
                },
                {"key": "broken", "slot": "badge"},  # missing name → skipped
            ]
        )
    )
    try:
        added = cosmetics.load_file(str(path))
        assert added == 1
        d = cosmetics.get("badge_test_seasonal")
        assert d is not None and d.slot == "badge"
        ctx = unlock_context(global_level=5, prestige=0, achievements=(), stats={})
        assert cosmetics.is_unlocked(d, ctx) is True
    finally:
        cosmetics.COSMETICS.pop("badge_test_seasonal", None)


def test_missing_cosmetics_file_is_fine():
    assert cosmetics.load_file("does/not/exist.json") == 0


# ── Storage ───────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_unlock_is_one_time_and_equipping_round_trips(identity_db):
    assert await db.unlock_cosmetic(USER, "badge_veteran", at=1.0) is True
    assert await db.unlock_cosmetic(USER, "badge_veteran", at=2.0) is False
    assert "badge_veteran" in await db.get_unlocked_cosmetics(USER)

    await db.set_equipped(USER, "badge", ["badge_veteran", "badge_angler"])
    assert (await db.get_equipped(USER))["badge"] == ["badge_veteran", "badge_angler"]
    # Order is the display order, so re-equipping rewrites positions.
    await db.set_equipped(USER, "badge", ["badge_angler"])
    assert (await db.get_equipped(USER))["badge"] == ["badge_angler"]
    await db.set_equipped(USER, "badge", [])
    assert "badge" not in await db.get_equipped(USER)


@pytest.mark.asyncio
async def test_revoking_takes_it_off_the_card_too(identity_db):
    await db.unlock_cosmetic(USER, "badge_veteran")
    await db.set_equipped(USER, "badge", ["badge_veteran"])
    assert await db.revoke_cosmetic(USER, "badge_veteran") is True
    assert await db.get_unlocked_cosmetics(USER) == {}
    assert await db.get_equipped(USER) == {}


# ── Rendering ─────────────────────────────────────────────────────────────────
def _card(**overrides):
    data = {
        "name": "Tester",
        "title": "Master Angler",
        "prestige": 3,
        "global_level": 42,
        "global_into": 500,
        "global_need": 2_150,
        "server_level": 7,
        "server_into": 120,
        "server_need": 400,
        "server_enabled": True,
        "chips": [("Coins", "1,234 NanoCoins"), ("Items", "12 carried")],
        "badges": [cosmetics.get("badge_veteran"), cosmetics.get("badge_angler")],
        "banner": cosmetics.get("banner_aurora"),
        "border": cosmetics.get("border_gold"),
        "nameplate": cosmetics.get("plate_neon"),
    }
    data.update(overrides)
    return data


def test_card_renders_an_image_of_the_expected_size(tmp_path, monkeypatch):
    monkeypatch.setattr(profile_card, "CACHE_DIR", str(tmp_path / "cache"))
    png = profile_card.render_card(_card())
    assert Image.open(io.BytesIO(png)).format == profile_card.IMAGE_FORMAT
    img = Image.open(__import__("io").BytesIO(png))
    assert img.size == (profile_card.W, profile_card.H)


def test_card_renders_for_a_brand_new_account(tmp_path, monkeypatch):
    """Level 0, no badges, no cosmetics, no avatar — still an image."""
    monkeypatch.setattr(profile_card, "CACHE_DIR", str(tmp_path / "cache"))
    png = profile_card.render_card(
        {
            "name": "New Person",
            "global_level": 0,
            "global_into": 0,
            "global_need": 100,
            "server_enabled": False,
            "chips": [],
            "badges": [],
        }
    )
    assert Image.open(io.BytesIO(png)).format == profile_card.IMAGE_FORMAT


def test_card_survives_a_broken_avatar_and_a_very_long_name(tmp_path, monkeypatch):
    monkeypatch.setattr(profile_card, "CACHE_DIR", str(tmp_path / "cache"))
    png = profile_card.render_card(_card(name="A" * 60, avatar=b"this is not an image"))
    assert Image.open(io.BytesIO(png)).format == profile_card.IMAGE_FORMAT


def test_generated_art_is_cached_on_disk(tmp_path, monkeypatch):
    monkeypatch.setattr(profile_card, "CACHE_DIR", str(tmp_path / "cache"))
    d = cosmetics.get("badge_veteran")
    first = profile_card.cosmetic_image(d, (56, 56))
    cached = profile_card._cached(d, (56, 56))
    assert os.path.exists(cached)
    assert profile_card.cosmetic_image(d, (56, 56)).size == first.size


def test_a_real_asset_file_wins_over_generated_art(tmp_path, monkeypatch):
    """Dropping in artwork later must not need a code change."""
    monkeypatch.setattr(profile_card, "CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(profile_card, "ASSET_DIR", str(tmp_path / "assets"))
    d = cosmetics.get("badge_veteran")
    art_dir = tmp_path / "assets" / "badge"
    art_dir.mkdir(parents=True)
    Image.new("RGBA", (8, 8), (255, 0, 0, 255)).save(art_dir / f"{d.key}.png")
    img = profile_card.cosmetic_image(d, (16, 16))
    assert img.getpixel((8, 8))[:3] == (255, 0, 0)


def test_prestige_emblem_changes_with_rank(tmp_path, monkeypatch):
    monkeypatch.setattr(profile_card, "CACHE_DIR", str(tmp_path / "cache"))
    low = profile_card.prestige_emblem(1)
    high = profile_card.prestige_emblem(9)
    assert low.size == high.size
    assert low.tobytes() != high.tobytes()  # visibly different tiers
