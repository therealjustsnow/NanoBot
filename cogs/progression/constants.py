"""cogs/progression/constants.py — tunables shared by helpers.py, engine, and
cog.py. No Discord imports — kept importable from pure-logic tests.
"""

# ── Prestige ─────────────────────────────────────────────────────────────────────
PRESTIGE_MAX = 10
PRESTIGE_POINTS_BASE = 100  # points required to advance FROM rank N is BASE*(N+1)
# Coin cost to advance FROM rank N is BASE*(N+1)**2 — quadratic, not linear.
# With the 20s cast cooldown a linear ladder would have been cleared in a third
# of the time; this curve instead makes the *first* prestige slightly cheaper
# than it used to be (20k vs 25k) and the tenth roughly eight times dearer, so
# the endgame sink outlives the faster faucet. Whole ladder: 7.7M coins,
# ~860h of active max-luck fishing (was 1.375M / ~460h) — so the endgame takes
# roughly twice as long as it used to, despite income tripling.
PRESTIGE_COST_BASE = 20_000
PRESTIGE_WEEKLY_BONUS_PER_RANK = 0.05  # +5% weekly-objective coin payouts per rank

PRESTIGE_TITLES = {
    0: "",
    1: "Prestige I",
    2: "Prestige II",
    3: "Prestige III",
    4: "Prestige IV",
    5: "Prestige V",
    6: "Prestige VI",
    7: "Prestige VII",
    8: "Prestige VIII",
    9: "Prestige IX",
    10: "Prestige X",
}

# ── Weekly objectives ────────────────────────────────────────────────────────────
WEEKLY_OBJECTIVE_COUNT = 3

# ── Display ──────────────────────────────────────────────────────────────────────
BAR_WIDTH = 12
