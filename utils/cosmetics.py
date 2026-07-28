"""utils/cosmetics.py — the profile cosmetics catalogue (badges, banners, …).

The same shape as ``utils/items.py``: definitions live in code (or in a JSON
file, see ``load_file``), the database only ever stores keys. Adding a cosmetic
never touches the schema, the renderer, or a command.

Three things make this extensible on purpose:

1. **Slots are data.** ``SLOTS`` maps a slot key to how many can be equipped at
   once, which card it dresses (``category``), and how the card treats it. A
   future "pet", "effect", or "frame" slot is one entry here — ``/profile
   equip``, the shop aisles, and the cards all loop over the registry.
2. **Unlock rules are data.** Each def carries an ``unlock`` dict —
   ``{"kind": "global_level", "value": 10}``, ``{"kind": "achievement",
   "key": "fish_caught_100"}``, ``{"kind": "prestige", "value": 3}``,
   ``{"kind": "stat", "stat": "casino_games", "value": 500}``,
   ``{"kind": "purchase"}`` for a shop cosmetic (with a ``price``), or
   ``{"kind": "manual"}`` for event/admin grants. ``is_unlocked`` evaluates all
   of them against one context dict, so a new rule kind is one branch.
3. **Art is optional.** A def declares a palette, a glyph, and optionally a
   ``pattern``/``style``; the renderer draws clean vector-style art from those
   if no file exists at ``assets/profile/<slot>/<key>.png``. Drop a real PNG in
   later and it wins, with no code change — which is exactly the "don't spend
   time on artwork now" trade.

PRICING
───────
Shop prices live **here, in code**, and are the same on every server. That is
not an oversight: a cosmetic is worn on a *global* account, so a per-guild
price would mean the cheapest server on the bot set what everyone paid — the
same reason the rod ladder and item values are bot-wide constants. A guild's
own ``/shop`` (roles and mod-fulfilled perks) is untouched by this: what it
sells can only ever reach its own members. See docs/global-economy.md.

The JSON file (``data/cosmetics.json`` by default) uses the same field names:

    [{"key": "beta_tester", "name": "Beta Tester", "slot": "badge",
      "glyph": "β", "palette": ["#5865F2", "#8B5CF6"],
      "unlock": {"kind": "manual"}, "description": "Was here early."}]
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field

log = logging.getLogger("NanoBot.cosmetics")


@dataclass(frozen=True)
class SlotDef:
    key: str
    label: str
    max_equipped: int = 1
    description: str = ""
    # Which card the slot dresses up. The cosmetic shop sorts its aisles by
    # this ("profile" → /shop profile, "wallet" → /shop wallet), so a future
    # card is a new category string, not a new command.
    category: str = "profile"


# How many of each cosmetic can be worn at once. Adding a slot here makes it
# equippable, listable, and (if the card knows how to draw it) renderable.
SLOTS: dict[str, SlotDef] = {
    "banner": SlotDef("banner", "Banner", 1, "The artwork behind your card."),
    "border": SlotDef("border", "Border", 1, "The frame around your card."),
    "nameplate": SlotDef("nameplate", "Nameplate", 1, "The plate behind your name."),
    "badge": SlotDef("badge", "Badges", 6, "Up to six badges shown on your card."),
    "wallet": SlotDef(
        "wallet",
        "Wallet Banner",
        1,
        "The artwork behind your wallet card.",
        category="wallet",
    ),
    "coin": SlotDef(
        "coin",
        "Coin Style",
        1,
        "The coin drawn on your wallet card.",
        category="wallet",
    ),
}

# Slot categories, in the order the shop lists them.
CATEGORIES: tuple[str, ...] = ("profile", "wallet")


@dataclass(frozen=True)
class CosmeticDef:
    key: str
    name: str
    slot: str
    description: str = ""
    # Unlock rule — see the module docstring. Manual means "granted by an
    # admin or an event", which is how limited/seasonal drops work; purchase
    # means "buy it in the cosmetic shop for `price` coins".
    unlock: dict = field(default_factory=lambda: {"kind": "manual"})
    # Colours the generated art uses (hex). Two colours = gradient.
    palette: tuple[str, ...] = ("#5865F2", "#8B5CF6")
    # Drawn on generated badge/coin art when there's no PNG for this key. Keep
    # to glyphs the bundled font covers (geometric/dingbat ranges) — colour
    # emoji render as tofu boxes in Pillow. tests/test_cosmetics.py guards this.
    glyph: str = "★"
    rarity: str = "common"
    sort: int = 0
    # Coin cost in the cosmetic shop. Only meaningful with a `purchase` unlock
    # rule; the price is bot-wide on purpose (see PRICING below).
    price: int = 0
    # How the renderer draws this one. `pattern` picks a banner treatment
    # (waves/rays/bokeh/…), `style` picks a border treatment (double/glow/…).
    # Both are plain data: a new look is one string plus one branch in
    # utils/profile_card.py, and an empty value means "the original look".
    pattern: str = ""
    style: str = ""

    @property
    def asset_name(self) -> str:
        return f"{self.slot}/{self.key}.png"

    @property
    def category(self) -> str:
        slot = SLOTS.get(self.slot)
        return slot.category if slot else "profile"


COSMETICS: dict[str, CosmeticDef] = {}


def register(*defs: CosmeticDef) -> None:
    """Add definitions to the catalogue. Re-registering an identical def is a
    no-op (module re-import); a *conflicting* one is a bug and raises."""
    for d in defs:
        if d.slot not in SLOTS:
            raise ValueError(f"cosmetic {d.key!r} uses unknown slot {d.slot!r}")
        existing = COSMETICS.get(d.key)
        if existing is not None and existing != d:
            raise ValueError(f"cosmetic key {d.key!r} registered twice differently")
        COSMETICS[d.key] = d


def get(key: str) -> CosmeticDef | None:
    return COSMETICS.get(key)


def find(query: str) -> CosmeticDef | None:
    """Resolve user input to a definition (key or display name, case-insensitive)."""
    q = (query or "").strip().lower()
    if q in COSMETICS:
        return COSMETICS[q]
    for d in COSMETICS.values():
        if d.name.lower() == q:
            return d
    return None


def in_slot(slot: str) -> list[CosmeticDef]:
    """Every cosmetic for one slot, in display order."""
    return sorted(
        (d for d in COSMETICS.values() if d.slot == slot),
        key=lambda d: (d.sort, d.name),
    )


def slots_in(category: str) -> list[SlotDef]:
    """The slots belonging to one card, in registry order."""
    return [s for s in SLOTS.values() if s.category == category]


def purchasable(category: str | None = None) -> list[CosmeticDef]:
    """Everything the cosmetic shop sells, cheapest first.

    A `purchase` rule is never satisfied by playing — `is_unlocked` returns
    False for it — so the only way to hold one of these is to spend coins,
    which is what makes the shop a real sink rather than a preview of things
    you'd have earned anyway.
    """
    return sorted(
        (
            d
            for d in COSMETICS.values()
            if (d.unlock or {}).get("kind") == "purchase"
            and (category is None or d.category == category)
        ),
        key=lambda d: (d.price, d.slot, d.name),
    )


# ── Unlock rules ──────────────────────────────────────────────────────────────
def describe_unlock(d: CosmeticDef) -> str:
    """A human sentence for how a cosmetic is earned (used by /profile shop-y
    listings and the locked view)."""
    rule = d.unlock or {}
    kind = rule.get("kind", "manual")
    if kind == "global_level":
        return f"Reach global level {rule.get('value', 0)}"
    if kind == "prestige":
        return f"Reach prestige {rule.get('value', 0)}"
    if kind == "achievement":
        return f"Earn the “{rule.get('key', '?')}” achievement"
    if kind == "stat":
        return f"Reach {rule.get('value', 0):,} {rule.get('stat', 'progress')}"
    if kind == "purchase":
        return f"Buy for {d.price:,} coins in the cosmetic shop"
    if kind == "default":
        return "Available to everyone"
    return "Awarded by staff or during an event"


def is_unlocked(d: CosmeticDef, ctx: dict) -> bool:
    """Does `ctx` satisfy this cosmetic's unlock rule?

    `ctx` carries whatever the caller has already loaded:
    ``{"global_level": int, "prestige": int, "achievements": set[str],
    "stats": {stat_key: value}}``. Unknown rule kinds return False — a manual
    grant is the only way to hold them, which is the safe default for
    event/staff cosmetics. ``purchase`` deliberately lands there too: playing
    never unlocks a shop cosmetic, paying for it does.
    """
    rule = d.unlock or {}
    kind = rule.get("kind", "manual")
    if kind == "default":
        return True
    if kind == "global_level":
        return ctx.get("global_level", 0) >= int(rule.get("value", 0))
    if kind == "prestige":
        return ctx.get("prestige", 0) >= int(rule.get("value", 0))
    if kind == "achievement":
        return rule.get("key") in (ctx.get("achievements") or ())
    if kind == "stat":
        stats = ctx.get("stats") or {}
        return stats.get(rule.get("stat", ""), 0) >= float(rule.get("value", 0))
    return False


def auto_unlockable() -> list[CosmeticDef]:
    """Cosmetics that can be earned by playing — the set the profile cog
    re-evaluates when someone opens a card. Neither manual grants nor shop
    purchases are in here: those arrive by an explicit unlock, never by
    qualifying."""
    return [
        d
        for d in COSMETICS.values()
        if (d.unlock or {}).get("kind") not in ("manual", "purchase")
    ]


# ── JSON extension ────────────────────────────────────────────────────────────
DEFAULT_FILE = os.path.join("data", "cosmetics.json")


def load_file(path: str = DEFAULT_FILE) -> int:
    """Merge cosmetics from a JSON file. Returns how many were added.

    This is the "no code change needed" path: a server operator can add a
    seasonal badge by editing one file and reloading. A malformed entry is
    logged and skipped rather than taking the bot down.
    """
    if not os.path.exists(path):
        return 0
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError) as exc:
        log.warning("Could not read cosmetics file %s: %s", path, exc)
        return 0
    added = 0
    for entry in raw if isinstance(raw, list) else []:
        try:
            d = CosmeticDef(
                key=str(entry["key"]),
                name=str(entry["name"]),
                slot=str(entry["slot"]),
                description=str(entry.get("description", "")),
                unlock=dict(entry.get("unlock") or {"kind": "manual"}),
                palette=tuple(entry.get("palette") or ("#5865F2", "#8B5CF6")),
                glyph=str(entry.get("glyph", "★")),
                rarity=str(entry.get("rarity", "common")),
                sort=int(entry.get("sort", 0)),
                price=int(entry.get("price", 0)),
                pattern=str(entry.get("pattern", "")),
                style=str(entry.get("style", "")),
            )
            register(d)
            added += 1
        except (KeyError, TypeError, ValueError) as exc:
            log.warning("Skipping bad cosmetic entry %r: %s", entry, exc)
    if added:
        log.info("Loaded %d cosmetic(s) from %s", added, path)
    return added


# ══════════════════════════════════════════════════════════════════════════════
#  Built-in catalogue
#
#  Deliberately small and generic — every one of these draws itself from its
#  palette + glyph, so the bot ships with a complete-looking profile system and
#  zero binary art. Replace any of them by dropping assets/profile/<slot>/
#  <key>.png next to it.
# ══════════════════════════════════════════════════════════════════════════════
register(
    # ── Banners (card backgrounds) ──
    CosmeticDef(
        key="banner_default",
        name="Midnight",
        slot="banner",
        description="The standard NanoBot card.",
        unlock={"kind": "default"},
        palette=("#1E2130", "#2B2D42"),
        sort=0,
    ),
    CosmeticDef(
        key="banner_ocean",
        name="Deep Current",
        slot="banner",
        description="For anglers who never leave the water.",
        unlock={"kind": "stat", "stat": "fish_caught", "value": 250},
        palette=("#0F2027", "#2C5364"),
        pattern="waves",
        sort=10,
    ),
    CosmeticDef(
        key="banner_ember",
        name="Ember",
        slot="banner",
        description="Unlocked at global level 25.",
        unlock={"kind": "global_level", "value": 25},
        palette=("#42160F", "#C1440E"),
        pattern="rays",
        sort=20,
    ),
    CosmeticDef(
        key="banner_aurora",
        name="Aurora",
        slot="banner",
        description="Unlocked at global level 50.",
        unlock={"kind": "global_level", "value": 50},
        palette=("#0B486B", "#3B8686"),
        pattern="aurora",
        sort=30,
    ),
    CosmeticDef(
        key="banner_royal",
        name="Royal Velvet",
        slot="banner",
        description="For prestiged accounts.",
        unlock={"kind": "prestige", "value": 1},
        palette=("#2C0735", "#7B2CBF"),
        pattern="bokeh",
        rarity="rare",
        sort=40,
    ),
    CosmeticDef(
        key="banner_seasonal_winter",
        name="Winter Drift",
        slot="banner",
        description="A seasonal drop.",
        unlock={"kind": "manual"},
        palette=("#243B55", "#8CA6DB"),
        pattern="stars",
        rarity="event",
        sort=90,
    ),
    # ── Borders ──
    CosmeticDef(
        key="border_none",
        name="No Border",
        slot="border",
        description="Clean edges.",
        unlock={"kind": "default"},
        palette=("#00000000", "#00000000"),
        sort=0,
    ),
    CosmeticDef(
        key="border_steel",
        name="Steel Frame",
        slot="border",
        description="Unlocked at global level 10.",
        unlock={"kind": "global_level", "value": 10},
        palette=("#8E9AAF", "#C9CCD5"),
        style="solid",
        sort=10,
    ),
    CosmeticDef(
        key="border_gold",
        name="Gilded Frame",
        slot="border",
        description="Unlocked at global level 40.",
        unlock={"kind": "global_level", "value": 40},
        palette=("#B8860B", "#FFD700"),
        style="double",
        rarity="rare",
        sort=20,
    ),
    CosmeticDef(
        key="border_prestige",
        name="Prestige Frame",
        slot="border",
        description="Awarded at prestige 3.",
        unlock={"kind": "prestige", "value": 3},
        palette=("#7B2CBF", "#E0AAFF"),
        style="glow",
        rarity="epic",
        sort=30,
    ),
    # ── Nameplates ──
    CosmeticDef(
        key="plate_default",
        name="Plain Plate",
        slot="nameplate",
        description="A simple dark plate.",
        unlock={"kind": "default"},
        palette=("#00000099", "#00000055"),
        sort=0,
    ),
    CosmeticDef(
        key="plate_glass",
        name="Frosted Glass",
        slot="nameplate",
        description="Unlocked at global level 15.",
        unlock={"kind": "global_level", "value": 15},
        palette=("#FFFFFF33", "#FFFFFF11"),
        sort=10,
    ),
    CosmeticDef(
        key="plate_neon",
        name="Neon Strip",
        slot="nameplate",
        description="Unlocked at global level 60.",
        unlock={"kind": "global_level", "value": 60},
        palette=("#FF006E", "#8338EC"),
        rarity="rare",
        sort=20,
    ),
    # ── Badges: staff / community ──
    CosmeticDef(
        key="badge_developer",
        name="Developer",
        slot="badge",
        description="Builds the bot.",
        unlock={"kind": "manual"},
        palette=("#5865F2", "#3B49B0"),
        glyph="</>",
        rarity="legendary",
        sort=0,
    ),
    CosmeticDef(
        key="badge_contributor",
        name="Contributor",
        slot="badge",
        description="Shipped something that made it in.",
        unlock={"kind": "manual"},
        palette=("#57F287", "#1F8A4C"),
        glyph="✚",
        rarity="epic",
        sort=1,
    ),
    CosmeticDef(
        key="badge_early_supporter",
        name="Early Supporter",
        slot="badge",
        description="Was here before it was finished.",
        unlock={"kind": "manual"},
        palette=("#FEE75C", "#C79B00"),
        glyph="✦",
        rarity="epic",
        sort=2,
    ),
    CosmeticDef(
        key="badge_beta_tester",
        name="Beta Tester",
        slot="badge",
        description="Found the bugs so you didn't have to.",
        unlock={"kind": "manual"},
        palette=("#EB459E", "#8B2A63"),
        glyph="β",
        rarity="epic",
        sort=3,
    ),
    CosmeticDef(
        key="badge_event_winner",
        name="Event Winner",
        slot="badge",
        description="Took first place in a bot event.",
        unlock={"kind": "manual"},
        palette=("#FFD700", "#B8860B"),
        glyph="♛",
        rarity="legendary",
        sort=4,
    ),
    # ── Badges: earned by playing ──
    CosmeticDef(
        key="badge_angler",
        name="Fishing Master",
        slot="badge",
        description="1,000 fish caught.",
        unlock={"kind": "stat", "stat": "fish_caught", "value": 1000},
        palette=("#3498DB", "#1B4F72"),
        glyph="≈",
        rarity="rare",
        sort=10,
    ),
    CosmeticDef(
        key="badge_high_roller",
        name="Casino Champion",
        slot="badge",
        description="1,000 casino games played.",
        unlock={"kind": "stat", "stat": "casino_games", "value": 1000},
        palette=("#E74C3C", "#7B241C"),
        glyph="♠",
        rarity="rare",
        sort=11,
    ),
    CosmeticDef(
        key="badge_tycoon",
        name="Tycoon",
        slot="badge",
        description="Held 3,000,000 coins.",
        unlock={"kind": "stat", "stat": "balance", "value": 3_000_000},
        palette=("#F1C40F", "#7D6608"),
        glyph="◉",
        rarity="epic",
        sort=12,
    ),
    CosmeticDef(
        key="badge_grinder",
        name="Grinder",
        slot="badge",
        description="500 work shifts.",
        unlock={"kind": "stat", "stat": "work_shifts", "value": 500},
        palette=("#95A5A6", "#4D5656"),
        glyph="⚒",
        rarity="rare",
        sort=13,
    ),
    CosmeticDef(
        key="badge_veteran",
        name="Veteran",
        slot="badge",
        description="Global level 50.",
        unlock={"kind": "global_level", "value": 50},
        palette=("#9B59B6", "#4A235A"),
        glyph="✪",
        rarity="epic",
        sort=14,
    ),
    CosmeticDef(
        key="badge_ascended",
        name="Ascended",
        slot="badge",
        description="Global level 100.",
        unlock={"kind": "global_level", "value": 100},
        palette=("#00F5D4", "#00785E"),
        glyph="◈",
        rarity="legendary",
        sort=15,
    ),
    CosmeticDef(
        key="badge_prestige_1",
        name="Prestige I",
        slot="badge",
        description="Reached prestige 1.",
        unlock={"kind": "prestige", "value": 1},
        palette=("#C0C0C0", "#6E6E6E"),
        glyph="★",
        rarity="rare",
        sort=20,
    ),
    CosmeticDef(
        key="badge_prestige_5",
        name="Prestige V",
        slot="badge",
        description="Reached prestige 5.",
        unlock={"kind": "prestige", "value": 5},
        palette=("#FFD700", "#8A6D00"),
        glyph="★",
        rarity="epic",
        sort=21,
    ),
    CosmeticDef(
        key="badge_prestige_10",
        name="Prestige X",
        slot="badge",
        description="Reached the highest prestige.",
        unlock={"kind": "prestige", "value": 10},
        palette=("#E0AAFF", "#7B2CBF"),
        glyph="✵",
        rarity="legendary",
        sort=22,
    ),
)

# ══════════════════════════════════════════════════════════════════════════════
#  More earned cosmetics
#
#  The first batch above rewarded levels and prestige almost exclusively, which
#  meant a member who fishes, mines or gambles saw nothing new for hours. These
#  hang off the lifetime stats those systems already keep (the same registry
#  achievements read), so playing anything at all moves something.
# ══════════════════════════════════════════════════════════════════════════════
register(
    CosmeticDef(
        key="banner_forge",
        name="The Forge",
        slot="banner",
        description="500 trips into the mine.",
        unlock={"kind": "stat", "stat": "mine_count", "value": 500},
        palette=("#2B1B17", "#B4530A"),
        pattern="peaks",
        rarity="rare",
        sort=12,
    ),
    CosmeticDef(
        key="banner_wanderer",
        name="Far Country",
        slot="banner",
        description="250 expeditions.",
        unlock={"kind": "stat", "stat": "explore_count", "value": 250},
        palette=("#14281D", "#4C956C"),
        pattern="hex",
        rarity="rare",
        sort=14,
    ),
    CosmeticDef(
        key="banner_jackpot",
        name="High Roller",
        slot="banner",
        description="A single win of 100,000 coins.",
        unlock={"kind": "stat", "stat": "casino_biggest_win", "value": 100_000},
        palette=("#3B0D11", "#C9184A"),
        pattern="bokeh",
        rarity="epic",
        sort=16,
    ),
    CosmeticDef(
        key="plate_carbon",
        name="Carbon Weave",
        slot="nameplate",
        description="250 work shifts.",
        unlock={"kind": "stat", "stat": "work_shifts", "value": 250},
        palette=("#1C1C1EDD", "#3A3A3CAA"),
        sort=12,
    ),
    CosmeticDef(
        key="badge_collector",
        name="Collector",
        slot="badge",
        description="Every species in the dex.",
        unlock={"kind": "stat", "stat": "fish_dex_size", "value": 30},
        palette=("#48CAE4", "#023E8A"),
        glyph="❖",
        rarity="epic",
        sort=16,
    ),
    CosmeticDef(
        key="badge_hoarder",
        name="Hoarder",
        slot="badge",
        description="500 items carried at once.",
        unlock={"kind": "stat", "stat": "items_owned", "value": 500},
        palette=("#A47148", "#5C3D24"),
        glyph="▲",
        rarity="rare",
        sort=17,
    ),
    CosmeticDef(
        key="badge_trailblazer",
        name="Trailblazer",
        slot="badge",
        description="500 expeditions.",
        unlock={"kind": "stat", "stat": "explore_count", "value": 500},
        palette=("#52B788", "#1B4332"),
        glyph="⚑",
        rarity="rare",
        sort=18,
    ),
    CosmeticDef(
        key="badge_tracker",
        name="Tracker",
        slot="badge",
        description="500 hunts.",
        unlock={"kind": "stat", "stat": "hunt_count", "value": 500},
        palette=("#6D4C3D", "#2F2019"),
        glyph="⚔",
        rarity="rare",
        sort=19,
    ),
    CosmeticDef(
        key="badge_streaker",
        name="On A Roll",
        slot="badge",
        description="A ten-game casino win streak.",
        unlock={"kind": "stat", "stat": "casino_best_streak", "value": 10},
        palette=("#FF9E00", "#8A4B00"),
        glyph="⚡",
        rarity="epic",
        sort=20,
    ),
    CosmeticDef(
        key="badge_benefactor",
        name="Benefactor",
        slot="badge",
        description="100,000 lifetime contribution points.",
        unlock={"kind": "stat", "stat": "contribution", "value": 100_000},
        palette=("#89C2D9", "#2C7DA0"),
        glyph="⚕",
        rarity="epic",
        sort=21,
    ),
)

# ══════════════════════════════════════════════════════════════════════════════
#  The cosmetic shop
#
#  A pure coin sink: nothing here is ever earned by playing (see `purchasable`),
#  and none of it affects a number anywhere. Prices are bot-wide constants —
#  see PRICING in the module docstring for why they can't be a guild setting.
#
#  Rough pacing, using the ~5,000 coins/hour fishing yardstick the shop quotes
#  prices against: a starter piece is well under an hour, the mid tier is an
#  evening, and the legendary tier is a long-term goal you save for.
# ══════════════════════════════════════════════════════════════════════════════
register(
    # ── Shop banners ──
    CosmeticDef(
        key="banner_sunset",
        name="Sunset Drive",
        slot="banner",
        description="Long shadows and warm light.",
        unlock={"kind": "purchase"},
        price=12_000,
        palette=("#3D1E4F", "#F4845F"),
        pattern="rays",
        sort=50,
    ),
    CosmeticDef(
        key="banner_arcade",
        name="Arcade",
        slot="banner",
        description="Neon grid, all the way down.",
        unlock={"kind": "purchase"},
        price=18_000,
        palette=("#160F29", "#F72585"),
        pattern="grid",
        sort=51,
    ),
    CosmeticDef(
        key="banner_glacier",
        name="Glacier",
        slot="banner",
        description="Cold peaks under a pale sky.",
        unlock={"kind": "purchase"},
        price=22_000,
        palette=("#14304A", "#A9D6E5"),
        pattern="peaks",
        sort=52,
    ),
    CosmeticDef(
        key="banner_circuit",
        name="Mainframe",
        slot="banner",
        description="Traces running somewhere important.",
        unlock={"kind": "purchase"},
        price=28_000,
        palette=("#04170F", "#2DC653"),
        pattern="circuit",
        rarity="rare",
        sort=53,
    ),
    CosmeticDef(
        key="banner_sakura",
        name="Sakura",
        slot="banner",
        description="Petals, drifting.",
        unlock={"kind": "purchase"},
        price=34_000,
        palette=("#2B1B2E", "#F7A1C4"),
        pattern="bokeh",
        rarity="rare",
        sort=54,
    ),
    CosmeticDef(
        key="banner_nebula",
        name="Nebula",
        slot="banner",
        description="A very expensive piece of sky.",
        unlock={"kind": "purchase"},
        price=55_000,
        palette=("#10002B", "#9D4EDD"),
        pattern="nebula",
        rarity="epic",
        sort=55,
    ),
    CosmeticDef(
        key="banner_bullion",
        name="Bullion",
        slot="banner",
        description="Tasteful? No. Expensive? Yes.",
        unlock={"kind": "purchase"},
        price=150_000,
        palette=("#4A3405", "#FFD60A"),
        pattern="rays",
        rarity="legendary",
        sort=56,
    ),
    # ── Shop borders ──
    CosmeticDef(
        key="border_carbon",
        name="Carbon Edge",
        slot="border",
        description="Thin, dark, and out of the way.",
        unlock={"kind": "purchase"},
        price=9_000,
        palette=("#2B2D34", "#6C757D"),
        style="dashed",
        sort=50,
    ),
    CosmeticDef(
        key="border_neon",
        name="Neon Glow",
        slot="border",
        description="A soft light around the whole card.",
        unlock={"kind": "purchase"},
        price=20_000,
        palette=("#00F5D4", "#00BBF9"),
        style="glow",
        rarity="rare",
        sort=51,
    ),
    CosmeticDef(
        key="border_rose",
        name="Rose Corners",
        slot="border",
        description="Corner flourishes, nothing in between.",
        unlock={"kind": "purchase"},
        price=35_000,
        palette=("#FF8FA3", "#C9184A"),
        style="corners",
        rarity="rare",
        sort=52,
    ),
    CosmeticDef(
        key="border_obsidian",
        name="Obsidian",
        slot="border",
        description="Two frames, both black.",
        unlock={"kind": "purchase"},
        price=60_000,
        palette=("#0B090A", "#5C5C5C"),
        style="double",
        rarity="epic",
        sort=53,
    ),
    CosmeticDef(
        key="border_regalia",
        name="Regalia",
        slot="border",
        description="A gold ribbon frame. Subtle it is not.",
        unlock={"kind": "purchase"},
        price=120_000,
        palette=("#FFD60A", "#7F5539"),
        style="ribbon",
        rarity="legendary",
        sort=54,
    ),
    # ── Shop nameplates ──
    CosmeticDef(
        key="plate_slate",
        name="Slate",
        slot="nameplate",
        description="A cleaner dark plate.",
        unlock={"kind": "purchase"},
        price=6_000,
        palette=("#212529DD", "#495057AA"),
        sort=50,
    ),
    CosmeticDef(
        key="plate_sunset",
        name="Sunset Plate",
        slot="nameplate",
        description="Warm gradient behind your name.",
        unlock={"kind": "purchase"},
        price=14_000,
        palette=("#F4845F", "#B5179E"),
        sort=51,
    ),
    CosmeticDef(
        key="plate_holo",
        name="Holofoil",
        slot="nameplate",
        description="Shifts from teal to violet.",
        unlock={"kind": "purchase"},
        price=30_000,
        palette=("#00F5D4", "#9D4EDD"),
        rarity="rare",
        sort=52,
    ),
    CosmeticDef(
        key="plate_royal",
        name="Royal Plate",
        slot="nameplate",
        description="Purple and gold, for the very rich.",
        unlock={"kind": "purchase"},
        price=55_000,
        palette=("#5A189A", "#FFD60A"),
        rarity="epic",
        sort=53,
    ),
    # ── Shop badges (pure vanity — they mean nothing, that's the point) ──
    CosmeticDef(
        key="badge_coffee",
        name="Caffeinated",
        slot="badge",
        description="Bought, not earned. No regrets.",
        unlock={"kind": "purchase"},
        price=5_000,
        palette=("#8D6346", "#4A2F1B"),
        glyph="☕",
        sort=50,
    ),
    CosmeticDef(
        key="badge_heart",
        name="Sweetheart",
        slot="badge",
        description="For the sentimental.",
        unlock={"kind": "purchase"},
        price=7_500,
        palette=("#FF758F", "#A4133C"),
        glyph="♥",
        sort=51,
    ),
    CosmeticDef(
        key="badge_note",
        name="Encore",
        slot="badge",
        description="Always something playing.",
        unlock={"kind": "purchase"},
        price=7_500,
        palette=("#7B2CBF", "#3C096C"),
        glyph="♪",
        sort=52,
    ),
    CosmeticDef(
        key="badge_bolt",
        name="Live Wire",
        slot="badge",
        description="Fast, loud, slightly dangerous.",
        unlock={"kind": "purchase"},
        price=12_000,
        palette=("#FFD60A", "#9A6700"),
        glyph="⚡",
        rarity="rare",
        sort=53,
    ),
    CosmeticDef(
        key="badge_frost",
        name="Cold Snap",
        slot="badge",
        description="A snowflake you paid for.",
        unlock={"kind": "purchase"},
        price=12_000,
        palette=("#CAF0F8", "#0077B6"),
        glyph="❄",
        rarity="rare",
        sort=54,
    ),
    CosmeticDef(
        key="badge_skull",
        name="Menace",
        slot="badge",
        description="Purely for intimidation.",
        unlock={"kind": "purchase"},
        price=20_000,
        palette=("#ADB5BD", "#212529"),
        glyph="☠",
        rarity="rare",
        sort=55,
    ),
    CosmeticDef(
        key="badge_anchor",
        name="Old Salt",
        slot="badge",
        description="Looks like you fish. You may not.",
        unlock={"kind": "purchase"},
        price=20_000,
        palette=("#468FAF", "#013A63"),
        glyph="⚓",
        rarity="rare",
        sort=56,
    ),
    CosmeticDef(
        key="badge_gilded_crown",
        name="Gilded Crown",
        slot="badge",
        description="Bought the title, not the throne.",
        unlock={"kind": "purchase"},
        price=90_000,
        palette=("#FFD60A", "#7F5539"),
        glyph="♛",
        rarity="epic",
        sort=57,
    ),
    CosmeticDef(
        key="badge_infinity",
        name="Infinite",
        slot="badge",
        description="The most coins anyone has spent on nothing.",
        unlock={"kind": "purchase"},
        price=150_000,
        palette=("#E0AAFF", "#3C096C"),
        glyph="∞",
        rarity="legendary",
        sort=58,
    ),
)

# ══════════════════════════════════════════════════════════════════════════════
#  Wallet cosmetics
#
#  The second card (utils/wallet_card.py) gets its own two slots rather than
#  reusing the profile banner: /balance is a different, smaller card, and
#  wearing one banner on both would make the pair look like a mistake.
# ══════════════════════════════════════════════════════════════════════════════
register(
    # ── Wallet banners ──
    CosmeticDef(
        key="wallet_default",
        name="Plain Billfold",
        slot="wallet",
        description="The standard wallet card.",
        unlock={"kind": "default"},
        palette=("#12161F", "#232A3B"),
        sort=0,
    ),
    CosmeticDef(
        key="wallet_leather",
        name="Worn Leather",
        slot="wallet",
        description="Softened by years of coins.",
        unlock={"kind": "purchase"},
        price=6_000,
        palette=("#3E2723", "#8D6346"),
        sort=10,
    ),
    CosmeticDef(
        key="wallet_mint",
        name="Mint Condition",
        slot="wallet",
        description="Unlocked at global level 20.",
        unlock={"kind": "global_level", "value": 20},
        palette=("#0B3D2E", "#52B788"),
        pattern="waves",
        sort=11,
    ),
    CosmeticDef(
        key="wallet_tide",
        name="Full Nets",
        slot="wallet",
        description="500,000 coins earned from fishing.",
        unlock={"kind": "stat", "stat": "fish_earned", "value": 500_000},
        palette=("#03256C", "#2E86AB"),
        pattern="waves",
        rarity="rare",
        sort=12,
    ),
    CosmeticDef(
        key="wallet_vault",
        name="The Vault",
        slot="wallet",
        description="Steel doors and a very long code.",
        unlock={"kind": "purchase"},
        price=25_000,
        palette=("#1B1B1E", "#6C757D"),
        pattern="grid",
        rarity="rare",
        sort=13,
    ),
    CosmeticDef(
        key="wallet_emerald",
        name="Emerald Ledger",
        slot="wallet",
        description="Held 250,000 coins at once.",
        unlock={"kind": "stat", "stat": "balance", "value": 250_000},
        palette=("#04241B", "#2DC653"),
        pattern="hex",
        rarity="rare",
        sort=14,
    ),
    CosmeticDef(
        key="wallet_neon",
        name="Neon Ledger",
        slot="wallet",
        description="Balance, backlit.",
        unlock={"kind": "purchase"},
        price=45_000,
        palette=("#0B0A1F", "#F72585"),
        pattern="circuit",
        rarity="epic",
        sort=15,
    ),
    CosmeticDef(
        key="wallet_royal",
        name="Royal Purse",
        slot="wallet",
        description="For prestiged accounts.",
        unlock={"kind": "prestige", "value": 2},
        palette=("#240046", "#C77DFF"),
        pattern="bokeh",
        rarity="epic",
        sort=16,
    ),
    CosmeticDef(
        key="wallet_bullion",
        name="Bullion Case",
        slot="wallet",
        description="The single most expensive thing in the shop.",
        unlock={"kind": "purchase"},
        price=200_000,
        palette=("#3D2C05", "#FFD60A"),
        pattern="rays",
        rarity="legendary",
        sort=17,
    ),
    # ── Coin styles ──
    CosmeticDef(
        key="coin_default",
        name="NanoCoin",
        slot="coin",
        description="The one everybody starts with.",
        unlock={"kind": "default"},
        palette=("#F0B429", "#9A6700"),
        glyph="◉",
        sort=0,
    ),
    CosmeticDef(
        key="coin_bronze",
        name="Bronze Piece",
        slot="coin",
        description="Heavier than it needs to be.",
        unlock={"kind": "purchase"},
        price=3_000,
        palette=("#CD7F32", "#6E3F16"),
        glyph="◎",
        sort=10,
    ),
    CosmeticDef(
        key="coin_silver",
        name="Silver Piece",
        slot="coin",
        description="Rings nicely on a table.",
        unlock={"kind": "purchase"},
        price=12_000,
        palette=("#D8DEE9", "#78808C"),
        glyph="◈",
        sort=11,
    ),
    CosmeticDef(
        key="coin_clover",
        name="Lucky Token",
        slot="coin",
        description="A 30-day fishing streak.",
        unlock={"kind": "stat", "stat": "fish_streak", "value": 30},
        palette=("#95D5B2", "#1B4332"),
        glyph="☘",
        rarity="rare",
        sort=12,
    ),
    CosmeticDef(
        key="coin_gold",
        name="Gold Piece",
        slot="coin",
        description="What you pictured all along.",
        unlock={"kind": "purchase"},
        price=45_000,
        palette=("#FFD60A", "#8A6D00"),
        glyph="★",
        rarity="rare",
        sort=13,
    ),
    CosmeticDef(
        key="coin_sun",
        name="Sun Medallion",
        slot="coin",
        description="Struck for somebody important.",
        unlock={"kind": "purchase"},
        price=90_000,
        palette=("#FFB703", "#BF4E00"),
        glyph="☀",
        rarity="epic",
        sort=14,
    ),
    CosmeticDef(
        key="coin_ruby",
        name="Ruby Chip",
        slot="coin",
        description="Held 1,000,000 coins at once.",
        unlock={"kind": "stat", "stat": "balance", "value": 1_000_000},
        palette=("#FF4D6D", "#6A040F"),
        glyph="◆",
        rarity="epic",
        sort=15,
    ),
    CosmeticDef(
        key="coin_prestige",
        name="Prestige Mark",
        slot="coin",
        description="Awarded at prestige 5.",
        unlock={"kind": "prestige", "value": 5},
        palette=("#E0AAFF", "#5A189A"),
        glyph="✵",
        rarity="legendary",
        sort=16,
    ),
)

# Defaults a fresh account should already be wearing, so a first /profile (or
# /balance) looks finished instead of empty.
DEFAULT_LOADOUT: dict[str, list[str]] = {
    "banner": ["banner_default"],
    "border": ["border_none"],
    "nameplate": ["plate_default"],
    "badge": [],
    "wallet": ["wallet_default"],
    "coin": ["coin_default"],
}
