"""
web/engine/
The economy, resolved without Discord.

Why this exists. NanoBot's game logic already lives in two clean halves: pure
roll-taking helpers (`cogs/*/helpers.py`, deliberately Discord-free) and atomic
database claims (`utils/db/*.try_claim_*`). What sits between them in the cogs
is *presentation* — building a `discord.Embed`. The web needs the middle without
the presentation, so these modules re-orchestrate the same two halves and return
plain dicts.

That means the parts where divergence would actually hurt cannot diverge:

*   **Cooldowns and one-time claims are the same rows.** A cast from the browser
    calls `db.try_claim_cast`, the very statement `/fish` calls. There is no web
    cooldown and no Discord cooldown, only *the* cooldown — so a member cannot
    cast on their phone and again on the site, which is the failure mode a
    second implementation would ship with.
*   **Every roll goes through the cogs' own helpers.** `rarity_odds`,
    `pick_spot_rarity`, `roll_weight`, `catch_value`, `roll_vein`,
    `apply_streak` — imported, never copied. Rebalancing a table in
    `cogs/fishing/constants.py` moves the web with it.

What is genuinely restated here is the *order of operations* — claim, then
streak, then luck, then roll, then persist. `tests/test_web_engine.py` pins that
order against the same expectations the cog's own tests use, and
`tests/test_web_engine_parity.py` asserts these modules import the shared
helpers rather than defining their own, so a copy-paste fix in one place fails
CI instead of quietly making the website a different game.

Every function here returns a plain dict, takes ids rather than objects, and
never touches `discord`.
"""

from . import activities, economy, fishing  # noqa: F401

__all__ = ["activities", "economy", "fishing"]
