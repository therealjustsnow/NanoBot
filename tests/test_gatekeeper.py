"""
Tests for cogs/gatekeeper.py — pure logic and perceptual hashing.

The slash-only /gatekeeper command group can't be dispatched through dpytest
(dpytest drives prefix/text commands), so coverage here focuses on the
detection logic (_evaluate) and the perceptual hash helpers.
"""

import io
import time as time_module
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


# ── learnavatar SSRF guard (_is_safe_public_url) ──────────────────────────────
# Only IP-literal hosts and scheme checks here — no hostname is resolved, so the
# suite stays offline/CI-safe (no DNS dependency).


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/",  # cloud metadata (link-local)
        "http://127.0.0.1/x.png",  # loopback
        "http://10.0.0.5/x.png",  # private range
        "http://192.168.1.1/a",  # private range
        "http://172.16.0.1/a",  # private range
        "http://[::1]/x.png",  # IPv6 loopback
        "http://0.0.0.0/x",  # unspecified
        "file:///etc/passwd",  # non-http scheme
        "ftp://198.51.100.7/x",  # non-http scheme
        "not a url",  # unparseable / no host
        "http:///nohost.png",  # missing host
    ],
)
def test_is_safe_public_url_rejects(url):
    assert gk._is_safe_public_url(url) is False


@pytest.mark.parametrize(
    "url",
    [
        "http://8.8.8.8/avatar.png",  # public IP literal
        "https://93.184.216.34/x.png",  # public IP literal
    ],
)
def test_is_safe_public_url_allows_public_ip_literals(url):
    assert gk._is_safe_public_url(url) is True


# ── captcha brute-force throttle ──────────────────────────────────────────────


def _bare_gatekeeper():
    # Skip Cog.__init__ wiring; we only exercise the throttle bookkeeping.
    cog = gk.Gatekeeper.__new__(gk.Gatekeeper)
    cog._verify_attempts = {}
    cog._verify_blocked_until = {}
    return cog


def test_verify_throttle_locks_after_max_wrong_answers():
    cog = _bare_gatekeeper()
    uid = 123
    assert cog._verify_lockout_remaining(uid) == 0
    # The first MAX-1 wrong answers don't lock.
    for _ in range(gk._MAX_VERIFY_ATTEMPTS - 1):
        assert cog._register_wrong_answer(uid) is False
    # The MAX-th wrong answer trips the lockout.
    assert cog._register_wrong_answer(uid) is True
    assert cog._verify_lockout_remaining(uid) > 0
    # Attempt counter is reset once locked out (cooldown is what gates now).
    assert uid not in cog._verify_attempts


def test_verify_throttle_clear_resets_state():
    cog = _bare_gatekeeper()
    uid = 7
    cog._register_wrong_answer(uid)
    cog._verify_blocked_until[uid] = time_module.time() + 999
    cog._clear_verify_throttle(uid)
    assert cog._verify_lockout_remaining(uid) == 0
    assert uid not in cog._verify_attempts
    assert uid not in cog._verify_blocked_until


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
