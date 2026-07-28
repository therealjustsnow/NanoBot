"""utils/profile_card.py — renders the /profile card as an image.

Everything is drawn with Pillow at call time; the bot ships no binary artwork.
Each cosmetic declares a palette, a glyph, and optionally a ``pattern`` or
``style`` (utils/cosmetics.py) and this module turns that into clean, flat
vector-style art — gradients, rounded rectangles, rings — cached to
``data/profile_cache/``. Drop a real PNG at ``assets/profile/<slot>/<key>.png``
and it is used instead, no code change: that's the "don't sink time into art
now, swap it in later" contract.

The two look registries (``_BANNER_PATTERNS`` for banner/wallet artwork,
``_BORDER_STYLES`` for frames) are what stop the catalogue looking like twenty
recolours of one gradient: a new look is one function plus one registry line,
and the cosmetic selects it by name. Random placement inside a pattern is
seeded on the cosmetic key, so art is deterministic and the on-disk cache never
goes stale.

Layout lives in the LAYOUT constants below rather than being scattered through
the drawing code, so moving a block or adding a row is a number change. The
renderer takes a plain dict (see ``render_card``) and knows nothing about
Discord, the database, or the cogs — which is what makes it testable headless
and reusable for future cards (server cards, leaderboard cards, …).

Rendering is CPU-bound: call it through ``asyncio.to_thread`` (the cog does).
"""

from __future__ import annotations

import io
import logging
import math
import os
import random

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from utils import cosmetics

log = logging.getLogger("NanoBot.profile_card")

# ── Where art comes from ─────────────────────────────────────────────────────
ASSET_DIR = os.path.join("assets", "profile")  # hand-made art (optional)
CACHE_DIR = os.path.join("data", "profile_cache")  # generated art

# First font that exists wins. DejaVu ships with almost every Linux image and
# covers the geometric glyphs the badges use; the last entry is Pillow's
# built-in, so text always renders even on a bare container.
_FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "C:/Windows/Fonts/segoeui.ttf",
)
_FONT_BOLD_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "C:/Windows/Fonts/segoeuib.ttf",
)

# ── Layout ───────────────────────────────────────────────────────────────────
W, H = 1000, 560
PAD = 40
RADIUS = 28

AVATAR_SIZE = 168
AVATAR_XY = (PAD, PAD + 8)
PRESTIGE_SIZE = 66

TEXT_X = AVATAR_XY[0] + AVATAR_SIZE + 32
NAME_Y = PAD + 6
TITLE_Y = NAME_Y + 52

BAR_W = W - TEXT_X - PAD
BAR_H = 22
GLOBAL_BAR_Y = 162
SERVER_BAR_Y = 232

CHIP_TOP = 300
CHIP_H = 62
CHIP_GAP = 14
CHIP_COLS = 3

# The rep tally rides top-right rather than joining the chip grid: that grid is
# a full 2x3 and a third row would collide with the badge showcase.
REP_PILL_W = 132

BADGE_SIZE = 56
BADGE_GAP = 12
BADGE_Y = H - PAD - BADGE_SIZE

INK = (255, 255, 255, 255)
INK_MUTED = (196, 201, 214, 255)
INK_FAINT = (140, 146, 162, 255)


# ── Small helpers ────────────────────────────────────────────────────────────
def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    for path in _FONT_BOLD_CANDIDATES if bold else _FONT_CANDIDATES:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except OSError:  # pragma: no cover - unreadable font file
                continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # pragma: no cover - Pillow < 10.1
        return ImageFont.load_default()


def _rgba(colour: str, fallback=(88, 101, 242, 255)) -> tuple[int, int, int, int]:
    """'#RRGGBB' or '#RRGGBBAA' → an RGBA tuple."""
    raw = (colour or "").lstrip("#")
    try:
        if len(raw) == 6:
            r, g, b = (int(raw[i : i + 2], 16) for i in (0, 2, 4))
            return (r, g, b, 255)
        if len(raw) == 8:
            r, g, b, a = (int(raw[i : i + 2], 16) for i in (0, 2, 4, 6))
            return (r, g, b, a)
    except ValueError:
        pass
    return fallback


def _palette(d: cosmetics.CosmeticDef | None, index: int = 0):
    if d and d.palette and index < len(d.palette):
        return _rgba(d.palette[index])
    return _rgba("#5865F2") if index == 0 else _rgba("#8B5CF6")


def _gradient(size, top, bottom, *, diagonal: bool = False) -> Image.Image:
    """A two-stop linear gradient. Cheap: built at 1px wide and resized."""
    w, h = size
    steps = max(2, h if not diagonal else h + w)
    strip = Image.new("RGBA", (1, steps))
    px = strip.load()
    for i in range(steps):
        t = i / (steps - 1)
        px[0, i] = tuple(int(top[c] + (bottom[c] - top[c]) * t) for c in range(4))
    grad = strip.resize((w, h))
    if diagonal:
        grad = grad.rotate(20, resample=Image.BICUBIC, expand=False)
    return grad


def _rounded_mask(size, radius: int) -> Image.Image:
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, size[0] - 1, size[1] - 1), radius, 255
    )
    return mask


def _circle_mask(size: int) -> Image.Image:
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size - 1, size - 1), 255)
    return mask


def _fit_text(draw, text: str, font_size: int, max_w: int, bold=True):
    """Shrink a font until the text fits — long display names shouldn't run off
    the card or get cut mid-word."""
    size = font_size
    while size > 14:
        font = _font(size, bold)
        if draw.textlength(text, font=font) <= max_w:
            return font
        size -= 2
    return _font(14, bold)


# ── Generated cosmetic art ───────────────────────────────────────────────────
def _asset_path(d: cosmetics.CosmeticDef) -> str | None:
    """Hand-made art for this cosmetic, if someone has added it."""
    path = os.path.join(ASSET_DIR, d.slot, f"{d.key}.png")
    return path if os.path.exists(path) else None


def _cached(d: cosmetics.CosmeticDef, size: tuple[int, int]) -> str:
    return os.path.join(CACHE_DIR, d.slot, f"{d.key}_{size[0]}x{size[1]}.png")


def cosmetic_image(d: cosmetics.CosmeticDef, size: tuple[int, int]) -> Image.Image:
    """The art for one cosmetic at a given size.

    Order: a real asset file → the on-disk generated cache → freshly generated
    (and cached). Generation is deterministic from the def, so the cache never
    needs invalidating unless the def itself changes.
    """
    asset = _asset_path(d)
    if asset:
        try:
            return Image.open(asset).convert("RGBA").resize(size, Image.LANCZOS)
        except OSError:
            log.warning("Unreadable profile asset %s — generating instead", asset)
    cache = _cached(d, size)
    if os.path.exists(cache):
        try:
            return Image.open(cache).convert("RGBA")
        except OSError:
            pass
    img = _generate(d, size)
    try:
        os.makedirs(os.path.dirname(cache), exist_ok=True)
        img.save(cache, "PNG")
    except OSError:  # pragma: no cover - read-only data dir
        log.debug("Could not cache generated art for %s", d.key)
    return img


def _generate(d: cosmetics.CosmeticDef, size: tuple[int, int]) -> Image.Image:
    if d.slot == "badge":
        return _generate_badge(d, size)
    if d.slot in ("banner", "wallet"):
        return _generate_banner(d, size)
    if d.slot == "coin":
        return _generate_coin(d, size)
    return _gradient(size, _palette(d, 0), _palette(d, 1))


# ── Banner patterns ──────────────────────────────────────────────────────────
# Each entry draws into a transparent overlay that is blurred and composited
# over the def's gradient. They all take (draw, sharp, w, h, accent, rng) and
# return a blur radius, so adding a look is one function plus one registry line
# — the same "extend by data" contract the catalogue itself has. `sharp` is a
# second overlay that is composited *without* the blur, which is how a heavily
# blurred pattern can still carry crisp detail (nebula's stars).
#
# Two rules keep them usable rather than merely pretty: nothing may be opaque
# enough to fight the text drawn on top, and the left third of the card (avatar,
# name, level bars) stays comparatively clear.


def _soft(colour, alpha: int):
    return (colour[0], colour[1], colour[2], alpha)


def _pattern_default(draw, sharp, w, h, accent, rng) -> float:
    """Two large off-canvas circles + a diagonal sweep: reads as depth, costs
    nothing, and stays out of the text areas on the left."""
    soft = _soft(accent, 38)
    draw.ellipse((w * 0.62, -h * 0.55, w * 1.25, h * 0.85), fill=soft)
    draw.ellipse((w * 0.45, h * 0.45, w * 1.1, h * 1.9), fill=soft)
    draw.polygon(
        [(w * 0.05, h), (w * 0.35, 0), (w * 0.45, 0), (w * 0.15, h)],
        fill=(255, 255, 255, 12),
    )
    return 1.2


def _pattern_waves(draw, sharp, w, h, accent, rng) -> float:
    """Stacked sine crests along the bottom — water without drawing water."""
    for i in range(4):
        base = h * (0.55 + i * 0.14)
        amp = h * 0.09 * (1 - i * 0.15)
        pts = [
            (x, base + amp * math.sin(x / w * math.pi * 2.2 + i * 0.9))
            for x in range(0, w + 12, 12)
        ]
        crest = list(pts)
        pts += [(w, h), (0, h)]
        draw.polygon(pts, fill=_soft(accent, 46 + i * 14))
        # A pale line along each crest: on a dark palette the fill alone is the
        # same colour as the water and the waves vanish.
        draw.line(crest, fill=(255, 255, 255, 34 + i * 10), width=3, joint="curve")
    return 1.4


def _pattern_rays(draw, sharp, w, h, accent, rng) -> float:
    """A fan from the top-right corner — light through a window."""
    ox, oy = w * 0.92, -h * 0.15
    for i in range(9):
        a0 = math.radians(96 + i * 10.5)
        a1 = a0 + math.radians(4.5)
        span = w * 2
        draw.polygon(
            [
                (ox, oy),
                (ox + span * math.cos(a0), oy + span * math.sin(a0)),
                (ox + span * math.cos(a1), oy + span * math.sin(a1)),
            ],
            fill=(255, 255, 255, 16 if i % 2 else 26),
        )
    draw.ellipse(
        (ox - w * 0.22, oy - w * 0.22, ox + w * 0.22, oy + w * 0.22),
        fill=_soft(accent, 60),
    )
    return 2.0


def _pattern_bokeh(draw, sharp, w, h, accent, rng) -> float:
    """Out-of-focus lights, weighted to the right so text stays clean."""
    for _ in range(22):
        r = rng.uniform(w * 0.02, w * 0.09)
        cx = rng.uniform(w * 0.25, w * 1.02)
        cy = rng.uniform(-h * 0.1, h * 1.05)
        alpha = int(rng.uniform(14, 46))
        draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=_soft(accent, alpha))
    return 3.0


def _pattern_grid(draw, sharp, w, h, accent, rng) -> float:
    """An even neon grid — arcade backdrop."""
    step = max(28, w // 26)
    for x in range(0, w + step, step):
        draw.line([(x, 0), (x, h)], fill=_soft(accent, 30), width=2)
    for y in range(0, h + step, step):
        draw.line([(0, y), (w, y)], fill=_soft(accent, 22), width=2)
    draw.rectangle((0, h * 0.72, w, h), fill=_soft(accent, 18))
    return 0.6


def _pattern_stars(draw, sharp, w, h, accent, rng) -> float:
    """A field of small dots plus a handful of four-point sparkles."""
    for _ in range(70):
        x, y = rng.uniform(0, w), rng.uniform(0, h)
        r = rng.uniform(1.0, 2.6)
        sharp.ellipse((x - r, y - r, x + r, y + r), fill=(255, 255, 255, 130))
    for _ in range(7):
        x, y = rng.uniform(w * 0.3, w), rng.uniform(0, h)
        s = rng.uniform(7, 16)
        draw.polygon(
            [(x, y - s), (x + s * 0.28, y), (x, y + s), (x - s * 0.28, y)],
            fill=(255, 255, 255, 150),
        )
        draw.polygon(
            [(x - s, y), (x, y - s * 0.28), (x + s, y), (x, y + s * 0.28)],
            fill=(255, 255, 255, 150),
        )
    return 0.5


def _pattern_hex(draw, sharp, w, h, accent, rng) -> float:
    """A honeycomb of outlines, fading in from the right."""
    r = max(22, w // 22)
    dx, dy = r * 1.5, r * math.sqrt(3)
    col = 0
    x = w * 0.30
    while x < w + r:
        y = -r if col % 2 == 0 else -r + dy / 2
        while y < h + r:
            pts = [
                (
                    x + r * math.cos(math.radians(60 * i)),
                    y + r * math.sin(math.radians(60 * i)),
                )
                for i in range(6)
            ]
            fade = min(1.0, (x - w * 0.25) / max(1.0, w * 0.75))
            draw.polygon(pts, outline=_soft(accent, int(20 + 45 * fade)))
            y += dy
        x += dx
        col += 1
    return 0.8


def _pattern_nebula(draw, sharp, w, h, accent, rng) -> float:
    """Overlapping clouds — heavy blur does most of the work."""
    for _ in range(9):
        r = rng.uniform(w * 0.12, w * 0.34)
        cx = rng.uniform(w * 0.2, w * 1.05)
        cy = rng.uniform(-h * 0.2, h * 1.2)
        draw.ellipse(
            (cx - r, cy - r * 0.7, cx + r, cy + r * 0.7),
            fill=_soft(accent, int(rng.uniform(20, 48))),
        )
    for _ in range(70):
        x, y = rng.uniform(0, w), rng.uniform(0, h)
        r = rng.uniform(0.9, 2.2)
        sharp.ellipse((x - r, y - r, x + r, y + r), fill=(255, 255, 255, 190))
    return 8.0


def _pattern_circuit(draw, sharp, w, h, accent, rng) -> float:
    """Orthogonal traces with pads — one continuous walk per line."""
    step = max(24, w // 30)
    for _ in range(14):
        x = rng.randrange(0, w, step)
        y = rng.randrange(0, h, step)
        pts = [(x, y)]
        for _ in range(rng.randint(3, 7)):
            if rng.random() < 0.5:
                x = max(0, min(w, x + rng.choice((-1, 1)) * step * rng.randint(1, 4)))
            else:
                y = max(0, min(h, y + rng.choice((-1, 1)) * step * rng.randint(1, 4)))
            pts.append((x, y))
        draw.line(pts, fill=_soft(accent, 55), width=2, joint="curve")
        draw.ellipse((x - 4, y - 4, x + 4, y + 4), fill=_soft(accent, 90))
    return 0.7


def _pattern_peaks(draw, sharp, w, h, accent, rng) -> float:
    """Two mountain ridges, the far one paler."""
    for layer, (base, height, alpha) in enumerate(((0.92, 0.42, 30), (1.02, 0.30, 52))):
        pts = [(0, h)]
        x = 0.0
        up = True
        while x < w:
            step = w * rng.uniform(0.10, 0.20)
            x = min(w, x + step)
            y = h * base - (h * height * rng.uniform(0.5, 1.0) if up else 0)
            pts.append((x, y))
            up = not up
        pts.append((w, h))
        draw.polygon(pts, fill=_soft(accent, alpha) if layer else (255, 255, 255, 16))
    return 1.0


def _pattern_aurora(draw, sharp, w, h, accent, rng) -> float:
    """Vertical curtains of light, blurred hard."""
    for i in range(6):
        x = w * (0.25 + i * 0.14)
        width = w * rng.uniform(0.04, 0.10)
        skew = w * rng.uniform(-0.12, 0.12)
        draw.polygon(
            [
                (x, -h * 0.1),
                (x + width, -h * 0.1),
                (x + width + skew, h * 1.1),
                (x + skew, h * 1.1),
            ],
            fill=_soft(accent, int(rng.uniform(28, 60))),
        )
    return 12.0


_BANNER_PATTERNS = {
    "": _pattern_default,
    "waves": _pattern_waves,
    "rays": _pattern_rays,
    "bokeh": _pattern_bokeh,
    "grid": _pattern_grid,
    "stars": _pattern_stars,
    "hex": _pattern_hex,
    "nebula": _pattern_nebula,
    "circuit": _pattern_circuit,
    "peaks": _pattern_peaks,
    "aurora": _pattern_aurora,
}


def _generate_banner(d: cosmetics.CosmeticDef, size) -> Image.Image:
    """A flat gradient plus the def's pattern — enough to read as designed
    artwork at card size without being noisy behind text.

    The random placement some patterns use is seeded on the cosmetic key, so a
    banner looks the same every time it is drawn (and the on-disk cache stays
    valid) while two banners never land identically.
    """
    img = _gradient(size, _palette(d, 0), _palette(d, 1))
    w, h = size
    overlay = Image.new("RGBA", size, (0, 0, 0, 0))
    crisp = Image.new("RGBA", size, (0, 0, 0, 0))
    pattern = _BANNER_PATTERNS.get(d.pattern or "", _pattern_default)
    blur = pattern(
        ImageDraw.Draw(overlay),
        ImageDraw.Draw(crisp),
        w,
        h,
        _palette(d, 1),
        random.Random(d.key),
    )
    img.alpha_composite(overlay.filter(ImageFilter.GaussianBlur(blur)))
    img.alpha_composite(crisp)
    return img


def _generate_coin(d: cosmetics.CosmeticDef, size) -> Image.Image:
    """A struck coin: milled edge, inner ring, the def's glyph in the middle.

    The wallet card's centrepiece, and the reason `coin` is its own slot — a
    member who has bought nothing still gets a proper coin, they just get the
    default one.
    """
    w, h = size
    side = min(w, h)
    scale = 4
    big = side * scale
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    face = _gradient((big, big), _palette(d, 0), _palette(d, 1), diagonal=False)
    img.paste(face, (0, 0), _circle_mask(big))
    draw = ImageDraw.Draw(img)

    # Milled edge — short spokes around the rim, the detail that reads as
    # "coin" rather than "circle" even at 96px.
    cx = cy = big / 2
    for i in range(48):
        angle = math.pi * 2 * i / 48
        r0, r1 = big * 0.455, big * 0.495
        draw.line(
            [
                (cx + r0 * math.cos(angle), cy + r0 * math.sin(angle)),
                (cx + r1 * math.cos(angle), cy + r1 * math.sin(angle)),
            ],
            fill=(255, 255, 255, 70),
            width=max(2, scale),
        )
    inset = big * 0.12
    draw.ellipse(
        (inset, inset, big - inset, big - inset),
        outline=(255, 255, 255, 110),
        width=max(2, scale),
    )
    glyph = d.glyph or "◉"
    font = _fit_text(draw, glyph, int(big * 0.46), int(big * 0.56), bold=True)
    bbox = draw.textbbox((0, 0), glyph, font=font)
    draw.text(
        (
            (big - (bbox[2] - bbox[0])) / 2 - bbox[0],
            (big - (bbox[3] - bbox[1])) / 2 - bbox[1],
        ),
        glyph,
        font=font,
        fill=(255, 255, 255, 240),
    )
    return img.resize((side, side), Image.LANCZOS)


def _generate_badge(d: cosmetics.CosmeticDef, size) -> Image.Image:
    """A rounded gem with the def's glyph — the badge equivalent of a favicon."""
    w, h = size
    scale = 4  # supersample so the edges and glyph stay crisp when scaled down
    big = (w * scale, h * scale)
    img = Image.new("RGBA", big, (0, 0, 0, 0))
    body = _gradient(big, _palette(d, 0), _palette(d, 1))
    mask = Image.new("L", big, 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, big[0] - 1, big[1] - 1), radius=int(big[0] * 0.28), fill=255
    )
    img.paste(body, (0, 0), mask)

    draw = ImageDraw.Draw(img)
    # Inner highlight ring — the cheap trick that makes flat art look lit.
    inset = int(big[0] * 0.07)
    draw.rounded_rectangle(
        (inset, inset, big[0] - inset, big[1] - inset),
        radius=int(big[0] * 0.22),
        outline=(255, 255, 255, 60),
        width=max(2, scale),
    )
    glyph = d.glyph or "★"
    font = _fit_text(draw, glyph, int(big[1] * 0.52), int(big[0] * 0.66), bold=True)
    bbox = draw.textbbox((0, 0), glyph, font=font)
    draw.text(
        (
            (big[0] - (bbox[2] - bbox[0])) / 2 - bbox[0],
            (big[1] - (bbox[3] - bbox[1])) / 2 - bbox[1],
        ),
        glyph,
        font=font,
        fill=(255, 255, 255, 235),
    )
    return img.resize(size, Image.LANCZOS)


def prestige_emblem(rank: int, size: int = PRESTIGE_SIZE) -> Image.Image:
    """A rank emblem: a coloured shield-star that changes metal as you climb.

    Prestige is meant to read at a glance, so the *shape* carries the tier
    (bronze → silver → gold → amethyst → radiant) and the numeral is secondary.
    """
    tiers = [
        ("#6E6E6E", "#3F3F3F"),  # 0 — unprestiged (drawn muted)
        ("#CD7F32", "#7A4A1D"),  # 1-2 bronze
        ("#C0C0C0", "#7D7D7D"),  # 3-4 silver
        ("#FFD700", "#A67C00"),  # 5-6 gold
        ("#B57BFF", "#5B2A9E"),  # 7-8 amethyst
        ("#7CF9E4", "#0E8F82"),  # 9-10 radiant
    ]
    idx = 0 if rank <= 0 else min(len(tiers) - 1, (rank + 1) // 2)
    top, bottom = _rgba(tiers[idx][0]), _rgba(tiers[idx][1])

    scale = 4
    big = size * scale
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    disc = _gradient((big, big), top, bottom)
    img.paste(disc, (0, 0), _circle_mask(big))
    draw = ImageDraw.Draw(img)
    draw.ellipse(
        (2 * scale, 2 * scale, big - 2 * scale, big - 2 * scale),
        outline=(255, 255, 255, 90),
        width=2 * scale,
    )
    # A star whose point count grows with the tier — instant visual ranking.
    points = 5 + max(0, idx - 1)
    cx = cy = big / 2
    outer, inner = big * 0.34, big * 0.15
    star = []
    for i in range(points * 2):
        r = outer if i % 2 == 0 else inner
        angle = math.pi / points * i - math.pi / 2
        star.append((cx + r * math.cos(angle), cy + r * math.sin(angle)))
    draw.polygon(star, fill=(255, 255, 255, 225))
    if rank > 0:
        font = _font(int(big * 0.24), bold=True)
        label = str(rank)
        bbox = draw.textbbox((0, 0), label, font=font)
        draw.text(
            (cx - (bbox[2] - bbox[0]) / 2 - bbox[0], big * 0.62),
            label,
            font=font,
            fill=(20, 20, 26, 255),
        )
    return img.resize((size, size), Image.LANCZOS)


# ── Border styles ────────────────────────────────────────────────────────────
# Same contract as the banner patterns: one function per look, registered by
# name, selected by the def's `style`. "" is the original two-line frame, so an
# existing border keeps rendering exactly as it did.


def _border_solid(draw, box, radius, inner, outer):
    x0, y0, x1, y1 = box
    draw.rounded_rectangle((x0, y0, x1, y1), radius, outline=outer, width=5)
    draw.rounded_rectangle(
        (x0 + 5, y0 + 5, x1 - 5, y1 - 5), radius - 5, outline=inner, width=2
    )


def _border_double(draw, box, radius, inner, outer):
    x0, y0, x1, y1 = box
    draw.rounded_rectangle((x0, y0, x1, y1), radius, outline=outer, width=4)
    draw.rounded_rectangle(
        (x0 + 12, y0 + 12, x1 - 12, y1 - 12), radius - 10, outline=inner, width=3
    )


def _border_glow(draw, box, radius, inner, outer):
    """Concentric strokes fading outwards — a light source, not a frame."""
    x0, y0, x1, y1 = box
    for i in range(7):
        alpha = int(150 * (1 - i / 7))
        draw.rounded_rectangle(
            (x0 + i * 3, y0 + i * 3, x1 - i * 3, y1 - i * 3),
            max(2, radius - i * 3),
            outline=(outer[0], outer[1], outer[2], alpha),
            width=3,
        )
    draw.rounded_rectangle((x0, y0, x1, y1), radius, outline=inner, width=2)


def _border_dashed(draw, box, radius, inner, outer):
    x0, y0, x1, y1 = box
    dash, gap = 26, 16
    for x in range(x0 + radius, x1 - radius, dash + gap):
        end = min(x + dash, x1 - radius)
        draw.line([(x, y0 + 3), (end, y0 + 3)], fill=outer, width=4)
        draw.line([(x, y1 - 3), (end, y1 - 3)], fill=outer, width=4)
    for y in range(y0 + radius, y1 - radius, dash + gap):
        end = min(y + dash, y1 - radius)
        draw.line([(x0 + 3, y), (x0 + 3, end)], fill=outer, width=4)
        draw.line([(x1 - 3, y), (x1 - 3, end)], fill=outer, width=4)
    draw.rounded_rectangle((x0, y0, x1, y1), radius, outline=inner, width=1)


def _border_corners(draw, box, radius, inner, outer):
    """Brackets at the four corners and nothing along the edges."""
    x0, y0, x1, y1 = box
    arm = 84
    for cx, cy, sx, sy in (
        (x0, y0, 1, 1),
        (x1, y0, -1, 1),
        (x0, y1, 1, -1),
        (x1, y1, -1, -1),
    ):
        draw.line([(cx + sx * radius, cy), (cx + sx * arm, cy)], fill=outer, width=6)
        draw.line([(cx, cy + sy * radius), (cx, cy + sy * arm)], fill=outer, width=6)
        draw.line(
            [(cx + sx * radius, cy + sy * 12), (cx + sx * (arm - 18), cy + sy * 12)],
            fill=inner,
            width=2,
        )


def _border_ribbon(draw, box, radius, inner, outer):
    """A thick outer band with an inner hairline and solid corner blocks."""
    x0, y0, x1, y1 = box
    draw.rounded_rectangle((x0, y0, x1, y1), radius, outline=outer, width=9)
    draw.rounded_rectangle(
        (x0 + 13, y0 + 13, x1 - 13, y1 - 13), radius - 11, outline=inner, width=2
    )
    block = 30
    for cx, cy, sx, sy in (
        (x0, y0, 1, 1),
        (x1, y0, -1, 1),
        (x0, y1, 1, -1),
        (x1, y1, -1, -1),
    ):
        draw.polygon(
            [
                (cx + sx * 4, cy + sy * block),
                (cx + sx * block, cy + sy * 4),
                (cx + sx * block, cy + sy * block),
            ],
            fill=inner,
        )


_BORDER_STYLES = {
    "": _border_solid,
    "solid": _border_solid,
    "double": _border_double,
    "glow": _border_glow,
    "dashed": _border_dashed,
    "corners": _border_corners,
    "ribbon": _border_ribbon,
}


def draw_border(card: Image.Image, border, *, radius: int = RADIUS) -> None:
    """Composite a border cosmetic onto a finished card, in place.

    Takes the card so a second card type can frame itself the same way; the
    wallet card deliberately doesn't (see utils/wallet_card.py).
    """
    if not border or border.key == "border_none":
        return
    w, h = card.size
    frame = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    style = _BORDER_STYLES.get(border.style or "", _border_solid)
    style(
        ImageDraw.Draw(frame),
        (2, 2, w - 3, h - 3),
        radius,
        _palette(border, 0),
        _palette(border, 1),
    )
    card.alpha_composite(frame)


# ── The card ─────────────────────────────────────────────────────────────────
def _draw_bar(base, xy, width, height, ratio, colour_a, colour_b):
    x, y = xy
    track = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    ImageDraw.Draw(track).rounded_rectangle(
        (0, 0, width - 1, height - 1), height // 2, fill=(255, 255, 255, 38)
    )
    base.alpha_composite(track, (x, y))
    filled = max(height, int(width * max(0.0, min(1.0, ratio))))
    if ratio > 0:
        fill = _gradient((filled, height), colour_a, colour_b)
        mask = Image.new("L", (filled, height), 0)
        ImageDraw.Draw(mask).rounded_rectangle(
            (0, 0, filled - 1, height - 1), height // 2, fill=255
        )
        base.paste(fill, (x, y), mask)


def _draw_chip(base, draw, xy, size, label, value, accent):
    x, y = xy
    w, h = size
    chip = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(chip).rounded_rectangle(
        (0, 0, w - 1, h - 1), 14, fill=(255, 255, 255, 26)
    )
    base.alpha_composite(chip, (x, y))
    draw.rounded_rectangle((x, y, x + 4, y + h - 1), 2, fill=accent)
    draw.text((x + 16, y + 10), label.upper(), font=_font(15), fill=INK_FAINT)
    value_font = _fit_text(draw, value, 24, w - 32)
    draw.text((x + 16, y + 30), value, font=value_font, fill=INK)


def render_card(data: dict) -> bytes:
    """Render a profile card and return PNG bytes.

    `data` keys (all optional except `name`):
      name, title, avatar (raw image bytes), prestige, rep,
      global_level / global_into / global_need,
      server_level / server_into / server_need / server_enabled,
      chips  — [(label, value), …] shown as stat tiles,
      badges — [CosmeticDef, …] (up to the badge slot's max),
      banner / border / nameplate — CosmeticDef or None,
      footer — small text bottom-right.
    """
    banner = data.get("banner") or cosmetics.get("banner_default")
    border = data.get("border")
    plate = data.get("nameplate")
    accent = _palette(banner, 1)

    card = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    background = (
        cosmetic_image(banner, (W, H))
        if banner
        else _gradient((W, H), _rgba("#1E2130"), _rgba("#2B2D42"))
    )
    card.paste(background, (0, 0))

    # Scrim: darken left-to-right so text stays readable over any banner art.
    scrim = _gradient((W, H), (0, 0, 0, 170), (0, 0, 0, 60))
    card.alpha_composite(scrim.rotate(90, expand=True).resize((W, H)))

    draw = ImageDraw.Draw(card)

    # ── Rep pill (top-right) ──
    # Drawn before the name so the name can be fitted to the space that is
    # actually left: the chip grid is a full 2x3 with no room for a seventh
    # tile, and a third row would land on the badge showcase.
    rep = data.get("rep")
    name_max_w = W - TEXT_X - PAD
    if rep is not None:
        _draw_chip(
            card,
            draw,
            (W - PAD - REP_PILL_W, PAD),
            (REP_PILL_W, CHIP_H),
            "rep",
            f"{int(rep):,}",
            accent,
        )
        name_max_w -= REP_PILL_W + 20

    # ── Name + title ──
    name = str(data.get("name", "Unknown"))
    name_font = _fit_text(draw, name, 44, name_max_w)
    if plate and plate.key != "plate_default":
        plate_w = int(draw.textlength(name, font=name_font)) + 36
        plate_img = _gradient((plate_w, 56), _palette(plate, 0), _palette(plate, 1))
        plate_mask = _rounded_mask((plate_w, 56), 16)
        card.paste(plate_img, (TEXT_X - 14, NAME_Y - 6), plate_mask)
    draw.text((TEXT_X, NAME_Y), name, font=name_font, fill=INK)
    title = data.get("title")
    if title:
        draw.text((TEXT_X, TITLE_Y), str(title), font=_font(22), fill=INK_MUTED)

    # ── Avatar + prestige emblem ──
    avatar_bytes = data.get("avatar")
    avatar = None
    if avatar_bytes:
        try:
            avatar = Image.open(io.BytesIO(avatar_bytes)).convert("RGBA")
        except OSError:
            avatar = None
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
    ring = Image.new("RGBA", (AVATAR_SIZE + 12, AVATAR_SIZE + 12), (0, 0, 0, 0))
    ImageDraw.Draw(ring).ellipse(
        (0, 0, AVATAR_SIZE + 11, AVATAR_SIZE + 11), fill=(255, 255, 255, 60)
    )
    card.alpha_composite(ring, (AVATAR_XY[0] - 6, AVATAR_XY[1] - 6))
    card.paste(avatar, AVATAR_XY, _circle_mask(AVATAR_SIZE))

    prestige = int(data.get("prestige", 0) or 0)
    if prestige > 0:
        # Sat on the avatar's lower-right edge, mostly outside the circle so it
        # reads as a rank pin rather than covering the face.
        emblem = prestige_emblem(prestige)
        card.alpha_composite(
            emblem,
            (
                AVATAR_XY[0] + AVATAR_SIZE - PRESTIGE_SIZE + 18,
                AVATAR_XY[1] + AVATAR_SIZE - PRESTIGE_SIZE + 18,
            ),
        )

    # ── Level bars ──
    def level_block(y, label, level, into, need, colours):
        draw.text((TEXT_X, y - 26), label, font=_font(16, bold=True), fill=INK_FAINT)
        value = f"Level {level:,}"
        lvl_font = _font(16, bold=True)
        draw.text(
            (TEXT_X + BAR_W - draw.textlength(value, font=lvl_font), y - 26),
            value,
            font=lvl_font,
            fill=INK,
        )
        ratio = (into / need) if need else 0
        _draw_bar(card, (TEXT_X, y), BAR_W, BAR_H, ratio, colours[0], colours[1])
        detail = f"{into:,} / {need:,} XP" if need else "—"
        draw.text(
            (
                TEXT_X + BAR_W - draw.textlength(detail, font=_font(15)),
                y + BAR_H + 6,
            ),
            detail,
            font=_font(15),
            fill=INK_FAINT,
        )

    level_block(
        GLOBAL_BAR_Y,
        "GLOBAL LEVEL",
        int(data.get("global_level", 0)),
        int(data.get("global_into", 0)),
        int(data.get("global_need", 0)),
        (_rgba("#5865F2"), _rgba("#00F5D4")),
    )
    if data.get("server_enabled", True):
        level_block(
            SERVER_BAR_Y,
            "SERVER LEVEL",
            int(data.get("server_level", 0)),
            int(data.get("server_into", 0)),
            int(data.get("server_need", 0)),
            (_rgba("#FEE75C"), _rgba("#EB459E")),
        )
    else:
        draw.text(
            (TEXT_X, SERVER_BAR_Y - 26),
            "SERVER LEVEL",
            font=_font(16, bold=True),
            fill=INK_FAINT,
        )
        draw.text(
            (TEXT_X, SERVER_BAR_Y),
            "Leveling is off in this server",
            font=_font(18),
            fill=INK_MUTED,
        )

    # ── Stat chips ──
    chips = list(data.get("chips") or [])[:6]
    chip_w = (W - 2 * PAD - CHIP_GAP * (CHIP_COLS - 1)) // CHIP_COLS
    for i, (label, value) in enumerate(chips):
        col, row = i % CHIP_COLS, i // CHIP_COLS
        _draw_chip(
            card,
            draw,
            (PAD + col * (chip_w + CHIP_GAP), CHIP_TOP + row * (CHIP_H + CHIP_GAP)),
            (chip_w, CHIP_H),
            str(label),
            str(value),
            accent,
        )

    # ── Badge showcase ──
    slot_max = cosmetics.SLOTS["badge"].max_equipped
    badges = list(data.get("badges") or [])[:slot_max]
    for i in range(slot_max):
        x = PAD + i * (BADGE_SIZE + BADGE_GAP)
        if i < len(badges):
            card.alpha_composite(
                cosmetic_image(badges[i], (BADGE_SIZE, BADGE_SIZE)), (x, BADGE_Y)
            )
        else:
            draw.rounded_rectangle(
                (x, BADGE_Y, x + BADGE_SIZE, BADGE_Y + BADGE_SIZE),
                radius=16,
                outline=(255, 255, 255, 22),
                width=2,
            )

    footer = data.get("footer")
    if footer:
        f = _font(16)
        draw.text(
            (W - PAD - draw.textlength(str(footer), font=f), H - PAD - 18),
            str(footer),
            font=f,
            fill=INK_FAINT,
        )

    # ── Border + rounded corners ──
    draw_border(card, border)

    out = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    out.paste(card, (0, 0), _rounded_mask((W, H), RADIUS))
    buf = io.BytesIO()
    out.save(buf, "PNG", optimize=True)
    return buf.getvalue()
