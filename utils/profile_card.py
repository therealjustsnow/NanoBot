"""utils/profile_card.py — renders the /profile card as an image.

Everything is drawn with Pillow at call time; the bot ships no binary artwork.
Each cosmetic declares a palette and a glyph (utils/cosmetics.py) and this
module turns that into clean, flat vector-style art — gradients, rounded
rectangles, rings — cached to ``data/profile_cache/``. Drop a real PNG at
``assets/profile/<slot>/<key>.png`` and it is used instead, no code change:
that's the "don't sink time into art now, swap it in later" contract.

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
    if d.slot == "banner":
        return _generate_banner(d, size)
    return _gradient(size, _palette(d, 0), _palette(d, 1))


def _generate_banner(d: cosmetics.CosmeticDef, size) -> Image.Image:
    """A flat gradient plus a couple of soft geometric shapes — enough to read
    as designed artwork at card size without being noisy behind text."""
    img = _gradient(size, _palette(d, 0), _palette(d, 1))
    w, h = size
    overlay = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    accent = _palette(d, 1)
    soft = (accent[0], accent[1], accent[2], 38)
    # Two large off-canvas circles + a diagonal sweep: reads as depth, costs
    # nothing, and stays out of the text areas on the left.
    draw.ellipse((w * 0.62, -h * 0.55, w * 1.25, h * 0.85), fill=soft)
    draw.ellipse((w * 0.45, h * 0.45, w * 1.1, h * 1.9), fill=soft)
    draw.polygon(
        [(w * 0.05, h), (w * 0.35, 0), (w * 0.45, 0), (w * 0.15, h)],
        fill=(255, 255, 255, 12),
    )
    img.alpha_composite(overlay.filter(ImageFilter.GaussianBlur(1.2)))
    return img


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
      name, title, avatar (raw image bytes), prestige,
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

    # ── Name + title ──
    name = str(data.get("name", "Unknown"))
    name_font = _fit_text(draw, name, 44, W - TEXT_X - PAD)
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
    if border and border.key != "border_none":
        frame = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        fdraw = ImageDraw.Draw(frame)
        fdraw.rounded_rectangle(
            (2, 2, W - 3, H - 3),
            RADIUS,
            outline=_palette(border, 1),
            width=5,
        )
        fdraw.rounded_rectangle(
            (7, 7, W - 8, H - 8), RADIUS - 5, outline=_palette(border, 0), width=2
        )
        card.alpha_composite(frame)

    out = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    out.paste(card, (0, 0), _rounded_mask((W, H), RADIUS))
    buf = io.BytesIO()
    out.save(buf, "PNG", optimize=True)
    return buf.getvalue()
