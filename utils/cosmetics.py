"""utils/cosmetics.py — the profile cosmetics catalogue (badges, banners, …).

The same shape as ``utils/items.py``: definitions live in code (or in a JSON
file, see ``load_file``), the database only ever stores keys. Adding a cosmetic
never touches the schema, the renderer, or a command.

Three things make this extensible on purpose:

1. **Slots are data.** ``SLOTS`` maps a slot key to how many can be equipped at
   once and how the card treats it. A future "pet", "effect", or "frame" slot
   is one entry here — ``/profile equip`` and the card loop over the registry.
2. **Unlock rules are data.** Each def carries an ``unlock`` dict —
   ``{"kind": "global_level", "value": 10}``, ``{"kind": "achievement",
   "key": "fish_caught_100"}``, ``{"kind": "prestige", "value": 3}``,
   ``{"kind": "stat", "stat": "casino_games", "value": 500}``, or
   ``{"kind": "manual"}`` for event/admin grants. ``is_unlocked`` evaluates all
   of them against one context dict, so a new rule kind is one branch.
3. **Art is optional.** A def declares a palette + a glyph; the renderer draws
   clean vector-style art from those if no file exists at
   ``assets/profile/<slot>/<key>.png``. Drop a real PNG in later and it wins,
   with no code change — which is exactly the "don't spend time on artwork
   now" trade.

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


# How many of each cosmetic can be worn at once. Adding a slot here makes it
# equippable, listable, and (if the card knows how to draw it) renderable.
SLOTS: dict[str, SlotDef] = {
    "banner": SlotDef("banner", "Banner", 1, "The artwork behind your card."),
    "border": SlotDef("border", "Border", 1, "The frame around your card."),
    "nameplate": SlotDef("nameplate", "Nameplate", 1, "The plate behind your name."),
    "badge": SlotDef("badge", "Badges", 6, "Up to six badges shown on your card."),
}


@dataclass(frozen=True)
class CosmeticDef:
    key: str
    name: str
    slot: str
    description: str = ""
    # Unlock rule — see the module docstring. Manual means "granted by an
    # admin or an event", which is how limited/seasonal drops work.
    unlock: dict = field(default_factory=lambda: {"kind": "manual"})
    # Colours the generated art uses (hex). Two colours = gradient.
    palette: tuple[str, ...] = ("#5865F2", "#8B5CF6")
    # Drawn on generated badge art when there's no PNG for this key. Keep to
    # glyphs the bundled font covers (geometric/dingbat ranges) — colour emoji
    # render as tofu boxes in Pillow. tests/test_cosmetics.py guards this.
    glyph: str = "★"
    rarity: str = "common"
    sort: int = 0

    @property
    def asset_name(self) -> str:
        return f"{self.slot}/{self.key}.png"


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
    if kind == "default":
        return "Available to everyone"
    return "Awarded by staff or during an event"


def is_unlocked(d: CosmeticDef, ctx: dict) -> bool:
    """Does `ctx` satisfy this cosmetic's unlock rule?

    `ctx` carries whatever the caller has already loaded:
    ``{"global_level": int, "prestige": int, "achievements": set[str],
    "stats": {stat_key: value}}``. Unknown rule kinds return False — a manual
    grant is the only way to hold them, which is the safe default for
    event/staff cosmetics.
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
    """Cosmetics that can be earned by playing (everything except manual-only
    grants) — the set the profile cog re-evaluates when someone opens a card."""
    return [d for d in COSMETICS.values() if (d.unlock or {}).get("kind") != "manual"]


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
        sort=10,
    ),
    CosmeticDef(
        key="banner_ember",
        name="Ember",
        slot="banner",
        description="Unlocked at global level 25.",
        unlock={"kind": "global_level", "value": 25},
        palette=("#42160F", "#C1440E"),
        sort=20,
    ),
    CosmeticDef(
        key="banner_aurora",
        name="Aurora",
        slot="banner",
        description="Unlocked at global level 50.",
        unlock={"kind": "global_level", "value": 50},
        palette=("#0B486B", "#3B8686"),
        sort=30,
    ),
    CosmeticDef(
        key="banner_royal",
        name="Royal Velvet",
        slot="banner",
        description="For prestiged accounts.",
        unlock={"kind": "prestige", "value": 1},
        palette=("#2C0735", "#7B2CBF"),
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
        sort=10,
    ),
    CosmeticDef(
        key="border_gold",
        name="Gilded Frame",
        slot="border",
        description="Unlocked at global level 40.",
        unlock={"kind": "global_level", "value": 40},
        palette=("#B8860B", "#FFD700"),
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
        description="Held 1,000,000 coins.",
        unlock={"kind": "stat", "stat": "balance", "value": 1_000_000},
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

# Defaults a fresh account should already be wearing, so a first /profile looks
# finished instead of empty.
DEFAULT_LOADOUT: dict[str, list[str]] = {
    "banner": ["banner_default"],
    "border": ["border_none"],
    "nameplate": ["plate_default"],
    "badge": [],
}
