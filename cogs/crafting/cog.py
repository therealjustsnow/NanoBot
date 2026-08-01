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
                                and how many of it you could make right now
  /craft make <recipe> [qty]  → craft an item (consumes materials, grants output)
                                qty takes `max` to make as many as you can, and
                                recipe takes `all` to make one of everything
  /craft info <recipe>        → a recipe's inputs, output, and what it does

Nobody counts their own ore before crafting, so every screen here answers "how
many can I make" without being asked, and `max`/`all` exist so the answer never
has to be typed back in. `all` is the one that consumes whole stacks, so it
previews the plan behind a confirm button (the /inventory sell all contract).
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
from .helpers import (
    MAX_CRAFT_QTY,
    clamp_craft_qty,
    craft_all_plan,
    find_recipe,
    is_craft_all,
    max_craftable,
    missing_inputs,
    parse_craft_qty,
)
from .recipes import RECIPES, RecipeDef
from .views import CraftAllConfirmView

log = logging.getLogger("NanoBot.crafting")

ACCENT = 0x8E7CC3

# How long a pending craft-everything confirmation stays pressable. Matches the
# inventory's bulk-sell window — same shape of decision, same patience for it.
CRAFT_CONFIRM_TIMEOUT = 60.0


class Crafting(commands.Cog):
    """Craft new items from materials earned elsewhere in the economy."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Per-(guild, user) locks serialize multi-step craft flows (the
        # economy /daily pattern) so a double-send can't double-consume or
        # race the refund-on-failure path.
        self._locks = h.KeyedLocks()
        # One pending craft-everything confirmation per member (the inventory's
        # _pending_sell contract) so a stale preview can't be pressed later
        # against a changed inventory.
        self._pending_craft: dict[int, CraftAllConfirmView] = {}

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
        self, interaction: discord.Interaction, current: str, *, bulk: bool = False
    ) -> list[app_commands.Choice[str]]:
        """Recipe picker: craftable-now recipes first, each showing how many of
        it you could make and what it costs, so nobody has to memorise a recipe
        key or count their own ore first.

        `bulk` adds the craft-everything row on top — offered only where it can
        actually be run (`/craft make`), never on the read-only `/craft info`.
        """
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
            possible = max_craftable(recipe, inventory)
            name = (
                f"{f'✅ ×{possible} ready' if possible else '🔒'} {emoji} {out_label}"
                f"{'' if recipe.output_qty == 1 else f' ×{recipe.output_qty}'}"
                f" — needs {self._fmt_inputs_plain(recipe)}"
            )
            choice = app_commands.Choice(name=name[:100], value=key)
            (ready if possible else locked).append(choice)
        choices = ready + locked
        # Suppressed at one craftable recipe: "everything" and the row directly
        # under it would do the same thing, and the bulk row costs a confirm
        # step (the inventory's per-category row, suppressed the same way).
        if bulk and len(ready) > 1 and (not q or q in "all everything"):
            plan = craft_all_plan(inventory)
            made = sum(n for _, n in plan)
            choices.insert(
                0,
                app_commands.Choice(
                    name=f"🛠️ Everything you can make — {len(plan)} recipes, "
                    f"{made} crafts"[:100],
                    value="all",
                ),
            )
        return choices[:25]

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
            "using a recipe. The list shows how many of each you can make right "
            "now; `max` makes as many as your materials allow, and `all` makes "
            "everything you can after showing you the plan. Purely optional — "
            "nothing else in the economy requires a crafted item.",
            "args": [],
            "perms": "None",
            "example": "{prefix}craft\n{prefix}craft make campfire_feast\n"
            "{prefix}craft make gem_ring max\n{prefix}craft make all\n"
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
        ready = 0
        for key in sorted(RECIPES):
            recipe = RECIPES[key]
            possible = max_craftable(recipe, inventory)
            ready += 1 if possible else 0
            # The count is the whole point of the marker: "craftable" without a
            # number still leaves you guessing at the quantity to type.
            mark = f"✅ **×{possible}**" if possible else "❌"
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
        if ready:
            embed.add_field(
                name="Shortcuts",
                value="`/craft make <recipe> max` — make as many as your "
                "materials allow\n"
                "`/craft make all` — make everything you can, after a preview",
                inline=False,
            )
        embed.set_footer(
            text="✅ ×N how many you can make right now · ❌ missing materials · "
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
        stacks = await db.get_inventory(ctx.author.id)
        inventory = {s["item_key"]: s["qty"] for s in stacks}
        possible = max_craftable(r, inventory)
        out = item_catalog.get(r.output_item)
        embed = h.embed(f"🛠️ {r.key}", r.description or "A crafting recipe.", ACCENT)
        embed.add_field(name="Inputs", value=self._fmt_inputs(r), inline=False)
        embed.add_field(
            name="Output",
            value=f"{item_catalog.display(r.output_item)} × {r.output_qty}",
            inline=False,
        )
        if possible:
            can_make = (
                f"**{possible:,}** right now — `/craft make {r.key} max` makes "
                f"{min(possible, MAX_CRAFT_QTY):,}"
            )
        else:
            can_make = self._shortfall_line(r, inventory)
        embed.add_field(name="You can make", value=can_make, inline=False)
        if out and out.effect:
            embed.add_field(name="On Use", value=self._fmt_effect(out.effect))
        if out and out.value > 0:
            embed.add_field(name="Sell Price", value=f"{out.value:,} coins")
        embed.set_footer(text=f"Make it with /craft make {r.key}")
        await ctx.reply(embed=embed)

    def _shortfall_line(self, recipe: RecipeDef, inventory: dict[str, int]) -> str:
        """What's stopping one craft — the answer "0" on its own never gives."""
        missing = missing_inputs(recipe, inventory)
        return "**0** — still need " + ", ".join(
            f"{item_catalog.display(k)} × **{v:,}**" for k, v in missing.items()
        )

    @craft_info.autocomplete("recipe")
    async def _craft_info_ac(self, interaction: discord.Interaction, current: str):
        return await self._recipe_choices(interaction, current)

    # ── /craft make ──────────────────────────────────────────────────────────
    @craft.command(
        name="make", description="Craft an item from materials in your inventory."
    )
    @discord.app_commands.describe(
        recipe="Pick a recipe (✅ ×N = how many you can make), or `all` for everything",
        qty="How many to craft — a number, or `max` for as many as you can",
    )
    async def craft_make(self, ctx: commands.Context, recipe: str, qty: str = "1"):
        # `recipe` intentionally isn't a keyword-only "consume rest" parameter:
        # discord.py's prefix parser only ever transforms the *first*
        # keyword-only parameter it meets and stops (see Command._parse_arguments),
        # so pairing one with a trailing `qty` would leave `qty` permanently
        # stuck at its default under prefix invocation. Recipe keys are single
        # underscore_separated tokens (no spaces), so a plain positional works
        # for both prefix and slash paths — mirrors /fish buy <item> <qty>.
        #
        # `qty` is a string rather than an int so it can carry `max`: the count
        # someone actually wants is nearly always "as many as I can", and an int
        # option can't express it without them first counting their own ore.
        uid = ctx.author.id
        if is_craft_all(recipe):
            return await self._craft_all(ctx)
        r = find_recipe(recipe)
        if r is None:
            return await ctx.reply(
                embed=h.err(
                    f"I don't know a recipe called **{recipe}**. Run `/craft` to "
                    f"see them all — e.g. {self._recipe_hint()} — or make "
                    "everything you can with `/craft make all`."
                ),
                ephemeral=True,
            )
        stacks = await db.get_inventory(uid)
        owned = {s["item_key"]: s["qty"] for s in stacks}
        count = parse_craft_qty(qty, max_craftable(r, owned))
        if count is None:
            return await ctx.reply(
                embed=h.err(
                    f"**{qty}** isn't a quantity. Give a number, or `max` to "
                    "make as many as your materials allow."
                ),
                ephemeral=True,
            )
        if count <= 0:
            # `max` of something you can't make once. Naming the shortfall beats
            # refusing with a zero, which is the answer they already had.
            need = ", ".join(
                f"{item_catalog.display(k)} × **{v:,}** more"
                for k, v in missing_inputs(r, owned).items()
            )
            return await ctx.reply(
                embed=h.err(
                    f"You can't make any {item_catalog.display(r.output_item)} "
                    f"yet — still need {need}.",
                    "🛠️ Nothing to Craft",
                ),
                ephemeral=True,
            )
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
            left = max_craftable(
                r, {s["item_key"]: s["qty"] for s in await db.get_inventory(uid)}
            )
        await globalxp.award(uid, "craft")
        desc = (
            f"Crafted **{count}** × {item_catalog.display(r.output_item)} "
            f"(× {r.output_qty} each) using {self._fmt_inputs(r, count)}."
        )
        # The question the last screen answered is the one they'll have again.
        desc += (
            f"\n\nMaterials left for **{left:,}** more."
            if left
            else "\n\nThat's the last of the materials for it."
        )
        await ctx.reply(embed=h.ok(desc, "🛠️ Crafted"))

    @craft_make.autocomplete("recipe")
    async def _craft_make_ac(self, interaction: discord.Interaction, current: str):
        return await self._recipe_choices(interaction, current, bulk=True)

    @craft_make.autocomplete("qty")
    async def _craft_qty_ac(self, interaction: discord.Interaction, current: str):
        """Quantities worth tapping, led by the max for the recipe already
        picked — the number nobody knows without being told."""
        recipe = getattr(interaction.namespace, "recipe", "") or ""
        possible = 0
        r = find_recipe(recipe) if recipe and not is_craft_all(recipe) else None
        if r is not None and interaction.guild_id:
            stacks = await db.get_inventory(interaction.user.id)
            possible = max_craftable(r, {s["item_key"]: s["qty"] for s in stacks})
        choices: list[app_commands.Choice[str]] = []
        if possible:
            capped = min(possible, MAX_CRAFT_QTY)
            label = f"Max — {capped}"
            if possible > capped:
                label += f" (materials for {possible:,}, {MAX_CRAFT_QTY} per craft)"
            choices.append(app_commands.Choice(name=label[:100], value="max"))
        elif r is not None:
            choices.append(
                app_commands.Choice(
                    name="Max — you can't make any of this yet", value="max"
                )
            )
        q = (current or "").strip()
        if q.isdigit() and q not in {c.value for c in choices}:
            choices.append(app_commands.Choice(name=f"{int(q):,}", value=q))
        for n in (1, 5, 10, MAX_CRAFT_QTY):
            if possible and n > possible:
                break
            if str(n) not in {c.value for c in choices}:
                choices.append(app_commands.Choice(name=str(n), value=str(n)))
        return choices[:25]

    # ── /craft make all ──────────────────────────────────────────────────────
    async def _craft_all(self, ctx: commands.Context):
        """Preview crafting everything, and wait for confirmation.

        Nothing is consumed here. Crafting all empties whole material stacks —
        including the diamond someone was saving for a treasure key — so it
        always shows what would go and crafts only on the press (the
        /inventory sell all contract).
        """
        stacks = await db.get_inventory(ctx.author.id)
        plan = craft_all_plan({s["item_key"]: s["qty"] for s in stacks})
        if not plan:
            return await ctx.reply(
                embed=h.warn(
                    "You haven't got the materials for any recipe yet. Run "
                    "`/craft` to see what each one takes — ore comes from "
                    "`/mine`, pelts and meat from `/adventure hunt`.",
                    "🛠️ Nothing to Craft",
                ),
                ephemeral=True,
            )
        embed = h.warn(
            f"This makes **{sum(n for _, n in plan):,}** items across "
            f"**{len(plan)}** recipes, consuming the materials for all of them.",
            "🛠️ Craft these?",
        )
        embed.add_field(name="What will be made", value=self._plan_field(plan))
        embed.set_footer(
            text="Nothing is made until you press Craft · expires in "
            f"{h.fmt_duration(int(CRAFT_CONFIRM_TIMEOUT))}"
        )
        view = CraftAllConfirmView(
            self, user_id=ctx.author.id, timeout=CRAFT_CONFIRM_TIMEOUT
        )
        previous = self._pending_craft.get(ctx.author.id)
        if previous is not None:
            await previous.cancel_quietly()
        self._pending_craft[ctx.author.id] = view
        view.message = await ctx.reply(embed=embed, view=view)

    def forget_pending_craft(self, user_id: int, view) -> None:
        """Drop a finished confirmation (called by the view on settle/timeout)."""
        if self._pending_craft.get(user_id) is view:
            self._pending_craft.pop(user_id, None)

    @staticmethod
    def _plan_field(plan: list[tuple[str, int]]) -> str:
        """The recipe breakdown, trimmed to fit an embed field's 1024-char cap."""
        lines = []
        for key, count in plan:
            recipe = RECIPES[key]
            lines.append(
                f"{item_catalog.display(recipe.output_item)} × "
                f"**{count * recipe.output_qty:,}** ({key} ×{count})"
            )
        body, used = [], 0
        for line in lines:
            if used + len(line) + 1 > 950:
                break
            body.append(line)
            used += len(line) + 1
        if len(body) < len(lines):
            body.append(f"…and **{len(lines) - len(body)}** more")
        return "\n".join(body)

    async def execute_craft_all(self, user_id: int) -> discord.Embed:
        """Run a confirmed craft-everything and return the result embed.

        The plan is recomputed against a fresh inventory read inside the lock,
        and each recipe goes through the same consume-then-refund-on-shortfall
        path as a single craft — so a material spent since the preview costs
        that one recipe rather than corrupting the rest.
        """
        made: list[tuple[str, int]] = []
        async with self._lock(user_id):
            stacks = await db.get_inventory(user_id)
            plan = craft_all_plan({s["item_key"]: s["qty"] for s in stacks})
            for key, count in plan:
                recipe = RECIPES[key]
                consumed: list[tuple[str, int]] = []
                short = False
                for item_key, need in recipe.inputs.items():
                    total = need * count
                    if await db.try_consume_item(user_id, item_key, total):
                        consumed.append((item_key, total))
                    else:
                        short = True
                        break
                if short:
                    for item_key, amount in consumed:
                        await db.add_item(user_id, item_key, amount)
                    continue
                await db.add_item(
                    user_id, recipe.output_item, recipe.output_qty * count
                )
                made.append((key, count))
        if not made:
            return h.warn(
                "The materials were gone by the time you pressed — nothing was "
                "made.",
                "🛠️ Nothing to Craft",
            )
        await globalxp.award(user_id, "craft")
        embed = h.ok(
            f"Crafted **{sum(n * RECIPES[k].output_qty for k, n in made):,}** items "
            f"across **{len(made)}** recipes.",
            "🛠️ Crafted",
        )
        embed.add_field(
            name="What was made", value=self._plan_field(made), inline=False
        )
        return embed


async def setup(bot: commands.Bot):
    await bot.add_cog(Crafting(bot))
