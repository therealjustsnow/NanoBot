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

1. **The channel the server nominated** (see below), starting from the channel
   they just used — `on_message` (they talked) or `on_command_completion`
   (they ran a command), whichever comes first.
2. **Their DMs**, if that channel send fails (no permission, deleted channel)
   or the server doesn't want level-ups posted there.
3. **Nowhere yet** — the level goes back into `pending_level` and is retried
   the next time they turn up, in any server.

The claim is a conditional UPDATE taken *before* sending and handed back if
both sends fail, so an announcement can't double-post and can't be silently
lost. The common path costs one primary-key SELECT per command.

### Which channel — the one setting a server owns

The level is account-wide, but a *channel* belongs to one server, so where the
message lands is the server's decision, not the account's. It's the exception
to "nobody tunes the global level", and it isn't really a tuning knob: it
changes nothing about what a global level is or how fast it's earned, only
which of a server's own channels a stray "level up!" is allowed to appear in.
(The case that prompted it: a level-up going off in a venting channel right
after someone vented.)

It lives in `level_config`, next to the server-level announcement settings —
`/level globalannounce`, a subcommand of the existing group, so it costs no
top-level slash slot — and `cogs/identity/helpers.announce_channel_id()`
resolves it, in order:

| Setting | Result |
|---|---|
| `/level globalannounce off` | nothing posted in that server; the member still gets a DM |
| `/level globalannounce #channel` | always that channel |
| neither, but `/level announce #channel` is set | that channel — a server that already routed level-ups doesn't say it twice |
| neither | wherever they were talking… |
| …unless that channel is in `/level ignore` | skipped → DM |

Only the *channel* is inherited from `/level announce`, never its on/off
switch: a server that stopped announcing its own levels hasn't said anything
about the global one, and `global_announce` defaults on so nothing changes for
a server that never touches it. A configured channel that's since been deleted
falls through to the DM rather than back to the member's current channel —
"not in here" has to survive the channel going away.

The message names any cosmetic that unlocks at exactly that level, which is
what makes the milestones feel like rewards rather than a number going up.

## Cosmetics

Definitions live in `utils/cosmetics.py`; the database stores keys only
(`cosmetic_unlocks`, `cosmetic_equipped`) — the same split as items and their
catalogue.

**Slots are data.** `SLOTS` maps a slot to how many can be worn at once:

| Slot | Card | Max worn | Drawn as |
|---|---|---|---|
| `banner` | profile | 1 | the card background |
| `border` | profile | 1 | the frame |
| `nameplate` | profile | 1 | the plate behind the name |
| `badge` | profile | 6 | the showcase row along the bottom |
| `wallet` | wallet | 1 | the `/balance` card background |
| `coin` | wallet | 1 | the coin beside the balance |

Each slot also declares a `category` — which card it dresses. That is what the
shop sorts its aisles by (`/shop profile`, `/shop wallet`), so a third card
later is a new category string rather than a new command.

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
{"kind": "purchase"}                                 # bought with coins (+ price=)
{"kind": "manual"}                                   # staff/event grant only
```

`cosmetics.is_unlocked(def, ctx)` evaluates all of them against one context
dict built per profile view, so checking the whole catalogue costs no extra
queries. Earnable cosmetics unlock **lazily** when a member opens their own
card (the achievement pattern) — and `/balance` does the same for the two
wallet slots, so a member who never opens `/profile` still collects them;
`manual` ones only ever arrive through `n!profile grant` (bot-owner,
prefix-only), which is what makes event drops feel like awards.

`purchase` is deliberately in the same "never earned" bucket as `manual`:
`is_unlocked` returns False for it and `auto_unlockable()` filters it out, so
the only way to hold one is `/shop unlock`. That is what makes the cosmetic
shop a real coin sink instead of a preview of things you would have got anyway,
and `tests/test_cosmetic_shop.py` asserts it from both ends.

**Prices are bot-wide, in code.** A cosmetic is worn on a *global* account, so
a per-guild price would mean the cheapest server on the bot set what everyone
paid — the same reasoning that made the coin faucets owner-only, and the mirror
of why a guild's own shop (roles, mod-fulfilled perks) stays per-guild. See
`docs/global-economy.md`.

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
from each definition's palette and glyph — gradient banners, rounded "gem"
badges with an inner highlight, struck coins with a milled edge, and prestige
emblems whose metal *and* star-point count change with rank — then caches the
result under `data/profile_cache/`.

Three registries stop the catalogue looking like twenty recolours of one
gradient, and they **compose**, which is where the variety actually comes from:

| Knob | Registry | What it decides | Values |
|---|---|---|---|
| `texture` | `_TEXTURES` | what the surface is made of | `clouds`, `nebula`, `silk`, `frost`, `embers`, `mesh`, `flat` |
| `pattern` | `_BANNER_PATTERNS` | the geometry drawn on it | `waves`, `rays`, `bokeh`, `grid`, `stars`, `hex`, `circuit`, `peaks`, `aurora`, `nebula` |
| `style` | `_BORDER_STYLES` | the frame | `solid`, `double`, `glow`, `dashed`, `corners`, `ribbon` |

An empty value is the original flat look, so nothing had to be restyled at
once. Silk + waves is water; clouds + stars is a night sky; mesh + circuit is a
neon ledger.

### Bundled artwork (the Gallery tier)

Generated art is the baseline; **36** cosmetics are real images instead —
`banner_art_*` (paintings, prints, manuscripts and textiles), `banner_space_*`
(telescope and mission imagery) and the `wallet_*` equivalents. They live in
`assets/profile/<slot>/<key>.webp` and win over generation through the same
asset-override path that has always existed.

The set is chosen for breadth rather than one canon: ukiyo-e (Hokusai,
Hiroshige), a Song-dynasty scroll, a Persian Shahnameh folio, an Egyptian
papyrus, a Mesoamerican codex, a Korean chaekgeori screen, Itō Jakuchū, Art
Nouveau (Mucha), a William Morris textile, Haeckel's and Audubon's scientific
plates, a 17th-century celestial atlas, European oils (van Gogh, Klimt, Turner,
Monet, Friedrich, Aivazovsky, Bierstadt), Kandinsky's abstraction, and imagery
from Hubble, Webb, Cassini, Juno, Curiosity and Apollo.

Everything bundled is **public domain**: paintings whose copyright expired
(via Wikimedia Commons) and NASA imagery, which as a U.S. federal work is not
subject to copyright. `scripts/fetch_cosmetic_art.py` reads
`assets/profile/art_manifest.json`, resolves each entry against the source API,
**re-checks the reported licence and refuses anything that is not public domain
or CC0**, crops it to the card's aspect, and regenerates
`assets/profile/CREDITS.md`. Adding a painting is one manifest entry plus one
`CosmeticDef`; `tests/test_cosmetic_art.py` fails if either half is missing, if
a bundled image has no credit, or if one is bundled under a licence the repo
may not redistribute.

Each art cosmetic keeps a palette sampled from its own image. That is not
decoration: the palette drives the card's accent colour (chip bars, the rep
pill), and if the file is ever missing the cosmetic degrades to generated art in
its own colours rather than breaking.

Ten can only be earned, each hung off whichever activity it suits — *The Ninth
Wave* for 2,000 fish caught, Haeckel's *Discomedusae* for a complete dex,
Audubon's flamingo for 300 hunts, Bierstadt's *Yosemite Valley* for 800 digs,
Wang Ximeng's scroll for 400 expeditions, Cellarius at global level 60,
*Pillars of Creation* at 75, Andromeda at 90, Cassini's Saturn at prestige 6,
*Earthrise* at prestige 4 — so the best-looking things on the card are not
purely a coin question.

A real painting needs treatment a generated gradient doesn't, and the renderer
applies it on the asset path only: **cover-crop** to the card's aspect (a plain
resize stretched every painting — that was a real bug), dim to `ART_CEILING`,
and vignette. The card then adds a heavier scrim and puts the chip grid and
badge row on their own soft panel, so the painting stays bright in the top half
instead of being dimmed into mud to make dense stats readable.

**It is all still Pillow** — no numpy, no native noise extension, no bundled
artwork. Stacking small random lattices through BICUBIC upscales *is* value
noise (the resize does the interpolation), and summing octaves of it gives
fractal Brownian motion — clouds, marble, terrain. `ImageOps.colorize` maps
that grayscale field through the palette (three stops if the def gives a third
colour), screen-blended radial gradients make mesh gradients, and a blur plus a
screen blend makes bloom. A card-sized banner generates in ~110 ms and is then
cached, so the cost is paid once per cosmetic per size.

Two automatic passes keep the art usable rather than merely pretty. `_tame`
measures the render's mean luminance and scales it back only if it is too
bright — a banner is a *background*, and gold or ice palettes plus bloom can
otherwise drown the white text; because it measures rather than being
hand-tuned per palette, it also covers cosmetics added later from
`data/cosmetics.json`. A small saturation push then undoes the greying that
taming and blooming cause. Randomness is seeded on the cosmetic key, so art is
deterministic and the on-disk cache never goes stale.

**Output format.** A textured card is photographic, and PNG is the wrong
container for it: ~430 KB and 2.5 s with `optimize=True`, versus ~50 KB of WebP
in a fraction of the time with no visible difference. The cards encode through
`profile_card.encode()` and the cogs name the attachment with
`profile_card.IMAGE_EXT`, so the format is one constant to change. The on-disk
art cache stays lossless PNG — it is composited into the next render rather
than displayed, so it must not accumulate compression artefacts.

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
reusable for future cards — `utils/cookie_card.py` and `utils/wallet_card.py`
are the two that took it up on that. Layout constants live at the top of each
module, so moving a block is a number change.

`utils/wallet_card.py` is the `/balance` card: one balance, three tallies
(rank, contribution, daily streak), a wallet banner and a coin style, and
deliberately **no border** — a frame on a card that small crowds the number,
and `/profile` is where a whole loadout is meant to be shown off.

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
| A shop cosmetic | A `CosmeticDef` with `unlock={"kind": "purchase"}` and a `price` |
| A new banner surface | One function + one `_TEXTURES` entry, then `texture="…"` on the defs |
| A new banner look | One function + one `_BANNER_PATTERNS` entry, then `pattern="…"` on the defs |
| A new border look | One function + one `_BORDER_STYLES` entry, then `style="…"` on the defs |
| A new unlock condition | One branch in `is_unlocked` + one in `describe_unlock` |
| A new global-XP source | One `XP_AWARDS` entry + one `await globalxp.award(...)` |
| A one-off event drop to a whole server | `n!profile grantall <cosmetic> [guild_id]` (bot owner, prefix-only) |
| Real artwork | Drop an image into `assets/profile/<slot>/` (webp/png/jpg), or add a manifest entry and run `scripts/fetch_cosmetic_art.py` |
| Animated cosmetics | Store a GIF asset and branch in `cosmetic_image` — the def/slot layer doesn't change |
| A profile theme | A `theme` slot whose palette overrides the card's ink colours |
