"""utils/cookie_card.py — renders the /fun cookie dashboard as an image.

The second card, and the reason `utils/profile_card.py` was written to be
reusable: it takes a plain dict, knows nothing about Discord or the database,
and draws everything with Pillow at call time (the bot still ships no binary
artwork). Layout constants live at the top, the same as over there.

Shape and helpers are borrowed from the profile card deliberately — the two
cards should look like they came from the same bot, and duplicating a gradient
routine to avoid importing one is how they stop matching. Only the pure drawing
primitives are imported; nothing about cosmetics or the profile layout is.

Rendering is CPU-bound: call it through ``asyncio.to_thread`` (the cog does).
"""

from __future__ import annotations

import io

from PIL import Image, ImageDraw

from utils.profile_card import (
    INK,
    INK_FAINT,
    INK_MUTED,
    _circle_mask,
    _fit_text,
    _font,
    _gradient,
    _rgba,
    _rounded_mask,
)

# ── Layout ───────────────────────────────────────────────────────────────────
W, H = 900, 420
PAD = 40
RADIUS = 28

AVATAR_SIZE = 132
AVATAR_XY = (PAD, PAD + 10)

TEXT_X = AVATAR_XY[0] + AVATAR_SIZE + 28
NAME_Y = PAD + 14
SUB_Y = NAME_Y + 50

# Two big tallies side by side — the whole point of the dashboard.
TALLY_TOP = 196
TALLY_H = 116
TALLY_GAP = 24
TALLY_W = (W - PAD * 2 - TALLY_GAP) // 2

FOOTER_Y = H - PAD + 2

# Warm biscuit palette, so the card reads as "cookie" without any emoji — the
# bundled font has no colour emoji and would render one as tofu.
DOUGH = _rgba("#C68642")
DOUGH_DARK = _rgba("#8B5A2B")
CHIP = _rgba("#4A2C14")
BG_TOP = _rgba("#2A1F17")
BG_BOTTOM = _rgba("#463225")
SENT_ACCENT = _rgba("#F2A65A")
GOT_ACCENT = _rgba("#E8C07D")

# Deterministic chip placement (offset from centre in cookie radii, 0-1) so a
# drawn cookie looks hand-made but never lands differently between renders.
_CHIP_SPOTS = ((-0.42, -0.30), (0.28, -0.44), (0.44, 0.18), (-0.20, 0.42), (0.02, 0.02))


def cookie_image(size: int) -> Image.Image:
    """A drawn cookie: dough disc, darker rim, five chips."""
    scale = 4  # supersample, then downscale — cheap anti-aliasing
    big = size * scale
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse((0, 0, big - 1, big - 1), fill=DOUGH_DARK)
    inset = big * 0.06
    draw.ellipse((inset, inset, big - 1 - inset, big - 1 - inset), fill=DOUGH)
    radius = big / 2
    chip_r = big * 0.085
    for fx, fy in _CHIP_SPOTS:
        cx = radius + fx * radius
        cy = radius + fy * radius
        draw.ellipse((cx - chip_r, cy - chip_r, cx + chip_r, cy + chip_r), fill=CHIP)
    return img.resize((size, size), Image.LANCZOS)


def _tally(base, draw, xy, size, label, value, accent):
    """One of the two big number panels."""
    x, y = xy
    w, h = size
    panel = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(panel).rounded_rectangle(
        (0, 0, w - 1, h - 1), 18, fill=(255, 255, 255, 24)
    )
    base.alpha_composite(panel, (x, y))
    draw.rounded_rectangle((x, y, x + 5, y + h - 1), 3, fill=accent)

    cookie = cookie_image(40)
    base.alpha_composite(cookie, (x + w - 40 - 18, y + 16))

    draw.text((x + 22, y + 18), label.upper(), font=_font(16), fill=INK_FAINT)
    text = f"{int(value):,}"
    draw.text((x + 22, y + 46), text, font=_fit_text(draw, text, 48, w - 100), fill=INK)


def render_cookie_card(data: dict) -> bytes:
    """Render the cookie dashboard and return PNG bytes.

    `data` keys (all optional except `name`):
      name, avatar (raw image bytes), sent, received,
      rank   — position on the received board, or None if they have none yet,
      streak_line / footer — small text lines.
    """
    card = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    card.paste(_gradient((W, H), BG_TOP, BG_BOTTOM), (0, 0))
    draw = ImageDraw.Draw(card)

    # A few oversized, very faint cookies as wallpaper.
    for i, (fx, fy, s) in enumerate(((0.80, 0.16, 190), (0.62, 0.86, 120))):
        ghost = cookie_image(s).copy()
        ghost.putalpha(ghost.getchannel("A").point(lambda a: int(a * 0.07)))
        card.alpha_composite(ghost, (int(W * fx), int(H * fy) - s // 2))

    # ── Avatar ──
    avatar = None
    if data.get("avatar"):
        try:
            avatar = Image.open(io.BytesIO(data["avatar"])).convert("RGBA")
        except OSError:
            avatar = None
    name = str(data.get("name", "Unknown"))
    if avatar is None:
        avatar = _gradient((AVATAR_SIZE, AVATAR_SIZE), DOUGH, DOUGH_DARK)
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
    card.paste(avatar, AVATAR_XY, _circle_mask(AVATAR_SIZE))
    ring = Image.new("RGBA", (AVATAR_SIZE + 10, AVATAR_SIZE + 10), (0, 0, 0, 0))
    ImageDraw.Draw(ring).ellipse(
        (0, 0, AVATAR_SIZE + 9, AVATAR_SIZE + 9), outline=DOUGH, width=4
    )
    card.alpha_composite(ring, (AVATAR_XY[0] - 5, AVATAR_XY[1] - 5))

    # ── Name + subtitle ──
    draw.text(
        (TEXT_X, NAME_Y),
        name,
        font=_fit_text(draw, name, 40, W - TEXT_X - PAD),
        fill=INK,
    )
    rank = data.get("rank")
    sub = "Cookie Jar" if rank is None else f"Cookie Jar  ·  #{int(rank):,} most loved"
    draw.text((TEXT_X, SUB_Y), sub, font=_font(20), fill=INK_MUTED)

    # ── The two tallies ──
    _tally(
        card,
        draw,
        (PAD, TALLY_TOP),
        (TALLY_W, TALLY_H),
        "given",
        data.get("sent", 0),
        SENT_ACCENT,
    )
    _tally(
        card,
        draw,
        (PAD + TALLY_W + TALLY_GAP, TALLY_TOP),
        (TALLY_W, TALLY_H),
        "received",
        data.get("received", 0),
        GOT_ACCENT,
    )

    line = data.get("streak_line")
    if line:
        draw.text(
            (PAD, TALLY_TOP + TALLY_H + 18), str(line), font=_font(20), fill=INK_MUTED
        )

    footer = data.get("footer")
    if footer:
        text = str(footer)
        font = _font(16)
        draw.text(
            (W - PAD - draw.textlength(text, font=font), FOOTER_Y - 22),
            text,
            font=font,
            fill=INK_FAINT,
        )

    out = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    out.paste(card, (0, 0), _rounded_mask((W, H), RADIUS))
    buffer = io.BytesIO()
    out.convert("RGBA").save(buffer, "PNG")
    return buffer.getvalue()
