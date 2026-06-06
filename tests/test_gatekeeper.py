"""
Tests for cogs/gatekeeper.py — pure logic and perceptual hashing.

The slash-only /gatekeeper command group can't be dispatched through dpytest
(dpytest drives prefix/text commands), so coverage here focuses on the
detection logic (_evaluate) and the perceptual hash helpers.
"""

import io
from datetime import timedelta

import discord
import pytest

import utils.db as db
from cogs import gatekeeper as gk

try:
    from PIL import Image

    _PILLOW_OK = True
except ImportError:  # pragma: no cover
    _PILLOW_OK = False


# ── Perceptual hash helpers ──────────────────────────────────────────────────


def _gradient_png(increasing: bool) -> bytes:
    """A 16x16 horizontal gradient. Increasing → every left<right pixel."""
    im = Image.new("L", (16, 16))
    for x in range(16):
        val = (x if increasing else (15 - x)) * 16
        for y in range(16):
            im.putpixel((x, y), val)
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


@pytest.mark.skipif(not _PILLOW_OK, reason="Pillow not installed")
def test_dhash_identical_image_zero_distance():
    data = _gradient_png(True)
    assert gk._dhash(data) == gk._dhash(data)
    assert gk._hamming(gk._dhash(data), gk._dhash(data)) == 0


@pytest.mark.skipif(not _PILLOW_OK, reason="Pillow not installed")
def test_dhash_opposite_gradients_are_far_apart():
    inc = gk._dhash(_gradient_png(True))
    dec = gk._dhash(_gradient_png(False))
    # Mirror-image gradients flip every comparison bit → maximal distance.
    assert gk._hamming(inc, dec) > gk._DHASH_THRESHOLD


def test_dhash_garbage_bytes_returns_none():
    assert gk._dhash(b"not an image") is None


def test_hamming_basic():
    assert gk._hamming(0b1010, 0b0000) == 2
    assert gk._hamming(0xFF, 0x00) == 8


# ── _evaluate detection logic ─────────────────────────────────────────────────


class _FakeMember:
    def __init__(self, created_at, avatar):
        self.created_at = created_at
        self.avatar = avatar


def _cfg(**overrides) -> dict:
    cfg = dict(db._GK_DEFAULTS)
    cfg.update(overrides)
    return cfg


def _cog() -> gk.Gatekeeper:
    cog = gk.Gatekeeper.__new__(gk.Gatekeeper)
    cog._catalog = []  # empty → stock-avatar check short-circuits, no network
    cog._session = None
    return cog


@pytest.mark.asyncio
async def test_evaluate_young_account_no_avatar():
    cog = _cog()
    member = _FakeMember(discord.utils.utcnow(), avatar=None)
    reasons = await cog._evaluate(member, _cfg())
    assert "new account" in reasons
    assert "no profile picture" in reasons


@pytest.mark.asyncio
async def test_evaluate_old_account_custom_avatar_allowed():
    cog = _cog()
    old = discord.utils.utcnow() - timedelta(days=60)
    member = _FakeMember(old, avatar=object())  # non-None = custom avatar
    reasons = await cog._evaluate(member, _cfg())
    assert reasons == []


@pytest.mark.asyncio
async def test_evaluate_old_account_no_avatar_muted():
    cog = _cog()
    old = discord.utils.utcnow() - timedelta(days=60)
    member = _FakeMember(old, avatar=None)
    reasons = await cog._evaluate(member, _cfg())
    assert reasons == ["no profile picture"]


@pytest.mark.asyncio
async def test_evaluate_and_mode_young_and_no_avatar_muted():
    cog = _cog()
    member = _FakeMember(discord.utils.utcnow(), avatar=None)
    reasons = await cog._evaluate(member, _cfg(match_mode="and"))
    assert reasons == ["new account", "no profile picture"]


@pytest.mark.asyncio
async def test_evaluate_and_mode_young_good_avatar_allowed():
    cog = _cog()
    # Young but has a custom avatar → AND requires both, so no mute.
    member = _FakeMember(discord.utils.utcnow(), avatar=object())
    reasons = await cog._evaluate(member, _cfg(match_mode="and"))
    assert reasons == []


@pytest.mark.asyncio
async def test_evaluate_and_mode_old_no_avatar_allowed():
    cog = _cog()
    old = discord.utils.utcnow() - timedelta(days=60)
    member = _FakeMember(old, avatar=None)
    reasons = await cog._evaluate(member, _cfg(match_mode="and"))
    assert reasons == []


@pytest.mark.asyncio
async def test_evaluate_or_mode_old_no_avatar_muted():
    # OR is the default; an old no-avatar account still gets muted.
    cog = _cog()
    old = discord.utils.utcnow() - timedelta(days=60)
    member = _FakeMember(old, avatar=None)
    reasons = await cog._evaluate(member, _cfg(match_mode="or"))
    assert reasons == ["no profile picture"]


@pytest.mark.asyncio
async def test_evaluate_respects_disabled_toggles():
    cog = _cog()
    member = _FakeMember(discord.utils.utcnow(), avatar=None)
    cfg = _cfg(mute_new_accounts=False, mute_default_avatar=False)
    reasons = await cog._evaluate(member, cfg)
    assert reasons == []


# ── Cog loading (dpytest) ─────────────────────────────────────────────────────


@pytest.mark.cogs("cogs.gatekeeper")
async def test_cog_loads_and_seeds_catalog(bot):
    """cog_load wires up the session, persistent view, and bundled catalog."""
    cog = bot.get_cog("Gatekeeper")
    assert cog is not None
    assert cog._session is not None
    # The bundled assets/gatekeeper_avatars/ seed image(s) are hashed on load.
    assert len(cog._catalog) >= 1


@pytest.mark.asyncio
async def test_evaluate_min_age_boundary():
    cog = _cog()
    # Exactly at the threshold (30d) → not younger than, so no age mute.
    at_threshold = discord.utils.utcnow() - timedelta(
        seconds=db._GK_DEFAULTS["min_account_age"]
    )
    member = _FakeMember(at_threshold, avatar=object())
    reasons = await cog._evaluate(member, _cfg())
    assert "new account" not in reasons
