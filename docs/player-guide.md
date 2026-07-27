# NanoBot Player Guide

Everything you can do with NanoCoins — fishing, jobs, the casino, crafting,
achievements — in one place. No prior knowledge needed. If you only read one
section, read the next one.

> **Slash or prefix, your choice.** Every command works as `/fish` or as
> `n!fish` (the `n!` prefix can be changed per server). On mobile, use the
> slash commands: most options now pop up a list you tap instead of typing.

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

1. `/daily` — free coins, once every 24 hours. Come back daily for a streak bonus.
2. `/fish` — cast a line. You can cast again every **20 seconds**.
3. `/fish sell` — turn your catch into coins (pick "Everything in your bag").
4. `/work` — a safe paycheck, once an hour.
5. `/profile` — see everything you've built so far.

Then keep going: `/adventure` shows every activity and whether you're off
cooldown right now.

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

A server running 5× XP doesn't earn you global levels any faster, and a server
with leveling switched off doesn't stop your global progress.

---

## 🎖️ Badges, banners, and cosmetics

Your card is customisable. Four kinds of cosmetic:

| Slot | What it changes | How many |
|---|---|---|
| **Banner** | The artwork behind your card | 1 |
| **Border** | The frame around it | 1 |
| **Nameplate** | The plate behind your name | 1 |
| **Badges** | The row along the bottom | up to 6 |

| Command | What it does |
|---|---|
| `/profile cosmetics [slot]` | Everything you own, and how to unlock the rest |
| `/profile equip <name>` | Wear it (the slot is worked out for you) |
| `/profile unequip <name>` | Take it off — or pass a slot name to clear it |
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
| `/balance [member]` | Your balance, global rank, contribution rank |
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

---

## 🎣 Fishing

The biggest earner in the game, and the most hands-on.

```
/fish              cast your line          (every 20 seconds)
/fish bag          what you're carrying
/fish sell [fish]  sell one species, or everything
/fish rod          your rod + the next upgrade
/fish upgrade      buy the next rod
/fish buy <item>   the bait shop
/fish bait         what bait you own and what's armed
/fish quest        today's quest
/fish events       what's boosting the water right now
/fish dex          your species collection
/fish stats        casts, catches, earnings, level, best catch
/fish top / global leaderboards
```

### What's new

- **The cast cooldown is now a flat 20 seconds for everyone**, everywhere. It
  used to be a per-server setting; since your fishing progress is one account,
  one cooldown is the only thing that made sense — and it can't be dodged by
  hopping servers.
- `/fish sell` now shows a **pick list of what's actually in your bag**,
  including a "sell everything" option.
- `/fish buy` shows the bait shop as a list with prices, marked ✅ if you can
  afford it.
- `/fish top` can show **this server or every server** (`scope` option).

### Rarities

Seven tiers: 🗑️ Junk → ⚪ Common → 🟢 Uncommon → 🔵 Rare → 🟣 Epic →
🟠 Legendary → 💰 Treasure. Thirty species in total (`/fish dex` tracks which
ones you've caught — selling never erases your dex).

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

Buying bait doesn't arm it — **use `/inventory use <bait>`** before casting.
`/fish bait` shows what you own and what's currently armed.

---

## 💼 Jobs and adventures

`/adventure` on its own is your dashboard: every activity, what it's for, its
cooldown, and whether you're ready right now.

| Command | Cooldown | Risk | Reward |
|---|---|---|---|
| `/work` | 1 hour | none | 60–140 coins + a 10-step career ladder |
| `/mine` | 30 min | 8% cave-in (nothing) | Ore to sell, rare bonus treasure key |
| `/adventure hunt` | 45 min | 12% injury fine | Pelts, meat, rare 🏆 Golden Antler, rare padlock |
| `/adventure explore` | 3 hours | wasted trip | Nothing → coins → keys/chests/charms → a big find |
| `/rob <member>` | 4 hours | 200 coin fine | 10–20% of their wallet (capped 1,000) |

*(These lengths are the same in every server.)*

**One cooldown, everywhere.** These aren't per server. Doing `/work` in one
server puts it on cooldown in every server you share with the bot, so there's
no hopping between servers to farm — your coins are one wallet, so your
cooldowns are one set too. A server admin can switch any activity off for their
own server, but nobody except the bot's owner can change how *long* a cooldown
lasts: since the cooldown follows you between servers, one server shortening it
would speed the activity up for its members everywhere.

**`/work`** is the safe floor. Shifts add up to promotions (🍵 Intern all the
way to 🏆 Legend of the Office), and each promotion pays a bit more.

**`/mine`** yields 🪨 Stone → ⚫ Coal → ⚙️ Iron → 🟡 Gold → 💎 Diamond. Better
pickaxes shift the odds toward the good stuff:

| Tier | Pickaxe | Price |
|---|---|---|
| 1 | ✊ Bare Hands | free |
| 2 | ⛏️ Stone Pickaxe | 750 |
| 3 | ⛏️ Iron Pickaxe | 4,000 |
| 4 | ⛏️ Steel Pickaxe | 24,000 |
| 5 | ⛏️ Obsidian Pickaxe | 100,000 |

`/mine stats` shows your tier, your dig count, and exactly what the next
pickaxe costs. `/mine upgrade` buys it.

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
| `/inventory` | Your items grouped by category, plus active buffs |
| `/inventory use <item> [qty]` | Use a consumable — this is how bait and charms are armed |
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

`/craft` lists every recipe with ✅ (you have the materials) or ❌.
`/craft make <recipe>` builds it — and the recipe option is a pick list that
shows what each one costs, so you never have to guess a name.

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
| `/progress badges [member]` | A wall of your badges |
| `/progress weekly` | This week's 3 objectives (auto-claims completed ones) |
| `/progress title <name>` | Pick which earned title shows on your profile |
| `/progress prestige` | Requirements and your rank |

**Achievements (40).** Milestones across fishing, wealth, the casino and
activities. Each pays once — coins, an item, or a **title** — and because
achievements are global you can never re-earn one by joining another server.

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

`/shop list` shows what your server sells; `/shop buy <item>` redeems it (the
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

## 🏆 Leaderboards

Every board offers two views. Pick with the `scope` option:

- **This server** (default) — the members of this server, ranked by their real
  (global) numbers.
- **Global** — everyone who uses the bot.

`/coin top` · `/coin contrib` · `/fish top` · `/casino top` ·
`/fish global <stat>` (cross-server, by stat: earnings, catches, casts,
heaviest catch, XP, best streak).

Your personal rank on `/balance`, `/fish stats` and `/casino stats` is your
**global** rank.

---

## 💡 Tips

- **Set a rhythm.** `/daily` once a day, `/work` when you're around, cast a few
  lines between things. `/adventure` tells you what's ready.
- **Arm your bait.** Buying it isn't enough — `/inventory use <bait>` first,
  and it only spends charges when you actually cast.
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
is the bot owner's call, not a server's — `/coin grant`, `/coin take` and
`/coin reset` are all bot-owner-only. What a server *can* do is set its own
shop prices, which only decides what its own rewards cost.

**I bought bait and nothing changed.**
Use it: `/inventory use <bait>`. Check `/fish bait` to confirm it's armed.

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
Winner — are handed out by staff. Equip up to six with `/profile equip`.

**Can I change my card's background?**
Yes: `/profile cosmetics` lists the banners, borders and nameplates you own,
and `/profile equip <name>` puts one on.

**Where did `/fish cooldown` go?**
The cast cooldown is now a fixed 20 seconds for everyone, so there's nothing
to configure.

---

## 🛠️ For server admins (short version)

| Area | Commands (Manage Server) |
|---|---|
| Currency & raids | `/coin name`, `/coin emoji`, `/coin raidsize`, `/coin config` |
| Shop | `/shop seed`, `/shop add`, `/shop edit`, `/shop remove`, `/shop pending`, `/shop fulfill` |
| Fishing | `/fish toggle`, `/fish event`, `/fish config` |
| Casino | `/casino limit`, `/casino toggle`, `/casino config` |
| Activities | `/adventure toggle`, `/adventure config` |
| Leveling | `/level …` (per-server chat XP — deliberately **not** global) |

Note that `/coin grant`, `/coin take` and `/coin reset` are all bot-owner-only.
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
