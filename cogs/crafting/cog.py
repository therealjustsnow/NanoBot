"""
cogs/crafting/cog.py
Crafting — turn raw materials into refined items using the shared inventory.

Materials and crafted output live in the global inventory, so a recipe can be
fed by ore mined in one server and bait bought in another.

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

import logging
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from utils import db
from utils import globalxp
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
        self._locks = h.KeyedLocks()

    def _lock(self, user_id: int):
        # Returns an async context manager; existing `async with self._lock(...)`
        # call sites are unchanged. KeyedLocks refcounts holder + waiters and
        # drops an entry when the last interested task releases, so the map no
        # longer grows for the lifetime of the process.
        return self._locks.hold(user_id)

    def _fmt_inputs(self, recipe: RecipeDef, qty: int = 1) -> str:
        parts = [
            f"{item_catalog.display(item_key)} × **{need * qty:,}**"
            for item_key, need in recipe.inputs.items()
        ]
        return ", ".join(parts)

    def _fmt_inputs_plain(self, recipe: RecipeDef, qty: int = 1) -> str:
        """Same as _fmt_inputs but markdown-free — autocomplete choice names are
        plain text, so `**bold**` would show as literal asterisks there."""
        parts = []
        for item_key, need in recipe.inputs.items():
            d = item_catalog.get(item_key)
            label = d.name if d else item_key
            parts.append(f"{need * qty}× {label}")
        return ", ".join(parts)

    async def _recipe_choices(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """Recipe picker: craftable-now recipes first, each showing what it makes
        and what it costs, so nobody has to memorise a recipe key."""
        q = (current or "").strip().lower()
        inventory: dict[str, int] = {}
        if interaction.guild_id:
            stacks = await db.get_inventory(interaction.user.id)
            inventory = {s["item_key"]: s["qty"] for s in stacks}
        ready: list[app_commands.Choice[str]] = []
        locked: list[app_commands.Choice[str]] = []
        for key in sorted(RECIPES):
            recipe = RECIPES[key]
            out = item_catalog.get(recipe.output_item)
            out_label = out.name if out else recipe.output_item
            emoji = out.emoji if out else "🛠️"
            if q and q not in key.lower() and q not in out_label.lower():
                continue
            craftable = not missing_inputs(recipe, inventory)
            name = (
                f"{'✅' if craftable else '🔒'} {emoji} {out_label}"
                f"{'' if recipe.output_qty == 1 else f' ×{recipe.output_qty}'}"
                f" — needs {self._fmt_inputs_plain(recipe)}"
            )
            choice = app_commands.Choice(name=name[:100], value=key)
            (ready if craftable else locked).append(choice)
        return (ready + locked)[:25]

    def _recipe_hint(self) -> str:
        """A few recipe keys to show when someone names one that doesn't exist."""
        return ", ".join(f"`{k}`" for k in sorted(RECIPES)[:5])

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
        # Makes the bare recipe list (with craftable-now markers) reachable as
        # /craft list — it was prefix-only, leaving /craft make/info as the
        # only slash entry points to a surface you have to browse first.
        fallback="list",
        extras={
            "category": "🪙 Economy",
            "sub": "🎒 Items & Crafting",
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
        stacks = await db.get_inventory(ctx.author.id)
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
            "use /craft make and pick a recipe from the list"
        )
        await ctx.reply(embed=embed)

    # ── /craft info ──────────────────────────────────────────────────────────
    @craft.command(
        name="info", description="See a recipe's inputs, output, and effect."
    )
    @discord.app_commands.describe(recipe="Pick a recipe from the list")
    async def craft_info(self, ctx: commands.Context, *, recipe: str):
        r = find_recipe(recipe)
        if r is None:
            return await ctx.reply(
                embed=h.err(
                    f"I don't know a recipe called **{recipe}**. Run `/craft` to "
                    f"see them all — e.g. {self._recipe_hint()}."
                ),
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
        embed.set_footer(text=f"Make it with /craft make {r.key}")
        await ctx.reply(embed=embed)

    @craft_info.autocomplete("recipe")
    async def _craft_info_ac(self, interaction: discord.Interaction, current: str):
        return await self._recipe_choices(interaction, current)

    # ── /craft make ──────────────────────────────────────────────────────────
    @craft.command(
        name="make", description="Craft an item from materials in your inventory."
    )
    @discord.app_commands.describe(
        recipe="Pick a recipe from the list (✅ = you have the materials)",
        qty="How many to craft (default 1)",
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
                embed=h.err(
                    f"I don't know a recipe called **{recipe}**. Run `/craft` to "
                    f"see them all — e.g. {self._recipe_hint()}."
                ),
                ephemeral=True,
            )
        count = clamp_craft_qty(qty)
        uid = ctx.author.id
        async with self._lock(uid):
            stacks = await db.get_inventory(uid)
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
                if await db.try_consume_item(uid, item_key, total):
                    consumed.append((item_key, total))
                else:
                    shortfall = item_key
                    break
            if shortfall is not None:
                for item_key, amount in consumed:
                    await db.add_item(uid, item_key, amount)
                have = await db.get_item_qty(uid, shortfall)
                return await ctx.reply(
                    embed=h.err(
                        f"Materials changed under you — you now only have "
                        f"**{have}** × {item_catalog.display(shortfall)}. "
                        "Nothing was consumed, try again."
                    ),
                    ephemeral=True,
                )
            await db.add_item(uid, r.output_item, r.output_qty * count)
        await globalxp.award(uid, "craft")
        await ctx.reply(
            embed=h.ok(
                f"Crafted **{count}** × {item_catalog.display(r.output_item)} "
                f"(× {r.output_qty} each) using {self._fmt_inputs(r, count)}.",
                "🛠️ Crafted",
            )
        )

    @craft_make.autocomplete("recipe")
    async def _craft_make_ac(self, interaction: discord.Interaction, current: str):
        return await self._recipe_choices(interaction, current)


async def setup(bot: commands.Bot):
    await bot.add_cog(Crafting(bot))
