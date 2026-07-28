# Profile card artwork

Two kinds of art end up on a card, and the split is deliberate.

**Generated art** is the baseline. Every badge, border, nameplate and most
banners are drawn at runtime from the definition's palette, glyph, `texture` and
`pattern` (`utils/cosmetics.py` → `utils/profile_card.py`) and cached under
`data/profile_cache/`. Nothing has to be downloaded for the bot to look
finished, and a new cosmetic costs one registry entry rather than a design job.

**Bundled artwork** is the premium tier: the `banner_art_*`, `banner_space_*`
and `wallet_art_*` cosmetics are real paintings and real telescope imagery,
stored here as WebP at card size. Everything bundled is **public domain** —
paintings whose copyright expired, and NASA imagery, which as a U.S. federal
work is not subject to copyright. `CREDITS.md` names every artist and links
every source.

## Fetching / refreshing the bundled art

```bash
python scripts/fetch_cosmetic_art.py            # fetch anything missing
python scripts/fetch_cosmetic_art.py --list     # resolve + report, download nothing
python scripts/fetch_cosmetic_art.py --force    # re-fetch everything
```

`art_manifest.json` is the source list. The script resolves each entry against
the Wikimedia Commons or NASA API, **re-checks the licence** (and refuses
anything that isn't public domain / CC0), crops to the card's aspect ratio, and
regenerates `CREDITS.md`. Adding a painting is one manifest entry plus one
`CosmeticDef` — `tests/test_cosmetic_art.py` fails if either half is missing.

## Adding your own art

Drop an image here, named after the cosmetic's key, in a folder named after its
slot. `.webp`, `.png`, `.jpg` all work:

```
assets/profile/
  badge/badge_developer.png      ← square, 256×256 or larger
  banner/banner_aurora.webp      ← wide, 1000×560 (the card size) or larger
  border/border_gold.png
  nameplate/plate_neon.png
  wallet/wallet_vault.webp       ← 920×450, the wallet card size
```

The renderer prefers a file here over generated art automatically — no code
change and no cache to clear (the cache key includes the file's modification
time, so replacing the image takes effect on the next render). Delete the file
to go back to the generated version.

Banners and wallet banners are **cropped to fill** rather than stretched, then
dimmed and vignetted so white text stays readable over them; you don't need to
pre-darken anything. Anything you add must be licensed for redistribution — the
generated fallbacks exist so the bot never depends on artwork it doesn't own.

## Adding new cosmetics

Either add a `CosmeticDef` in `utils/cosmetics.py`, or — with no code at all —
create `data/cosmetics.json`:

```json
[
  {
    "key": "badge_summer_2026",
    "name": "Summer 2026",
    "slot": "badge",
    "glyph": "☀",
    "palette": ["#F9C80E", "#EA3546"],
    "unlock": {"kind": "manual"},
    "rarity": "event",
    "description": "Played during the summer event."
  }
]
```

Unlock kinds: `default`, `global_level`, `prestige`, `achievement`, `stat`,
`purchase` (the coin shop — pair it with a `price`), `manual`. Glyphs must exist
in the bundled font (geometric symbols and dingbats are safe; colour emoji are
not) — `tests/test_cosmetics.py` checks this for the built-in set.

See `docs/identity-and-levels.md` for the full picture.
