# Gatekeeper stock-avatar catalog (bundled seeds)

Reference images for the gatekeeper's stock-avatar detection (`cogs/gatekeeper.py`).

Discord's pickable "stock" avatars (the illustrated blob characters offered
during mobile signup) are stored as **real** custom avatars — `member.avatar`
is not `None` — so they can't be detected by a null check. Instead the bot
computes a perceptual (difference) hash of each image here and compares it
against every joining member's avatar.

- Images in **this** folder ship with the repo and are version-controlled.
- Mods add more at runtime with `/gatekeeper learnavatar` (or paste a URL);
  those land in the gitignored `data/gatekeeper_avatars/` folder.

Both folders are scanned on cog load and after each `learnavatar`. Drop any
`.png`/`.webp`/`.jpg` here to extend the bundled seed set. One image per
distinct stock avatar (different background colours are different images).
