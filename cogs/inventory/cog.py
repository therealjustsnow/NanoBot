"""
cogs/inventory.py
Generic item inventory — the shared item layer of the NanoCoin economy.

Items are GLOBAL, exactly like the coin balance: one stack per (user, item),
carried into every server. Everything a member owns that isn't coins or bagged
fish lives here: bait,
consumables, crafting materials, treasure, keys, and event drops. Items are
defined code-side in the shared catalogue (utils/items.py) and stored as
(item_key, qty) stacks, so any economy cog can grant, check, or consume items
without knowing about any other cog. Using a consumable stores an *effect*
(timed buff or charge pack) that the interested cog — fishing, activities,
casino — reads and applies; the inventory itself stays feature-agnostic.

Slash command budget: one group (/inventory …, alias /inv).

──────────────────────────────────────────────────────
Commands
──────────────────────────────────────────────────────
  /inventory [category]         → your items and active effects, grouped by
                                  category with totals (alias: inv)
  /inventory view [category]    → same view (the slash-reachable `fallback`;
                                  a group's own callback can't be invoked over
                                  slash, so without it there was no way to
                                  simply *look* at your items over slash)
  /inventory use <item> [qty]   → use a consumable (applies its effect)
  /inventory sell <item> [qty]  → sell sellable items for coins; `item` also
                                  takes a bulk target — `all` for everything
                                  sellable, or a category (`cat:material`) —
                                  both offered as one-tap rows by the picker.
                                  A bulk target previews what would go and
                                  sells only once the Sell button is pressed.
  /inventory give <member> <item> [qty] → give items to another member
  /inventory info <item>        → what an item is and does
"""

import logging
import random
import time
from typing import Optional

import discord
from discord.ext import commands

from utils import db
from utils import helpers as h
from utils import items as item_catalog

from .constants import (
    CATEGORY_SELL_PREFIX,
    EFFECT_MAX_DURATION,
    EFFECT_MAX_USES,
    MAX_BULK,
    SELL_ALL_ALIASES,
    SELL_CONFIRM_TIMEOUT,
)
from .helpers import chest_payout
from .views import SellConfirmView

log = logging.getLogger("NanoBot.inventory")

ACCENT = 0x8E7CC3


class Inventory(commands.Cog):
    """Own, use, sell, and gift items — the economy's shared item layer."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Per-(guild, user) locks serialize multi-step use/sell flows (the
        # economy /daily pattern) so a double-send can't double-apply.
        self._locks = h.KeyedLocks()
        # One live bulk-sell confirmation per member (transient — nothing is
        # consumed until the button lands, so these need no persistence).
        self._pending_sell: dict[int, SellConfirmView] = {}

    def _lock(self, user_id: int):
        # Returns an async context manager; existing `async with self._lock(...)`
        # call sites are unchanged. KeyedLocks refcounts holder + waiters and
        # drops an entry when the last interested task releases, so the map no
        # longer grows for the lifetime of the process.
        return self._locks.hold(user_id)

    def _money(self, econ: dict, amount: int) -> str:
        return h.fmt_coins(amount, econ["currency_name"], econ["currency_emoji"])

    # ── item pickers ─────────────────────────────────────────────────────────
    # Item names are multi-word ("Lucky Charm") and keys are underscored, so
    # typing one from memory is the worst part of the inventory. Every item
    # option is a tap-to-pick list built from what the member actually owns.
    async def _owned_choices(
        self,
        interaction: discord.Interaction,
        current: str,
        *,
        usable_only: bool = False,
        sellable_only: bool = False,
    ) -> list[discord.app_commands.Choice[str]]:
        q = (current or "").strip().lower()
        stacks = (
            await db.get_inventory(interaction.user.id) if interaction.guild_id else []
        )
        choices: list[discord.app_commands.Choice[str]] = []
        for stack in stacks:
            key = stack["item_key"]
            d = item_catalog.get(key)
            name = d.name if d else key
            if usable_only and not (d and (d.effect or key == "treasure_chest")):
                continue
            if sellable_only and not (d and d.value > 0):
                continue
            if q and q not in key.lower() and q not in name.lower():
                continue
            label = f"{d.emoji if d else '📦'} {name} ×{stack['qty']:,}"
            if sellable_only and d:
                label += f" — {d.value * stack['qty']:,} coins for the lot"
            elif d and d.effect:
                label += f" — {d.effect['key']} +{d.effect.get('magnitude', 0):g}"
            choices.append(discord.app_commands.Choice(name=label[:100], value=key))
        return choices[:25]

    async def _catalog_choices(
        self, interaction: discord.Interaction, current: str
    ) -> list[discord.app_commands.Choice[str]]:
        """Every item that exists — for /inventory info, which works on items
        you don't own yet."""
        q = (current or "").strip().lower()
        choices = []
        for d in item_catalog.ITEMS.values():
            if q and q not in d.key.lower() and q not in d.name.lower():
                continue
            cat = item_catalog.CATEGORY_LABELS.get(d.category, d.category.title())
            choices.append(
                discord.app_commands.Choice(
                    name=f"{d.emoji} {d.name} — {cat}"[:100], value=d.key
                )
            )
        return choices[:25]

    # ══════════════════════════════════════════════════════════════════════════
    #  /inventory group
    # ══════════════════════════════════════════════════════════════════════════
    @commands.hybrid_group(
        name="inventory",
        aliases=["inv"],
        description="Your items: bait, consumables, materials, and treasure.",
        invoke_without_command=True,
        # Without `fallback` the bare item view is prefix-only — Discord can't
        # invoke a group itself, so /inventory offered use/sell/give/info and
        # no way to simply *look* at what you own.
        fallback="view",
        extras={
            "category": "🪙 Economy",
            "sub": "🎒 Items & Crafting",
            "short": "View, use, sell, and gift your items",
            "usage": "inventory [category]",
            "desc": "Everything you own beyond coins lives here: bait, "
            "consumables, crafting materials, treasure, and keys earned from "
            "economy activities. Use consumables for temporary buffs, sell "
            "spare materials for coins (one item, a whole category, or "
            "everything at once), or give items to friends.",
            "args": ["category — show one category only (optional)"],
            "perms": "None",
            "example": "{prefix}inventory\n{prefix}inventory bait\n"
            "{prefix}inventory use lucky charm\n"
            "{prefix}inventory sell iron ore 5\n{prefix}inventory sell all",
        },
    )
    @commands.guild_only()
    @discord.app_commands.describe(category="Show one category only (optional)")
    async def inventory(self, ctx: commands.Context, category: Optional[str] = None):
        if ctx.invoked_subcommand is None:
            await self._show(ctx, category)

    @inventory.autocomplete("category")
    async def _category_ac(self, interaction: discord.Interaction, current: str):
        """Only the categories this member actually owns something in, each with
        its stack count — an empty inventory still lists them so the picker is
        never blank."""
        owned: dict[str, int] = {}
        for stack in await db.get_inventory(interaction.user.id):
            d = item_catalog.get(stack["item_key"])
            owned[d.category if d else "misc"] = (
                owned.get(d.category if d else "misc", 0) + 1
            )
        cur = (current or "").lower()
        out = []
        for cat in item_catalog.CATEGORY_ORDER:
            label = item_catalog.CATEGORY_LABELS.get(cat, cat.title())
            if cur and cur not in cat.lower() and cur not in label.lower():
                continue
            count = owned.get(cat, 0)
            name = f"{label} — {count} item(s)" if count else f"{label} — none yet"
            out.append(discord.app_commands.Choice(name=name[:100], value=cat))
        return out[:25]

    async def _show(self, ctx: commands.Context, category: Optional[str] = None):
        """The inventory overview. `category` narrows it to one catalogue
        category; anything unrecognised is reported rather than silently
        showing everything."""
        wanted = None
        if category:
            key = category.strip().lower().removeprefix("cat:")
            wanted = next(
                (
                    c
                    for c in item_catalog.CATEGORY_ORDER
                    if c == key or item_catalog.CATEGORY_LABELS.get(c, c).lower() == key
                ),
                None,
            )
            if wanted is None:
                names = ", ".join(f"`{c}`" for c in item_catalog.CATEGORY_ORDER)
                return await ctx.reply(
                    embed=h.err(
                        f"There's no **{category}** category. Try one of: {names}."
                    ),
                    ephemeral=True,
                )

        stacks = await db.get_inventory(ctx.author.id)
        effects = await db.get_active_effects(ctx.author.id)
        if not stacks and not effects:
            return await ctx.reply(
                embed=h.info(
                    "Your inventory is empty. Items come from fishing, "
                    "activities like `/mine` and `/adventure hunt`, and economy "
                    "events. Anything you find shows up here.",
                    "🎒 Inventory",
                ),
                ephemeral=True,
            )

        by_cat: dict[str, list[str]] = {}
        total_items = 0
        total_value = 0
        for stack in stacks:
            d = item_catalog.get(stack["item_key"])
            cat = d.category if d else "misc"
            total_items += stack["qty"]
            if d and d.value > 0:
                total_value += d.value * stack["qty"]
            if wanted and cat != wanted:
                continue
            line = f"{item_catalog.display(stack['item_key'])} × **{stack['qty']:,}**"
            if d and d.value > 0:
                line += f" · sells {d.value:,} ea"
            by_cat.setdefault(cat, []).append(line)

        summary = f"**{total_items:,}** item(s) across **{len(stacks)}** stack(s)"
        if total_value:
            summary += f" · worth **{total_value:,}** if you sold the lot"
        if wanted:
            label = item_catalog.CATEGORY_LABELS.get(wanted, wanted.title())
            summary += f"\nShowing **{label}** only — run `/inventory` for everything."
        embed = h.embed(f"🎒 {ctx.author.display_name}'s Inventory", summary, ACCENT)

        for cat in item_catalog.CATEGORY_ORDER:
            if cat not in by_cat:
                continue
            # Discord caps a field at 1024 chars; spill into continuation
            # fields rather than truncating stacks out of sight.
            label = item_catalog.CATEGORY_LABELS.get(cat, cat.title())
            chunk: list[str] = []
            size = 0
            part = 0
            for line in by_cat[cat]:
                if size + len(line) + 1 > 1024 and chunk:
                    part += 1
                    embed.add_field(
                        name=label if part == 1 else f"{label} (cont.)",
                        value="\n".join(chunk),
                        inline=False,
                    )
                    chunk, size = [], 0
                chunk.append(line)
                size += len(line) + 1
            if chunk:
                part += 1
                embed.add_field(
                    name=label if part == 1 else f"{label} (cont.)",
                    value="\n".join(chunk),
                    inline=False,
                )
        if wanted and not by_cat:
            embed.add_field(
                name=item_catalog.CATEGORY_LABELS.get(wanted, wanted.title()),
                value="Nothing here yet.",
                inline=False,
            )
        if effects:
            now = time.time()
            lines = []
            for key, eff in sorted(effects.items()):
                if eff["expires_at"]:
                    left = h.fmt_duration(max(1, int(eff["expires_at"] - now)))
                    lines.append(f"✨ **{key}** +{eff['magnitude']:g} · {left} left")
                else:
                    lines.append(
                        f"✨ **{key}** +{eff['magnitude']:g} · "
                        f"{eff['uses_left']} use(s) left"
                    )
            embed.add_field(
                name="Active Effects", value="\n".join(lines)[:1024], inline=False
            )
        embed.set_footer(
            text="/inventory use · sell · give · info — or /inventory sell all"
        )
        await ctx.reply(embed=embed)

    # ── /inventory use ───────────────────────────────────────────────────────
    @inventory.command(name="use", description="Use a consumable item.")
    @discord.app_commands.describe(
        item="Pick a usable item you own", qty="How many to use at once (default 1)"
    )
    # NOTE: `item`/`qty` are plain positional params, not `*, item, qty` —
    # discord.py's prefix parser only transforms the first keyword-only
    # parameter, which silently pins qty at its default under prefix
    # invocation. Multi-word names need quotes in prefix form ("lucky charm"),
    # or use the underscore key (lucky_charm); slash options are unaffected.
    async def inventory_use(
        self, ctx: commands.Context, item: str, qty: Optional[int] = 1
    ):
        d = item_catalog.find(item)
        if d is None:
            return await ctx.reply(
                embed=h.err(
                    f"I don't know any item called **{item}**. Run `/inventory` to "
                    "see what you own."
                ),
                ephemeral=True,
            )
        qty = max(1, min(int(qty or 1), MAX_BULK))
        async with self._lock(ctx.author.id):
            if d.key == "treasure_chest":
                return await self._open_chest(ctx, qty)
            if not d.effect:
                return await ctx.reply(
                    embed=h.warn(f"{item_catalog.display(d.key)} can't be used."),
                    ephemeral=True,
                )
            # Stacking cap: qty multiplies the granted duration/charges, so
            # clamp qty to whatever fits under the per-command effect ceiling
            # (never below 1 — a single big item is always usable). Only the
            # clamped qty is consumed, so no items are eaten without effect.
            per_uses = int(d.effect.get("uses", 0))
            per_duration = float(d.effect.get("duration", 0))
            if per_duration > 0:
                qty = min(qty, max(1, int(EFFECT_MAX_DURATION // per_duration)))
            elif per_uses > 0:
                qty = min(qty, max(1, EFFECT_MAX_USES // per_uses))
            uses = per_uses * qty
            duration = per_duration * qty
            if not await db.try_consume_item(ctx.author.id, d.key, qty):
                have = await db.get_item_qty(ctx.author.id, d.key)
                return await ctx.reply(
                    embed=h.err(
                        f"You need **{qty}** × {item_catalog.display(d.key)} "
                        f"but only have **{have}**."
                    ),
                    ephemeral=True,
                )
            await db.grant_effect(
                ctx.author.id,
                d.effect["key"],
                float(d.effect.get("magnitude", 0)),
                duration=duration,
                uses=uses,
            )
        if duration:
            detail = f"active for **{h.fmt_duration(int(duration))}**"
        else:
            detail = f"**{uses}** use(s) ready"
        await ctx.reply(
            embed=h.ok(
                f"Used {qty} × {item_catalog.display(d.key)} — "
                f"`{d.effect['key']}` effect {detail}.",
                "✨ Item Used",
            )
        )

    @inventory_use.autocomplete("item")
    async def _use_ac(self, interaction: discord.Interaction, current: str):
        return await self._owned_choices(interaction, current, usable_only=True)

    async def _open_chest(self, ctx: commands.Context, qty: int):
        """Chests need a key each: consume chest+key pairs, pay coins."""
        uid = ctx.author.id
        keys = await db.get_item_qty(uid, "treasure_key")
        chests = await db.get_item_qty(uid, "treasure_chest")
        qty = min(qty, keys, chests)
        if qty <= 0:
            return await ctx.reply(
                embed=h.warn(
                    "Opening a chest takes one 🧰 Treasure Chest **and** one "
                    "🗝️ Treasure Key. You need at least one of each."
                ),
                ephemeral=True,
            )
        if not await db.try_consume_item(uid, "treasure_chest", qty):
            return await ctx.reply(
                embed=h.err("Those chests just vanished — try again.")
            )
        if not await db.try_consume_item(uid, "treasure_key", qty):
            await db.add_item(uid, "treasure_chest", qty)
            return await ctx.reply(embed=h.err("Your keys just vanished — try again."))
        coins = sum(chest_payout(random.random()) for _ in range(qty))
        await db.add_coins(uid, coins)
        econ = await db.get_econ_config(ctx.guild.id)  # currency label only
        await ctx.reply(
            embed=h.embed(
                "🧰 Chest Opened!" if qty == 1 else f"🧰 {qty} Chests Opened!",
                f"Inside you find {self._money(econ, coins)}!",
                0xF1C40F,
            )
        )

    # ── /inventory sell ──────────────────────────────────────────────────────
    @staticmethod
    def _sell_category(item: str) -> Optional[str]:
        """The catalogue category a bulk sell target names, or None.

        Accepts the explicit `cat:material` form the picker hands back and a
        bare category name. Only consulted after an item lookup misses, so an
        item can never be shadowed by a category.
        """
        q = (item or "").strip().lower()
        if q.startswith(CATEGORY_SELL_PREFIX):
            q = q[len(CATEGORY_SELL_PREFIX) :].strip()
        return q if q in item_catalog.CATEGORY_ORDER else None

    @inventory.command(
        name="sell",
        description="Sell items for coins — one item, a whole category, or everything.",
    )
    @discord.app_commands.describe(
        item="Pick a sellable item, a whole category, or everything you own",
        qty="How many to sell (default: all of them; with a bulk pick, per item)",
    )
    async def inventory_sell(
        self, ctx: commands.Context, item: str, qty: Optional[int] = None
    ):
        if (item or "").strip().lower() in SELL_ALL_ALIASES:
            return await self._sell_bulk(ctx, None, qty)
        d = item_catalog.find(item)
        if d is None:
            category = self._sell_category(item)
            if category is not None:
                return await self._sell_bulk(ctx, category, qty)
            return await ctx.reply(
                embed=h.err(
                    f"I don't know any item called **{item}**. Run `/inventory` to "
                    "see what you own, or sell everything at once with "
                    "`/inventory sell all`."
                ),
                ephemeral=True,
            )
        if d.value <= 0:
            return await ctx.reply(
                embed=h.warn(f"{item_catalog.display(d.key)} can't be sold."),
                ephemeral=True,
            )
        async with self._lock(ctx.author.id):
            have = await db.get_item_qty(ctx.author.id, d.key)
            count = have if qty is None else max(1, min(int(qty), MAX_BULK))
            if count <= 0 or not await db.try_consume_item(ctx.author.id, d.key, count):
                return await ctx.reply(
                    embed=h.err(
                        f"You have **{have}** × {item_catalog.display(d.key)} to sell."
                    ),
                    ephemeral=True,
                )
            coins = d.value * count
            await db.add_coins(ctx.author.id, coins)
        econ = await db.get_econ_config(ctx.guild.id)
        await ctx.reply(
            embed=h.ok(
                f"Sold **{count}** × {item_catalog.display(d.key)} for "
                f"{self._money(econ, coins)}.",
                "💰 Sold",
            )
        )

    # ── bulk sell (all / one category) ───────────────────────────────────────
    @staticmethod
    def _bulk_label(category: Optional[str]) -> str:
        return (
            item_catalog.CATEGORY_LABELS.get(category, category.title())
            if category
            else "your inventory"
        )

    @staticmethod
    def _bulk_plan(
        stacks: list[dict], category: Optional[str], qty: Optional[int]
    ) -> list[tuple[str, int, int]]:
        """What a bulk sell would move: [(item_key, count, coins), …].

        Pure over a stack list, so the preview and the sale that follows it
        agree on the rules — the sale simply runs it against a fresh read.
        """
        per_stack = None if qty is None else max(1, min(int(qty), MAX_BULK))
        plan = []
        for stack in stacks:
            d = item_catalog.get(stack["item_key"])
            if d is None or d.value <= 0:
                continue
            if category is not None and d.category != category:
                continue
            count = min(stack["qty"], MAX_BULK)
            if per_stack is not None:
                count = min(count, per_stack)
            if count > 0:
                plan.append((d.key, count, d.value * count))
        return plan

    @staticmethod
    def _plan_field(plan: list[tuple[str, int, int]]) -> str:
        """The item breakdown, trimmed to fit an embed field's 1024-char cap."""
        lines = [
            f"{item_catalog.display(key)} × **{count:,}** — {coins:,}"
            for key, count, coins in plan
        ]
        body, used = [], 0
        for line in lines:
            if used + len(line) + 1 > 950:
                break
            body.append(line)
            used += len(line) + 1
        if len(body) < len(lines):
            body.append(f"…and **{len(lines) - len(body)}** more")
        return "\n".join(body)

    async def _sell_bulk(
        self, ctx: commands.Context, category: Optional[str], qty: Optional[int]
    ):
        """Preview a bulk sell and wait for confirmation.

        Nothing is consumed here — a bulk sell empties whole stacks, including
        treasure someone may have been saving, so it always shows what would go
        and sells only when the member presses the button.
        """
        plan = self._bulk_plan(await db.get_inventory(ctx.author.id), category, qty)
        if not plan:
            return await ctx.reply(
                embed=h.warn(
                    f"Nothing in {self._bulk_label(category)} can be sold right "
                    "now. Materials and treasure from fishing, mining, and "
                    "hunting are what sell.",
                    "🎒 Nothing to Sell",
                ),
                ephemeral=True,
            )
        econ = await db.get_econ_config(ctx.guild.id)
        count = sum(n for _, n, _ in plan)
        total = sum(c for _, _, c in plan)
        embed = h.warn(
            f"This sells **{count:,}** items from {self._bulk_label(category)} "
            f"for {self._money(econ, total)}.\n"
            "Items that can't be sold (keys, consumables) stay put.",
            "💰 Sell these?",
        )
        embed.add_field(name="What will sell", value=self._plan_field(plan))
        embed.set_footer(
            text=f"Nothing is sold until you press Sell · expires in "
            f"{h.fmt_duration(int(SELL_CONFIRM_TIMEOUT))}"
        )
        view = SellConfirmView(
            self,
            user_id=ctx.author.id,
            guild_id=ctx.guild.id,
            category=category,
            qty=qty,
            timeout=SELL_CONFIRM_TIMEOUT,
        )
        # One pending confirmation per member: a second bulk sell retires the
        # first, so a stale preview can't be pressed later against a changed
        # inventory.
        previous = self._pending_sell.get(ctx.author.id)
        if previous is not None:
            await previous.cancel_quietly()
        self._pending_sell[ctx.author.id] = view
        view.message = await ctx.reply(embed=embed, view=view)

    def forget_pending_sell(self, user_id: int, view) -> None:
        """Drop a finished confirmation (called by the view on settle/timeout)."""
        if self._pending_sell.get(user_id) is view:
            self._pending_sell.pop(user_id, None)

    async def execute_bulk_sell(
        self,
        user_id: int,
        guild_id: int,
        category: Optional[str],
        qty: Optional[int],
    ) -> discord.Embed:
        """Run a confirmed bulk sell and return the result embed.

        Each stack is spent with the same atomic try_consume_item as a single
        sell, so a stack drained since the preview is skipped rather than paid
        for, and the coins are credited once for the whole run.
        """
        sold: list[tuple[str, int, int]] = []
        total = new_bal = 0
        async with self._lock(user_id):
            plan = self._bulk_plan(await db.get_inventory(user_id), category, qty)
            for key, count, coins in plan:
                if not await db.try_consume_item(user_id, key, count):
                    continue
                total += coins
                sold.append((key, count, coins))
            if total:
                new_bal = await db.add_coins(user_id, total)
        if not sold:
            return h.warn(
                f"Nothing in {self._bulk_label(category)} was left to sell.",
                "🎒 Nothing to Sell",
            )
        econ = await db.get_econ_config(guild_id)
        embed = h.ok(
            f"Sold **{sum(n for _, n, _ in sold):,}** items from "
            f"{self._bulk_label(category)} for {self._money(econ, total)}.\n"
            f"Balance: {self._money(econ, new_bal)}",
            "💰 Sold",
        )
        embed.add_field(name="What sold", value=self._plan_field(sold), inline=False)
        return embed

    @inventory_sell.autocomplete("item")
    async def _sell_ac(self, interaction: discord.Interaction, current: str):
        """Sellable stacks, with one-tap bulk rows on top: everything sellable
        first, then each category you actually have something in."""
        q = (current or "").strip().lower()
        stacks = (
            await db.get_inventory(interaction.user.id) if interaction.guild_id else []
        )
        sellable = []
        for stack in stacks:
            d = item_catalog.get(stack["item_key"])
            if d and d.value > 0:
                sellable.append((d, stack["qty"]))
        choices: list[discord.app_commands.Choice[str]] = []
        if sellable:
            if not q or q in "everything" or q in "all":
                count = sum(n for _, n in sellable)
                worth = sum(d.value * n for d, n in sellable)
                choices.append(
                    discord.app_commands.Choice(
                        name=f"💰 Everything sellable — {count:,} items "
                        f"for {worth:,} coins"[:100],
                        value="all",
                    )
                )
            by_cat: dict[str, list[int]] = {}
            for d, n in sellable:
                tally = by_cat.setdefault(d.category, [0, 0])
                tally[0] += n
                tally[1] += d.value * n
            # One category is what "everything" already covers — don't repeat it.
            if len(by_cat) > 1:
                for cat in item_catalog.CATEGORY_ORDER:
                    if cat not in by_cat:
                        continue
                    label = item_catalog.CATEGORY_LABELS.get(cat, cat.title())
                    if q and q not in cat and q not in label.lower():
                        continue
                    count, worth = by_cat[cat]
                    choices.append(
                        discord.app_commands.Choice(
                            name=f"📦 All {label} — {count:,} items "
                            f"for {worth:,} coins"[:100],
                            value=f"{CATEGORY_SELL_PREFIX}{cat}",
                        )
                    )
        choices.extend(
            await self._owned_choices(interaction, current, sellable_only=True)
        )
        return choices[:25]

    # ── /inventory give ──────────────────────────────────────────────────────
    @inventory.command(name="give", description="Give items to another member.")
    @discord.app_commands.describe(
        member="Who to give items to",
        item="Pick an item you own",
        qty="How many to give (default 1)",
    )
    async def inventory_give(
        self,
        ctx: commands.Context,
        member: discord.Member,
        item: str,
        qty: Optional[int] = 1,
    ):
        if member.bot or member.id == ctx.author.id:
            return await ctx.reply(
                embed=h.err("You can only give items to another (human) member."),
                ephemeral=True,
            )
        d = item_catalog.find(item)
        if d is None:
            return await ctx.reply(
                embed=h.err(f"I don't know any item called **{item}**."),
                ephemeral=True,
            )
        qty = max(1, min(int(qty or 1), MAX_BULK))
        if not await db.transfer_item(ctx.author.id, member.id, d.key, qty):
            have = await db.get_item_qty(ctx.author.id, d.key)
            return await ctx.reply(
                embed=h.err(
                    f"You have **{have}** × {item_catalog.display(d.key)} — "
                    f"not enough to give **{qty}**."
                ),
                ephemeral=True,
            )
        await ctx.reply(
            embed=h.ok(
                f"Gave **{qty}** × {item_catalog.display(d.key)} to "
                f"{member.mention}.",
                "🎁 Gift Sent",
            )
        )

    @inventory_give.autocomplete("item")
    async def _give_ac(self, interaction: discord.Interaction, current: str):
        return await self._owned_choices(interaction, current)

    # ── /inventory info ──────────────────────────────────────────────────────
    @inventory.command(name="info", description="See what an item is and does.")
    @discord.app_commands.describe(item="Pick any item to look up")
    async def inventory_info(self, ctx: commands.Context, *, item: str):
        d = item_catalog.find(item)
        if d is None:
            return await ctx.reply(
                embed=h.err(
                    f"I don't know any item called **{item}**. Pick one from the "
                    "list, or run `/inventory` to see what you own."
                ),
                ephemeral=True,
            )
        embed = h.embed(
            f"{d.emoji} {d.name}", d.description or "A mysterious item.", ACCENT
        )
        embed.add_field(
            name="Category",
            value=item_catalog.CATEGORY_LABELS.get(d.category, d.category.title()),
        )
        if d.value > 0:
            embed.add_field(name="Sell Price", value=f"{d.value:,} coins")
        if d.effect:
            if d.effect.get("duration"):
                eff = (
                    f"`{d.effect['key']}` +{d.effect.get('magnitude', 0):g} for "
                    f"{h.fmt_duration(int(d.effect['duration']))}"
                )
            else:
                eff = (
                    f"`{d.effect['key']}` +{d.effect.get('magnitude', 0):g} × "
                    f"{d.effect.get('uses', 0)} use(s)"
                )
            embed.add_field(name="On Use", value=eff)
        await ctx.reply(embed=embed)

    @inventory_info.autocomplete("item")
    async def _info_ac(self, interaction: discord.Interaction, current: str):
        return await self._catalog_choices(interaction, current)


async def setup(bot: commands.Bot):
    await bot.add_cog(Inventory(bot))
