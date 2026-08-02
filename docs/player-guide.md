# NanoBot Player Guide

Everything you can do with NanoCoins — fishing, jobs, the casino, crafting,
achievements — in one place. No prior knowledge needed. If you only read one
section, read the next one.

> **Slash or prefix, your choice.** Every command works as `/fish` or as
> `n!fish` (the `n!` prefix can be changed per server). On mobile, use the
> slash commands: most options now pop up a list you tap instead of typing.
>
> One quirk worth knowing: where a command has subcommands, Discord's slash
> picker can't run the bare name — `/fish` is a folder there, and casting is
> `/fish cast`. So this guide names features by their short name (`/fish`,
> `/shop`, `/progress`) but the tables and step-by-steps give you the exact
> thing to tap. Typing `n!fish` on the prefix side always works.

---

## 📌 The big change: your account is global

**Your economy belongs to you, not to a server.**

Coins, items, your fishing rod, your pickaxe, your achievements, your prestige
rank — all of it follows you into every server that has NanoBot. Fish for an
hour in one community, then walk into another and buy something with what you
earned. Nothing resets, nothing is left behind.

**What that means in practice**

| | |
|---|---|
| 💰 One wallet | `/balance` shows the same number everywhere. |
| 🎒 One inventory | Ore mined in one server crafts in another. |
| 🎣 One angler | Your rod, level, bag and dex are the same everywhere. |
| 🏅 One collection | An achievement is earned once, forever. |
| ⏳ One set of cooldowns | `/daily` is once a day **total**, not once per server. Same for `/work`, `/fish`, quests and challenges. |

**What each server still controls**

- Whether a feature is switched on at all (a server can disable fishing, the
  casino, `/rob`, and so on).
- Its own currency **name and emoji** — the same coins might be "NanoCoins"
  here and "Doubloons" there. It's still one balance.
- Which activities are switched on, and its raid party size.
- Its **shop** (server roles and custom rewards) and its **casino jackpot**.
- Its leaderboard view — see [Leaderboards](#-leaderboards).

**What no single server controls: how many coins exist.** Reward *amounts* —
`/daily`, its streak bonus, `/squad`, `/raid`, level-up coins — are set once for
the whole bot by its owner. They have to be: they pay into a wallet you spend in
every server, so one server raising its daily would be handing its members coins
usable everywhere else. Prices are the opposite — a shop purchase *removes*
coins in exchange for that server's own role or perk, so what it costs can only
ever affect that server, and its mods set it.

**Did I lose anything in the switch?** No. If you had balances in several
servers, they were added together into one wallet, your best rod/pickaxe/rank
was kept, and anything you'd already claimed stayed claimed.

---

## 🚀 Start here (your first five minutes)

1. `/daily` — free coins, once every 24 hours. **The amount is a roll** — most days are ordinary, some are great, and roughly one in a hundred is a jackpot. Claiming on consecutive days adds a streak bonus on top.
2. `/fish cast` — cast a line. You can cast again every **20 seconds**.
3. `/fish sell` — turn your catch into coins (pick "Everything in your bag").
4. `/adventure dashboard` — see everything that's waiting and hit **Collect all**.
5. `/profile card` — see everything you've built so far.

Then come back later today, or tomorrow. Everything banks up for twelve hours,
so there is nothing to keep on top of.

---

## 🖥️ The website

Some servers run NanoBot's web dashboard. If yours does, the link is in the
server (ask a mod) — sign in with Discord and everything below is playable in a
browser: casting, the bag, the map, mining and adventuring, your inventory, the
shop, the leaderboards and your profile card.

**It's the same account and the same game.** Not a copy of it — literally the
same cooldowns, the same wallet, the same claims. Cast on the website and your
`/fish` cooldown in Discord is running. Collect your banked runs there and
they're collected everywhere. There is no way to get two of anything by using
both, and nothing you do in one place is worth less than doing it in the other.

A couple of things stay in Discord on purpose: equipping cosmetics
(`/profile equip`, which can dress a whole card in one go) and the co-op boards
(`/squad`, `/raid`), which need other people pressing buttons in a channel.

---

## 📇 Your profile card

`/profile` draws your whole account as an **image you can share** — avatar,
name, your equipped cosmetics, and everything you've built:

| On the card | What it shows |
|---|---|
| 🌐 Global level | Your account level + XP bar (see below) |
| 🏠 Server level | This server's level + XP bar |
| ⭐ Prestige emblem | A rank pin on your avatar — the metal and star change as you climb |
| 🪙 Coins | Your global balance |
| 🏅 Achievements | How many you've earned, and your points |
| 🎣 Fishing | Level and current rod |
| 🎰 Casino | Net winnings and games played |
| 💼 Work & Mining | Career title and pickaxe tier |
| 🎒 Items | How many you're carrying |
| 🎖️ Badges | Up to six badges you've equipped |

Your **banner**, **border**, **nameplate** and **badges** are all yours to
pick — see [Cosmetics](#-badges-banners-and-cosmetics).

Want more detail on one area? `/fish stats`, `/casino stats`,
`/mine stats`, `/progress`.

---

## 🌐 Two levels: global and server

There are two separate level systems, and they never interfere with each other.

**Global level** — your *account*. It goes up when you do almost anything with
the bot: chatting, fishing, casino games, activities, claiming `/daily`,
finishing quests, earning achievements. Every action is worth the same fixed
amount in every server: nobody can configure it, speed it up, or slow it down.
Level 10 is a few evenings; level 87 is a long-running account.

**Server level** — *this community's* XP, exactly as before. Server admins set
the XP rate, the cooldown, role rewards, and announcements, and they can turn
it off entirely. It measures how active you are here.

```
Global Level: 87        ← the same in every server
Server Level: 24        ← this server only
```

Both are on your profile card, and `/rank` shows them side by side.

**You'll be told when you level up.** The bot posts it in the channel you were
using; if it can't talk there it DMs you, and if that's closed too it keeps the
message and tells you the next time you use a command anywhere. You never miss
one, and you never get it twice.

Servers can point global level-ups at one channel (or switch them off there —
you'd still get the DM), so don't be surprised if yours turns up in the
server's level-up channel instead of where you were typing. Channels a server
excluded from XP never get one.

A server running 5× XP doesn't earn you global levels any faster, and a server
with leveling switched off doesn't stop your global progress.

---

## 🎖️ Badges, banners, and cosmetics

Both of your cards are customisable — the profile card and the wallet card
(`/balance`) have their own slots:

| Slot | Card | What it changes | How many |
|---|---|---|---|
| **Banner** | profile | The artwork behind your card | 1 |
| **Border** | profile | The frame around it | 1 |
| **Nameplate** | profile | The plate behind your name | 1 |
| **Badges** | profile | The row along the bottom | up to 6 |
| **Wallet banner** | wallet | The artwork behind your balance | 1 |
| **Coin style** | wallet | The coin next to your balance | 1 |

| Command | What it does |
|---|---|
| `/profile cosmetics [slot]` | Everything you own, and how to unlock the rest |
| `/profile preview <name>` | See it on **your** card before you buy — nothing is equipped or charged |
| `/profile equip [name]` | Wear it (the slot is worked out for you). Name several, separated by commas — or run it bare and tap a few from the menu |
| `/profile unequip [name]` | Take it off — several at once, a slot name to clear that slot, or bare to pick from what's on |
| `/profile badges [member]` | The badge gallery, yours or someone else's |

On slash commands you never type a cosmetic name: `equip` and `unequip` pop up
a list you tap. `equip` shows what you can wear now first, then what's already
on, then the locked ones with what unlocks them — so the list is also the
answer to "how do I get that one?".

**How you unlock them**

- **Global level** — Steel Frame at 10, Frosted Glass at 15, Ember at 25,
  Gilded Frame at 40, Aurora + the Veteran badge at 50, Neon Strip at 60,
  Ascended at 100.
- **Prestige** — Royal Velvet at prestige 1, the Prestige Frame at 3, plus
  Prestige I/V/X badges.
- **Coins** — a whole aisle of banners, borders, nameplates, badges, wallet
  banners and coin styles is simply for sale: see `/shop profile` and
  `/shop wallet` below. Bought ones can't be earned any other way, and earned
  ones can't be bought.

Most banners are drawn fresh by the bot — clouds, marble, mesh gradients, frost
and molten metal, each in its own palette — so no two look like the same
recolour. The **Gallery** banners are real artwork instead — 36 of them, from all over:
*The Starry Night*, *The Great Wave off Kanagawa*, a Persian Shahnameh folio, an
Egyptian Book of the Dead, a Korean chaekgeori screen, a William Morris textile,
Mucha, Kandinsky, Audubon's flamingo, a 1660 star chart, and photographs from
Hubble, Webb, Cassini, Curiosity and Apollo 8. All of it is public domain, and
every artist is credited in `assets/profile/CREDITS.md`.

Ten of them can't be bought at any price, and each is tied to what you actually
do: *The Ninth Wave* for anglers (2,000 fish), Haeckel's jellyfish plate for a
complete dex, Audubon's flamingo for 300 hunts, Bierstadt's *Yosemite Valley*
for 800 digs, a Song-dynasty scroll for 400 expeditions, and the deep-sky ones
for global levels 60/75/90 and prestige 4/6.
- **Playing** — Deep Current (250 fish), Fishing Master (1,000 fish), Casino
  Champion (1,000 games), Tycoon (3,000,000 coins), Grinder (500 shifts).
- **Staff & events** — Developer, Contributor, Early Supporter, Beta Tester,
  Event Winner and seasonal drops are handed out, not earned by grinding.

Anything you've earned unlocks automatically the next time you open your own
card — you'll see a "🎁 Unlocked" line above it.

**Prestige looks the part.** Instead of a number, prestige shows as an emblem
pinned to your avatar: bronze, then silver, gold, amethyst and radiant, with an
extra star point at each tier.

---

## 🪙 Coins

| Command | What it does |
|---|---|
| `/balance [member]` | Your **wallet card**: balance, global rank, contribution, daily streak |
| `/daily` | Claim your daily coins (24h). Consecutive days add a streak bonus |
| `/pay <member> <amount>` | Send coins to someone |
| `/coin gamble <amount>` | Double-or-nothing, ~45% to win |
| `/coin top [page] [scope]` | Richest members — this server, or everywhere |
| `/coin contrib [page] [scope]` | Top co-op contributors |

**Daily streak.** Claim on consecutive days and each day adds a bonus on top
of the base amount (your server sets both). Miss a day and the streak resets.
Because the claim is global, claiming in *any* server counts as your claim for
the day.

**Where coins come from:** `/daily`, fishing, `/work`, `/mine`, `/adventure
hunt`, `/adventure explore`, `/rob`, the casino, co-op (`/squad`, `/raid`),
selling items, achievements, and level-ups (if your server enables that).

**Where they go:** rods, pickaxes, bait, the shop, `/pay`, the casino,
prestige. Spending is what keeps prices meaningful.

**If a price ever drops:** you get the difference back. When we rebalance
something you already own — a pickaxe tier, a rod, a fishing charter — the coins
are put straight into your wallet and you'll get a DM saying what changed and
how much came back. You never need to ask, and prices going *up* never costs you
anything you've already bought.

---

## 🎣 Fishing

The biggest earner in the game, and the most hands-on.

```
/fish              cast your line          (every 20 seconds)
/fish hub          your dashboard — and the buttons for everything below
/fish bag          what you're carrying
/fish sell [fish]  sell one species, or everything
/fish travel       the map: charter and move between fishing spots
/fish shop         bait and tackle — and the ⚡ menu that arms what you own
/fish trap         set a fish trap, or pull one that's ready
/fish rod          your rod + the next upgrade
/fish upgrade      buy the next rod
/fish buy <item>   buy bait or tackle directly
/fish bait         what bait you own and what's armed
/fish quest        today's quest
/fish events       what's boosting the water right now
/fish dex          your species collection
/fish stats        casts, catches, earnings, level, best catch
/fish top / global leaderboards
```

### You don't have to type any of that

Every cast comes back with buttons:

**🎣 Cast · 💰 Sell all · 🎒 Bag · 🗺️ Travel · 🛒 Shop · 🪤 Trap**

Tap 🎣 and the same message becomes your next catch. A whole session is one
message and your thumb. `/fish hub` opens the same buttons over a dashboard
showing your spot, rod, level, bag, streak and today's quest.

Bare `/fish` still casts, so nothing you already do has changed. If you press
Cast too early you're told privately — your last catch stays on screen.

### What's new

- **Fishing spots.** Five places to fish, each with species found nowhere else.
  See below.
- **Tackle.** A 🕸️ Cast Net pulls three fish from one cast; a 🪤 Fish Trap
  fishes for you while you're away — one at every spot you've chartered.
- **The cast cooldown is a flat 20 seconds for everyone**, everywhere. Since
  your fishing progress is one account, one cooldown is the only thing that
  made sense — and it can't be dodged by hopping servers.
- `/fish sell` shows a **pick list of what's actually in your bag**, including
  a "sell everything" option.
- `/fish top` can show **this server or every server** (`scope` option).

### 🗺️ Where you fish

`/fish travel` opens the map. Each spot has fish you can catch nowhere else,
bends the odds its own way, and — past the first — carries a chance your line
snaps and you lose the catch.

| Spot | Needs | Charter | Snag risk | Only here |
|---|---|---|---|---|
| 🪷 Old Pond | — | free | none | your starting water |
| 🏞️ River Bend | level 5 | 6,000 | 3% | 🦐 Crayfish, 🐍 River Eel, 🐟 Sturgeon |
| 🪸 Coral Reef | level 12 | 35,000 | 6% | 🐠 Clownfish, 🐴 Seahorse, 🐢 Sea Turtle, 🛸 Manta Ray |
| 🌊 The Deep | level 22 | 140,000 | 10% | 🔦 Lanternfish, 🪱 Gulper Eel, 🦑 Colossal Squid, 🦈 Frilled Shark |
| 🕳️ Abyssal Trench | level 35 | 600,000 | 15% | 🐚 Hadal Snailfish, 👻 Abyssal Ray, 🐉 Leviathan, 🔮 Void Pearl |

**A charter is bought once and yours forever**, in every server — after that,
travelling back and forth is free. Nothing is ever taken away: every fish you
could catch at the Old Pond is still catchable everywhere else.

The deeper spots are a **trade, not a straight upgrade**. The Deep and the
Trench hand you far more junk than the pond does; they're worth it because of
what else is down there. Expect to lose a line now and then — a snag costs you
that catch and the one bait charge that cast used, never the rest of your bait.

### 🪤 Tackle

| Item | Price | What it does |
|---|---|---|
| 🕸️ Cast Net | 500 | Your next 3 casts pull **three fish each** |
| 🪤 Fish Trap | 250 | Set it and walk away; a full basket in 2 hours |

Neither makes you money on paper — like bait, they cost more than they return.
What you're buying is **time**: the 20-second cooldown is what actually limits
fishing, and a net beats it while a trap ignores it completely.

A trap is set where you're standing and pays out from *that* water, so a trap
left at the Trench is worth a lot more than one in the pond. **You can leave
one at every spot you've chartered** — one per place, so a new charter is
somewhere new to leave a trap as well as somewhere new to fish.

`/fish trap` does whichever of the two is possible right now: it pulls every
trap that's finished soaking (wherever they are — you don't have to travel back
to collect), and otherwise sets one in the water you're standing in. `/fish hub`
lists what's soaking and when each is up.

### Rarities

Seven tiers: 🗑️ Junk → ⚪ Common → 🟢 Uncommon → 🔵 Rare → 🟣 Epic →
🟠 Legendary → 💰 Treasure. Forty-four species in total, fourteen of which live
at one spot only (`/fish dex` tracks which ones you've caught — selling never
erases your dex, and a species you caught once stays in it even if you never go
back).

Heavier specimens of the same species are worth more. Your heaviest non-junk
catch is remembered as your personal best. Treasure isn't a fish: it pays
coins immediately.

### Rods (your luck ladder)

| Tier | Rod | Price | Luck |
|---|---|---|---|
| 1 | 🥢 Twig & String | free | — |
| 2 | 🎣 Wooden Rod | 750 | 15% |
| 3 | 🎣 Fiberglass Rod | 5,000 | 30% |
| 4 | 🎣 Carbon Rod | 24,000 | 45% |
| 5 | ✨ Golden Rod | 100,000 | 60% |
| 6 | 🔱 Mythic Trident | 450,000 | 75% |

Luck pulls the odds away from junk and common and into the good tiers. Buy the
next one with `/fish upgrade`.

### Levels, streaks, quests, events

- **XP** every catch (rarer fish = more XP). Each level adds a little luck, up
  to +15%.
- **Daily streak:** your first cast of the day extends it and pays a bonus of
  10 coins per streak day (capped at 100).
- **Daily quest** (`/fish quest`): one per day — catch N fish, catch N of a
  rarity, or earn N coins. The reward pays out automatically.
- **Events** (`/fish events`): rarely, the water changes for 10–20 minutes —
  🐟 Feeding Frenzy (double value), ⭐ Double XP, or 🍀 Lucky Waters (+luck).
  Everyone in the server benefits.

### Bait (`/fish buy`)

| Item | Price | Effect |
|---|---|---|
| 🪱 Worm | 25 | +5% luck, 5 casts |
| 🦐 Shrimp | 100 | +12% luck, 5 casts |
| 🟢 Glowgrub | 300 | +25% luck, 5 casts |
| 🧲 Treasure Magnet | 500 | +35% luck, 3 casts |
| ⭐ XP Potion | 200 | XP multiplier, timed |

(Nets and traps are on the same shelf — see 🪤 Tackle above.)

Buying bait doesn't arm it. The quickest way is the **⚡ menu in `/fish shop`**:
it lists the bait, nets and charms you own and arms everything you pick in one
press, right where you bought them. `/inventory use <bait>` does the same thing
from anywhere, and `/fish bait` shows what's currently armed.

---

## 💼 Jobs and adventures

`/adventure` on its own is your dashboard, and the place to start. It opens
with how many runs you have banked right now, then your daily streak, then your
two progression tracks (your work career title and how many shifts to the next
one, plus your pickaxe tier), then every activity — what it's for, how fast it
comes back, how many you've got waiting, and how many times you've done it.

**And it has buttons.** Work, dig, hunt and explore each get one, so you can do
everything that's ready without typing another command. There's a 🔄 Refresh
button too, since charges tick back up while the card is sitting there. `/rob`
doesn't get a button — it needs you to pick a target.

On a phone, tap `/adventure dashboard` — Discord can't run a command group by
itself, so that's the same card under a name slash can reach.

**You don't have to go looking for it.** Every individual activity — `/work`,
`/mine dig`, `/adventure hunt`, `/adventure explore` — answers with its result
*and* a short version of the dashboard underneath, buttons included. So a
paycheck also tells you two digs and a hunt are waiting, and you can run them
from the same message. Press one and the result on top is replaced by the new
one, so working through a full set of banked runs doesn't fill the channel.
That happens on a refusal too, which is when it helps most: "work is on
cooldown" on its own is a dead end.

| Command | Comes back | Banks | Risk | Reward |
|---|---|---|---|---|
| `/work` | 3 hours | 4 (12h) | none | 200–360 coins + a 10-step career ladder |
| `/mine dig` | 3 hours | 4 (12h) | 8% cave-in (nothing) | A vein of 4–14 ore, rare bonus treasure key |
| `/adventure hunt` | 4 hours | 3 (12h) | 12% injury fine | A bag of 4–9 pelts/meat, rare 🏆 Golden Antler, rare padlock |
| `/adventure explore` | 6 hours | 2 (12h) | wasted trip | Nothing → coins → keys/chests/charms → a big find |
| `/rob <member>` | 4 hours | 1 | 200 coin fine | 10–20% of their wallet (capped 1,000) |

*(These lengths are the same in every server.)*

**This is built for checking in twice a day, not every twenty minutes.**

Everything except `/rob` banks up for **twelve hours**. Come back at lunch and
again in the evening and you will have lost nothing at all — the clock ran the
whole time you were away, and it was all still sitting there. There is no alarm
to set and no optimal schedule; showing up when you feel like it *is* the
optimal schedule.

Playing more often doesn't earn you more from these — you just collect the same
total in smaller pieces. If you want a loop that rewards sitting down with it,
that's fishing.

**📥 Collect all** does the whole lot in one press. A full set of buckets is
about thirteen banked runs, and tapping thirteen buttons is nobody's idea of a
good time — one press runs them all and shows you the total. The individual
buttons are still there if you only want to do the one thing, and
`/adventure collect` works if the buttons have expired.

**One cooldown, everywhere.** These aren't per server. Doing `/work` in one
server puts it on cooldown in every server you share with the bot, so there's
no hopping between servers to farm — your coins are one wallet, so your
cooldowns are one set too. A server admin can switch any activity off for their
own server, but nobody except the bot's owner can change how *long* a cooldown
lasts: since the cooldown follows you between servers, one server shortening it
would speed the activity up for its members everywhere.

### 🔥 Your daily streak

The first activity you run each day starts (or continues) a streak, and a
streak adds **+5% to every coin reward, up to +25%** on day 6 and after. Miss a
day and it starts over at one. Any of the five counts — it's asking whether you
turned up, not which button you pressed. Your dashboard shows what it's
currently worth.

### 🎲 Encounters

Roughly one run in seven turns into a decision instead of a result: a manager
asking you to stay late, a till that's come up over at close, a seam running
deeper than the props go, a stag on the treeline, three wolves with designs on
your kill, a hooded trader with a sealed box, a stone door in a hillside.

You get two or three buttons, and there's no right answer written on any of
them — one is usually steadier, one usually swings harder, and which is
actually better depends on how you feel about losing. Take your time; the
buttons stay live for a couple of minutes. Walk away and you simply don't get
the bonus, which is deliberate.

**Some choices lead to another one.** Pocket the four hundred that shouldn't be
in the till and you'll be standing there the next morning while the manager
runs the tape back. Take the stag and you still have to get it two miles to the
road in the dark. Follow the seam and find a pocket that keeps going — with an
hour of lamp oil left. Each stage is its own decision and pays out on its own,
so a chain that you leave half-answered keeps whatever the earlier stages
already gave you.

**`/work`** is the safe floor. Shifts add up to promotions (🍵 Intern all the
way to 🏆 Legend of the Office), and each promotion pays a bit more.

**`/mine`** yields 🪨 Stone → ⚫ Coal → ⚙️ Iron → 🟡 Gold → 💎 Diamond. Each ore
in a vein is rolled separately, so a fat seam can turn up a diamond next to the
gravel. Better pickaxes shift the odds toward the good stuff, and the top three
break extra ore out of every seam on top of that:

| Tier | Pickaxe | Price | Luck | Extra ore |
|---|---|---|---|---|
| 1 | ✊ Bare Hands | free | — | — |
| 2 | ⛏️ Stone Pickaxe | 750 | 15% | — |
| 3 | ⛏️ Iron Pickaxe | 3,000 | 30% | +1 |
| 4 | ⛏️ Steel Pickaxe | 7,500 | 45% | +2 |
| 5 | ⛏️ Obsidian Pickaxe | 24,000 | 60% | +4 |

Every one of them pays for itself: the cheapest inside a few days of ordinary
mining, the Obsidian inside about three weeks. `/mine stats` shows your tier,
your dig count, and exactly what the next pickaxe costs. `/mine upgrade` buys it.

**`/rob`** is the only player-vs-player command. You need at least 250 coins,
your target needs 500, and a 🔒 Padlock (found while hunting, used from your
inventory) blocks anyone from robbing you for a day. Fail and you pay a
200-coin fine. Servers can switch it off entirely.

---

## 🎒 Inventory, items and buffs

Everything you own that isn't coins or bagged fish lives in `/inventory`
(short: `/inv`).

| Command | What it does |
|---|---|
| `/inventory view` | Your items grouped by category, with totals and active buffs |
| `/inventory view [category]` | The same view, slash-reachable; add a category (`bait`, `material`, …) to show just that one |
| `/inventory use [item] [qty]` | Use a consumable — this is how bait and charms are armed. Name several, separated by commas, or run it bare and tap them from a menu |
| `/inventory sell <item> [qty]` | Sell items for coins |
| `/inventory sell all` | Sell **everything** sellable in one go |
| `/inventory sell cat:material` | Sell one whole category (materials, treasure, …) |
| `/inventory give <member> <item> [qty]` | Give items to someone |
| `/inventory info <item>` | What an item is and does |

Every one of those now shows a **tap-to-pick list** of what you actually own —
no more typing "lucky charm" exactly right.

**Clearing out a full bag.** You don't have to run `/inventory sell` once per
item. The sell picker's top row is **💰 Everything sellable** (with the coins
it's worth), followed by one row per category — **📦 All ⛏️ Materials**, **📦
All 💎 Treasure** — so a stuffed inventory empties in a single tap. Items that
can't be sold (keys, consumables like 🍀 Lucky Charm) are never included, and
adding a `qty` caps how many of *each* item goes, e.g. `/inventory sell all 5`.

**Nothing goes without a confirm.** A bulk sell first shows you the full list
of what would go and what it's worth, with **💰 Sell** and **✖️ Cancel**
buttons — nothing leaves your inventory until you press Sell. Ignore it and it
expires after a minute, having sold nothing. (Selling a single item by name is
unchanged: it goes straight through.)

**Buffs.** Using a consumable stores an effect: either timed (a 🍀 Lucky Charm
for a while) or charge-based (bait that spends one charge per cast). Active
effects show at the bottom of `/inventory` and on your `/profile`.

**Chests.** 🧰 Treasure Chest + 🗝️ Treasure Key → `/inventory use treasure
chest` pays 250–750 coins. You need one of each.

### 🛠️ Crafting

`/craft` lists every recipe with **✅ ×N** (how many you can make right now) or
❌. `/craft make <recipe>` builds it — and the recipe option is a pick list that
shows what each one costs, so you never have to guess a name.

Two shortcuts, because nobody counts their own ore first:

- `/craft make <recipe> max` — makes as many as your materials allow.
- `/craft make all` — makes everything you can, across every recipe. It shows
  you the whole plan first and makes nothing until you press **Craft**, since
  it will spend material you might have been saving.

Eight recipes turn raw materials into useful things: luck charms, a reinforced
rob-shield, a golden lure, decorative collectibles worth more than their
parts, and spare treasure keys. Crafting is entirely optional — nothing else
needs it.

---

## 🎰 Casino

`/casino` shows the games, the current bet limits, the jackpot, and your
record. Every bet is taken from your global wallet.

| Game | How it works | Pays |
|---|---|---|
| `/casino flip <bet> <side>` | Heads or tails (pick from the list) | 1.92× |
| `/casino dice <bet>` | Your 2d6 vs the dealer's, tie = bet back | 2.1× |
| `/casino slots <bet>` | Three reels; pairs and triples pay | varies |
| `/casino roulette <bet> <space>` | European wheel — red/black/odd/even/high/low, or an exact number | 2× / 35× |
| `/casino blackjack <bet>` | Hit/Stand buttons vs the dealer, blackjack pays 3:2 | 2× / 2.5× |

Plus `/casino jackpot`, `/casino challenge`, `/casino stats [member]`,
`/casino top [page] [scope]`.

**Win streaks.** Three wins in a row starts adding a bonus to your payouts —
+5% per win, up to +25%. A loss resets it.

**The jackpot.** 20% of every net loss anyone takes feeds that **server's**
progressive pot. Hit three 7️⃣ on the slots and you take the whole thing. (The
pot is per server; your stats and streak are yours everywhere.)

**Daily challenges.** `/casino challenge` gives you two a day — win N games,
play N games, wager N coins, or land a big payout. They pay out automatically
the moment you finish one. One set per day for you as a person, not per
server.

**Roulette got easier to bet on:** the `space` option lists red/black/odd/
even/high/low and every number, so you tap instead of remembering the
vocabulary.

---

## 🏅 Progression

`/progress` is your long game. Nothing here needs opting in — it's all
calculated from what you've already done, whenever you look.

| Command | What it does |
|---|---|
| `/progress` | Achievements, points, weekly status, prestige |
| `/progress achievements [member]` | What you've earned and what's next |
| `/progress badges [member]` | Your trophy case, as a picture (also `trophies`, `case`) |
| `/progress weekly` | This week's 3 objectives (auto-claims completed ones) |
| `/progress title <name>` | Pick which earned title shows on your profile |
| `/progress prestige` | Requirements and your rank |

**Achievements (40).** Milestones across fishing, wealth, the casino and
activities. Each pays once — coins, an item, or a **title** — and because
achievements are global you can never re-earn one by joining another server.

**The trophy case.** `/progress badges` draws every achievement as a trophy on
a shelf, and every one of them is made for the exact thing you did — right down
to which milestone it was. Catch 10 fish and you get a minnow; 100 gets you a
proper fish; 1,000 gets you a marlin. A 3-day streak is a flame, a week is a
calendar, a month is the tide. Your first 1,000 coins is a piggy bank, 100,000
is a top hat. Hunting starts at a paw print and ends at a stag; exploring goes
compass then globe; the casino runs chip, die, roulette wheel. What the
achievement is worth decides what it stands on — a plain block, then a column,
then a stepped plinth, then a laurel wreath, in bronze, silver, gold and
prismatic, each taller than the last, so the big ones tower over the shelf. The
plate on the base carries its category's colour (blue fishing, gold wealth,
pink casino, green activities). Ones you haven't earned yet stand there as
empty outlines, so the case is also the list of what's left to chase.

**Titles.** Some achievements grant a title. `/progress title` lists the ones
you've earned (and a "none" option). It shows on your `/profile`.

**Weekly objectives.** Three each week, drawn just for you. Progress is
measured from the moment the week's objectives are created, and rewards
auto-claim when you finish. They're the same three in every server.

**Prestige.** The endgame sink: from rank *N*, advancing needs
100×(N+1) achievement points **and** 20,000×(N+1)² coins, up to rank 10 —
so rank 1 costs 20,000 and each rank after it climbs steeply.
Nothing is reset or lost — you get a ⭐ badge, a Prestige title, and a +5% per
rank bonus on weekly-objective payouts. `/progress prestige confirm` does it.

---

## 🤝 Playing together

- **`/squad [members] [activity]`** — tag up to five teammates (or tag nobody
  to open a picker for up to 25). Everyone confirms with a button, then the
  whole party earns coins **and contribution points**.
- **`/raid [activity]`** — opens a join board anyone can press **Join** on. The
  host (or a mod) presses **Finish** and everyone who joined gets paid.

**Each of these pays you once per cooldown** — 30 minutes for a squad, an hour
for a raid, the same in every server. You can join as many boards as you like;
you just won't be paid twice inside the window. If you're on cooldown when a
board settles, everyone else is still paid in full and you're named on the
result, so your timing never costs the party.

Contribution points are a lifetime stat — spending coins never lowers them —
and they drive `/coin contrib` and its rank titles.

---

## 👍 Rep and 🍪 cookies

Two ways to say something nice, neither worth a single coin.

| Command | What it does |
|---|---|
| `/profile rep <member>` | Give someone a reputation point — once every 24 hours |
| `/profile rep` | Your rep total, your global position, and when your next one is ready |
| `/fun cookie <member>` | Give someone a cookie |
| `/fun cookie` | Your **cookie jar** card: how many you've given and received |

Rep shows in the top-right of your profile card. You can't rep yourself, you
can't rep a bot, and you get exactly one to give per day — that's the whole
point of it meaning anything. If a rep somehow fails to land, the day isn't
spent.

Cookies have no limit beyond a few seconds between them, because they're worth
nothing and that's fine. The jar card shows both numbers, because being
generous counts as much as being popular.

Both follow your account into every server, like everything else you own.

---

## 🛒 The shop

`/shop` opens the hub — three aisles, one wallet:

| Aisle | What it sells | Buy with |
|---|---|---|
| `/shop profile` | Banners, borders, nameplates and badges for `/profile` | `/shop unlock <name>` |
| `/shop wallet` | Wallet banners and coin styles for `/balance` | `/shop unlock <name>` |
| `/shop server` | The rewards your mods set up here | `/shop buy <item>` |

Both cosmetic aisles **show you the art** — each page comes with a picture of
what's on it, and `/profile preview <name>` puts any of it on your own card
before you spend a coin.

**Browse it with the buttons.** Every aisle has ◀️ ▶️ arrows to turn the page
and a row underneath to hop between the aisles, so you never have to run the
command again with a bigger page number (`page:` still works if you'd rather
jump straight to one). The buttons belong to whoever ran the command — the
listing counts your coins — and they stop responding after a few minutes.

**The cosmetic aisles** cost coins and nothing else — no stat, no level. What
you buy is yours on **every** server, so the prices are the same everywhere and
your server's mods can't change them. Each listing marks 🟢 when you can afford
it, 🔒 with how much more you need, or ✅ when you already own it. Wear it with
`/profile equip <name>`.

**The server aisle** is your server's own; `/shop buy <item>` redeems it (the
option is a pick list with prices and ✅/🔒 affordability).

Two kinds of reward: **roles**, granted instantly by the bot, and **custom**
rewards, queued for a moderator to hand over. Some items have limited stock,
a per-person limit, or a cooldown.

The shop belongs to the server — but you pay with your global wallet, so coins
earned anywhere buy rewards here. Each price also shows roughly **how long it
takes to earn** ("~25m to earn"), measured in solid fishing time. It's a floor,
not a promise: nobody fishes non-stop, so the real wait is longer. Mods see the
same figure when they add an item, which is how they pick a price that matches
the effort they had in mind.

---

## 🗳️ Voting

`/vote` gives you links to top.gg and discordbotlist.com. It takes about thirty
seconds, you can vote on each site once every 12 hours, and it genuinely helps
more people find the bot.

**Every vote pays you:**

| | |
|---|---|
| 🪙 Coins | **250**, rising to **400** as you build a vote streak |
| 📈 Coin boost | **+25%** on everything you earn, for **6 hours** |
| 🍀 Luck boost | **+10%** for **6 hours** — better fish, better ore, better heists |
| ⏰ Reminders | **50** slots instead of 25 |
| 🎁 Milestone | Every **5th** vote drops a 🎁 Treasure Chest |

The two boosts are timed, and voting again refreshes them rather than stacking
them — so there's no point saving votes up. The coin boost applies to coins you
**earn** (activities, selling fish), not to coins that just move around, so
nobody can vote their way to a bigger `/pay` or a luckier casino night.

NanoBot will DM you when your cooldown resets. `/vote notify off` stops that.

---

## 🏆 Leaderboards

Every board offers two views. Pick with the `scope` option:

- **This server** (default) — the members of this server, ranked by their real
  (global) numbers.
- **Global** — everyone who uses the bot.

`/coin top` · `/coin contrib` · `/fish top` · `/casino top` · `/level top` ·
`/fish global <stat>` (cross-server, by stat: earnings, catches, casts,
heaviest catch, XP, best streak).

**Every board has buttons.** ◀️ ▶️ turn the page, and 🏠 This server / 🌍 Global
switches the view without running the command again (`/fish global` gets a
dropdown for its stat instead). `page:` and `scope:` still work if you'd rather
jump straight to one. The buttons belong to whoever ran the command and stop
responding after a few minutes.

Your personal rank on `/balance`, `/fish stats` and `/casino stats` is your
**global** rank.

---

## 💡 Tips

- **Open `/adventure dashboard` and hit Collect all.** Everything banks for twelve hours,
  so two visits a day loses you nothing and one press collects the lot.
- **Run something every day.** Even one activity keeps your streak alive, and
  the streak is worth up to +25% on every coin reward.
- **Set a rhythm.** `/daily` once a day, `/adventure` when you're around, cast
  a few lines between things.
- **Arm your bait.** Buying it isn't enough — tap the ⚡ menu in `/fish shop`
  (or `/inventory use <bait>`) first, and it only spends charges when you
  actually cast.
- **Upgrade the rod before you hoard.** Rod luck compounds with every future
  cast; a pile of coins doesn't.
- **Sell in bulk.** `/fish sell` → "Everything in your bag".
- **Chests need keys.** Hold onto 🗝️ Treasure Keys — chests without them are
  dead weight.
- **The house wins on average.** The casino is entertainment, not income; the
  streak bonus is the only edge you control.
- **Don't rob broke people.** Minimum balances are enforced, and a padlocked
  target just wastes your 4-hour cooldown.
- **Open `/profile` now and then.** Cosmetics you've earned only unlock when
  you look at your own card.
- **Check `/progress weekly` on Mondays.** The objectives are picked for you
  and pay well.

---

## ❓ FAQ

**Do I have to start over in a new server?**
No. That's the whole point of the global economy — your account comes with you.

**Someone in another server has coins I've never seen on our leaderboard. Why?**
The default leaderboard shows only *this server's* members. Switch `scope` to
Global to see everyone.

**Why can't I `/daily` again in another server?**
The claim is once per day for you as a person. Same for `/work`, `/fish`, the
daily quest and casino challenges.

**Why does the currency have a different name here?**
Each server picks its own name and emoji. It's still one balance.

**Can a server admin take my coins?**
No. Your wallet is the same in every server, so adding to it or taking from it
is the bot owner's call, not a server's — `coin grant`, `coin take` and
`coin reset` are all bot-owner-only. What a server *can* do is set its own
shop prices, which only decides what its own rewards cost.

**I bought bait and nothing changed.**
Arm it: the ⚡ menu at the bottom of `/fish shop`, or `/inventory use <bait>`.
Check `/fish bait` to confirm.

**Fishing/casino/rob doesn't work here.**
The server has that feature switched off. Ask a moderator, or play it in
another server — your progress is the same account either way.

**What's the difference between my global level and my server level?**
Global level is your account across every server and nobody can configure it.
Server level is this community's chat XP, tuned by its admins. Both are on
`/profile` and `/rank`.

**How do I get badges?**
Play (fishing, casino, work, prestige, global levels) and they unlock
automatically when you open `/profile`. A few — Developer, Beta Tester, Event
Winner — are handed out by staff. Equip up to six with `/profile equip` — name
them in one go (`/profile equip angler, high roller`) or run it bare and tap.

**Can I change my card's background?**
Yes: `/profile cosmetics` lists the banners, borders and nameplates you own,
and `/profile equip <name>` puts one on. If nothing you own appeals, `/shop
profile` and `/shop wallet` sell more for coins.

**Why does my balance look different?**
`/balance` draws a card now instead of listing numbers — same figures, plus
your daily streak. `/shop wallet` sells backgrounds and coin styles for it.

**Where did `/fish cooldown` go?**
The cast cooldown is now a fixed 20 seconds for everyone, so there's nothing
to configure.

---

## 🛠️ For server admins (short version)

| Area | Commands (Manage Server) |
|---|---|
| Currency & raids | `/coin name`, `/coin emoji`, `/coin raidsize`, `/coin config` |
| Shop | `/shop seed`, `/shop add`, `/shop edit`, `/shop remove`, `/shop pending`, `/shop fulfill` |
| Fishing | `/fish toggle`, `/fish config` |
| Casino | `/casino limit`, `/casino toggle`, `/casino config` |
| Activities | `/adventure toggle`, `/adventure config` |
| Leveling | `/level …` (per-server chat XP — deliberately **not** global) |

Note that `coin grant`, `coin take` and `coin reset` are all bot-owner-only
(and prefix-only — they aren't in the slash list at all).
With one global wallet, granting mints coins that spend in every server and
taking deletes coins members earned in servers this one has never seen —
neither is a single server's call.

Reward amounts and activity cooldowns aren't in that table because they aren't
per server — they set the pace of an economy every server shares, so the bot's
owner sets them once (`!econ`, `!cooldown`). `/coin config` and
`/adventure config` show the live values, marked bot-wide. What is yours to tune
is the **shop**: its prices are a sink, so nothing you charge can leak into
another server.

Deeper reading: [`docs/global-economy.md`](global-economy.md) (what's global
and why, plus the migration) and [`docs/economy-design.md`](economy-design.md)
(systems design).
