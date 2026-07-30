"""utils/trophy_card.py — renders the /progress badges trophy case as an image.

The fourth card, after the profile, the cookie jar and the wallet, and built to
the same contract: a plain dict in, encoded image bytes out, no Discord and no
database. Only the pure drawing primitives are imported from
``utils/profile_card.py`` — duplicating a gradient routine to avoid an import is
how two cards stop looking like one bot.

Why a drawn case rather than a row of emoji: an achievement's emoji is a colour
emoji, and the bundled font has none, so a wall of them renders as tofu the
moment it leaves Discord's own text rendering. Every trophy here is therefore
*drawn* — and once you are drawing them anyway, the shape can carry meaning the
emoji never did. A trophy's **form and metal** come from what the achievement is
worth (a 10-point medal, an 80-point prismatic star), and its **accent** from
which part of the bot it came from, so a case reads at a glance: mostly bronze
medals is a new account, a shelf of gold cups is a specialist.

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

__all__ = ["render_trophy_case", "case_size", "trophy_image", "COLS", "IMAGE_EXT"]

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
ART_W, ART_H = 62, 76  # the trophy itself
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
# Form by tier: the silhouette carries the rank before any colour does.
TIER_FORMS = ("medal", "cup", "grand", "star")

GHOST_METAL = (255, 255, 255, 26)  # a locked trophy: the shape, none of the shine
GHOST_STONE = (255, 255, 255, 16)

_SUPERSAMPLE = 4


# ── The trophies ─────────────────────────────────────────────────────────────
def _draw_medal(metal, stone, w: float, h: float) -> None:
    """A ribboned medal on a little block — the entry-level trophy."""
    metal.polygon(
        [
            (0.33 * w, 0.02 * h),
            (0.47 * w, 0.02 * h),
            (0.57 * w, 0.42 * h),
            (0.45 * w, 0.42 * h),
        ],
        fill=255,
    )
    metal.polygon(
        [
            (0.53 * w, 0.02 * h),
            (0.67 * w, 0.02 * h),
            (0.55 * w, 0.42 * h),
            (0.43 * w, 0.42 * h),
        ],
        fill=255,
    )
    metal.ellipse((0.22 * w, 0.34 * h, 0.78 * w, 0.86 * h), fill=255)
    stone.rounded_rectangle(
        (0.26 * w, 0.86 * h, 0.74 * w, 0.98 * h), radius=0.03 * h, fill=255
    )


def _draw_cup(metal, stone, w: float, h: float, *, grand: bool) -> None:
    """The classic two-handled cup. `grand` is the taller, wider-mouthed one."""
    top = 0.10 * h if grand else 0.18 * h
    bottom = 0.52 * h if grand else 0.54 * h
    mouth = (0.20 if grand else 0.24) * w
    metal.ellipse((mouth, top - 0.05 * h, w - mouth, top + 0.06 * h), fill=255)
    metal.polygon(
        [(mouth, top), (w - mouth, top), (0.64 * w, bottom), (0.36 * w, bottom)],
        fill=255,
    )
    metal.ellipse((0.36 * w, bottom - 0.11 * h, 0.64 * w, bottom + 0.07 * h), fill=255)

    handle_w = int(max(2, 0.055 * h))
    metal.arc(
        (0.02 * w, top + 0.02 * h, 0.34 * w, bottom - 0.02 * h),
        95,
        265,
        fill=255,
        width=handle_w,
    )
    metal.arc(
        (0.66 * w, top + 0.02 * h, 0.98 * w, bottom - 0.02 * h),
        275,
        85,
        fill=255,
        width=handle_w,
    )
    if grand:
        # A finial on the rim — the one silhouette difference that separates the
        # two cups at thumbnail size.
        metal.ellipse((0.44 * w, top - 0.13 * h, 0.56 * w, top - 0.01 * h), fill=255)

    metal.rectangle((0.45 * w, bottom, 0.55 * w, 0.74 * h), fill=255)
    metal.polygon(
        [
            (0.34 * w, 0.82 * h),
            (0.66 * w, 0.82 * h),
            (0.58 * w, 0.74 * h),
            (0.42 * w, 0.74 * h),
        ],
        fill=255,
    )
    stone.rounded_rectangle(
        (0.24 * w, 0.82 * h, 0.76 * w, 0.98 * h), radius=0.03 * h, fill=255
    )


def _draw_star(metal, stone, w: float, h: float) -> None:
    """A star on a tapered pedestal — the top tier."""
    cx, cy = 0.5 * w, 0.30 * h
    outer, inner = 0.28 * h, 0.12 * h
    points = []
    for i in range(10):
        r = outer if i % 2 == 0 else inner
        angle = math.pi / 5 * i - math.pi / 2
        points.append((cx + r * math.cos(angle), cy + r * math.sin(angle)))
    metal.polygon(points, fill=255)
    metal.polygon(
        [
            (0.42 * w, 0.56 * h),
            (0.58 * w, 0.56 * h),
            (0.62 * w, 0.82 * h),
            (0.38 * w, 0.82 * h),
        ],
        fill=255,
    )
    stone.rounded_rectangle(
        (0.22 * w, 0.82 * h, 0.78 * w, 0.98 * h), radius=0.03 * h, fill=255
    )


def _accent_band(draw, form: str, w: float, h: float, accent, scale: int) -> None:
    """The category's colour, in the one place each form has room for it."""
    if form == "medal":
        draw.polygon(
            [
                (0.33 * w, 0.02 * h),
                (0.47 * w, 0.02 * h),
                (0.57 * w, 0.42 * h),
                (0.45 * w, 0.42 * h),
            ],
            fill=accent,
        )
        draw.polygon(
            [
                (0.53 * w, 0.02 * h),
                (0.67 * w, 0.02 * h),
                (0.55 * w, 0.42 * h),
                (0.43 * w, 0.42 * h),
            ],
            fill=accent,
        )
        draw.ellipse(
            (0.36 * w, 0.48 * h, 0.64 * w, 0.72 * h),
            outline=(255, 255, 255, 150),
            width=2 * scale,
        )
        return
    if form == "star":
        draw.rectangle((0.40 * w, 0.66 * h, 0.62 * w, 0.74 * h), fill=accent)
        return
    top = 0.10 * h if form == "grand" else 0.18 * h
    bottom = 0.52 * h if form == "grand" else 0.54 * h
    band_y = top + (bottom - top) * 0.52
    band_h = 0.07 * h
    left = 0.24 * w + (0.12 * w) * ((band_y - top) / max(1.0, bottom - top))
    draw.polygon(
        [
            (left, band_y),
            (w - left, band_y),
            (w - left - 0.02 * w, band_y + band_h),
            (left + 0.02 * w, band_y + band_h),
        ],
        fill=accent,
    )


@functools.lru_cache(maxsize=256)
def trophy_image(
    tier: int, accent: str, earned: bool, size: tuple[int, int] = (ART_W, ART_H)
) -> Image.Image:
    """One trophy, drawn from its tier and its category's accent colour.

    Cached: a case is forty trophies drawn from at most a couple of dozen
    distinct (tier, accent, earned) combinations, so the whole wall costs a
    handful of renders. Callers only ever composite the result, never mutate it.
    """
    tier = max(0, min(len(TIER_FORMS) - 1, int(tier)))
    form = TIER_FORMS[tier]
    w, h = size
    scale = _SUPERSAMPLE
    big = (w * scale, h * scale)
    bw, bh = float(big[0]), float(big[1])

    metal_mask = Image.new("L", big, 0)
    stone_mask = Image.new("L", big, 0)
    metal = ImageDraw.Draw(metal_mask)
    stone = ImageDraw.Draw(stone_mask)
    if form == "medal":
        _draw_medal(metal, stone, bw, bh)
    elif form == "star":
        _draw_star(metal, stone, bw, bh)
    else:
        _draw_cup(metal, stone, bw, bh, grand=(form == "grand"))

    img = Image.new("RGBA", big, (0, 0, 0, 0))
    if not earned:
        # A ghost of what goes here: same silhouette, no metal, no shine.
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

    _accent_band(ImageDraw.Draw(img), form, bw, bh, _rgba(accent), scale)
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
        art = trophy_image(
            int(item.get("tier", 0)), str(item.get("accent") or "#5865F2"), earned
        )
        art_x = cell_x + (CELL_W - ART_W) // 2

        if earned:
            # A contact shadow on the board — without it every trophy floats.
            shadow = Image.new("RGBA", (ART_W + 16, 18), (0, 0, 0, 0))
            ImageDraw.Draw(shadow).ellipse((0, 0, ART_W + 15, 17), fill=(0, 0, 0, 120))
            shadow = shadow.filter(ImageFilter.GaussianBlur(4))
            card.alpha_composite(shadow, (art_x - 8, art_top + ART_H - 10))
        card.alpha_composite(art, (art_x, art_top))

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
    # The shape ladder is the whole point of drawing the trophies, so say what
    # it means once: a viewer who knows a star costs more than a medal can read
    # someone else's case at a glance.
    legend_x = PAD
    for tier, label in list(data.get("legend") or [])[: len(TIER_FORMS)]:
        card.alpha_composite(
            trophy_image(int(tier), "#8A93A8", True, (22, 28)),
            (legend_x, case_bottom + 4),
        )
        text = _font_safe(label)
        font = _font(13)
        draw.text((legend_x + 26, case_bottom + 12), text, font=font, fill=INK_FAINT)
        legend_x += 26 + int(draw.textlength(text, font=font)) + 18

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
