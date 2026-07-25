# Profile card artwork

The bot ships **no binary artwork**. Every badge, banner, border and nameplate
on a `/profile` card is generated at runtime from its definition's palette and
glyph (`utils/cosmetics.py` → `utils/profile_card.py`) and cached under
`data/profile_cache/`.

## Replacing generated art with real art

Drop a PNG here, named after the cosmetic's key, in a folder named after its
slot:

```
assets/profile/
  badge/badge_developer.png      ← square, 256×256 or larger
  banner/banner_aurora.png       ← wide, 1000×560 (the card size) or larger
  border/border_gold.png
  nameplate/plate_neon.png
```

The renderer prefers a file here over generated art automatically — no code
change, no restart-time build step, no cache to clear (asset files are checked
before the cache). Delete the file to go back to the generated version.

Anything you add must be licensed for commercial use; the generated fallbacks
exist so the bot never depends on artwork it doesn't own.

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
`manual`. Glyphs must exist in the bundled font (geometric symbols and
dingbats are safe; colour emoji are not) — `tests/test_cosmetics.py` checks
this for the built-in set.

See `docs/identity-and-levels.md` for the full picture.
