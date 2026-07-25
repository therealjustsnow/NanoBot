# Identity, cosmetics, and the two level systems

The profile card is the front door to a member's account. This is how it's put
together, and — the part that matters most — why there are two independent
level systems.

## Two levels, on purpose

| | Server level | Global level |
|---|---|---|
| Lives in | `cogs/leveling.py` (unchanged) | `utils/globalxp.py` + `cogs/identity/` |
| Scope | one guild (`user_levels`) | the account (`global_levels`) |
| Who tunes it | server admins: XP rate, cooldown, ignored channels, role rewards, announcements, resets | **nobody** — hard-coded |
| Earned from | messages in that server | normalized actions anywhere |
| Answers | "how active are you *in this community*?" | "how much have you played the bot, ever?" |

Neither feeds the other. That's the design constraint everything else follows
from:

* A server running 5× XP grants **no** extra global XP.
* A server with leveling switched off still earns its members global XP.
* Global XP is never copied or scaled from server XP — it's awarded per action,
  at a flat rate, from the `XP_AWARDS` table.

Both numbers appear together on `/profile` and on `/rank`, labelled, so nobody
has to guess which "level" a number refers to.

### The global curve

Cumulative XP to reach level *L* is `25·L² + 75·L` — level 10 = 3,250,
level 50 = 66,250, level 87 = 195,750. It's a plain quadratic so early levels
come fast and late ones are a real grind. `level_from_xp` is its exact inverse,
so the two never disagree.

### What earns global XP

`utils/globalxp.XP_AWARDS` is the whole list. Adding a source is one entry there
plus one `await globalxp.award(user_id, "action")` at the site that performs it.

| Action | XP | Awarded at |
|---|---|---|
| `message` | 8 | `cogs/identity` `on_message` (60s per-user cooldown) |
| `daily` | 40 | `/daily` |
| `fish_cast` | 5 | a cast that lands |
| `fish_sell` | 2 | selling a haul |
| `casino_game` | 4 | every settled casino game |
| `activity` | 6 | `/work`, `/mine`, `/hunt`, `/explore`, `/rob` |
| `craft` | 5 | `/craft make` |
| `shop` | 5 | redeeming a shop reward |
| `coop` | 15 | a confirmed `/squad` or finished `/raid` |
| `quest` | 25 | a completed daily quest / casino challenge |
| `achievement` | 50 | earning an achievement |

The message cooldown is per *account*, so chatting in five servers at once
still earns one message's worth.

## Level-up announcements

An award can fire from anywhere — a chat listener, a `/fish` cast, someone
*else's* `/raid` payout — and most of those places aren't somewhere a message
can be sent. So `globalxp.award()` doesn't announce anything; it records the
level in `global_levels.pending_level` (MAX-merged, so levelling twice before
delivery names the level they're actually on) and the identity cog delivers it
the next time it sees the member:

1. **The channel they just used** — `on_message` (they talked) or
   `on_command_completion` (they ran a command), whichever comes first.
2. **Their DMs**, if that channel send fails (no permission, deleted channel).
3. **Nowhere yet** — the level goes back into `pending_level` and is retried
   the next time they turn up, in any server.

The claim is a conditional UPDATE taken *before* sending and handed back if
both sends fail, so an announcement can't double-post and can't be silently
lost. The common path costs one primary-key SELECT per command.

The message names any cosmetic that unlocks at exactly that level, which is
what makes the milestones feel like rewards rather than a number going up.

## Cosmetics

Definitions live in `utils/cosmetics.py`; the database stores keys only
(`cosmetic_unlocks`, `cosmetic_equipped`) — the same split as items and their
catalogue.

**Slots are data.** `SLOTS` maps a slot to how many can be worn at once:

| Slot | Max worn | Drawn as |
|---|---|---|
| `banner` | 1 | the card background |
| `border` | 1 | the frame |
| `nameplate` | 1 | the plate behind the name |
| `badge` | 6 | the showcase row along the bottom |

Adding a slot is one entry. `/profile equip` infers the slot from whatever
you're equipping, so it needs no changes — and because its picker walks
`SLOTS`/`COSMETICS` rather than a hard-coded list, a new slot shows up there
too; the card draws any slot it knows how
to draw and ignores the rest, which keeps a half-built slot from breaking
rendering.

**Unlock rules are data.** Each definition carries an `unlock` dict:

```python
{"kind": "default"}                                  # everyone has it
{"kind": "global_level", "value": 25}
{"kind": "prestige", "value": 3}
{"kind": "achievement", "key": "fish_caught_100"}
{"kind": "stat", "stat": "casino_games", "value": 1000}
{"kind": "manual"}                                   # staff/event grant only
```

`cosmetics.is_unlocked(def, ctx)` evaluates all of them against one context
dict built per profile view, so checking the whole catalogue costs no extra
queries. Earnable cosmetics unlock **lazily** when a member opens their own
card (the achievement pattern); `manual` ones only ever arrive through
`/profile grant`, which is what makes event drops feel like awards.

**Adding cosmetics without touching code.** `data/cosmetics.json` is merged at
cog load:

```json
[{"key": "badge_summer_2026", "name": "Summer 2026", "slot": "badge",
  "glyph": "☀", "palette": ["#F9C80E", "#EA3546"],
  "unlock": {"kind": "manual"}, "rarity": "event",
  "description": "Played during the summer event."}]
```

Bad entries are logged and skipped rather than taking the bot down.

## Artwork

The bot ships **no binary art**. `utils/profile_card.py` generates everything
from each definition's palette and glyph — gradient banners with soft geometry,
rounded "gem" badges with an inner highlight, and prestige emblems whose metal
*and* star-point count change with rank — then caches the result under
`data/profile_cache/`.

To use real artwork later, drop a PNG at
`assets/profile/<slot>/<key>.png`. The renderer prefers it automatically; no
code change, no cache bust needed (the asset path is checked first).

Font choice walks a candidate list (DejaVu → Liberation → system → Pillow's
built-in), so text renders on a bare container. Badge glyphs must exist in that
font — `tests/test_cosmetics.py` fails if one would render as a tofu box, which
is why the badges use geometric symbols rather than colour emoji.

## The card

`render_card(data)` takes a plain dict and returns PNG bytes. It knows nothing
about Discord or the database, which is what makes it testable headless and
reusable for future cards (a server card, a leaderboard card). Layout constants
live at the top of the module, so moving a block is a number change.

A render is ~200 ms of CPU, so the cog defers first and renders in a worker
thread behind a small semaphore. Anything that can fail on its own —the avatar
download, an unreadable asset, a missing font — degrades instead of erroring: a
missing avatar becomes an initial tile, a broken asset falls back to generated
art.

## Extending it

| Want to add | Do this |
|---|---|
| A badge | One `CosmeticDef` (or one JSON entry) |
| A banner/border/nameplate | Same, with `slot=` set |
| A new cosmetic *slot* | One `SlotDef` in `SLOTS` (+ draw it in the card if it's visual) |
| A new unlock condition | One branch in `is_unlocked` + one in `describe_unlock` |
| A new global-XP source | One `XP_AWARDS` entry + one `await globalxp.award(...)` |
| A one-off event drop to a whole server | `/profile grantall <cosmetic> [guild_id]` (bot owner) |
| Real artwork | Drop PNGs into `assets/profile/<slot>/` |
| Animated cosmetics | Store a GIF asset and branch in `cosmetic_image` — the def/slot layer doesn't change |
| A profile theme | A `theme` slot whose palette overrides the card's ink colours |
