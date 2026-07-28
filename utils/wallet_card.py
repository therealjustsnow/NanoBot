"""utils/wallet_card.py — renders the /balance wallet card as an image.

The third card, after the profile and the cookie jar, and built the same way:
plain dict in, PNG bytes out, no Discord and no database. Only the pure drawing
primitives are imported from ``utils/profile_card.py`` — duplicating a gradient
routine to avoid an import is how two cards stop looking like one bot.

Deliberately *not* a second profile card. A wallet answers one question — how
much have I got — so the balance is the whole composition and everything else
(rank, contribution, streak) is a supporting tally. It carries a banner and a
coin style and no border: a frame on a card this small crowds the number, and
the profile card is where a loadout is meant to be shown off.

Rendering is CPU-bound: call it through ``asyncio.to_thread`` (the cog does).
"""

from __future__ import annotations

import io

from PIL import Image, ImageDraw

from utils import cosmetics
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
    cosmetic_image,
)

# ── Layout ───────────────────────────────────────────────────────────────────
W, H = 920, 450
PAD = 40
RADIUS = 28

AVATAR_SIZE = 108
AVATAR_XY = (PAD, PAD)

TEXT_X = AVATAR_XY[0] + AVATAR_SIZE + 24
NAME_Y = PAD + 12
SUB_Y = NAME_Y + 44

# The balance block: a big coin on the left, the number beside it.
COIN_SIZE = 96
BALANCE_Y = 178
COIN_XY = (PAD, BALANCE_Y)
BALANCE_X = PAD + COIN_SIZE + 26

# Three tallies across the bottom, with a footer line under them.
TALLY_H = 92
TALLY_TOP = H - PAD - 26 - TALLY_H
TALLY_GAP = 16
TALLY_COLS = 3
TALLY_W = (W - PAD * 2 - TALLY_GAP * (TALLY_COLS - 1)) // TALLY_COLS


def _tally(base, draw, xy, size, label, value, sub, accent):
    """One bottom panel: small caps label, the number, one line of detail."""
    x, y = xy
    w, h = size
    panel = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(panel).rounded_rectangle(
        (0, 0, w - 1, h - 1), 16, fill=(255, 255, 255, 26)
    )
    base.alpha_composite(panel, (x, y))
    draw.rounded_rectangle((x, y, x + 5, y + h - 1), 3, fill=accent)

    draw.text((x + 18, y + 12), str(label).upper(), font=_font(15), fill=INK_FAINT)
    text = str(value)
    draw.text((x + 18, y + 33), text, font=_fit_text(draw, text, 30, w - 36), fill=INK)
    if sub:
        detail = str(sub)
        draw.text(
            (x + 18, y + 68),
            detail,
            font=_fit_text(draw, detail, 16, w - 36, bold=False),
            fill=INK_MUTED,
        )


def render_wallet_card(data: dict) -> bytes:
    """Render the wallet card and return PNG bytes.

    `data` keys (all optional except `name`):
      name, avatar (raw image bytes), balance, currency (plural name),
      rank        — position on the global rich list, or None,
      contribution / contribution_rank / contribution_title,
      streak      — current /daily streak,
      daily_line  — "Daily ready" / "Daily in 4h", shown under the streak,
      footer      — small text bottom-right,
      wallet / coin — CosmeticDef or None (the two wallet slots).
    """
    banner = data.get("wallet") or cosmetics.get("wallet_default")
    coin = data.get("coin") or cosmetics.get("coin_default")
    accent = _rgba((banner.palette[1] if banner and len(banner.palette) > 1 else ""))

    card = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    background = (
        cosmetic_image(banner, (W, H))
        if banner
        else _gradient((W, H), _rgba("#12161F"), _rgba("#232A3B"))
    )
    card.paste(background, (0, 0))
    # Darken from the bottom up: the tallies sit on the busy part of a banner,
    # the header on the calm part.
    scrim = _gradient((W, H), (0, 0, 0, 40), (0, 0, 0, 165))
    card.alpha_composite(scrim)
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
        avatar = _gradient((AVATAR_SIZE, AVATAR_SIZE), accent, _rgba("#1E2130"))
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

    # ── Name + subtitle ──
    draw.text(
        (TEXT_X, NAME_Y),
        name,
        font=_fit_text(draw, name, 38, W - TEXT_X - PAD),
        fill=INK,
    )
    draw.text((TEXT_X, SUB_Y), "Wallet", font=_font(20, bold=True), fill=INK_FAINT)
    draw.text(
        (TEXT_X + draw.textlength("Wallet", font=_font(20, bold=True)) + 12, SUB_Y),
        "· the same balance in every server",
        font=_font(18),
        fill=INK_MUTED,
    )

    # ── The balance: the whole point of the card ──
    if coin:
        card.alpha_composite(cosmetic_image(coin, (COIN_SIZE, COIN_SIZE)), COIN_XY)
    currency = str(data.get("currency") or "coins").upper()
    amount = f"{int(data.get('balance', 0)):,}"
    # The currency name rides the number's baseline rather than sitting under
    # it: a nine-figure balance is wide, and stacking the label put it into the
    # tallies. Fit the number to whatever is left after reserving room for it.
    label_font = _font(20, bold=True)
    label_w = draw.textlength(currency, font=label_font) + 16
    amount_font = _fit_text(draw, amount, 76, W - BALANCE_X - PAD - label_w)
    draw.text((BALANCE_X, BALANCE_Y), amount, font=amount_font, fill=INK)
    amount_box = draw.textbbox((BALANCE_X, BALANCE_Y), amount, font=amount_font)
    draw.text(
        (amount_box[2] + 14, amount_box[3] - 26),
        currency,
        font=label_font,
        fill=INK_FAINT,
    )

    # ── Tallies ──
    rank = data.get("rank")
    contribution = int(data.get("contribution", 0) or 0)
    contrib_rank = data.get("contribution_rank")
    streak = int(data.get("streak", 0) or 0)
    tallies = [
        (
            "rank",
            f"#{int(rank):,}" if rank else "—",
            "richest, globally" if rank else "no coins yet",
        ),
        (
            "contribution",
            f"{contribution:,}",
            str(data.get("contribution_title") or "")
            + (f" · #{int(contrib_rank):,}" if contrib_rank else ""),
        ),
        (
            "daily streak",
            f"{streak:,}" if streak else "—",
            str(data.get("daily_line") or ""),
        ),
    ]
    for i, (label, value, sub) in enumerate(tallies):
        _tally(
            card,
            draw,
            (PAD + i * (TALLY_W + TALLY_GAP), TALLY_TOP),
            (TALLY_W, TALLY_H),
            label,
            value,
            sub,
            accent,
        )

    footer = data.get("footer")
    if footer:
        text = str(footer)
        font = _font(15)
        draw.text(
            (W - PAD - draw.textlength(text, font=font), H - PAD - 4),
            text,
            font=font,
            fill=INK_FAINT,
        )

    out = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    out.paste(card, (0, 0), _rounded_mask((W, H), RADIUS))
    buffer = io.BytesIO()
    out.save(buffer, "PNG", optimize=True)
    return buffer.getvalue()
