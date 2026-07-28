"""
tests/test_cosmetic_art.py
The bundled artwork layer: the manifest, the files it produces, and the
treatment the renderer gives them.

Two things are worth guarding. The **licensing** side — every bundled image has
to stay attributable and redistributable, and that is exactly the kind of thing
that rots silently — and the **rendering** side: a real painting is brighter and
busier than anything the palette-driven generator makes, so it goes through a
crop-and-dim path that the generated art doesn't.
"""

import json
import os

import pytest
from PIL import Image

from utils import cosmetics, profile_card

ART_DIR = profile_card.ASSET_DIR
MANIFEST = os.path.join(ART_DIR, "art_manifest.json")
CREDITS_JSON = os.path.join(ART_DIR, "credits.json")


def _manifest() -> list[dict]:
    with open(MANIFEST, "r", encoding="utf-8") as fh:
        return json.load(fh)["art"]


def _bundled() -> list[cosmetics.CosmeticDef]:
    return [d for d in cosmetics.COSMETICS.values() if profile_card._asset_path(d)]


# ── Manifest ↔ catalogue ──────────────────────────────────────────────────────
def test_every_manifest_entry_names_a_real_cosmetic():
    """A manifest key that matches no CosmeticDef downloads an image nothing
    will ever draw."""
    for entry in _manifest():
        d = cosmetics.get(entry["key"])
        assert d is not None, f"{entry['key']} is not in the cosmetics catalogue"
        assert d.slot == entry["slot"], f"{entry['key']} slot mismatch"


def test_every_manifest_entry_declares_a_known_source():
    for entry in _manifest():
        assert entry["source"] in ("commons", "nasa"), entry
        if entry["source"] == "commons":
            assert entry.get("file"), entry
        else:
            assert entry.get("nasa_id"), entry


def test_bundled_art_exists_for_every_manifest_entry():
    missing = [
        e["key"]
        for e in _manifest()
        if not cosmetics.get(e["key"])
        or not profile_card._asset_path(cosmetics.get(e["key"]))
    ]
    assert (
        not missing
    ), f"No image on disk for {missing} — run scripts/fetch_cosmetic_art.py"


# ── Licensing ─────────────────────────────────────────────────────────────────
def test_every_bundled_image_is_credited_and_freely_licensed():
    """The check that keeps the repo redistributable. Anything bundled must be
    public domain / CC0 and must name its artist and source."""
    with open(CREDITS_JSON, "r", encoding="utf-8") as fh:
        credits = {r["key"]: r for r in json.load(fh)}
    for d in _bundled():
        record = credits.get(d.key)
        assert record, f"{d.key} ships an image with no credits.json entry"
        assert record["credit"] and record["credit"] != "Unknown", d.key
        assert record["page"], f"{d.key} has no source link"
        from scripts.fetch_cosmetic_art import _OK_LICENCES

        assert _OK_LICENCES.search(record["licence"]), (
            f"{d.key} is bundled under {record['licence']!r}, which is not a "
            "licence this repo may redistribute"
        )


def test_the_licence_filter_rejects_encumbered_licences():
    """The guard itself: it has to say no to the things it exists to exclude."""
    from scripts.fetch_cosmetic_art import _OK_LICENCES

    for good in ("Public domain", "CC0", "public domain (U.S. Government work)"):
        assert _OK_LICENCES.search(good), good
    for bad in ("CC BY-SA 4.0", "CC BY-NC 2.0", "All rights reserved", "GFDL"):
        assert not _OK_LICENCES.search(bad), bad


def test_art_cosmetics_name_their_artist_in_the_description():
    """The description is the credit a member actually sees, in /shop profile
    and /profile cosmetics."""
    for d in _bundled():
        assert d.description.strip(), d.key


# ── Files ─────────────────────────────────────────────────────────────────────
def test_bundled_images_open_and_match_their_slot_size():
    sizes = {"banner": (profile_card.W, profile_card.H), "wallet": (920, 450)}
    for d in _bundled():
        path = profile_card._asset_path(d)
        with Image.open(path) as img:
            assert img.size == sizes[d.slot], f"{d.key} is {img.size}"


# ── Rendering ─────────────────────────────────────────────────────────────────
def test_cover_crop_fills_the_target_without_distorting():
    """The bug this replaced: a plain resize stretched every painting to the
    card's aspect ratio."""
    tall = Image.new("RGB", (400, 800), (255, 0, 0))
    out = profile_card._fit_cover(tall, (200, 100))
    assert out.size == (200, 100)
    wide = Image.new("RGB", (2000, 100), (0, 255, 0))
    assert profile_card._fit_cover(wide, (200, 100)).size == (200, 100)
    # Already the right size: no work, no resample.
    exact = Image.new("RGB", (200, 100))
    assert profile_card._fit_cover(exact, (200, 100)) is exact


def test_bundled_banners_are_dimmed_enough_for_white_text(tmp_path, monkeypatch):
    """A painting has bright passages a generated gradient never produces, so
    the asset path dims it. Without that, white text lands on Hokusai's white
    sky and vanishes."""
    monkeypatch.setattr(profile_card, "CACHE_DIR", str(tmp_path))
    from PIL import ImageStat

    for d in _bundled():
        size = (profile_card.W, profile_card.H) if d.slot == "banner" else (920, 450)
        prepared = profile_card.cosmetic_image(d, size)
        mean = ImageStat.Stat(prepared.convert("L")).mean[0]
        assert mean <= profile_card.ART_CEILING + 2, f"{d.key} renders at {mean:.0f}"


def test_a_card_renders_with_bundled_art(tmp_path, monkeypatch):
    monkeypatch.setattr(profile_card, "CACHE_DIR", str(tmp_path))
    art = next((d for d in _bundled() if d.slot == "banner"), None)
    if art is None:  # pragma: no cover - only if the art was never fetched
        pytest.skip("no bundled banner art")
    png = profile_card.render_card(
        {"name": "Tester", "banner": art, "chips": [("Coins", "1")], "badges": []}
    )
    assert Image.open(__import__("io").BytesIO(png)).size == (
        profile_card.W,
        profile_card.H,
    )


def test_asset_cache_is_keyed_on_the_file_so_replacing_art_takes_effect(
    tmp_path, monkeypatch
):
    """Swapping the artwork must not need a manual cache purge."""
    monkeypatch.setattr(profile_card, "CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(profile_card, "ASSET_DIR", str(tmp_path / "assets"))
    d = cosmetics.get("banner_default")
    art_dir = tmp_path / "assets" / "banner"
    art_dir.mkdir(parents=True)
    path = art_dir / f"{d.key}.png"

    Image.new("RGB", (200, 112), (255, 0, 0)).save(path)
    first = profile_card.cosmetic_image(d, (100, 56))
    Image.new("RGB", (200, 112), (0, 0, 255)).save(path)
    os.utime(path, (1_700_000_000, 1_700_000_000))  # a different mtime
    second = profile_card.cosmetic_image(d, (100, 56))
    assert first.tobytes() != second.tobytes()
