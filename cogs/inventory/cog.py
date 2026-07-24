"""
cogs/inventory.py
Generic item inventory — the shared item layer of the NanoCoin economy.

Everything a member owns that isn't coins or bagged fish lives here: bait,
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
  /inventory                    → your items and active effects (alias: inv)
  /inventory use <item> [qty]   → use a consumable (applies its effect)
  /inventory sell <item> [qty]  → sell sellable items for coins
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

from .constants import EFFECT_MAX_DURATION, EFFECT_MAX_USES, MAX_BULK
from .helpers import chest_payout

log = logging.getLogger("NanoBot.inventory")

ACCENT = 0x8E7CC3


class Inventory(commands.Cog):
    """Own, use, sell, and gift items — the economy's shared item layer."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Per-(guild, user) locks serialize multi-step use/sell flows (the
        # economy /daily pattern) so a double-send can't double-apply.
        self._locks = h.KeyedLocks()

    def _lock(self, guild_id: int, user_id: int):
        # Returns an async context manager; existing `async with self._lock(...)`
        # call sites are unchanged. KeyedLocks refcounts holder + waiters and
        # drops an entry when the last interested task releases, so the map no
        # longer grows for the lifetime of the process.
        return self._locks.hold((guild_id, user_id))

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
            await db.get_inventory(interaction.guild_id, interaction.user.id)
            if interaction.guild_id
            else []
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
        extras={
            "category": "🪙 Economy",
            "short": "View, use, sell, and gift your items",
            "usage": "inventory [subcommand]",
            "desc": "Everything you own beyond coins lives here: bait, "
            "consumables, crafting materials, treasure, and keys earned from "
            "economy activities. Use consumables for temporary buffs, sell "
            "spare materials for coins, or give items to friends.",
            "args": [],
            "perms": "None",
            "example": "{prefix}inventory\n{prefix}inventory use lucky charm\n"
            "{prefix}inventory sell iron ore 5",
        },
    )
    @commands.guild_only()
    async def inventory(self, ctx: commands.Context):
        if ctx.invoked_subcommand is None:
            await self._show(ctx)

    async def _show(self, ctx: commands.Context):
        stacks = await db.get_inventory(ctx.guild.id, ctx.author.id)
        effects = await db.get_active_effects(ctx.guild.id, ctx.author.id)
        if not stacks and not effects:
            return await ctx.reply(
                embed=h.info(
                    "Your inventory is empty. Items come from fishing, "
                    "activities like /mine and /hunt, and economy events.",
                    "🎒 Inventory",
                ),
                ephemeral=True,
            )
        by_cat: dict[str, list[str]] = {}
        for stack in stacks:
            d = item_catalog.get(stack["item_key"])
            cat = d.category if d else "misc"
            line = f"{item_catalog.display(stack['item_key'])} × **{stack['qty']:,}**"
            if d and d.value > 0:
                line += f" · sells {d.value:,} ea"
            by_cat.setdefault(cat, []).append(line)
        embed = h.embed(f"🎒 {ctx.author.display_name}'s Inventory", "", ACCENT)
        for cat in item_catalog.CATEGORY_ORDER:
            if cat in by_cat:
                embed.add_field(
                    name=item_catalog.CATEGORY_LABELS.get(cat, cat.title()),
                    value="\n".join(by_cat[cat])[:1024],
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
        async with self._lock(ctx.guild.id, ctx.author.id):
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
            if not await db.try_consume_item(ctx.guild.id, ctx.author.id, d.key, qty):
                have = await db.get_item_qty(ctx.guild.id, ctx.author.id, d.key)
                return await ctx.reply(
                    embed=h.err(
                        f"You need **{qty}** × {item_catalog.display(d.key)} "
                        f"but only have **{have}**."
                    ),
                    ephemeral=True,
                )
            await db.grant_effect(
                ctx.guild.id,
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
        gid, uid = ctx.guild.id, ctx.author.id
        keys = await db.get_item_qty(gid, uid, "treasure_key")
        chests = await db.get_item_qty(gid, uid, "treasure_chest")
        qty = min(qty, keys, chests)
        if qty <= 0:
            return await ctx.reply(
                embed=h.warn(
                    "Opening a chest takes one 🧰 Treasure Chest **and** one "
                    "🗝️ Treasure Key. You need at least one of each."
                ),
                ephemeral=True,
            )
        if not await db.try_consume_item(gid, uid, "treasure_chest", qty):
            return await ctx.reply(
                embed=h.err("Those chests just vanished — try again.")
            )
        if not await db.try_consume_item(gid, uid, "treasure_key", qty):
            await db.add_item(gid, uid, "treasure_chest", qty)
            return await ctx.reply(embed=h.err("Your keys just vanished — try again."))
        coins = sum(chest_payout(random.random()) for _ in range(qty))
        await db.add_coins(gid, uid, coins)
        econ = await db.get_econ_config(gid)
        await ctx.reply(
            embed=h.embed(
                "🧰 Chest Opened!" if qty == 1 else f"🧰 {qty} Chests Opened!",
                f"Inside you find {self._money(econ, coins)}!",
                0xF1C40F,
            )
        )

    # ── /inventory sell ──────────────────────────────────────────────────────
    @inventory.command(
        name="sell", description="Sell items from your inventory for coins."
    )
    @discord.app_commands.describe(
        item="Pick a sellable item you own",
        qty="How many to sell (default: all of them)",
    )
    async def inventory_sell(
        self, ctx: commands.Context, item: str, qty: Optional[int] = None
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
        if d.value <= 0:
            return await ctx.reply(
                embed=h.warn(f"{item_catalog.display(d.key)} can't be sold."),
                ephemeral=True,
            )
        async with self._lock(ctx.guild.id, ctx.author.id):
            have = await db.get_item_qty(ctx.guild.id, ctx.author.id, d.key)
            count = have if qty is None else max(1, min(int(qty), MAX_BULK))
            if count <= 0 or not await db.try_consume_item(
                ctx.guild.id, ctx.author.id, d.key, count
            ):
                return await ctx.reply(
                    embed=h.err(
                        f"You have **{have}** × {item_catalog.display(d.key)} to sell."
                    ),
                    ephemeral=True,
                )
            coins = d.value * count
            await db.add_coins(ctx.guild.id, ctx.author.id, coins)
        econ = await db.get_econ_config(ctx.guild.id)
        await ctx.reply(
            embed=h.ok(
                f"Sold **{count}** × {item_catalog.display(d.key)} for "
                f"{self._money(econ, coins)}.",
                "💰 Sold",
            )
        )

    @inventory_sell.autocomplete("item")
    async def _sell_ac(self, interaction: discord.Interaction, current: str):
        return await self._owned_choices(interaction, current, sellable_only=True)

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
        if not await db.transfer_item(
            ctx.guild.id, ctx.author.id, member.id, d.key, qty
        ):
            have = await db.get_item_qty(ctx.guild.id, ctx.author.id, d.key)
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
