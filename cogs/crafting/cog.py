"""
cogs/crafting/cog.py
Crafting — turn raw materials into refined items using the shared inventory.

Crafting rides the existing generic inventory layer (utils/items.py +
utils/db/items.py): it consumes item stacks a member already owns — ores from
/mine, pelts/meat from /hunt, bait from /fish, keys/charms from /explore — and
grants a crafted output, which is itself just another item usable through
/inventory use|sell|give. Recipes are a registry (cogs/crafting/recipes.py):
adding new content is a single entry there, never a change to this file.

Crafting is entirely OPTIONAL for progression — nothing else in the economy
requires a crafted item to function. It's a bonus sink for material
stockpiles and a source of a few convenience effects and collectibles, not a
gate on any other feature.

Slash command budget: one group (/craft …) — subcommands cost no extra
top-level slots.

──────────────────────────────────────────────────────
Commands
──────────────────────────────────────────────────────
  /craft                      → list every recipe with a craftable-now marker
  /craft make <recipe> [qty]  → craft an item (consumes materials, grants output)
  /craft info <recipe>        → a recipe's inputs, output, and what it does
"""

import asyncio
import logging
from typing import Optional

import discord
from discord.ext import commands

from utils import db
from utils import helpers as h
from utils import items as item_catalog

from . import items as _crafting_items  # noqa: F401 - registers craft_* ItemDefs
from .helpers import clamp_craft_qty, find_recipe, missing_inputs
from .recipes import RECIPES, RecipeDef

log = logging.getLogger("NanoBot.crafting")

ACCENT = 0x8E7CC3


class Crafting(commands.Cog):
    """Craft new items from materials earned elsewhere in the economy."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Per-(guild, user) locks serialize multi-step craft flows (the
        # economy /daily pattern) so a double-send can't double-consume or
        # race the refund-on-failure path.
        self._locks: dict[tuple[int, int], asyncio.Lock] = {}

    def _lock(self, guild_id: int, user_id: int) -> asyncio.Lock:
        key = (guild_id, user_id)
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        return lock

    def _fmt_inputs(self, recipe: RecipeDef, qty: int = 1) -> str:
        parts = [
            f"{item_catalog.display(item_key)} × **{need * qty:,}**"
            for item_key, need in recipe.inputs.items()
        ]
        return ", ".join(parts)

    def _fmt_effect(self, effect: dict) -> str:
        if effect.get("duration"):
            return (
                f"`{effect['key']}` +{effect.get('magnitude', 0):g} for "
                f"{h.fmt_duration(int(effect['duration']))}"
            )
        return (
            f"`{effect['key']}` +{effect.get('magnitude', 0):g} × "
            f"{effect.get('uses', 0)} use(s)"
        )

    # ══════════════════════════════════════════════════════════════════════════
    #  /craft group
    # ══════════════════════════════════════════════════════════════════════════
    @commands.hybrid_group(
        name="craft",
        description="Craft items from materials in your inventory.",
        invoke_without_command=True,
        extras={
            "category": "🪙 Economy",
            "short": "Craft new items from your materials",
            "usage": "craft [subcommand]",
            "desc": "Turn materials earned from fishing, mining, hunting, and "
            "exploring into consumables, collectibles, or other economy items "
            "using a recipe. Purely optional — nothing else in the economy "
            "requires a crafted item.",
            "args": [],
            "perms": "None",
            "example": "{prefix}craft\n{prefix}craft make campfire_feast\n"
            "{prefix}craft info gem_ring",
        },
    )
    @commands.guild_only()
    async def craft(self, ctx: commands.Context):
        if ctx.invoked_subcommand is None:
            await self._list(ctx)

    async def _list(self, ctx: commands.Context):
        stacks = await db.get_inventory(ctx.guild.id, ctx.author.id)
        inventory = {s["item_key"]: s["qty"] for s in stacks}
        lines = []
        for key in sorted(RECIPES):
            recipe = RECIPES[key]
            mark = "✅" if not missing_inputs(recipe, inventory) else "❌"
            out_disp = item_catalog.display(recipe.output_item)
            lines.append(
                f"{mark} **{key}** → {out_disp} × {recipe.output_qty}\n"
                f"┕ Needs: {self._fmt_inputs(recipe)}"
            )
        embed = h.embed(
            "🛠️ Crafting Recipes",
            "\n".join(lines)[:4000] if lines else "No recipes are configured.",
            ACCENT,
        )
        embed.set_footer(
            text="✅ craftable right now · ❌ missing materials · "
            "/craft info <recipe> for details"
        )
        await ctx.reply(embed=embed)

    # ── /craft info ──────────────────────────────────────────────────────────
    @craft.command(
        name="info", description="See a recipe's inputs, output, and effect."
    )
    @discord.app_commands.describe(recipe="The recipe to look up")
    async def craft_info(self, ctx: commands.Context, *, recipe: str):
        r = find_recipe(recipe)
        if r is None:
            return await ctx.reply(
                embed=h.err(f"I don't know a recipe called **{recipe}**."),
                ephemeral=True,
            )
        out = item_catalog.get(r.output_item)
        embed = h.embed(f"🛠️ {r.key}", r.description or "A crafting recipe.", ACCENT)
        embed.add_field(name="Inputs", value=self._fmt_inputs(r), inline=False)
        embed.add_field(
            name="Output",
            value=f"{item_catalog.display(r.output_item)} × {r.output_qty}",
            inline=False,
        )
        if out and out.effect:
            embed.add_field(name="On Use", value=self._fmt_effect(out.effect))
        if out and out.value > 0:
            embed.add_field(name="Sell Price", value=f"{out.value:,} coins")
        await ctx.reply(embed=embed)

    # ── /craft make ──────────────────────────────────────────────────────────
    @craft.command(
        name="make", description="Craft an item from materials in your inventory."
    )
    @discord.app_commands.describe(
        recipe="The recipe to craft", qty="How many to craft (default 1)"
    )
    async def craft_make(self, ctx: commands.Context, recipe: str, qty: int = 1):
        # `recipe` intentionally isn't a keyword-only "consume rest" parameter:
        # discord.py's prefix parser only ever transforms the *first*
        # keyword-only parameter it meets and stops (see Command._parse_arguments),
        # so pairing one with a trailing `qty` would leave `qty` permanently
        # stuck at its default under prefix invocation. Recipe keys are single
        # underscore_separated tokens (no spaces), so a plain positional works
        # for both prefix and slash paths — mirrors /fish buy <item> <qty>.
        r = find_recipe(recipe)
        if r is None:
            return await ctx.reply(
                embed=h.err(f"I don't know a recipe called **{recipe}**."),
                ephemeral=True,
            )
        count = clamp_craft_qty(qty)
        gid, uid = ctx.guild.id, ctx.author.id
        async with self._lock(gid, uid):
            stacks = await db.get_inventory(gid, uid)
            inventory = {s["item_key"]: s["qty"] for s in stacks}
            missing = missing_inputs(r, inventory, count)
            if missing:
                need = ", ".join(
                    f"{item_catalog.display(k)} × **{v:,}** more"
                    for k, v in missing.items()
                )
                return await ctx.reply(
                    embed=h.err(
                        f"Missing materials to craft **{count}** × {r.key}: {need}"
                    ),
                    ephemeral=True,
                )
            # Consume inputs one at a time. If a later item comes up short
            # (a concurrent spend elsewhere), refund everything already
            # consumed so materials never partially vanish.
            consumed: list[tuple[str, int]] = []
            shortfall: Optional[str] = None
            for item_key, need in r.inputs.items():
                total = need * count
                if await db.try_consume_item(gid, uid, item_key, total):
                    consumed.append((item_key, total))
                else:
                    shortfall = item_key
                    break
            if shortfall is not None:
                for item_key, amount in consumed:
                    await db.add_item(gid, uid, item_key, amount)
                have = await db.get_item_qty(gid, uid, shortfall)
                return await ctx.reply(
                    embed=h.err(
                        f"Materials changed under you — you now only have "
                        f"**{have}** × {item_catalog.display(shortfall)}. "
                        "Nothing was consumed, try again."
                    ),
                    ephemeral=True,
                )
            await db.add_item(gid, uid, r.output_item, r.output_qty * count)
        await ctx.reply(
            embed=h.ok(
                f"Crafted **{count}** × {item_catalog.display(r.output_item)} "
                f"(× {r.output_qty} each) using {self._fmt_inputs(r, count)}.",
                "🛠️ Crafted",
            )
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Crafting(bot))
