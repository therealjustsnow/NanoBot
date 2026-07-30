"""
tests/test_trophy_card.py
The /progress badges trophy case (utils/trophy_card.py) and the pure
progression helpers that decide what stands in it.

Same contract as the other card renderers: the card must never be the reason a
command fails, so it has to produce an image for a maxed account, a brand-new
one, a broken avatar and a very long name. It is Discord- and DB-free, so
everything here is a plain dict in and bytes out.
"""

import io

import pytest
from PIL import Image, ImageStat

from cogs.progression.constants import TROPHY_TIER_LABELS, TROPHY_TIER_POINTS
from cogs.progression.definitions import ACHIEVEMENTS, CATEGORY_ACCENTS
from cogs.progression.helpers import category_accent, trophy_groups, trophy_tier
from utils import cosmetics, profile_card, trophy_card

FULL = {
    "name": "Longestpossible Displayname Here",
    "title": "Master Angler",
    "points": 1_240,
    "prestige": 7,
    "legend": list(zip(range(len(TROPHY_TIER_LABELS)), TROPHY_TIER_LABELS)),
    "footer": "/progress achievements for what's next",
}


def _groups(earned=True):
    return trophy_groups(ACHIEVEMENTS, [a.key for a in ACHIEVEMENTS] if earned else [])


def _render(data: dict) -> Image.Image:
    """Render and decode, asserting the bytes really are the format the cog
    names the attachment after."""
    raw = trophy_card.render_trophy_case(data)
    img = Image.open(io.BytesIO(raw))
    assert img.format == profile_card.IMAGE_FORMAT
    return img


# ── The renderer ─────────────────────────────────────────────────────────────
def test_renders_a_full_case():
    img = _render(dict(FULL, groups=_groups()))
    assert img.size == trophy_card.case_size(_groups())


def test_renders_a_brand_new_account():
    """Every trophy locked, no title, no prestige — the case is still drawn in
    full, because it doubles as the list of what's left to earn."""
    img = _render({"name": "Fresh", "groups": _groups(earned=False)})
    assert img.size == trophy_card.case_size(_groups(earned=False))


def test_renders_with_no_groups_at_all():
    """A caller with nothing to show must still get a card back rather than a
    traceback (a zero-shelf case would divide by zero on the completion bar)."""
    assert _render({"name": "Nobody"}).size[0] == trophy_card.W


def test_a_broken_avatar_degrades_to_an_initial_tile():
    img = _render(dict(FULL, groups=_groups(), avatar=b"not an image"))
    assert img.size == trophy_card.case_size(_groups())


def test_the_case_is_dressed_in_the_members_cosmetics():
    img = _render(
        dict(
            FULL,
            groups=_groups(),
            banner=cosmetics.get("banner_default"),
            border=cosmetics.get("border_gold"),
        )
    )
    assert img.size == trophy_card.case_size(_groups())


def test_rendering_is_deterministic():
    """Generated art is seeded on the cosmetic key and every trophy is drawn
    from its tier, so two renders of the same data are byte-identical — which
    is what keeps the trophy cache and the art cache safe."""
    data = dict(FULL, groups=_groups())
    assert trophy_card.render_trophy_case(data) == trophy_card.render_trophy_case(data)


def test_the_case_grows_a_shelf_at_a_time():
    """Layout is derived, so adding achievements needs no layout change: one
    more shelf is one more row of height, and a shelf holds COLS trophies."""
    one = [{"label": "One", "items": [{"name": "A", "tier": 0, "earned": True}]}]
    two = [
        {
            "label": "Two",
            "items": [
                {"name": str(i), "tier": 0, "earned": True}
                for i in range(trophy_card.COLS + 1)
            ],
        }
    ]
    assert (
        trophy_card.case_size(two)[1] - trophy_card.case_size(one)[1]
        == trophy_card.ROW_PITCH
    )


def test_every_tier_draws_something_and_a_locked_one_is_dimmer():
    """The shape ladder is the point of drawing trophies at all, so each tier
    has to render — and a locked trophy must read as a ghost of the earned one
    rather than as the same object."""
    for tier in range(len(trophy_card.TIER_FORMS)):
        earned = trophy_card.trophy_image(tier, "#3BA7FF", True)
        locked = trophy_card.trophy_image(tier, "#3BA7FF", False)
        assert earned.size == locked.size == (trophy_card.ART_W, trophy_card.ART_H)
        assert earned.getbbox() is not None, "an earned trophy drew nothing"
        assert locked.getbbox() is not None, "a locked trophy drew nothing"
        # Same silhouette, far less ink.
        assert _ink(locked) < _ink(earned) / 2


def _ink(img: Image.Image) -> float:
    """Mean opacity — how solidly the tile is drawn on."""
    return ImageStat.Stat(img.convert("RGBA").getchannel("A")).mean[0]


def test_an_out_of_range_tier_never_raises():
    """Tiers come from a points table that can be re-tuned; a value past the
    end of the ladder must clamp rather than take the whole card down."""
    assert trophy_card.trophy_image(99, "#FFFFFF", True).getbbox() is not None
    assert trophy_card.trophy_image(-3, "not a colour", True).getbbox() is not None


# ── The progression side ─────────────────────────────────────────────────────
def test_trophy_tier_climbs_with_points():
    assert trophy_tier(0) == 0
    assert trophy_tier(TROPHY_TIER_POINTS[0] - 1) == 0
    for i, threshold in enumerate(TROPHY_TIER_POINTS):
        assert trophy_tier(threshold) == i + 1
    assert trophy_tier(10_000) == len(TROPHY_TIER_POINTS)


def test_every_tier_has_a_trophy_to_draw():
    """One legend row and one silhouette per tier — a points table with more
    tiers than the card can draw would silently render them all as stars."""
    assert len(TROPHY_TIER_LABELS) == len(TROPHY_TIER_POINTS) + 1
    assert len(trophy_card.TIER_FORMS) == len(TROPHY_TIER_LABELS)
    assert len(trophy_card.TIER_METALS) == len(trophy_card.TIER_FORMS)


def test_every_category_has_its_own_accent():
    """The accent is how a case says where a trophy came from, so two
    categories sharing one (or a category missing one) loses that."""
    categories = {a.category for a in ACHIEVEMENTS}
    assert categories <= set(CATEGORY_ACCENTS)
    assert len(set(CATEGORY_ACCENTS.values())) == len(CATEGORY_ACCENTS)
    for category in categories:
        assert category_accent(category).startswith("#")


def test_trophy_groups_covers_the_whole_registry_in_order():
    groups = trophy_groups(ACHIEVEMENTS, ["fish_caught_10"])
    items = [item for group in groups for item in group["items"]]
    assert len(items) == len(ACHIEVEMENTS)
    assert [g["label"] for g in groups] == list(
        dict.fromkeys(a.category.split(" ", 1)[-1] for a in ACHIEVEMENTS)
    )
    earned = [i["name"] for i in items if i["earned"]]
    assert earned == ["Bait & Switch"]


def test_group_labels_carry_no_emoji_the_card_font_cant_draw():
    """A category label is a heading on a drawn card, not a chat line — a
    colour emoji renders as tofu there."""
    for group in trophy_groups(ACHIEVEMENTS, []):
        assert group["label"] == profile_card._font_safe(group["label"])
        assert group["label"].isascii()


@pytest.mark.parametrize("earned", [True, False])
def test_a_case_from_the_real_registry_renders(earned):
    """The registry is what actually gets drawn, so render it both ways: forty
    plaques of real names is where a layout runs out of room."""
    groups = _groups(earned=earned)
    assert _render(dict(FULL, groups=groups)).size == trophy_card.case_size(groups)
