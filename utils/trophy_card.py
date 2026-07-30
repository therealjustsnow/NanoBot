"""utils/trophy_card.py — renders the /progress badges trophy case as an image.

The fourth card, after the profile, the cookie jar and the wallet, and built to
the same contract: a plain dict in, encoded image bytes out, no Discord and no
database. Only the pure drawing primitives are imported from
``utils/profile_card.py`` — duplicating a gradient routine to avoid an import is
how two cards stop looking like one bot.

Why a drawn case rather than a row of emoji: an achievement's emoji is a colour
emoji, and the bundled font has none, so a wall of them renders as tofu the
moment it leaves Discord's own text rendering. Every trophy here is therefore
*drawn* — and once you are drawing them anyway, they can work the way real
trophies do, which is that you can tell what one was won for from across the
room:

  * the **figure** on top is the subject — a fish, a pair of antlers, a die, a
    hard hat — chosen by what the achievement measures (`TOPPERS`);
  * the **stand** under it is what it cost — a plain block, then a column, then
    a stepped plinth, then a laurel wreath (`TIER_STANDS`) — in bronze, silver,
    gold or prismatic (`TIER_METALS`), and drawn taller with each tier
    (`TIER_SCALE`) so the big ones tower over the small ones;
  * the **plate** on the plinth is the category's colour.

So a case reads at a glance — a row of short bronze pieces is a new account, a
gold fish towering over the fishing shelf is a specialist — and reads *exactly*
on a second look, without a legend for the figures.

Locked achievements are drawn too, as flat ghosts of the trophy that goes there.
A case with holes in it is the discovery UI — it shows what is missing and how
big it is — and it means a brand-new account gets a full case rather than an
empty rectangle.

Layout is derived, not fixed: the card sizes itself to however many shelves the
catalogue needs (``case_size``), so adding achievements needs no layout change.

Rendering is CPU-bound: call it through ``asyncio.to_thread`` (the cog does).
"""

from __future__ import annotations

import functools
import io
import math

from PIL import Image, ImageChops, ImageDraw, ImageFilter

from utils import cosmetics
from utils.profile_card import IMAGE_EXT  # re-exported: the cogs name files with it
from utils.profile_card import _asset_path as _art_file
from utils.profile_card import (
    INK,
    INK_FAINT,
    INK_MUTED,
    PANEL_FILL,
    _circle_mask,
    _draw_bar,
    _draw_chip,
    _fit_text,
    _font,
    _font_safe,
    _gradient,
    _palette,
    _rgba,
    _rounded_mask,
    cosmetic_image,
    draw_border,
    encode,
    prestige_emblem,
)

__all__ = [
    "render_trophy_case",
    "case_size",
    "trophy_image",
    "TOPPERS",
    "COLS",
    "IMAGE_EXT",
]

# ── Layout ───────────────────────────────────────────────────────────────────
W = 1040
PAD = 40
RADIUS = 28

AVATAR_SIZE = 104
AVATAR_XY = (PAD, PAD)
TEXT_X = PAD + AVATAR_SIZE + 24

EYEBROW_Y = PAD + 2
NAME_Y = PAD + 20
TITLE_Y = NAME_Y + 48

BAR_H = 14
BAR_Y = 140
CHIP_TOP = 176
CHIP_H = 62
CHIP_GAP = 14
CHIP_COLS = 4

CASE_TOP = CHIP_TOP + CHIP_H + 26
FRAME = 14  # the cabinet's own frame, drawn inside the case bounds
INNER_PAD = 16

COLS = 8  # trophies per shelf
CELL_W = (W - PAD * 2 - (FRAME + INNER_PAD) * 2) // COLS
ART_W, ART_H = 66, 84  # the trophy itself
LABEL_H = 18  # the category heading at the top of a shelf
PLATE_H = 28  # the two-line name plaque under a trophy
SLAB_H = 12  # the shelf board the row stands on
ROW_GAP = 10
ROW_PITCH = LABEL_H + ART_H + 3 + PLATE_H + SLAB_H + ROW_GAP

FOOTER_H = 34

# ── Palette ──────────────────────────────────────────────────────────────────
CASE_BACK_TOP = (14, 15, 22, 235)  # the cabinet interior, darkest at the top
CASE_BACK_BOTTOM = (26, 28, 40, 235)
FRAME_LIGHT = _rgba("#6B4A2A")
FRAME_DARK = _rgba("#33210F")
SLAB_TOP = _rgba("#7A542F")
SLAB_FACE = _rgba("#4A3018")
SHELF_LIGHT = (255, 236, 190, 34)  # the warm strip under each shelf board
STONE_TOP = _rgba("#3A3F52")
STONE_BOTTOM = _rgba("#20232F")

# Metal by tier. Same ladder the prestige emblem climbs, so the two read as one
# ranking rather than two colour schemes.
TIER_METALS = (
    ("#CD7F32", "#6F3F16"),  # 0 bronze
    ("#E4E6EC", "#83868F"),  # 1 silver
    ("#FFD86B", "#A87400"),  # 2 gold
    ("#A9EEFF", "#6C3DF4"),  # 3 prismatic
)

GHOST_METAL = (255, 255, 255, 26)  # a locked trophy: the shape, none of the shine
GHOST_STONE = (255, 255, 255, 16)

_SUPERSAMPLE = 4


# ── The figures ──────────────────────────────────────────────────────────────
# A real trophy tells you what it was won for before you read the plate: the
# thing on top is a fish, or a pair of antlers, or a die. So the figure is the
# achievement's *subject* — one function per topper, registered by name in
# `TOPPERS` and selected by whatever the achievement measures — while the stand
# under it carries the tier. A new topper is one function plus one registry
# line, the same contract the banner patterns in utils/profile_card.py use.
#
# Each takes the metal mask and the box (x0, y0, x1, y1) it may draw in, and
# fills 255 for metal. Filling 0 cuts a hole back out, which is how detail that
# survives at 40px gets drawn — pips in a die, eye holes in a mask, a
# fish-shaped void in a coin — rather than by stacking colours that vanish at
# this size. They take the mask itself rather than a Draw so a figure can
# compose rotated sub-masks (see the fanned cards).


def _box(box):
    """(x0, y0, x1, y1) → (x0, y0, width, height, centre_x, centre_y)."""
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    return x0, y0, w, h, x0 + w / 2, y0 + h / 2


def _star_points(draw, cx, cy, outer, inner, fill=255) -> None:
    points = []
    for i in range(10):
        r = outer if i % 2 == 0 else inner
        angle = math.pi / 5 * i - math.pi / 2
        points.append((cx + r * math.cos(angle), cy + r * math.sin(angle)))
    draw.polygon(points, fill=fill)


def _fish_shape(draw, box, fill=255) -> None:
    """The fish silhouette, shared by the two fishing figures."""
    x0, y0, w, h, cx, cy = _box(box)
    draw.ellipse(
        (x0 + 0.02 * w, cy - 0.24 * h, x0 + 0.72 * w, cy + 0.24 * h), fill=fill
    )
    draw.polygon(  # tail
        [
            (x0 + 0.64 * w, cy),
            (x0 + w, cy - 0.32 * h),
            (x0 + 0.92 * w, cy),
            (x0 + w, cy + 0.32 * h),
        ],
        fill=fill,
    )
    draw.polygon(  # dorsal fin
        [
            (x0 + 0.22 * w, cy - 0.18 * h),
            (x0 + 0.44 * w, cy - 0.44 * h),
            (x0 + 0.56 * w, cy - 0.14 * h),
        ],
        fill=fill,
    )


def _topper_fish(mask, box) -> None:
    """A leaping fish — the catch itself."""
    _fish_shape(ImageDraw.Draw(mask), box)


def _topper_fish_coin(mask, box) -> None:
    """A coin with the catch cut out of it: fish that turned into money."""
    draw = ImageDraw.Draw(mask)
    _x0, _y0, w, h, cx, cy = _box(box)
    r = min(w, h) * 0.48
    draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=255)
    draw.ellipse(
        (cx - r * 0.84, cy - r * 0.84, cx + r * 0.84, cy + r * 0.84),
        outline=0,
        width=int(max(1, r * 0.10)),
    )
    _fish_shape(
        draw, (cx - r * 0.66, cy - r * 0.58, cx + r * 0.66, cy + r * 0.58), fill=0
    )


def _topper_scale(mask, box) -> None:
    """A balance scale — the heaviest one you ever landed."""
    draw = ImageDraw.Draw(mask)
    x0, y0, w, h, cx, _cy = _box(box)
    bar = max(2.0, h * 0.06)
    draw.rectangle((cx - bar / 2, y0 + 0.16 * h, cx + bar / 2, y0 + h), fill=255)
    draw.rectangle(
        (x0 + 0.06 * w, y0 + 0.16 * h, x0 + 0.94 * w, y0 + 0.16 * h + bar), fill=255
    )
    draw.ellipse((cx - 0.08 * w, y0, cx + 0.08 * w, y0 + 0.16 * h), fill=255)
    for side in (0.18, 0.82):  # the two pans, hung from the beam
        px = x0 + side * w
        draw.line((px, y0 + 0.18 * h, px, y0 + 0.48 * h), fill=255, width=int(bar / 2))
        draw.chord(
            (px - 0.18 * w, y0 + 0.40 * h, px + 0.18 * w, y0 + 0.76 * h),
            0,
            180,
            fill=255,
        )


def _topper_flame(mask, box) -> None:
    """A flame — a streak that's still going."""
    draw = ImageDraw.Draw(mask)
    x0, y0, w, h, cx, _cy = _box(box)
    draw.polygon(
        [
            (cx, y0),
            (x0 + 0.86 * w, y0 + 0.44 * h),
            (x0 + 0.88 * w, y0 + 0.74 * h),
            (cx, y0 + h),
            (x0 + 0.12 * w, y0 + 0.74 * h),
            (x0 + 0.16 * w, y0 + 0.38 * h),
            (x0 + 0.44 * w, y0 + 0.54 * h),
        ],
        fill=255,
    )
    draw.polygon(  # the cooler core, cut out
        [
            (cx, y0 + 0.48 * h),
            (x0 + 0.68 * w, y0 + 0.76 * h),
            (cx, y0 + 0.94 * h),
            (x0 + 0.32 * w, y0 + 0.76 * h),
        ],
        fill=0,
    )


def _topper_book(mask, box) -> None:
    """An open book — the dex, a collection rather than a haul."""
    draw = ImageDraw.Draw(mask)
    x0, y0, w, h, cx, _cy = _box(box)
    gap = 0.045 * w  # the valley down the middle is what says "open"
    for side in (-1, 1):
        near, far = cx + side * gap, cx + side * 0.50 * w
        draw.polygon(  # the page, sloping up from the spine to the outer edge
            [
                (near, y0 + 0.30 * h),
                (far, y0 + 0.10 * h),
                (far, y0 + 0.62 * h),
                (near, y0 + 0.80 * h),
            ],
            fill=255,
        )
        draw.polygon(  # the block of pages under it, seen edge-on
            [
                (near, y0 + 0.80 * h),
                (far, y0 + 0.62 * h),
                (far, y0 + 0.80 * h),
                (near, y0 + 0.98 * h),
            ],
            fill=255,
        )
        draw.line(  # one crease per page, cut so the two don't read as one sheet
            (near + side * 0.10 * w, y0 + 0.74 * h, far, y0 + 0.57 * h),
            fill=0,
            width=int(max(1, h * 0.025)),
        )


def _topper_coins(mask, box) -> None:
    """A stack of coins — money *held*, which is not money earned."""
    draw = ImageDraw.Draw(mask)
    _x0, y0, w, h, cx, _cy = _box(box)
    # Three coins seen edge-on with daylight between them: the gaps are what
    # make it a stack of coins rather than one lump.
    face = 0.24 * h
    for i, (dx, dw) in enumerate(((0.02, 0.78), (-0.06, 0.66), (0.06, 0.54))):
        left = cx + dx * w - dw * w / 2
        right = cx + dx * w + dw * w / 2
        top = y0 + h - face - i * face * 1.18
        draw.ellipse((left, top, right, top + face), fill=255)
        draw.ellipse(  # a rim, so each one reads as struck metal
            (
                left + 0.045 * w,
                top + 0.035 * h,
                right - 0.045 * w,
                top + face - 0.035 * h,
            ),
            outline=0,
            width=int(max(1, h * 0.02)),
        )


def _topper_shield(mask, box) -> None:
    """A shield with a star cut out — service to the server."""
    draw = ImageDraw.Draw(mask)
    x0, y0, w, h, cx, _cy = _box(box)
    draw.polygon(
        [
            (x0 + 0.10 * w, y0 + 0.06 * h),
            (x0 + 0.90 * w, y0 + 0.06 * h),
            (x0 + 0.90 * w, y0 + 0.54 * h),
            (cx, y0 + h),
            (x0 + 0.10 * w, y0 + 0.54 * h),
        ],
        fill=255,
    )
    _star_points(draw, cx, y0 + 0.46 * h, min(w, h) * 0.26, min(w, h) * 0.12, fill=0)


def _topper_die(mask, box) -> None:
    """A die, pips cut out — every game played, win or lose."""
    draw = ImageDraw.Draw(mask)
    _x0, _y0, w, h, cx, cy = _box(box)
    side = min(w, h) * 0.92
    left, top = cx - side / 2, cy - side / 2
    draw.rounded_rectangle((left, top, left + side, top + side), side * 0.18, fill=255)
    pip = side * 0.10
    for fx, fy in ((0.27, 0.27), (0.73, 0.27), (0.5, 0.5), (0.27, 0.73), (0.73, 0.73)):
        px, py = left + fx * side, top + fy * side
        draw.ellipse((px - pip, py - pip, px + pip, py + pip), fill=0)


def _topper_bell(mask, box) -> None:
    """A jackpot bell — the one big win, not the thousand small ones."""
    draw = ImageDraw.Draw(mask)
    x0, y0, w, h, cx, _cy = _box(box)
    draw.ellipse((cx - 0.08 * w, y0, cx + 0.08 * w, y0 + 0.14 * h), fill=255)
    draw.pieslice(
        (x0 + 0.14 * w, y0 + 0.08 * h, x0 + 0.86 * w, y0 + 0.98 * h), 180, 360, fill=255
    )
    draw.rectangle(
        (x0 + 0.14 * w, y0 + 0.52 * h, x0 + 0.86 * w, y0 + 0.74 * h), fill=255
    )
    draw.rounded_rectangle(
        (x0 + 0.02 * w, y0 + 0.72 * h, x0 + 0.98 * w, y0 + 0.84 * h), 0.05 * h, fill=255
    )
    draw.ellipse((cx - 0.09 * w, y0 + 0.86 * h, cx + 0.09 * w, y0 + h), fill=255)


def _topper_cards(mask, box) -> None:
    """Three cards fanned — a run of hands that all went your way."""
    _x0, _y0, w, h, cx, cy = _box(box)
    card_w, card_h = w * 0.40, h * 0.70
    edge = max(2.0, card_w * 0.10)
    for angle, dx, dy in ((24, -0.24, 0.08), (0, 0.0, -0.02), (-24, 0.24, 0.08)):
        # Rotation needs a layer of its own — ImageDraw can't lay a rotated
        # rounded rectangle down directly. Each card is cut out slightly larger
        # than it is drawn, so the one behind it keeps a visible edge instead of
        # the three merging into one blob.
        pad = int(max(card_w, card_h))
        cut = Image.new("L", (pad * 2, pad * 2), 0)
        body = Image.new("L", (pad * 2, pad * 2), 0)
        for layer, grow, fill in ((cut, edge, 255), (body, 0.0, 255)):
            ImageDraw.Draw(layer).rounded_rectangle(
                (
                    pad - card_w / 2 - grow,
                    pad - card_h / 2 - grow,
                    pad + card_w / 2 + grow,
                    pad + card_h / 2 + grow,
                ),
                card_w * 0.16,
                fill=fill,
            )
        at = (int(cx + dx * w) - pad, int(cy + dy * h) - pad)
        mask.paste(0, at, _hard(cut.rotate(angle, resample=Image.BICUBIC)))
        mask.paste(255, at, _hard(body.rotate(angle, resample=Image.BICUBIC)))


def _hard(layer: Image.Image) -> Image.Image:
    """A rotated mask, re-thresholded — a soft edge pasted as a mask would
    smear the metal gradient instead of cutting it."""
    return layer.point(lambda v: 255 if v > 110 else 0)


def _topper_hardhat(mask, box) -> None:
    """A hard hat — shifts worked."""
    draw = ImageDraw.Draw(mask)
    x0, y0, w, h, cx, _cy = _box(box)
    draw.pieslice(
        (x0 + 0.16 * w, y0 + 0.10 * h, x0 + 0.84 * w, y0 + 1.22 * h), 180, 360, fill=255
    )
    draw.rounded_rectangle(
        (x0, y0 + 0.62 * h, x0 + w, y0 + 0.80 * h), 0.09 * h, fill=255
    )
    draw.rectangle((cx - 0.05 * w, y0 + 0.14 * h, cx + 0.05 * w, y0 + 0.62 * h), fill=0)


def _topper_pickaxe(mask, box) -> None:
    """A pickaxe — trips down the mine."""
    draw = ImageDraw.Draw(mask)
    x0, y0, w, h, cx, _cy = _box(box)
    # The head: one long curved bar across the top, tapering to points. Drawn as
    # a polygon rather than an arc, because an arc of this weight loses its
    # points and reads as a scribble at 40px.
    draw.polygon(
        [
            (x0 + 0.02 * w, y0 + 0.30 * h),
            (x0 + 0.30 * w, y0 + 0.06 * h),
            (x0 + 0.70 * w, y0 + 0.06 * h),
            (x0 + 0.98 * w, y0 + 0.30 * h),
            (x0 + 0.86 * w, y0 + 0.34 * h),
            (x0 + 0.66 * w, y0 + 0.20 * h),
            (x0 + 0.34 * w, y0 + 0.20 * h),
            (x0 + 0.14 * w, y0 + 0.34 * h),
        ],
        fill=255,
    )
    draw.rectangle(  # the shaft
        (cx - 0.07 * w, y0 + 0.12 * h, cx + 0.07 * w, y0 + h), fill=255
    )
    draw.rounded_rectangle(  # the collar where the head is wedged on
        (cx - 0.13 * w, y0 + 0.14 * h, cx + 0.13 * w, y0 + 0.26 * h),
        0.02 * h,
        fill=255,
    )


def _topper_antlers(mask, box) -> None:
    """A stag's head — hunts completed."""
    draw = ImageDraw.Draw(mask)
    _x0, y0, w, h, cx, _cy = _box(box)
    draw.ellipse((cx - 0.20 * w, y0 + 0.44 * h, cx + 0.20 * w, y0 + 0.86 * h), fill=255)
    draw.ellipse((cx - 0.12 * w, y0 + 0.66 * h, cx + 0.12 * w, y0 + h), fill=255)
    width = int(max(3, h * 0.075))
    for side in (-1, 1):
        base = (cx + side * 0.10 * w, y0 + 0.50 * h)
        tip = (cx + side * 0.46 * w, y0 + 0.06 * h)
        draw.line((*base, *tip), fill=255, width=width)
        for t, spread in ((0.42, 0.26), (0.78, 0.20)):  # two tines, not four
            px = base[0] + (tip[0] - base[0]) * t
            py = base[1] + (tip[1] - base[1]) * t
            draw.line(
                (px, py, px + side * spread * w, py - spread * h * 0.8),
                fill=255,
                width=width,
            )


def _topper_compass(mask, box) -> None:
    """A compass rose — places explored."""
    draw = ImageDraw.Draw(mask)
    _x0, _y0, w, h, cx, cy = _box(box)
    r = min(w, h) * 0.48
    draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=255)
    draw.ellipse((cx - r * 0.80, cy - r * 0.80, cx + r * 0.80, cy + r * 0.80), fill=0)
    draw.polygon(
        [
            (cx, cy - r * 0.70),
            (cx + r * 0.24, cy + r * 0.14),
            (cx, cy + r * 0.70),
            (cx - r * 0.24, cy - r * 0.14),
        ],
        fill=255,
    )
    draw.polygon(
        [
            (cx, cy + r * 0.70),
            (cx + r * 0.24, cy + r * 0.14),
            (cx - r * 0.24, cy - r * 0.14),
        ],
        fill=0,
    )


def _topper_mask(mask, box) -> None:
    """A bandit's mask — wallets that changed hands."""
    draw = ImageDraw.Draw(mask)
    x0, _y0, w, h, cx, cy = _box(box)
    draw.rounded_rectangle(
        (x0 + 0.04 * w, cy - 0.28 * h, x0 + 0.96 * w, cy + 0.24 * h), 0.22 * h, fill=255
    )
    for side in (-1, 1):
        ex = cx + side * 0.24 * w
        draw.ellipse(
            (ex - 0.15 * w, cy - 0.16 * h, ex + 0.15 * w, cy + 0.06 * h), fill=0
        )


def _topper_anvil(mask, box) -> None:
    """An anvil — the tool ladder, rather than the trips it paid for."""
    draw = ImageDraw.Draw(mask)
    x0, y0, w, h, _cx, _cy = _box(box)
    draw.polygon(
        [
            (x0 + 0.06 * w, y0 + 0.24 * h),
            (x0 + 0.76 * w, y0 + 0.24 * h),
            (x0 + w, y0 + 0.36 * h),
            (x0 + 0.76 * w, y0 + 0.46 * h),
            (x0 + 0.06 * w, y0 + 0.46 * h),
        ],
        fill=255,
    )
    draw.polygon(
        [
            (x0 + 0.26 * w, y0 + 0.46 * h),
            (x0 + 0.56 * w, y0 + 0.46 * h),
            (x0 + 0.66 * w, y0 + 0.80 * h),
            (x0 + 0.18 * w, y0 + 0.80 * h),
        ],
        fill=255,
    )
    draw.rounded_rectangle(
        (x0 + 0.06 * w, y0 + 0.80 * h, x0 + 0.78 * w, y0 + h), 0.04 * h, fill=255
    )


def _topper_star(mask, box) -> None:
    """The fallback: a plain star, for anything with no figure of its own."""
    _x0, _y0, w, h, cx, cy = _box(box)
    _star_points(ImageDraw.Draw(mask), cx, cy, min(w, h) * 0.52, min(w, h) * 0.23)


TOPPERS = {
    "fish": _topper_fish,
    "fish_coin": _topper_fish_coin,
    "scale": _topper_scale,
    "flame": _topper_flame,
    "book": _topper_book,
    "coins": _topper_coins,
    "shield": _topper_shield,
    "die": _topper_die,
    "bell": _topper_bell,
    "cards": _topper_cards,
    "hardhat": _topper_hardhat,
    "pickaxe": _topper_pickaxe,
    "antlers": _topper_antlers,
    "compass": _topper_compass,
    "mask": _topper_mask,
    "anvil": _topper_anvil,
    "star": _topper_star,
}


# ── The stands ───────────────────────────────────────────────────────────────
# What the tier says. A ten-point trophy is a figure on a block; an eighty-point
# one is the same figure on a fluted column, a stepped plinth and a laurel
# wreath. The figure stays roughly the same size across tiers on purpose — a
# real trophy grows its base, and shrinking the figure at the top would make the
# most impressive trophies the hardest ones to read.
#
# (figure_bottom, column_top, plinth_top, steps, wreath), as fractions of height.
TIER_STANDS = (
    (0.64, 0.64, 0.72, 1, False),  # 0 — the figure on a plain block
    (0.58, 0.58, 0.76, 1, False),  # 1 — a column appears
    (0.56, 0.56, 0.72, 2, False),  # 2 — taller column, stepped plinth
    (0.56, 0.56, 0.72, 2, True),  # 3 — and a laurel wreath around the figure
)

# And how tall each tier stands. A trophy case reads at arm's length because the
# big ones tower over the small ones, so the whole trophy is drawn to a fraction
# of the cell and stood on the shelf — the detail differences between the stands
# are what you notice second, once something has caught your eye.
TIER_SCALE = (0.78, 0.87, 0.94, 1.0)


def _draw_stand(metal, stone, w: float, h: float, tier: int) -> tuple:
    """Draw the plinth and column for `tier`, and return the figure's box."""
    figure_bottom, column_top, plinth_top, steps, wreath = TIER_STANDS[tier]
    figure_bottom, column_top, plinth_top = (
        figure_bottom * h,
        column_top * h,
        plinth_top * h,
    )

    if column_top < plinth_top:  # a column, with a capital under the figure
        half = (0.075 if tier >= 2 else 0.06) * w
        metal.rectangle(
            (0.5 * w - half, column_top, 0.5 * w + half, plinth_top), fill=255
        )
        metal.rounded_rectangle(
            (
                0.5 * w - half * 2.2,
                column_top - 0.03 * h,
                0.5 * w + half * 2.2,
                column_top + 0.025 * h,
            ),
            0.012 * h,
            fill=255,
        )

    if steps > 1:  # an upper step, narrower than the plinth it stands on
        stone.rounded_rectangle(
            (0.28 * w, plinth_top, 0.72 * w, plinth_top + 0.10 * h), 0.02 * h, fill=255
        )
        stone.rounded_rectangle(
            (0.16 * w, plinth_top + 0.10 * h, 0.84 * w, h), 0.025 * h, fill=255
        )
    else:
        stone.rounded_rectangle(
            (0.22 * w, plinth_top, 0.78 * w, h), 0.025 * h, fill=255
        )

    if wreath:  # laurel: an arc curving up around the figure, with leaves on it
        centre_y = figure_bottom * 0.60
        metal.arc(
            (0.04 * w, centre_y - 0.34 * h, 0.96 * w, centre_y + 0.40 * h),
            25,
            155,
            fill=255,
            width=int(max(2, 0.026 * h)),
        )
        for side in (-1, 1):
            for i in range(4):
                t = 0.16 + i * 0.21
                lx = 0.5 * w + side * (0.08 + 0.38 * t) * w
                ly = centre_y + (0.19 - 0.42 * (1 - t) ** 2) * h
                metal.ellipse(
                    (lx - 0.05 * w, ly - 0.032 * h, lx + 0.05 * w, ly + 0.032 * h),
                    fill=255,
                )

    return (0.09 * w, 0.02 * h, 0.91 * w, figure_bottom)


def _name_plate(draw, w: float, h: float, tier: int, accent) -> None:
    """The engraved plate on the plinth, in the category's colour — where a real
    trophy says what it was won for, and the one spot of colour on the metal."""
    _figure_bottom, _column_top, plinth_top, steps, _wreath = TIER_STANDS[tier]
    top = plinth_top * h + (0.135 if steps > 1 else 0.05) * h
    draw.rounded_rectangle(
        (0.30 * w, top, 0.70 * w, top + 0.07 * h), 0.012 * h, fill=accent
    )


@functools.lru_cache(maxsize=512)
def trophy_image(
    topper: str,
    tier: int,
    accent: str,
    earned: bool,
    size: tuple[int, int] = (ART_W, ART_H),
) -> Image.Image:
    """One trophy: `topper`'s figure on the stand its `tier` earns, in that
    tier's metal, with the category's `accent` on the plate.

    Cached: a case is forty trophies over a couple of dozen distinct
    combinations, so the whole wall costs a handful of renders. Callers only
    ever composite the result, never mutate it.
    """
    tier = max(0, min(len(TIER_STANDS) - 1, int(tier)))
    figure = TOPPERS.get(topper) or _topper_star
    w, h = size
    scale = _SUPERSAMPLE
    big = (w * scale, h * scale)
    bw, bh = float(big[0]), float(big[1])

    metal_mask = Image.new("L", big, 0)
    stone_mask = Image.new("L", big, 0)
    box = _draw_stand(
        ImageDraw.Draw(metal_mask), ImageDraw.Draw(stone_mask), bw, bh, tier
    )
    figure(metal_mask, box)

    img = Image.new("RGBA", big, (0, 0, 0, 0))
    if not earned:
        # A ghost of what goes here: the same silhouette, no metal, no shine —
        # so a locked case still says what each trophy is going to be.
        img.paste(Image.new("RGBA", big, GHOST_METAL), (0, 0), metal_mask)
        img.paste(Image.new("RGBA", big, GHOST_STONE), (0, 0), stone_mask)
        return img.resize(size, Image.LANCZOS)

    light, dark = TIER_METALS[tier]
    img.paste(
        _gradient(big, _rgba(light), _rgba(dark), diagonal=True), (0, 0), metal_mask
    )
    img.paste(_gradient(big, STONE_TOP, STONE_BOTTOM), (0, 0), stone_mask)

    # Sheen: a soft diagonal highlight clipped to the metal, which is what makes
    # a flat silhouette read as polished rather than painted.
    sheen = Image.new("RGBA", big, (0, 0, 0, 0))
    ImageDraw.Draw(sheen).polygon(
        [(0.10 * bw, 0.0), (0.42 * bw, 0.0), (0.20 * bw, bh), (-0.10 * bw, bh)],
        fill=(255, 255, 255, 90),
    )
    sheen = sheen.filter(ImageFilter.GaussianBlur(6 * scale))
    sheen.putalpha(ImageChops.multiply(sheen.getchannel("A"), metal_mask))
    img.alpha_composite(sheen)

    _name_plate(ImageDraw.Draw(img), bw, bh, tier, _rgba(accent))
    return img.resize(size, Image.LANCZOS)


# ── The case ─────────────────────────────────────────────────────────────────
def _shelves(groups) -> list[dict]:
    """Categories → shelves of at most COLS trophies.

    Layout belongs to the card, so callers hand over whole categories and this
    decides how many boards that needs. A category longer than one shelf
    continues onto the next, unlabelled, rather than starting a second heading.
    """
    shelves: list[dict] = []
    for group in groups or []:
        items = list(group.get("items") or [])
        label = _font_safe(group.get("label") or "")
        earned = sum(1 for item in items if item.get("earned"))
        for start in range(0, max(1, len(items)), COLS):
            shelves.append(
                {
                    "label": label if start == 0 else "",
                    "count": f"{earned}/{len(items)}" if start == 0 else "",
                    "items": items[start : start + COLS],
                }
            )
    return shelves


def case_size(groups) -> tuple[int, int]:
    """The finished card's dimensions for this catalogue — the card grows a
    shelf at a time rather than cramming, so adding achievements is free."""
    rows = max(1, len(_shelves(groups)))
    height = (
        CASE_TOP + FRAME + INNER_PAD + rows * ROW_PITCH + FRAME + FOOTER_H + PAD // 2
    )
    return W, height


def _draw_case(card: Image.Image, draw, shelves, top: int, bottom: int) -> None:
    """The cabinet: frame, lit interior, and a board per shelf."""
    x0, x1 = PAD, W - PAD
    # Frame first, interior painted inside it.
    draw.rounded_rectangle((x0, top, x1, bottom), 18, fill=FRAME_DARK)
    draw.rounded_rectangle(
        (x0, top, x1, bottom), 18, outline=FRAME_LIGHT, width=max(2, FRAME // 4)
    )
    inner = (x0 + FRAME, top + FRAME, x1 - FRAME, bottom - FRAME)
    iw, ih = inner[2] - inner[0], inner[3] - inner[1]
    interior = _gradient((iw, ih), CASE_BACK_TOP, CASE_BACK_BOTTOM)
    card.paste(interior, (inner[0], inner[1]), _rounded_mask((iw, ih), 8))

    row_top = inner[1] + INNER_PAD
    for shelf in shelves:
        # Warm light spilling down from the board above onto this row.
        glow = _gradient((iw, ART_H + PLATE_H), SHELF_LIGHT, (0, 0, 0, 0))
        card.alpha_composite(glow, (inner[0], row_top + LABEL_H))

        _draw_shelf_row(card, draw, shelf, row_top, inner)
        row_top += ROW_PITCH

    # Glass: one diagonal streak over the whole case, and a highlight along the
    # top edge. Cheap, and it is the thing that says "case" rather than "grid".
    glass = Image.new("RGBA", (iw, ih), (0, 0, 0, 0))
    ImageDraw.Draw(glass).polygon(
        [(0.06 * iw, ih), (0.34 * iw, 0), (0.52 * iw, 0), (0.24 * iw, ih)],
        fill=(255, 255, 255, 12),
    )
    ImageDraw.Draw(glass).rectangle((0, 0, iw, 2), fill=(255, 255, 255, 26))
    glass.putalpha(
        ImageChops.multiply(glass.getchannel("A"), _rounded_mask((iw, ih), 8))
    )
    card.alpha_composite(glass, (inner[0], inner[1]))


def _wrap(draw, text: str, font, max_w: float, lines: int = 2) -> list[str]:
    """Break a trophy name over at most `lines`, ellipsising what won't fit.

    Achievement names run to twenty-odd characters and a plaque is one cell
    wide, so a single line would clip most of them mid-word — "Master Angle"
    names nothing.
    """
    words, out, current = str(text).split(), [], ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if current and draw.textlength(candidate, font=font) > max_w:
            out.append(current)
            current = word
            if len(out) == lines:
                break
        else:
            current = candidate
    if len(out) < lines and current:
        out.append(current)
    if not out:
        return []
    # Whatever is left over is squeezed into the last line with an ellipsis.
    while draw.textlength(out[-1], font=font) > max_w and len(out[-1]) > 1:
        out[-1] = out[-1][:-2] + "…"
    return out


def _draw_shelf_row(card, draw, shelf, row_top: int, inner) -> None:
    """One board: its heading, its trophies, their plaques, and the shelf front."""
    label = shelf.get("label")
    if label:
        font = _font(13, bold=True)
        draw.text((inner[0] + 14, row_top), label.upper(), font=font, fill=INK_MUTED)
        count = shelf.get("count") or ""
        if count:
            draw.text(
                (
                    inner[0] + 14 + draw.textlength(label.upper(), font=font) + 10,
                    row_top,
                ),
                count,
                font=_font(13),
                fill=INK_FAINT,
            )

    art_top = row_top + LABEL_H
    for i, item in enumerate(shelf["items"]):
        cell_x = inner[0] + INNER_PAD + i * CELL_W
        earned = bool(item.get("earned"))
        tier = max(0, min(len(TIER_SCALE) - 1, int(item.get("tier", 0))))
        # Every trophy stands *on* the board, so a shorter tier is drawn shorter
        # and bottom-aligned rather than floating in the middle of its cell.
        scale = TIER_SCALE[tier]
        size = (round(ART_W * scale), round(ART_H * scale))
        art = trophy_image(
            str(item.get("topper") or "star"),
            tier,
            str(item.get("accent") or "#5865F2"),
            earned,
            size,
        )
        art_x = cell_x + (CELL_W - size[0]) // 2
        art_y = art_top + ART_H - size[1]

        if earned:
            # A contact shadow on the board — without it every trophy floats.
            shadow = Image.new("RGBA", (size[0] + 16, 18), (0, 0, 0, 0))
            ImageDraw.Draw(shadow).ellipse(
                (0, 0, size[0] + 15, 17), fill=(0, 0, 0, 120)
            )
            shadow = shadow.filter(ImageFilter.GaussianBlur(4))
            card.alpha_composite(shadow, (art_x - 8, art_top + ART_H - 10))
        card.alpha_composite(art, (art_x, art_y))

        # Name plaque. Locked names are shown too — the case is the list of what
        # is left, and a blank ghost would tell nobody what to go and get.
        name = _font_safe(item.get("name") or "")
        if not name:
            continue
        plate_x0, plate_x1 = cell_x + 3, cell_x + CELL_W - 3
        plate_y = art_top + ART_H + 3
        draw.rounded_rectangle(
            (plate_x0, plate_y, plate_x1, plate_y + PLATE_H),
            6,
            fill=(0, 0, 0, 120) if earned else (0, 0, 0, 60),
        )
        font = _font(11, bold=earned)
        rows = _wrap(draw, name, font, plate_x1 - plate_x0 - 8)
        centre = (plate_x0 + plate_x1) / 2
        for line_no, line in enumerate(rows):
            draw.text(
                (
                    centre - draw.textlength(line, font=font) / 2,
                    plate_y + (PLATE_H - 13 * len(rows)) / 2 + line_no * 13,
                ),
                line,
                font=font,
                fill=INK if earned else INK_FAINT,
            )

    # The board itself: a lit top edge over a dark front face.
    slab_y = art_top + ART_H + 3 + PLATE_H
    draw.rectangle((inner[0] + 4, slab_y, inner[2] - 4, slab_y + 3), fill=SLAB_TOP)
    draw.rounded_rectangle(
        (inner[0] + 4, slab_y + 3, inner[2] - 4, slab_y + SLAB_H), 3, fill=SLAB_FACE
    )


def render_trophy_case(data: dict) -> bytes:
    """Render the trophy case and return encoded image bytes (see IMAGE_FORMAT).

    `data` keys (all optional except `name`):
      name, avatar (raw image bytes), title, prestige, points,
      groups — [{"label": str, "items": [{"name", "tier", "accent", "earned"}]}]
               in display order; the card decides how many shelves that needs,
      banner / border / nameplate — CosmeticDef or None (the case is dressed in
               the same cosmetics as the profile card, so it is recognisably
               the same member's),
      footer — small text bottom-right.
    """
    groups = list(data.get("groups") or [])
    shelves = _shelves(groups)
    width, height = case_size(groups)

    items = [item for group in groups for item in (group.get("items") or [])]
    total = len(items)
    earned_count = sum(1 for item in items if item.get("earned"))

    banner = data.get("banner") or cosmetics.get("banner_default")
    accent = _palette(banner, 1)

    card = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    background = (
        cosmetic_image(banner, (width, height))
        if banner
        else _gradient((width, height), _rgba("#1E2130"), _rgba("#2B2D42"))
    )
    card.paste(background, (0, 0))
    # The case covers most of the card, so the banner is really only seen
    # behind the header — keep that end light and darken towards the bottom.
    photographic = banner is not None and _art_file(banner) is not None
    card.alpha_composite(
        _gradient(
            (width, height),
            (0, 0, 0, 110 if photographic else 80),
            (0, 0, 0, 200 if photographic else 175),
        )
    )
    draw = ImageDraw.Draw(card)

    # ── Avatar ──
    avatar = None
    if data.get("avatar"):
        try:
            avatar = Image.open(io.BytesIO(data["avatar"])).convert("RGBA")
        except OSError:
            avatar = None
    name = str(data.get("name", "Unknown"))
    if avatar is None:
        avatar = _gradient((AVATAR_SIZE, AVATAR_SIZE), accent, _palette(banner, 0))
        initial = name[:1].upper() or "?"
        idraw = ImageDraw.Draw(avatar)
        font = _font(int(AVATAR_SIZE * 0.5), bold=True)
        bbox = idraw.textbbox((0, 0), initial, font=font)
        idraw.text(
            (
                (AVATAR_SIZE - (bbox[2] - bbox[0])) / 2 - bbox[0],
                (AVATAR_SIZE - (bbox[3] - bbox[1])) / 2 - bbox[1],
            ),
            initial,
            font=font,
            fill=(255, 255, 255, 230),
        )
    avatar = avatar.resize((AVATAR_SIZE, AVATAR_SIZE), Image.LANCZOS)
    ring = Image.new("RGBA", (AVATAR_SIZE + 10, AVATAR_SIZE + 10), (0, 0, 0, 0))
    ImageDraw.Draw(ring).ellipse(
        (0, 0, AVATAR_SIZE + 9, AVATAR_SIZE + 9), fill=(255, 255, 255, 55)
    )
    card.alpha_composite(ring, (AVATAR_XY[0] - 5, AVATAR_XY[1] - 5))
    card.paste(avatar, AVATAR_XY, _circle_mask(AVATAR_SIZE))
    prestige = int(data.get("prestige", 0) or 0)
    if prestige > 0:
        emblem = prestige_emblem(prestige, 54)
        card.alpha_composite(
            emblem, (AVATAR_XY[0] + AVATAR_SIZE - 42, AVATAR_XY[1] + AVATAR_SIZE - 42)
        )

    # ── Name, title, collection bar ──
    draw.text(
        (TEXT_X, EYEBROW_Y), "TROPHY CASE", font=_font(15, bold=True), fill=INK_FAINT
    )
    text_w = W - TEXT_X - PAD
    draw.text((TEXT_X, NAME_Y), name, font=_fit_text(draw, name, 40, text_w), fill=INK)
    title = data.get("title")
    if title:
        draw.text((TEXT_X, TITLE_Y), _font_safe(title), font=_font(20), fill=INK_MUTED)

    _draw_bar(
        card,
        (TEXT_X, BAR_Y),
        text_w,
        BAR_H,
        (earned_count / total) if total else 0,
        _rgba("#FFD86B"),
        _rgba("#FF9F45"),
    )

    # ── Chips ──
    pct = round(100 * earned_count / total) if total else 0
    chips = [
        ("trophies", f"{earned_count:,}/{total:,}"),
        ("points", f"{int(data.get('points', 0) or 0):,}"),
        ("complete", f"{pct}%"),
        ("prestige", f"Rank {prestige}" if prestige else "—"),
    ]
    chip_w = (W - 2 * PAD - CHIP_GAP * (CHIP_COLS - 1)) // CHIP_COLS
    for i, (label, value) in enumerate(chips):
        _draw_chip(
            card,
            draw,
            (PAD + i * (chip_w + CHIP_GAP), CHIP_TOP),
            (chip_w, CHIP_H),
            label,
            value,
            accent,
        )

    # ── The case ──
    case_bottom = height - FOOTER_H - PAD // 2
    _draw_case(card, draw, shelves, CASE_TOP, case_bottom)

    # ── Legend ──
    # The figure says what a trophy is for and the stand says what it cost, so
    # the legend shows the four stands carrying one neutral figure: read it once
    # and you can read anyone's case at a glance.
    legend_x = PAD
    for tier, label in list(data.get("legend") or [])[: len(TIER_STANDS)]:
        card.alpha_composite(
            trophy_image("star", int(tier), "#8A93A8", True, (24, 30)),
            (legend_x, case_bottom + 2),
        )
        text = _font_safe(label)
        font = _font(13)
        draw.text((legend_x + 28, case_bottom + 12), text, font=font, fill=INK_FAINT)
        legend_x += 28 + int(draw.textlength(text, font=font)) + 18

    footer = data.get("footer")
    if footer:
        font = _font(15)
        text = _font_safe(footer)
        draw.text(
            (W - PAD - draw.textlength(text, font=font), case_bottom + 12),
            text,
            font=font,
            fill=INK_FAINT,
        )

    draw_border(card, data.get("border"), radius=RADIUS)
    out = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    out.paste(card, (0, 0), _rounded_mask((width, height), RADIUS))
    return encode(out)
