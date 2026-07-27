# Global Economy — scope audit + migration

The economy used to be **per guild**: the same person had a separate wallet,
inventory, rod, pickaxe, casino record, achievements and prestige in every
server the bot was in. Joining a second community meant starting from zero,
and playing in five meant maintaining five unrelated save files.

It is now **per user**. Progress belongs to the account; only genuinely
server-owned things (settings, the guild's shop, its jackpot, its live boards)
still carry a `guild_id`.

Read this alongside `docs/economy-design.md` (systems design) and `CLAUDE.md`
(per-cog reference).

## The rule

> For every piece of stored data, ask: *would a member expect this to follow
> them into another server?* If yes, it is keyed by `user_id`. If it is a
> decision the **server** made, it stays keyed by `guild_id`.

## 1. Converted to global (user-scoped)

| Table | Holds | Was | Now |
|---|---|---|---|
| `economy` | coins, `last_daily`, daily streak, lifetime contribution | `(guild, user)` | `user` |
| `user_items` | every item stack (bait, ore, pelts, keys, chests, crafted goods) | `(guild, user, item)` | `(user, item)` |
| `user_effects` | active buffs/charges (luck, bait, XP potions, rob shield) | `(guild, user, effect)` | `(user, effect)` |
| `fishing_stats` | rod tier, XP, casts, catches, lifetime earnings, personal best, login streak, cast cooldown | `(guild, user)` | `user` |
| `fishing_catches` | the unsold bag | `(guild, user)` | `user` |
| `fishing_species` | lifetime dex counts | `(guild, user, species)` | `(user, species)` |
| `fishing_quests` | the daily quest row + its one-time claim | `(guild, user, day)` | `(user, day)` |
| `casino_stats` | games, wagered, won, wins, biggest win, current + best streak | `(guild, user)` | `user` |
| `casino_challenges` | the 2 daily challenges' progress + claim flags | `(guild, user, day, key)` | `(user, day, key)` |
| `activities_stats` | pickaxe tier, work/mine/hunt/explore/rob counts **and every cooldown stamp** | `(guild, user)` | `user` |
| `achievements_earned` | earned achievements + award time | `(guild, user, key)` | `(user, key)` |
| `weekly_objectives` | weekly objective baselines + claim flags | `(guild, user, week, key)` | `(user, week, key)` |
| `progression` | prestige rank, selected title | `(guild, user)` | `user` |

Code-side conversions that go with them:

- Every accessor in `utils/db/{economy,items,fishing,casino,activities,progression}.py`
  dropped its `guild_id` parameter (≈70 functions, ~400 call sites).
- Per-user locks: the `KeyedLocks` in economy/fishing/casino/activities/
  inventory/crafting/progression are keyed by `user_id`, not `(guild, user)` —
  otherwise two servers could race the same wallet.
- Deterministic generators reseeded on the user: `generate_quest`,
  `generate_challenges`, `pick_weekly_objectives`. A member now gets *one*
  daily quest and *one* pair of casino challenges per day, the same in every
  server (they used to differ per guild, which would have mismatched the now
  single global progress row).
- `cogs/progression/stats.py` — the whole `STAT_PROVIDERS` contract is
  `fn(user_id)`.
- Leveling's level-up coin reward credits the global wallet.
- `/fish global`'s stat registry no longer needs `GROUP BY user_id`: one row
  per user makes it a plain `ORDER BY` (`select` → `column`).

## 2. Deliberately still per guild

| Data | Why |
|---|---|
| `economy_config` (currency name/emoji, raid party size) | A server's own flavour, and how many of its members one raid board holds. The reward *amounts* left this table in migration 5 — see the faucets/sinks note below. |
| `shop_items` prices | The one economy number a guild sets that moves real value — and safely, because it is a **sink**. See below. |
| `fishing_config`, `casino_config` (limits, on/off), `activities_config` (per-activity on/off) | Server rules — whether a feature runs *here*. Cooldown **lengths** are not among them; see the note under the table. |
| `casino_config.jackpot_pool` | A progressive pot fed by that server's losses. Global pooling would let a big server's losses fund a small server's win. |
| `shop_items`, `shop_purchases` | The shop hands out **that guild's roles** and mod-fulfilled rewards; per-item limits/cooldowns are properties of the guild's item. Purchases spend the global wallet. |
| `economy_events` | Server events (already supports a global row via the `guild_id='0'` sentinel). |
| `economy_raids`, `economy_squads`, `casino_blackjack` | Live message/board state — a channel in a guild, not progression. |
| Leveling (`user_levels`, `level_config`, `level_rewards`, `level_ignored_channels`) | Chat XP measures participation *in that community*, and its rewards are that guild's roles. Deliberately not global; only the optional coin reward crosses over. |

**Why a cooldown *length* can't be a server setting at all.** A global claim
closes the obvious hole — you can't run `/work` once per server. It leaves a
subtler one: since the claim is shared, the **shortest** length among a member's
servers is the one that actually governs them, and the coins it pays spend
everywhere. One server setting `/work` to 60s would have been a coin printer for
its members in every other server, and a floor only bounds how bad that gets —
it never makes the setting *mean* anything, because the number a member actually
runs on still depends on which servers they happen to be in.

So lengths are bot-wide and owner-only: they live in `bot_settings`
(`utils/db/settings.py`), are set with `!cooldown` in the owner-only admin cog,
and `cogs/activities/helpers.effective_cooldown` turns a stored value (or its
absence) into a length, falling back to the activity's default rather than to
"no cooldown". Migration 4 dropped the old `activities_config.*_cooldown`
columns. `/fish` reached the same place from the other direction: its cast
cooldown is a fixed 20s constant (migration 2 dropped its column). A guild still
owns whether an activity runs there, which affects only its own members.

### Every faucet needs a rate limit, not just a price

Two holes stayed open after the amounts moved, and both were the same mistake:
a faucet whose *amount* was governed but whose *rate* was not.

`/squad` and `/raid` paid every participant with no per-user claim at all —
only the invoker carried a few seconds of command cooldown, so two members
could confirm a squad every half-minute, forever, with no risk. They now take
the same atomic `try_claim_activity` claim an activity does (defaults 30m and
1h, bot-wide via `!cooldown coop|raid`, chosen against the same "≤~150
coins/hour" guardrail). A member on cooldown is skipped and named rather than
blocking the board, so one person's timing can't cost the party.

`/fish event` let a Manage-Server mod start a x2-catch-value frenzy for three
hours, back to back, indefinitely — an income *multiplier* on a global wallet,
which is a faucet knob however it is framed. It is bot-owner-only now. The
random ~0.4%-per-cast events are untouched and still fire for everyone; mods
keep `/fish toggle`.

The rule that falls out: when adding a coin faucet, decide **who sets the
amount** and **what bounds the rate**. A reward with no claim behind it is
unbounded no matter how small the number is.

### Faucets are bot-wide; sinks stay with the guild

The same argument settles every remaining economy setting, and the dividing line
is simply **which direction coins move**.

A **faucet** creates coins. `/daily` and its streak bonus, `/squad`, `/raid`,
and the level-up payout all mint into the one global wallet, so a per-guild
amount had the identical defect as a per-guild cooldown: the most generous
server set the rate for its members *everywhere*, and there is no floor or
ceiling that makes such a number mean anything. Migration 5 moved all five into
`bot_settings`, behind the owner-only `!econ`.

A **sink** destroys coins. A shop purchase takes coins out of circulation and
hands back that guild's own Discord role or a mod-fulfilled perk — something
that exists only inside that server. Pricing it badly is self-correcting and
strictly local: too cheap and its own rewards are easy, too dear and its own
members don't buy. Nothing leaks outward, so **shop prices stay per-guild** and
always will. The casino is the same shape (a house edge plus a jackpot pool fed
by that guild's own losses), which is why its limits stay per-guild too.

The practical objection to this split is that a mod pricing a shop item has to
balance it against income they no longer set. That is backwards: before the
move there was no single income rate to balance against, because a member's real
earning rate depended on which servers they happened to be in. Now there is one,
so the bot can state it. `cogs/economy/helpers.seconds_to_afford` converts a
price into a time to earn and `/shop list|add` shows it ("~25m to earn"), which
is the number a mod was actually reaching for. The denominator is fishing: at
the 20s cast cooldown it pays ~5,000 coins/hour against `/work`'s 100 and
`/daily`'s ~4, so any figure that blended the faucets would be wrong by an order
of magnitude. It is a floor — nobody fishes continuously — and the UI says so.
`tests/test_economy_balance.py` recomputes the same quantity from the raw drop
tables and asserts the two agree, so the shop's estimate cannot drift away from
the balance model.

## 3. Migration (`utils/db/globalize.py`, migration 1)

Runs once on startup after the table setup, inside the existing
`@db.migration(N)` framework (`PRAGMA user_version`), and is safe to re-run:
each table is skipped if it no longer has a `guild_id` column, so a fresh
database and a retried migration are both no-ops.

Merge rules — chosen so **nobody loses value and nothing can be paid twice**:

| Kind of column | Rule | Rationale |
|---|---|---|
| Earned totals (coins, contribution, casts, catches, XP, games, wagered, won, shift/dig counts, item quantities, dex counts, effect charges) | **SUM** | Every one was legitimately earned. Summing is the only rule that never destroys progress. |
| Permanent unlocks + records (rod tier, pickaxe tier, prestige rank, heaviest catch, biggest win, best streak, effect magnitude) | **MAX** | Keep the best thing you ever earned. |
| Cooldown stamps (`last_daily`, `last_cast`, `last_work`, `last_mine`, …) | **MAX** (most recent) | Taking the earliest would grant a free extra claim per server. |
| One-time claim flags (quest/challenge/objective `claimed`, achievements) | **claimed anywhere → claimed** | The reward was already paid; the merge must not re-open it. |
| `achievements_earned.earned_at` | **MIN** | The badge's true first-earned time. |
| `weekly_objectives.baseline` | **MIN** | Keeps the member's best progress this period. |
| `progression.selected_title` | title from the highest-prestige row | Matches the surviving rank. |

**Why SUM for coins.** A member with 1,000 coins in five servers ends with
5,000 in one wallet. Their *total holdings* are unchanged — only the walls
between them are gone. The alternatives both fail the brief: MAX silently
deletes progress the member earned, and averaging invents a number nobody
had. Prices/rewards are unchanged by the merge, so relative purchasing power
across the player base moves together, not per-person.

### Edge cases and how they're handled

- **Same user in many guilds** — the whole point; see the rules above. Covered
  by `tests/test_globalize_migration.py`.
- **In-flight weekly objective.** Baselines were snapshotted against a
  *per-guild* stat; after merging, the lifetime stat jumps, so an unclaimed
  objective can complete immediately. One-off, for the current period only, and
  it can't double-pay because `claimed` carried over.
- **In-flight daily quest / casino challenge.** Same shape: progress takes the
  furthest-along row, and a claimed row always wins the merge.
- **A cooldown appears to "reset forward".** A member mid-cooldown in server A
  and idle in server B ends up on the later of the two stamps — never an extra
  free claim.
- **`best_key` vs `MAX(best_weight)`.** SQLite only guarantees bare columns
  come from the extreme row when a query has exactly one min/max aggregate;
  `fishing_stats` has several, so the species name is fetched by an explicit
  correlated subquery.
- **Crash mid-migration.** Each table swap runs inside the migration's
  transaction and `user_version` only advances on success, so a failure rolls
  back to the original tables and retries next start.
- **Encrypted databases** (SQLCipher) are unaffected — the migration is plain
  SQL over the same connection.

There is no automatic rollback: the merge is lossy in the sense that the old
per-guild split can't be reconstructed. Back up `data/nanobot.db` before the
first start on this version.

## 4. Leaderboards

Every economy board offers **both** views through a shared `scope` option
(`utils/helpers.SCOPE_CHOICES`), defaulting to this server:

| Command | Server view | Global view |
|---|---|---|
| `/coin top` | this guild's members, ranked by their global balance | every wallet |
| `/coin contrib` | this guild's members | every contributor |
| `/fish top` | this guild's anglers | every angler |
| `/casino top` | this guild's players | every player |
| `/fish global <stat>` | — | already global, per-stat registry |

Server views are the *same global rows*, filtered to the guild's member ids
(`db.*_leaderboard_for(user_ids)` → `_core.rows_for_users`, which chunks the
`IN` clause at 900 ids so guild size can't hit SQLite's bound-parameter cap)
and then ranked/paged in Python by `helpers.page_rows`. Personal ranks
(`/balance`, `/fish stats`, `/casino stats`) are global and labelled as such.

## 5. Governance consequences (read before deploying)

Making wallets global changes who can affect what:

- **`/coin reset` is now bot-owner only.** A guild admin wiping "their" economy
  would be deleting coins the member earned everywhere else, irreversibly.
- **`/coin grant` / `/coin take` stay Manage Server** but now move coins in a
  member's global wallet; the reply says so. Only add the bot to servers whose
  staff you'd trust with that. A future refinement could cap per-guild minting.
- **Daily/reward arbitrage.** The claim is global but the *amount* comes from
  the server it's claimed in, so a member can claim wherever the reward is
  richest. Same for co-op/raid rewards. That's the accepted price of keeping
  per-guild tuning; a bot-wide cap would be the lever if it's abused.
- **`/rob` moves coins in the target's global wallet.** It's still gated by the
  per-guild toggle, min balances, cooldown, and the padlock shield.
- **Cooldown farming is fixed.** `/daily`, `/work`, `/mine`, `/fish`, quests
  and challenges were previously claimable once *per server*; they're now once
  per user, closing an unintended faucet.

## 6. Follow-ups this unlocks

- A `/profile` card that shows the whole account (wallet, fishing, casino,
  activities, prestige) — now a single-user query.
- Cross-server events and seasons (the `week` column is already a generic
  period key; `economy_events` already has a global sentinel).
- Trading/gifting between users who share no server.
- DM support: economy commands are still `guild_only()` because they read the
  *guild's* settings (currency name, reward amounts). A DM path would need a
  bot-wide default config — a small change now that the data no longer needs a
  guild.
- Per-guild *cosmetic* leaderboards on top of global data (already the case)
  without any extra storage.
