"""
tests/test_wallet_card.py
The /balance card renderer (utils/wallet_card.py).

Same contract as the profile card: the renderer must never be the reason a
command fails, so it has to produce a PNG for a maxed account, a brand-new one,
a broken avatar and a very long name. It is Discord- and DB-free, so everything
here is a plain dict in and bytes out.
"""

import io

from PIL import Image

from utils import cosmetics, profile_card, wallet_card

FULL = {
    "name": "Longestpossible Displayname Here",
    "balance": 987_654_321,
    "currency": "NanoCoins",
    "rank": 3,
    "contribution": 45_230,
    "contribution_rank": 7,
    "contribution_title": "Patron",
    "streak": 12,
    "daily_line": "Daily ready now",
    "footer": "One wallet in every server",
}


def _render(data: dict) -> Image.Image:
    """Render and decode, asserting the bytes really are the format the cogs
    name the attachment after."""
    raw = wallet_card.render_wallet_card(data)
    img = Image.open(io.BytesIO(raw))
    assert img.format == profile_card.IMAGE_FORMAT
    return img


def test_renders_a_full_account():
    img = _render(
        dict(
            FULL,
            wallet=cosmetics.get("wallet_neon"),
            coin=cosmetics.get("coin_gold"),
        )
    )
    assert img.size == (wallet_card.W, wallet_card.H)


def test_renders_a_brand_new_account():
    """No coins, no rank, no streak, nothing equipped."""
    img = _render({"name": "Fresh"})
    assert img.size == (wallet_card.W, wallet_card.H)


def test_a_broken_avatar_degrades_to_an_initial_tile():
    img = _render(dict(FULL, avatar=b"not an image"))
    assert img.size == (wallet_card.W, wallet_card.H)


def test_every_wallet_cosmetic_renders():
    """Each wallet banner and coin style has to survive being drawn — a bad
    palette or a glyph the font lacks would only show up on someone's card."""
    for slot in ("wallet", "coin"):
        for d in cosmetics.in_slot(slot):
            data = dict(FULL)
            data[slot] = d
            assert _render(data).size == (wallet_card.W, wallet_card.H)


def test_rendering_is_deterministic():
    """Generated art is seeded on the cosmetic key, so two renders of the same
    data are byte-identical — which is what makes the on-disk art cache safe."""
    data = dict(FULL, wallet=cosmetics.get("wallet_vault"))
    assert wallet_card.render_wallet_card(data) == wallet_card.render_wallet_card(data)


def test_the_wallet_card_has_no_border_slot():
    """A deliberate design line, not an oversight: the card is small and the
    balance is the composition. If a border slot is ever added to the wallet,
    this test is the place that says the decision changed."""
    assert [s.key for s in cosmetics.slots_in("wallet")] == ["wallet", "coin"]
