# Economy System Design

Architecture notes for the 2026 economy overhaul: generic inventory, new
activities, the casino, and the fishing progression/global-leaderboard
expansion. Read alongside `CLAUDE.md` (which stays the canonical per-cog
reference).

## Goals

- Long-term engagement: progression (XP/levels, career ladders, rod/pickaxe
  tiers, streaks), collection (dex, materials), and daily hooks (quests,
  streak bonuses, limited-time events).
- Multiple interconnected activities with *distinct risk/reward profiles*,
  not five reskins of the same coin faucet.
- Healthy competition: guild leaderboards everywhere, plus a cross-server
  global fishing leaderboard.
- Zero schema churn for future content: new items, effects, events, and
  leaderboard stats are **data/registry entries**, not migrations.

## Layering

```
utils/items.py          code-side item catalogue (ItemDef registry)
utils/db/items.py       user_items / user_effects / economy_events tables
cogs/inventory/         /inventory UI: view, use, sell, give, info
        ▲  (item + effect layer — feature-agnostic)
        │
cogs/fishing/  cogs/activities/  cogs/casino/  cogs/economy/  (features)
        │
utils/db/<domain>.py    per-feature tables (fishing, activities, casino, …)
utils/db/economy.py     coins: the single source of truth for money
```

Rules that keep the layers decoupled:

- **Coins only move through `utils/db/economy.py`** (`add_coins`,
  `try_debit_coins`, `transfer_coins`). Every debit is an atomic conditional
  UPDATE; no feature keeps its own balance column.
- **No cog imports another cog.** Cross-feature interaction happens through
  data: items (`user_items`), effects (`user_effects`), and events
  (`economy_events`).
- **Items are code-defined, DB-referenced.** The DB stores only
  `(guild_id, user_id, item_key, qty)`; semantics live in the `ItemDef`
  registry (`utils/items.py`). Feature packages register their own items at
  import time (`cogs/fishing/items.py`, `cogs/activities/items.py`), so a new
  item — or a whole new category — never touches the schema. Unknown keys
  (removed items still owned) degrade gracefully to their raw key.
- **Effects are an interpretation contract.** `/inventory use` writes an
  item's `effect` dict into `user_effects`; whichever cog cares reads it.
  Current vocabulary:
  - `luck` — timed, generic; fishing adds it to rod luck, activities add it
    to rare-find odds.
  - `fish_bait` — charge-based; one charge consumed per cast, magnitude =
    luck bonus.
  - `fish_xp` — timed XP multiplier for fishing.
  - `rob_shield` — timed; blocks `/rob` against the holder.
  An effect is timed (`expires_at`) or charge-based (`uses_left`), never
  both; re-granting replaces (freshest consumable wins, no stacking).
- **Events are data.** `economy_events` rows are `(guild_id, event_key,
  magnitude, data, ends_at)`; `guild_id = '0'` means global (every guild).
  Features read the active set (`db.get_active_events`) and apply whatever
  their keys mean to them — new event types need no schema or shared code.

## Risk/reward profiles

| Activity | Cooldown | Profile |
|---|---|---|
| `/work` | 1h | Safe, low variance; career-ladder progression |
| `/mine` | 30m | Materials into inventory; pickaxe tiers as coin sink; small failure chance |
| `/hunt` | 45m | Materials + rare trophy; injury fine risk; drops `/rob` defense |
| `/explore` | 3h | High variance; treasure keys/chests/charms |
| `/rob` | 4h | PvP; capped steal, fine on failure, item counterplay (`rob_shield`) |
| `/fish` | ~60s | High-frequency core loop: XP, streaks, quests, events, bait |
| `/casino …` | none | Pure risk; house edge 3–8%, progressive jackpot as long-shot |
| `/daily`, `/squad`, `/raid` | 24h/social | Existing social/co-op faucets, unchanged |

Sinks offsetting the new faucets: bait/consumable purchases, rod + pickaxe
ladders, casino house edge, rob fines, shop items.

## Concurrency invariants (the patterns every accessor follows)

- Cooldown claims: single conditional upsert (`try_claim_cast`,
  `try_claim_activity`) — a double-send can't double-collect.
- Spends: conditional UPDATE that fails instead of going negative
  (`try_debit_coins`, `try_consume_item`, `consume_effect_use`).
- One-winner races: conditional UPDATE guarded on the expected prior state
  (`set_rod_level`, pickaxe upgrades, `try_claim_jackpot`) with refund on
  loss.
- Multi-step read-check-write flows serialize per `(guild, user)` with an
  in-cog `asyncio.Lock` (the `/daily` pattern).

## Global fishing leaderboard

`utils/db/fishing.py` keeps a `GLOBAL_STATS` registry mapping a stat key to a
whitelisted SQL aggregate over `fishing_stats` grouped by `user_id` across
all guilds. `/fish global <stat>` builds its choices from the registry, so a
new global ranking is one registry entry — no query or command changes.
Guild-scoped boards stay per-feature (`/fish top`, `/casino top`,
`/coin top`, `/coin contrib`).

## Indexing

Hot paths are point lookups on `(guild_id, user_id[, key])` primary keys.
Leaderboards ride dedicated `(guild_id, <stat> DESC)` indexes
(`economy_guild_coins`, `fishing_stats_earned`, casino equivalent). The
global leaderboard is a full-table aggregate over `fishing_stats` — fine at
current scale (thousands of rows); if it ever hurts, add a materialized
per-user rollup table without touching the command surface.
