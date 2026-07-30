"""Price-refund batches: who is owed what when a ladder gets cheaper.

A member who bought the Obsidian Pickaxe at 100,000 and woke up to find it costs
24,000 has been charged for a mistake in the balance model rather than for
anything they did. There is no receipt to reverse — the bot stores the tier you
own, not what you handed over — so a refund is reconstructed from what a member
owns against the tables below.

Why these numbers are written out rather than computed
──────────────────────────────────────────────────────
It is tempting to derive a refund from `PICKAXES`/`RODS`/`SPOTS` at runtime. That
would be wrong the moment a price moves again: a batch is a statement about what
things cost *on a particular day*, and the live tables only ever know today's
answer. Frozen numbers also make the manifest reviewable — every figure below can
be checked against the commit that changed it, which is not true of a number
assembled at import time.

What a batch may contain
────────────────────────
Only *reductions*. A price rise is never clawed back: the member agreed to what
was on the label, and taking coins out of a wallet to correct our own balance
work would be a far worse trade than over-paying a few early adopters.

The known imprecision
─────────────────────
We cannot tell when a tier was bought, and these ladders got *more* expensive on
26 July before getting cheaper. Someone who bought the Obsidian Pickaxe at its
original 25,000 is refunded the full 76,000 difference and comes out ahead. That
is a deliberate choice and the only one available that doesn't punish the much
larger group who genuinely overpaid: the refund is a goodwill gesture, and a
goodwill gesture that shortchanges most people to avoid over-paying a few is not
one. The exposure is bounded by how few members own a top tier at all.

Adding a batch
──────────────
Append a `RefundBatch` with a new `key` (the key is what makes it once-only —
see utils/db/refunds.py) and the per-tier differences. The sweep in cog.py picks
it up on the next start, and everything else — idempotency, delivery, retry — is
already handled.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class RefundItem:
    """One thing that got cheaper, and what its owners are owed for it.

    `kind` decides how ownership is read: a `pickaxe`/`rod` entry pays anyone at
    *or above* that tier index (owning tier 3 means having paid for 1, 2 and 3),
    while a `spot` entry pays whoever holds that charter, since charters are
    bought individually rather than as a ladder.

    `label` is the item's name on the day of the change, not a lookup. A DM that
    says "abyss: 150,000" reads like a database leaked; and a name resolved at
    send time would go wrong the moment something is renamed or retired.

    Both prices are recorded rather than just their difference, which costs a
    number and buys the only guard available against the silent failure mode
    here: `now` is checked against the live ladder, so repricing something and
    forgetting to add a batch fails CI instead of quietly shortchanging whoever
    already bought it (see tests/test_refunds_manifest.py).
    """

    kind: str
    ref: int | str
    was: int
    now: int
    label: str

    @property
    def amount(self) -> int:
        """What an owner is owed. Never negative — a price *rise* is not a debt
        the member has to us, and a batch that contains one is a mistake rather
        than a bill."""
        return max(0, self.was - self.now)

    def owned_by(self, pickaxe_level: int, rod_level: int, spots: set[str]) -> bool:
        if self.kind == "pickaxe":
            return pickaxe_level >= self.ref
        if self.kind == "rod":
            return rod_level >= self.ref
        return self.ref in spots


@dataclass(frozen=True)
class RefundBatch:
    """One repricing event and everything it made cheaper."""

    key: str
    summary: str
    items: list[RefundItem] = field(default_factory=list)

    def kinds(self) -> set[str]:
        """Which ownership tables this batch actually needs to read."""
        return {item.kind for item in self.items}

    def owed(
        self,
        pickaxe_level: int = 0,
        rod_level: int = 0,
        spots: set[str] | None = None,
    ) -> tuple[int, list[str]]:
        """Coins owed to one member, and a readable line per item.

        The lines are what the DM's breakdown is built from: a bare "you have
        been refunded 93,500" reads like an accident rather than a correction,
        and gives nobody a way to check it.
        """
        total = 0
        detail: list[str] = []
        for item in self.items:
            if item.amount > 0 and item.owned_by(
                pickaxe_level, rod_level, spots or set()
            ):
                total += item.amount
                detail.append(f"{item.label} — {item.amount:,}")
        return total, detail


# ── The batches, oldest first ────────────────────────────────────────────────
# 2026-07-30. Two repricings land together because neither had been paid out:
#
#   * the pickaxe ladder, repriced against mining's own throughput rather than
#     the rod curve it had been copied from (tiers 3/4/5: 4,000 → 3,000,
#     24,000 → 7,500, 100,000 → 24,000);
#   * the two endgame goals trimmed the day before, when every price was re-cut
#     against days of ordinary play rather than hours of grinding (Mythic
#     Trident 450,000 → 380,000, the Abyssal Trench charter 600,000 → 450,000).
#
# A batch names spots by their registry key, so `abyss` here is the Abyssal
# Trench in cogs/fishing/spots.py — the sweep matches on the stored key, and a
# key that no longer exists simply never matches anyone.
#
# Tier indexes are into PICKAXES/RODS, where index 0 is the free starting gear —
# so `2` is the third entry in the list and the second thing anyone buys.
REFUND_BATCHES: list[RefundBatch] = [
    RefundBatch(
        key="2026-07-ladder-reprice",
        summary=(
            "We rebalanced the pickaxe ladder and trimmed the two most "
            "expensive goals in the game, so a few things you already own now "
            "cost less than you paid."
        ),
        items=[
            RefundItem("pickaxe", 2, 4_000, 3_000, "⛏️ Iron Pickaxe"),
            RefundItem("pickaxe", 3, 24_000, 7_500, "⛏️ Steel Pickaxe"),
            RefundItem("pickaxe", 4, 100_000, 24_000, "⛏️ Obsidian Pickaxe"),
            RefundItem("rod", 5, 450_000, 380_000, "🔱 Mythic Trident"),
            RefundItem("spot", "abyss", 600_000, 450_000, "🌊 Abyssal Trench charter"),
        ],
    ),
]
