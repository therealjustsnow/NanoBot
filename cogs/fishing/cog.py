"""
cogs/fishing.py
Fishing minigame — a direct NanoCoin economy tie-in.

Fishing progress is GLOBAL: rod, XP/level, bag, dex, quests, streak, and
lifetime earnings belong to the user and follow them into every server. The
cast cooldown is one fixed bot-wide value (CAST_COOLDOWN, 60s) claimed per
user, so it can't be dodged by hopping servers; each server only chooses
whether fishing is enabled at all.

Members cast a line every CAST_COOLDOWN seconds and pull up fish across seven
rarity tiers (junk → common → uncommon → rare → epic → legendary → treasure).
Fish land in a bag and sell for NanoCoins; treasure pays coins on the spot.
Better rods — bought with coins, a deliberate money sink — shift the odds away
from junk toward the high tiers, alongside a fishing level (earned from XP per
catch) and bait bought at the /fish shop (armed via /inventory use — see
cogs/fishing/items.py). Lifetime earnings drive a fishing leaderboard (plus a
cross-server /fish global one), a per-species dex tracks collection progress,
and personal bests remember the heaviest catch. A once-a-day streak rewards
logging in to fish, a daily quest gives members something concrete to chase,
and rare server-wide events (feeding frenzy, double XP, lucky waters) spice up
a session. Coins ride the existing economy tables (db.add_coins), so /fish
earnings spend anywhere coins do: /shop, /pay, /coin gamble.

Slash command budget: one group (/fish …) — subcommands cost no extra
top-level slots.

──────────────────────────────────────────────────────
Commands
──────────────────────────────────────────────────────
  /fish                       → cast your line (same as /fish cast)
  /fish cast                  → cast your line (20s cooldown, bot-wide)
  /fish hub                   → the dashboard: spot, rod, bag, streak, quest —
                                and the buttons that reach all of the below
  /fish bag                   → what you've caught, grouped by species
  /fish sell [fish|all]       → sell one species — or everything — for coins
  /fish travel [spot]         → the map: charter and move between spots
  /fish shop                  → bait and tackle, one tap each
  /fish trap                  → set a fish trap, or pull a soaked one
  /fish rod                   → your rod + the next upgrade
  /fish upgrade               → buy the next rod tier with coins
  /fish dex                   → species collection progress
  /fish stats [member]        → casts, catches, earnings, level, best catch
  /fish top [page] [scope]    → top earners (this server or every server)
  /fish global [stat] [page]  → cross-server leaderboard
  /fish buy <item> [qty]      → buy bait, tackle or an XP potion with coins
  /fish bait                  → your owned bait + what's currently armed
  /fish quest                 → today's quest and your progress
  /fish events                → active fishing events and time left
  /fish toggle                → enable/disable fishing     (Manage Server)
  /fish config                → show settings              (Manage Server)

Every one of those is also a button. A cast is a twenty-second cooldown, so an
hour of fishing used to be a hundred and eighty typed commands; the reply to a
cast now carries 🎣 Cast · 💰 Sell · 🎒 Bag · 🗺️ Travel · 🛒 Shop · 🪤 Trap and
edits itself in place (cogs/fishing/views.py). Bare `/fish` still casts — it is
the most-typed command in the bot and swapping it for a menu would be a poor
trade — so the dashboard lives at `/fish hub`.

Where you fish is the other half (cogs/fishing/spots.py): five spots, each
gated on a fishing level and a one-off coin charter, each with species found
nowhere else, its own bend on the rarity table, and its own chance of a snag.

Owner tool — prefix only (`with_app_command=False`), because forcing an event
turns up a faucet that pays out in every server:

  n!fish event <key> [minutes] → force-start a fishing event
"""

import logging
import random
import time
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

import discord
from discord import app_commands
from discord.ext import commands

from utils import db
from utils import globalxp
from utils import paginator
from utils import helpers as h
from utils.helpers import SCOPE_CHOICES
from utils import items as item_catalog

from . import items as _fishing_items  # noqa: F401 - registers bait/consumable defs
from .items import FISH_TRAP
from .constants import (
    CAST_COOLDOWN,
    EVENT_DURATION_RANGE,
    EVENT_LABELS,
    EVENT_POOL,
    EVENT_CHANCE,
    FISH,
    FISH_BY_RARITY,
    MAX_LUCK,
    NET_CATCHES,
    RARITIES,
    RARITY_ODDS,
    RODS,
    TRAP_CATCHES,
    TRAP_SOAK,
    XP_PER_RARITY,
)
from .helpers import (
    catch_value,
    fish_level,
    find_fish,
    fmt_weight,
    generate_quest,
    level_luck_bonus,
    level_progress,
    next_rod,
    next_streak,
    pick_event,
    quest_label,
    quest_reward,
    rarity_odds,
    rod_info,
    roll_event_duration,
    roll_weight,
    streak_bonus_coins,
    treasure_coins,
)
from .spots import (
    SPOT_ORDER,
    exclusive_keys,
    find_spot,
    pick_spot_fish,
    pick_spot_rarity,
    resolve_spot,
    roll_hazard,
    spot_info,
    unlock_state,
)
from .views import FishingView, TackleShopView, TravelView

log = logging.getLogger("NanoBot.fishing")

# Rarity → how impressive, for picking the headline catch out of a net haul.
_RARITY_RANK: dict[str, int] = {
    rarity: index for index, (rarity, _p) in enumerate(RARITY_ODDS)
}


@dataclass
class Screen:
    """One rendered fishing screen, ready to be shown.

    Every action — a cast, a sale, the travel map, the shop — returns one of
    these instead of replying, so the same code serves a command and a button
    press. `ok` is False for a refusal (disabled here, still on cooldown,
    nothing to sell); a command replies to those ephemerally, and a button
    keeps the screen the member was looking at and answers privately rather
    than replacing a fresh catch with an error.

    `after` is work that has to happen *once the screen is on screen* — the
    only case is announcing a fishing event a cast happened to start, which
    reads as nonsense arriving before the catch that triggered it. Whoever
    displays the screen awaits it.
    """

    embed: discord.Embed
    ok: bool = True
    after: Optional[Callable[[], Awaitable[None]]] = None

    async def settle(self) -> None:
        """Run the deferred work, if any. Safe to call on every screen."""
        if self.after is not None:
            await self.after()


class Fishing(commands.Cog):
    """Cast, collect, and sell fish for NanoCoins — one angler profile per
    user, shared across servers."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Per-user (global — the wallet and bag are too) locks serialize
        # sell/upgrade read-check-write (the economy /daily pattern) so a
        # double-send, even from two different servers at once, can't sell the
        # same fish twice or buy the same rod twice. Cast needs no lock — its cooldown
        # claim is a single atomic upsert in the DB layer.
        self._locks = h.KeyedLocks()

    def _lock(self, user_id: int):
        # Returns an async context manager; existing `async with self._lock(...)`
        # call sites are unchanged. KeyedLocks refcounts holder + waiters and
        # drops an entry when the last interested task releases, so the map no
        # longer grows for the lifetime of the process.
        return self._locks.hold(user_id)

    def _money(self, econ: dict, amount: int) -> str:
        return h.fmt_coins(amount, econ["currency_name"], econ["currency_emoji"])

    # ══════════════════════════════════════════════════════════════════════════
    #  /fish  group
    # ══════════════════════════════════════════════════════════════════════════
    @commands.hybrid_group(
        name="fish",
        description="Fishing minigame: catch fish and sell them for coins.",
        invoke_without_command=True,
        extras={
            "category": "🪙 Economy",
            "sub": "🎲 Games",
            "short": "Catch and sell fish for coins",
            "usage": "fish [subcommand]",
            "desc": "Cast a line every 60 seconds, collect fish across seven "
            "rarity tiers, and sell them for coins. Buy better rods to improve "
            "your odds, climb the earnings leaderboard, and complete the species "
            "dex. "
            "Admins can turn fishing on or off here.",
            "args": [],
            "perms": "Admin subcommands require Manage Server",
            "example": "{prefix}fish\n{prefix}fish sell\n{prefix}fish rod",
        },
    )
    @commands.guild_only()
    async def fish(self, ctx: commands.Context):
        if ctx.invoked_subcommand is None:
            await self._do_cast(ctx)

    # ── /fish cast ───────────────────────────────────────────────────────────
    @fish.command(name="cast", description="Cast your line and see what bites.")
    async def fish_cast(self, ctx: commands.Context):
        await self._do_cast(ctx)

    async def _do_cast(self, ctx: commands.Context):
        await self._send(ctx, await self.cast_once(ctx.guild, ctx.author, ctx.channel))

    async def _send(self, ctx: commands.Context, screen: "Screen", view=None):
        """Reply with a screen, hanging the fishing buttons off it.

        A successful screen gets the buttons; a refusal doesn't, and goes out
        ephemerally — there's nothing to do from "fishing is disabled here",
        and offering a Cast button under it would be a lie.
        """
        if screen.ok and view is None:
            view = FishingView(self, ctx.author.id)
        elif not screen.ok:
            view = None
        message = await ctx.reply(
            embed=screen.embed, view=view, ephemeral=not screen.ok
        )
        if view is not None:
            view.message = message
        await screen.settle()

    async def cast_once(
        self, guild: discord.Guild, member: discord.abc.User, channel=None
    ) -> "Screen":
        """Check, claim and resolve one cast. The single entry point.

        /fish, /fish cast and the 🎣 Cast button all land here, so a change to
        how a cast resolves can't drift between them. It returns the screen
        instead of sending it, which is what lets the button edit the message
        in place rather than posting a new one every twenty seconds.
        """
        cfg = await db.get_fishing_config(guild.id)
        if not cfg["enabled"]:
            return Screen(h.err("Fishing is disabled on this server."), ok=False)
        retry = await db.try_claim_cast(member.id, time.time(), CAST_COOLDOWN)
        if retry:
            return Screen(
                h.warn(
                    f"Your line is still in the water. Cast again in "
                    f"**{h.fmt_duration(retry)}**.",
                    "🎣 Not Yet",
                ),
                ok=False,
            )
        embed = await self._resolve_cast(guild, member)
        # Announced *after* the catch is on screen: an event notice that
        # arrives first reads as a non-sequitur, since it's the cast that
        # started it.
        return Screen(embed, after=lambda: self._maybe_start_event(guild, channel))

    async def _resolve_cast(
        self, guild: discord.Guild, member: discord.abc.User
    ) -> discord.Embed:
        """Everything after the claim: luck, the spot, the haul, the notes."""
        econ = await db.get_econ_config(guild.id)
        fisher = await db.get_fisher(member.id)
        spot = resolve_spot(fisher["spot"])
        today = int(time.time() // 86400)

        # First cast of a new UTC day: bump the login streak + a small bonus.
        streak_note = None
        if fisher["last_day"] != today:
            new_streak = next_streak(fisher["last_day"], today, fisher["streak_days"])
            if await db.try_claim_daily_streak(member.id, today, new_streak):
                bonus = streak_bonus_coins(new_streak)
                if bonus:
                    await db.add_coins(member.id, bonus)
                    await db.add_fishing_earned(member.id, bonus)
                    streak_note = (
                        f"🔥 **{new_streak}**-day streak! +{self._money(econ, bonus)}"
                    )
                else:
                    streak_note = f"🔥 **{new_streak}**-day streak!"

        # Luck: rod tier + fishing level + any armed bait/luck effect + a
        # guild-wide lucky-waters event, clamped to the safe [0, MAX_LUCK] range
        # rarity_odds expects (it also clamps internally, this is belt-and-braces).
        effects = await db.get_active_effects(member.id)
        events = await db.get_active_events(guild.id)
        generic_luck = effects.get("luck", {}).get("magnitude", 0.0)
        bait_luck = 0.0
        if "fish_bait" in effects:
            # One charge, spent the moment the line goes in — before the hazard
            # roll below, and deliberately so. A snag costs this single use and
            # never reaches back into the rest of the stack.
            if await db.consume_effect_use(member.id, "fish_bait"):
                bait_luck = effects["fish_bait"]["magnitude"]
        xp_effect_mult = effects.get("fish_xp", {}).get("magnitude") or 1.0
        catches = 1
        if "fish_net" in effects and await db.consume_effect_use(member.id, "fish_net"):
            catches = max(1, int(effects["fish_net"]["magnitude"] or NET_CATCHES))
        event_luck = 0.0
        value_mult = 1.0
        xp_event_mult = 1.0
        for ev in events:
            if ev["event_key"] == "lucky_waters":
                event_luck = max(event_luck, ev["magnitude"])
            elif ev["event_key"] == "frenzy":
                value_mult = max(value_mult, ev["magnitude"])
            elif ev["event_key"] == "double_xp":
                xp_event_mult = max(xp_event_mult, ev["magnitude"])

        rod_luck = rod_info(fisher["rod_level"])["luck"]
        level_bonus = level_luck_bonus(fish_level(fisher["xp"]))
        total_luck = max(
            0.0,
            min(
                MAX_LUCK,
                rod_luck + level_bonus + generic_luck + bait_luck + event_luck,
            ),
        )

        await globalxp.award(member.id, "fish_cast")
        old_xp = fisher["xp"]
        info = spot_info(spot)

        # Today's quest — regenerated deterministically, created on first read.
        qgen = generate_quest(member.id, today)
        quest = await db.get_or_create_quest(
            member.id, today, qgen["quest_key"], qgen["target"]
        )

        # ── The snag ─────────────────────────────────────────────────────────
        # Rolled once for the whole cast, so a net is a bigger bet as well as a
        # bigger haul. XP is still paid: the line went in the water.
        if roll_hazard(random.random(), spot):
            xp_gain = max(1, round(XP_PER_RARITY["common"] * xp_event_mult))
            new_xp = await db.add_fishing_xp(member.id, xp_gain)
            desc = (
                f"{info['emoji']} **{info['name']}**\n"
                "The line goes taut, sings, and snaps. Whatever that was, it's "
                "keeping it."
            )
            if bait_luck:
                desc += "\nYour bait went with it — one charge, no more."
            desc += self._catch_extras(fish_level(old_xp), fish_level(new_xp))
            if streak_note:
                desc += f"\n{streak_note}"
            embed = h.warn(desc, "🎣 Snapped!")
            embed.set_footer(text=f"{info['name']} · snags happen out here")
            return embed

        # ── The haul ─────────────────────────────────────────────────────────
        hauled: list[dict] = []
        treasure_coins_total = 0
        xp_total = 0
        for _ in range(catches):
            rarity = pick_spot_rarity(random.random(), rarity_odds(total_luck), spot)
            key = pick_spot_fish(rarity, random.random(), spot)
            xp_total += max(
                1, round(XP_PER_RARITY[rarity] * xp_effect_mult * xp_event_mult)
            )
            if rarity == "treasure":
                coins = max(1, int(treasure_coins(key, random.random()) * value_mult))
                treasure_coins_total += coins
                hauled.append({"key": key, "rarity": rarity, "coins": coins})
                continue
            weight = roll_weight(key, random.random())
            value = max(1, int(catch_value(key, weight) * value_mult))
            await db.record_catch(
                member.id, key, weight, value, track_best=rarity != "junk"
            )
            hauled.append(
                {"key": key, "rarity": rarity, "weight": weight, "value": value}
            )

        if treasure_coins_total:
            await db.add_coins(member.id, treasure_coins_total)
            await db.add_fishing_earned(member.id, treasure_coins_total)
            quest = await self._bump_quest(
                member.id, guild, quest, today, kind="earn", coins=treasure_coins_total
            )
        for entry in hauled:
            if entry["rarity"] != "treasure":
                quest = await self._bump_quest(
                    member.id, guild, quest, today, kind="catch", rarity=entry["rarity"]
                )
        new_xp = await db.add_fishing_xp(member.id, xp_total)
        quest_note = await self._award_quest_if_complete(member.id, guild, today, quest)

        embed = self._haul_embed(econ, fisher, spot, hauled)
        desc = embed.description
        desc += self._catch_extras(fish_level(old_xp), fish_level(new_xp))
        if streak_note:
            desc += f"\n{streak_note}"
        if quest_note:
            desc += f"\n{quest_note}"
        embed.description = desc
        embed.set_footer(
            text=f"{info['emoji']} {info['name']} · bag: "
            f"{await db.count_bag(member.id)}"
        )
        return embed

    def _haul_embed(
        self, econ: dict, fisher: dict, spot: str, hauled: list[dict]
    ) -> discord.Embed:
        """Render one cast's catches.

        A single catch keeps the shape it always had — the species as the
        title, its own rarity colour — because that is the message members see
        a hundred times an hour and there was nothing wrong with it. A net's
        haul becomes a list titled by the biggest thing in it.
        """
        best = max(hauled, key=lambda c: _RARITY_RANK.get(c["rarity"], 0))
        label, color = RARITIES[best["rarity"]]

        if len(hauled) == 1:
            catch = hauled[0]
            entry = FISH[catch["key"]]
            if catch["rarity"] == "treasure":
                desc = (
                    f"{label}\nYou found {self._money(econ, catch['coins'])} inside — "
                    "added straight to your balance!"
                )
            elif catch["rarity"] == "junk":
                desc = (
                    f"{label}\nWell… it's something. Worth "
                    f"{self._money(econ, catch['value'])}."
                )
            else:
                desc = (
                    f"{label}\nWeight: **{fmt_weight(catch['weight'])}**\n"
                    f"Worth {self._money(econ, catch['value'])} when sold."
                )
                if catch["weight"] > fisher["best_weight"]:
                    desc += "\n🏆 **New personal best!**"
            return h.embed(f"{entry['emoji']} {entry['name']}!", desc, color)

        lines = []
        for catch in hauled:
            entry = FISH[catch["key"]]
            if catch["rarity"] == "treasure":
                lines.append(
                    f"{entry['emoji']} **{entry['name']}** — "
                    f"{self._money(econ, catch['coins'])} on the spot"
                )
            else:
                line = f"{entry['emoji']} **{entry['name']}**"
                if catch["rarity"] != "junk":
                    line += f" · {fmt_weight(catch['weight'])}"
                lines.append(f"{line} — {self._money(econ, catch['value'])}")
        best_weight = max(
            (c["weight"] for c in hauled if c["rarity"] not in ("treasure", "junk")),
            default=0.0,
        )
        desc = "🕸️ The net comes up heavy.\n" + "\n".join(lines)
        if best_weight > fisher["best_weight"]:
            desc += "\n🏆 **New personal best!**"
        return h.embed(f"{FISH[best['key']]['emoji']} A Netful!", desc, color)

    def _catch_extras(self, old_level: int, new_level: int) -> str:
        """A level-up line to append to a catch embed, or "" if none happened."""
        if new_level > old_level:
            return f"\n🎉 Fishing level up! You're now level **{new_level}**."
        return ""

    async def _bump_quest(
        self,
        user_id: int,
        guild: discord.Guild,
        quest: dict,
        today: int,
        *,
        kind: str,
        rarity: Optional[str] = None,
        coins: int = 0,
    ) -> dict:
        """Advance today's quest if this event matches its type. Returns the
        (possibly updated) quest dict."""
        if quest["claimed"]:
            return quest
        quest_key = quest["quest_key"]
        amount = 0
        if kind == "catch" and quest_key == "catch_any":
            amount = 1
        elif (
            kind == "catch"
            and quest_key.startswith("catch_rarity:")
            and quest_key.split(":", 1)[1] == rarity
        ):
            amount = 1
        elif kind == "earn" and quest_key == "earn_coins":
            amount = coins
        if amount <= 0:
            return quest
        new_progress = await db.bump_quest_progress(user_id, today, amount)
        return {**quest, "progress": new_progress}

    async def _award_quest_if_complete(
        self, user_id: int, guild: discord.Guild, today: int, quest: dict
    ) -> Optional[str]:
        """Claim + pay out a just-completed quest once. Returns an embed note,
        or None if nothing was awarded."""
        if quest["claimed"] or quest["progress"] < quest["target"]:
            return None
        if not await db.try_claim_quest_reward(user_id, today):
            return None
        coins, xp = quest_reward(quest["quest_key"], quest["target"])
        if coins:
            await db.add_coins(user_id, coins)
            await db.add_fishing_earned(user_id, coins)
        if xp:
            await db.add_fishing_xp(user_id, xp)
        await globalxp.award(user_id, "quest")
        econ = await db.get_econ_config(guild.id)
        return f"✅ Daily quest complete! +{self._money(econ, coins)}, +{xp} XP"

    async def _maybe_start_event(self, guild: discord.Guild, channel=None) -> None:
        """~EVENT_CHANCE odds per cast to kick off a guild fishing event, if
        none is already running (guild-scoped or global).

        `channel` is where the announcement goes and may be absent — a cast run
        from a button knows its channel, but nothing here depends on having
        one: the event still starts, it just isn't announced.
        """
        if random.random() >= EVENT_CHANCE:
            return
        if await db.get_active_events(guild.id):
            return
        event = pick_event(random.random())
        duration = roll_event_duration(random.random(), EVENT_DURATION_RANGE)
        await db.start_event(guild.id, event["key"], event["magnitude"], duration)
        if channel is None:
            return
        label = EVENT_LABELS.get(event["key"], event["key"])
        embed = h.embed(
            f"🌊 {label} has begun!",
            f"Something's stirring in the water — this bonus lasts "
            f"**{h.fmt_duration(duration)}**. Check `/fish events` any time.",
            RARITIES["treasure"][1],
        )
        try:
            await channel.send(embed=embed)
        except discord.HTTPException:
            log.warning("Failed to announce fishing event in guild %s", guild.id)

    # ══════════════════════════════════════════════════════════════════════════
    #  The hub — one card, and the buttons that reach everything from it
    # ══════════════════════════════════════════════════════════════════════════
    @fish.command(
        name="hub",
        description="Your fishing dashboard — spot, rod, bag, streak and quest.",
    )
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def fish_hub(self, ctx: commands.Context):
        await self._send(ctx, await self.hub_screen(ctx.guild, ctx.author))

    async def hub_screen(
        self, guild: discord.Guild, member: discord.abc.User
    ) -> "Screen":
        """Everything an angler wants at a glance, on one card.

        Deliberately *not* the bare `/fish` — that casts, it is the most-typed
        command in the bot, and taking it away to show a menu would be a poor
        trade. This is where the buttons live between casts.
        """
        econ = await db.get_econ_config(guild.id)
        fisher = await db.get_fisher(member.id)
        spot = resolve_spot(fisher["spot"])
        info = spot_info(spot)
        level = fish_level(fisher["xp"])
        rod = rod_info(fisher["rod_level"])
        bag = await db.get_bag(member.id)
        bag_count = sum(row["qty"] for row in bag)
        bag_value = sum(row["total_value"] for row in bag)

        embed = h.embed(
            "🎣 Fishing",
            f"{info['emoji']} **{info['name']}** — {info['blurb']}",
            h.BLUE,
        )
        embed.add_field(
            name="Angler",
            value=f"{rod['emoji']} **{rod['name']}**\n"
            f"Level **{level}** · {rod['luck']:.0%} rod luck",
            inline=True,
        )
        embed.add_field(
            name="Bag",
            value=f"**{bag_count}** fish\nWorth {self._money(econ, bag_value)}",
            inline=True,
        )
        streak = fisher["streak_days"]
        embed.add_field(
            name="Streak",
            value=(
                f"🔥 **{streak}** day{'' if streak == 1 else 's'}"
                if streak
                else "Cast today to start one"
            ),
            inline=True,
        )

        today = int(time.time() // 86400)
        qgen = generate_quest(member.id, today)
        quest = await db.get_or_create_quest(
            member.id, today, qgen["quest_key"], qgen["target"]
        )
        done = "✅ " if quest["claimed"] else ""
        embed.add_field(
            name="Today's quest",
            value=f"{done}{quest_label(quest['quest_key'], quest['target'])}\n"
            f"**{min(quest['progress'], quest['target'])}/{quest['target']}**",
            inline=False,
        )

        events = await db.get_active_events(guild.id)
        if events:
            embed.add_field(
                name="Running now",
                value="\n".join(
                    f"{EVENT_LABELS.get(ev['event_key'], ev['event_key'])} — "
                    f"{h.fmt_duration(max(0, int(ev['ends_at'] - time.time())))} left"
                    for ev in events
                ),
                inline=False,
            )

        trap = await db.get_trap(member.id)
        if trap:
            left = int(TRAP_SOAK - (time.time() - trap["set_at"]))
            where = spot_info(resolve_spot(trap["spot"]))["name"]
            embed.add_field(
                name="Trap",
                value=f"🪤 Soaking at **{where}** — "
                + (
                    "ready to pull" if left <= 0 else f"ready in {h.fmt_duration(left)}"
                ),
                inline=False,
            )
        embed.set_footer(text="Tap a button — no need to type another command")
        return Screen(embed)

    # ── /fish bag ────────────────────────────────────────────────────────────
    @fish.command(name="bag", description="See the fish you're carrying.")
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def fish_bag(self, ctx: commands.Context):
        await self._send(ctx, await self.bag_screen(ctx.guild, ctx.author))

    async def bag_screen(
        self, guild: discord.Guild, member: discord.abc.User
    ) -> "Screen":
        bag = await db.get_bag(member.id)
        econ = await db.get_econ_config(guild.id)
        if not bag:
            return Screen(h.info("Your bag is empty. Try `/fish cast`!", "🎒 Bag"))
        lines = []
        for row in bag:
            entry = FISH.get(row["fish_key"])
            if not entry:
                continue
            lines.append(
                f"{entry['emoji']} **{entry['name']}** ×{row['qty']} — "
                f"{self._money(econ, row['total_value'])}"
            )
        total = sum(row["total_value"] for row in bag)
        embed = h.embed("🎒 Bag", "\n".join(lines), h.BLUE)
        embed.set_footer(text="Sell everything with /fish sell")
        embed.add_field(name="Total value", value=self._money(econ, total))
        return Screen(embed)

    # ── /fish sell ───────────────────────────────────────────────────────────
    @fish.command(
        name="sell",
        description="Sell your catch for coins — one species, or everything.",
    )
    @app_commands.describe(
        fish="Pick a species from your bag (leave blank to sell everything)"
    )
    async def fish_sell(self, ctx: commands.Context, *, fish: Optional[str] = None):
        key = None
        if fish is not None and fish.strip().lower() not in ("all", "everything", "*"):
            key = find_fish(fish)
            if key is None:
                return await ctx.reply(
                    embed=h.err(
                        f"I don't know a fish called **{fish}**. Check `/fish bag`, "
                        "or run `/fish sell` with no fish to sell everything."
                    ),
                    ephemeral=True,
                )
        await self._send(ctx, await self.sell_screen(ctx.guild, ctx.author, key))

    async def sell_screen(
        self,
        guild: discord.Guild,
        member: discord.abc.User,
        key: Optional[str] = None,
    ) -> "Screen":
        econ = await db.get_econ_config(guild.id)
        # The lock covers the DB work only — replying inside it would hold this
        # member's economy lock for a whole Discord round trip.
        async with self._lock(member.id):
            count, total = await db.sell_catches(member.id, key)
            if count:
                new_bal = await db.add_coins(member.id, total)
                await db.add_fishing_earned(member.id, total)
                await globalxp.award(member.id, "fish_sell")
        if count == 0:
            what = f"any **{FISH[key]['name']}**" if key else "anything to sell"
            return Screen(
                h.warn(f"You don't have {what}. Try `/fish cast`!", "🎒 Empty"),
                ok=False,
            )

        today = int(time.time() // 86400)
        qgen = generate_quest(member.id, today)
        quest = await db.get_or_create_quest(
            member.id, today, qgen["quest_key"], qgen["target"]
        )
        quest = await self._bump_quest(
            member.id, guild, quest, today, kind="earn", coins=total
        )
        quest_note = await self._award_quest_if_complete(member.id, guild, today, quest)

        what = FISH[key]["name"] if key else "item"
        desc = (
            f"Sold **{count}** {what}{'' if count == 1 else 's'} for "
            f"{self._money(econ, total)}.\n"
            f"Balance: {self._money(econ, new_bal)}"
        )
        if quest_note:
            desc += f"\n{quest_note}"
        return Screen(h.ok(desc, "💰 Sold"))

    @fish_sell.autocomplete("fish")
    async def _fish_sell_ac(self, interaction: discord.Interaction, current: str):
        """Pick from what's actually in your bag — no typing species names."""
        q = (current or "").strip().lower()
        bag = await db.get_bag(interaction.user.id) if interaction.guild_id else []
        choices: list[app_commands.Choice[str]] = []
        total = sum(row["total_value"] for row in bag)
        if bag and (not q or q in "everything" or q in "all"):
            choices.append(
                app_commands.Choice(
                    name=f"💰 Everything in your bag — {total:,} coins", value="all"
                )
            )
        for row in bag:
            entry = FISH.get(row["fish_key"])
            if not entry:
                continue
            if (
                q
                and q not in row["fish_key"].lower()
                and q not in entry["name"].lower()
            ):
                continue
            choices.append(
                app_commands.Choice(
                    name=f"{entry['emoji']} {entry['name']} ×{row['qty']} — "
                    f"{row['total_value']:,} coins"[:100],
                    value=row["fish_key"],
                )
            )
        return choices[:25]

    # ══════════════════════════════════════════════════════════════════════════
    #  /fish travel — the map
    # ══════════════════════════════════════════════════════════════════════════
    @fish.command(
        name="travel",
        description="Fish somewhere else — new species, different odds.",
    )
    @app_commands.describe(spot="Where to fish (the list shows what's open)")
    async def fish_travel(self, ctx: commands.Context, *, spot: Optional[str] = None):
        if spot is None:
            screen, state = await self.travel_screen(ctx.guild, ctx.author)
            return await self._send(ctx, screen, TravelView(self, ctx.author.id, state))
        key = find_spot(spot)
        if key is None:
            return await ctx.reply(
                embed=h.err(
                    f"There's nowhere called **{spot}**. Run `/fish travel` to see "
                    "the map."
                ),
                ephemeral=True,
            )
        screen, state = await self.travel_to(ctx.guild, ctx.author, key)
        await self._send(ctx, screen, TravelView(self, ctx.author.id, state))

    @fish_travel.autocomplete("spot")
    async def _fish_travel_ac(self, interaction: discord.Interaction, current: str):
        """Every spot, with what it would take — so the picker doubles as the
        map and never comes back empty on a fresh account."""
        q = (current or "").strip().lower()
        fisher = await db.get_fisher(interaction.user.id)
        unlocked = await db.get_unlocked_spots(interaction.user.id)
        balance = await db.get_balance(interaction.user.id)
        level = fish_level(fisher["xp"])
        here = resolve_spot(fisher["spot"])
        choices: list[app_commands.Choice[str]] = []
        for key in SPOT_ORDER:
            info = spot_info(key)
            if q and q not in key and q not in info["name"].lower():
                continue
            state = unlock_state(key, level, unlocked, balance)
            if key == here:
                mark = "📍 here"
            elif state["state"] == "open":
                mark = "✅ open"
            elif state["state"] == "buyable":
                mark = f"🪙 {state['reason']}"
            elif state["state"] == "poor":
                mark = f"🔒 {state['reason']}"
            else:
                mark = f"🔒 {state['reason']}"
            choices.append(
                app_commands.Choice(
                    name=f"{info['emoji']} {info['name']} — {mark}"[:100], value=key
                )
            )
        return choices[:25]

    async def travel_screen(
        self, guild: discord.Guild, member: discord.abc.User
    ) -> tuple["Screen", dict]:
        """The map, plus the state its buttons need."""
        econ = await db.get_econ_config(guild.id)
        fisher = await db.get_fisher(member.id)
        unlocked = await db.get_unlocked_spots(member.id)
        balance = await db.get_balance(member.id)
        level = fish_level(fisher["xp"])
        here = resolve_spot(fisher["spot"])
        states = {
            key: unlock_state(key, level, unlocked, balance) for key in SPOT_ORDER
        }

        embed = h.embed(
            "🗺️ The Map",
            "Every spot holds species you'll find nowhere else, and bends the "
            "odds its own way. A charter is bought once and yours forever — "
            "travelling between spots you've chartered is free.",
            h.BLUE,
        )
        for key in SPOT_ORDER:
            info = spot_info(key)
            state = states[key]
            bits = [info["blurb"]]
            exclusives = exclusive_keys(key)
            if exclusives:
                bits.append(
                    "Only here: "
                    + ", ".join(
                        f"{FISH[k]['emoji']} {FISH[k]['name']}" for k in exclusives
                    )
                )
            if info["hazard"]:
                bits.append(f"Snag risk: **{info['hazard']:.0%}** per cast")
            if key == here:
                bits.append("📍 **You're here.**")
            elif state["state"] == "open":
                bits.append("✅ Chartered — travel any time.")
            elif state["state"] == "buyable":
                bits.append(f"🪙 Charter: **{self._money(econ, info['price'])}**")
            elif state["state"] == "poor":
                bits.append(
                    f"🔒 Charter: {self._money(econ, info['price'])} — "
                    f"you have {self._money(econ, balance)}"
                )
            else:
                bits.append(f"🔒 {state['reason']}")
            embed.add_field(
                name=f"{info['emoji']} {info['name']}",
                value="\n".join(bits),
                inline=False,
            )
        embed.set_footer(text=f"Fishing level {level}")
        return Screen(embed), {"current": here, "spots": states}

    async def travel_to(
        self, guild: discord.Guild, member: discord.abc.User, spot: str
    ) -> tuple["Screen", dict]:
        """Travel to a spot, chartering it first if that's what's needed.

        Re-checks everything the button's label claimed, because that label was
        painted from a snapshot and both the level and the wallet move. The
        charter debits *before* the unlock lands and hands the coins back if
        the unlock loses a race — the /fish upgrade pattern.
        """
        econ = await db.get_econ_config(guild.id)
        info = spot_info(spot)
        async with self._lock(member.id):
            fisher = await db.get_fisher(member.id)
            unlocked = await db.get_unlocked_spots(member.id)
            balance = await db.get_balance(member.id)
            level = fish_level(fisher["xp"])
            state = unlock_state(spot, level, unlocked, balance)

            if state["state"] == "locked":
                screen = Screen(
                    h.err(
                        f"{info['emoji']} **{info['name']}** needs fishing level "
                        f"**{info['level']}** — you're level **{level}**.",
                        "🔒 Not Yet",
                    ),
                    ok=False,
                )
            elif state["state"] == "poor":
                screen = Screen(
                    h.err(
                        f"A charter to {info['emoji']} **{info['name']}** costs "
                        f"{self._money(econ, info['price'])} — you have "
                        f"{self._money(econ, balance)}.",
                        "🪙 Short",
                    ),
                    ok=False,
                )
            elif state["state"] == "buyable":
                if not await db.try_debit_coins(member.id, info["price"]):
                    screen = Screen(
                        h.err("You can't afford that charter.", "🪙 Short"), ok=False
                    )
                elif not await db.unlock_spot(member.id, spot, time.time()):
                    # A racing second charter already landed — hand it back.
                    await db.add_coins(member.id, info["price"])
                    await db.set_spot(member.id, spot)
                    screen = Screen(
                        h.info(
                            f"You already hold that charter — sailing to "
                            f"{info['emoji']} **{info['name']}**.",
                            "🗺️ Travelled",
                        )
                    )
                else:
                    await db.set_spot(member.id, spot)
                    screen = Screen(
                        h.ok(
                            f"Charter bought for {self._money(econ, info['price'])}.\n"
                            f"{info['emoji']} **{info['name']}** — {info['blurb']}\n"
                            + self._exclusives_line(spot),
                            "🗺️ New Waters",
                        )
                    )
            else:
                await db.set_spot(member.id, spot)
                screen = Screen(
                    h.ok(
                        f"{info['emoji']} **{info['name']}** — {info['blurb']}\n"
                        + self._exclusives_line(spot),
                        "🗺️ Travelled",
                    )
                )
        _map, state = await self.travel_screen(guild, member)
        return screen, state

    def _exclusives_line(self, spot: str) -> str:
        keys = exclusive_keys(spot)
        if not keys:
            return "Everything common swims here."
        return "Only here: " + ", ".join(
            f"{FISH[k]['emoji']} **{FISH[k]['name']}**" for k in keys
        )

    # ══════════════════════════════════════════════════════════════════════════
    #  /fish trap — fishing while you're not here
    # ══════════════════════════════════════════════════════════════════════════
    @fish.command(
        name="trap",
        description="Set a fish trap, or pull one that's finished soaking.",
    )
    async def fish_trap(self, ctx: commands.Context):
        await self._send(ctx, await self.trap_screen(ctx.guild, ctx.author))

    async def trap_screen(
        self, guild: discord.Guild, member: discord.abc.User
    ) -> "Screen":
        """One command for both halves: pull a ready trap, else set a new one.

        Two commands would mean remembering which state you're in, and the
        answer is always "do the thing that's possible right now".
        """
        econ = await db.get_econ_config(guild.id)
        async with self._lock(member.id):
            trap = await db.get_trap(member.id)
            if trap is not None:
                left = int(TRAP_SOAK - (time.time() - trap["set_at"]))
                if left > 0:
                    where = spot_info(resolve_spot(trap["spot"]))
                    return Screen(
                        h.info(
                            f"Your trap is still soaking at {where['emoji']} "
                            f"**{where['name']}** — ready in "
                            f"**{h.fmt_duration(left)}**.",
                            "🪤 Soaking",
                        ),
                        ok=False,
                    )
                pulled = await db.claim_trap(member.id, time.time(), TRAP_SOAK)
                if pulled is None:
                    return Screen(
                        h.warn("That trap was already pulled.", "🪤 Empty"), ok=False
                    )
                return await self._pull_trap(guild, member, econ, pulled["spot"])

            if not await db.try_consume_item(member.id, FISH_TRAP, 1):
                return Screen(
                    h.err(
                        "You don't have a 🪤 **Fish Trap**. Buy one with "
                        "`/fish buy trap`.",
                        "🪤 No Trap",
                    ),
                    ok=False,
                )
            fisher = await db.get_fisher(member.id)
            spot = resolve_spot(fisher["spot"])
            if not await db.set_trap(member.id, spot, time.time()):
                # Lost a race with another set — give the trap back rather than
                # eating it, since only one can be in the water.
                await db.add_item(member.id, FISH_TRAP, 1)
                return Screen(
                    h.warn("You've already got a trap in the water.", "🪤 Set"),
                    ok=False,
                )
            info = spot_info(spot)
            return Screen(
                h.ok(
                    f"Trap set at {info['emoji']} **{info['name']}**. Come back in "
                    f"**{h.fmt_duration(TRAP_SOAK)}** for a full basket.",
                    "🪤 Set",
                )
            )

    async def _pull_trap(
        self, guild: discord.Guild, member: discord.abc.User, econ: dict, spot: str
    ) -> "Screen":
        """Roll a soaked trap's catch into the bag.

        Rolled at the spot it was *set* in, not where the angler is standing
        now — the trap has been sitting in that water for hours, and letting a
        member set one in the pond and pull it at the Trench would make travel
        pointless. Rod luck applies; bait doesn't, since nobody was holding it.
        """
        spot = resolve_spot(spot)
        fisher = await db.get_fisher(member.id)
        luck = rod_info(fisher["rod_level"])["luck"] + level_luck_bonus(
            fish_level(fisher["xp"])
        )
        luck = max(0.0, min(MAX_LUCK, luck))
        odds = rarity_odds(luck)

        coins = 0
        counts: dict[str, int] = {}
        for _ in range(TRAP_CATCHES):
            rarity = pick_spot_rarity(random.random(), odds, spot)
            key = pick_spot_fish(rarity, random.random(), spot)
            counts[key] = counts.get(key, 0) + 1
            if rarity == "treasure":
                coins += max(1, treasure_coins(key, random.random()))
                continue
            weight = roll_weight(key, random.random())
            await db.record_catch(
                member.id,
                key,
                weight,
                catch_value(key, weight),
                track_best=rarity != "junk",
            )
        if coins:
            await db.add_coins(member.id, coins)
            await db.add_fishing_earned(member.id, coins)

        info = spot_info(spot)
        lines = [
            f"{FISH[k]['emoji']} **{FISH[k]['name']}**" + (f" ×{n}" if n > 1 else "")
            for k, n in counts.items()
        ]
        desc = (
            f"You haul the trap up from {info['emoji']} **{info['name']}**:\n"
            + "\n".join(lines)
        )
        if coins:
            desc += (
                f"\n\nPlus {self._money(econ, coins)} of treasure, paid on the spot."
            )
        desc += "\n\nSell it with `/fish sell`."
        return Screen(h.ok(desc, "🪤 Full Basket"))

    # ══════════════════════════════════════════════════════════════════════════
    #  The tackle shop
    # ══════════════════════════════════════════════════════════════════════════
    @fish.command(
        name="shop", description="Bait, tackle and consumables — buy with a tap."
    )
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def fish_shop(self, ctx: commands.Context):
        screen, stock = await self.shop_screen(ctx.guild, ctx.author)
        await self._send(ctx, screen, TackleShopView(self, ctx.author.id, stock))

    async def shop_screen(
        self, guild: discord.Guild, member: discord.abc.User
    ) -> tuple["Screen", list[dict]]:
        """The shelf, plus what its buttons need to know."""
        econ = await db.get_econ_config(guild.id)
        balance = await db.get_balance(member.id)
        stock: list[dict] = []
        lines: list[str] = []
        for item in self._bait_shop_items():
            owned = await db.get_item_qty(member.id, item.key)
            affordable = balance >= item.price
            stock.append(
                {
                    "key": item.key,
                    "label": f"{item.name} — {item.price:,}",
                    "emoji": item.emoji,
                    "affordable": affordable,
                }
            )
            mark = "" if affordable else "🔒 "
            have = f" · you have **{owned}**" if owned else ""
            lines.append(
                f"{mark}{item.emoji} **{item.name}** — "
                f"{self._money(econ, item.price)}{have}\n{item.description}"
            )
        embed = h.embed("🛒 Tackle Shop", "\n\n".join(lines), h.BLUE)
        embed.set_footer(
            text=f"You have {balance:,} · buttons buy one, /fish buy takes a quantity"
        )
        return Screen(embed), stock

    async def buy_one(
        self, guild: discord.Guild, member: discord.abc.User, item_key: str
    ) -> "Screen":
        """Buy a single item — what the shop's buttons do."""
        return await self.buy_items(guild, member, item_key, 1)

    # ── /fish rod ────────────────────────────────────────────────────────────
    @fish.command(name="rod", description="See your rod and the next upgrade.")
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def fish_rod(self, ctx: commands.Context):
        fisher = await db.get_fisher(ctx.author.id)
        econ = await db.get_econ_config(ctx.guild.id)
        rod = rod_info(fisher["rod_level"])
        nxt = next_rod(fisher["rod_level"])
        desc = (
            f"{rod['emoji']} **{rod['name']}** "
            f"(tier {fisher['rod_level'] + 1}/{len(RODS)})\n"
            f"Luck: **{rod['luck']:.0%}** less junk, better rare odds"
        )
        if nxt:
            desc += (
                f"\n\nNext: {nxt['emoji']} **{nxt['name']}** — "
                f"{self._money(econ, nxt['price'])}\n"
                "Buy it with `/fish upgrade`."
            )
        else:
            desc += "\n\nYou own the best rod there is. 🎉"
        await ctx.reply(embed=h.embed("🎣 Your Rod", desc, h.BLUE))

    # ── /fish upgrade ────────────────────────────────────────────────────────
    @fish.command(name="upgrade", description="Buy the next rod tier with coins.")
    async def fish_upgrade(self, ctx: commands.Context):
        econ = await db.get_econ_config(ctx.guild.id)
        balance = 0
        # Decide and settle under the lock, reply after releasing it: a Discord
        # round trip (or a 429) shouldn't block this member's other economy
        # commands.
        async with self._lock(ctx.author.id):
            fisher = await db.get_fisher(ctx.author.id)
            nxt = next_rod(fisher["rod_level"])
            outcome = "ok"
            if nxt is None:
                outcome = "maxed"
            elif not await db.try_debit_coins(ctx.author.id, nxt["price"]):
                outcome = "poor"
                balance = await db.get_balance(ctx.author.id)
            elif not await db.set_rod_level(
                ctx.author.id,
                fisher["rod_level"] + 1,
                expected=fisher["rod_level"],
            ):
                # Someone (a second racing invocation) already advanced the rod
                # — hand the coins back.
                await db.add_coins(ctx.author.id, nxt["price"])
                outcome = "raced"
        if outcome == "maxed":
            return await ctx.reply(
                embed=h.info("You already own the best rod there is!", "🎣 Maxed"),
                ephemeral=True,
            )
        if outcome == "poor":
            return await ctx.reply(
                embed=h.err(
                    f"{nxt['emoji']} **{nxt['name']}** costs "
                    f"{self._money(econ, nxt['price'])} — you have "
                    f"{self._money(econ, balance)}."
                ),
                ephemeral=True,
            )
        if outcome == "raced":
            return await ctx.reply(
                embed=h.warn("That upgrade already went through.", "🎣 Rod"),
                ephemeral=True,
            )
        await ctx.reply(
            embed=h.ok(
                f"You bought the {nxt['emoji']} **{nxt['name']}** for "
                f"{self._money(econ, nxt['price'])}!\n"
                f"Luck is now **{nxt['luck']:.0%}** — fewer boots, more trophies.",
                "🎣 Upgraded",
            )
        )

    # ── /fish dex ────────────────────────────────────────────────────────────
    @fish.command(name="dex", description="Your species collection progress.")
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def fish_dex(self, ctx: commands.Context):
        counts = await db.get_species_counts(ctx.author.id)
        # Treasure never enters the bag/species table, so it isn't collectible.
        collectible = [r for r, _ in RARITIES.items() if r != "treasure"]
        total = sum(len(FISH_BY_RARITY[r]) for r in collectible)
        found = sum(1 for k in counts if FISH[k]["rarity"] != "treasure")
        embed = h.embed(
            "📖 Fish Dex",
            f"Discovered **{found}/{total}** species.",
            h.BLUE,
        )
        for rarity in collectible:
            label, _color = RARITIES[rarity]
            keys = FISH_BY_RARITY[rarity]
            caught = [k for k in keys if k in counts]
            if not caught:
                value = f"Nothing yet (0/{len(keys)})"
            else:
                value = ", ".join(
                    f"{FISH[k]['emoji']} {FISH[k]['name']} ×{counts[k]}" for k in caught
                )
                value = f"({len(caught)}/{len(keys)}) {value}"
            embed.add_field(name=label, value=value[:1024], inline=False)
        await ctx.reply(embed=embed)

    # ── /fish stats ──────────────────────────────────────────────────────────
    @fish.command(name="stats", description="Fishing stats for you or another member.")
    @app_commands.describe(member="Whose stats to show (defaults to you)")
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def fish_stats(
        self, ctx: commands.Context, member: Optional[discord.Member] = None
    ):
        member = member or ctx.author
        if member.bot:
            return await ctx.reply(embed=h.err("Bots don't fish."), ephemeral=True)
        fisher = await db.get_fisher(member.id)
        econ = await db.get_econ_config(ctx.guild.id)
        rod = rod_info(fisher["rod_level"])
        rank = await db.get_fishing_rank(member.id)

        embed = h.embed(f"🎣 {member.display_name}", color=h.BLUE)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="Casts", value=f"**{fisher['casts']:,}**", inline=True)
        embed.add_field(name="Caught", value=f"**{fisher['caught']:,}**", inline=True)
        embed.add_field(
            name="Earned", value=self._money(econ, fisher["earned"]), inline=True
        )
        embed.add_field(name="Rod", value=f"{rod['emoji']} {rod['name']}", inline=True)
        embed.add_field(
            name="Global rank",
            value=f"**#{rank[0]}**" if rank else "Unranked",
            inline=True,
        )
        level, into, needed = level_progress(fisher["xp"])
        embed.add_field(
            name="Level", value=f"**{level}** ({into}/{needed} XP)", inline=True
        )
        if fisher["streak_days"]:
            embed.add_field(
                name="Streak", value=f"🔥 **{fisher['streak_days']}** days", inline=True
            )
        if fisher["best_key"] and fisher["best_key"] in FISH:
            best = FISH[fisher["best_key"]]
            embed.add_field(
                name="Best catch",
                value=f"{best['emoji']} {best['name']} — "
                f"**{fmt_weight(fisher['best_weight'])}**",
                inline=True,
            )
        await ctx.reply(embed=embed)

    # ── /fish top ────────────────────────────────────────────────────────────
    @fish.command(name="top", description="Top-earning anglers, here or everywhere.")
    @app_commands.describe(
        page="Page number (10 per page)",
        scope="This server's members, or everyone",
    )
    @app_commands.choices(scope=SCOPE_CHOICES)
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def fish_top(
        self, ctx: commands.Context, page: int = 1, scope: str = "server"
    ):
        await paginator.send(
            ctx,
            lambda p, s: self._top_page(ctx, p, s or "server"),
            page=page,
            switch=paginator.scope_switch(scope),
        )

    async def _top_page(
        self, ctx: commands.Context, page: int, scope: str
    ) -> paginator.Page:
        econ = await db.get_econ_config(ctx.guild.id)
        per = 10
        if scope == "global":
            total = await db.count_fishers()
            pages = max(1, (total + per - 1) // per)
            page = max(1, min(page, pages))
            offset = (page - 1) * per
            rows = await db.get_fishing_leaderboard(per, offset)
        else:
            all_rows = await db.get_fishing_leaderboard_for(h.member_ids(ctx.guild))
            rows, page, pages, total = h.page_rows(
                all_rows, lambda r: (-r["earned"], r["user_id"]), page, per
            )
            offset = (page - 1) * per
        if total == 0:
            return paginator.Page(
                h.info("No one has earned anything yet. Try `/fish`!", "🎣 Anglers")
            )

        medals = {1: "🥇", 2: "🥈", 3: "🥉"}
        lines = []
        for i, row in enumerate(rows):
            pos = offset + i + 1
            badge = medals.get(pos, f"`#{pos}`")
            lines.append(
                f"{badge} **{self._name_for(ctx, row['user_id'])}** — "
                f"{self._money(econ, row['earned'])} ({row['caught']:,} caught)"
            )
        title = "🎣 Top Anglers" + (" (Global)" if scope == "global" else "")
        embed = h.embed(title, "\n".join(lines), h.BLUE)
        embed.set_footer(text=f"Page {page}/{pages} · {total} anglers")
        return paginator.Page(embed, page, pages)

    def _name_for(self, ctx: commands.Context, user_id: int) -> str:
        """Display name for a leaderboard row — a global board lists anglers who
        may not be in this server."""
        member = ctx.guild.get_member(user_id) if ctx.guild else None
        if member:
            return member.display_name
        user = self.bot.get_user(user_id)
        return user.display_name if user else f"User {user_id}"

    # ── /fish global ─────────────────────────────────────────────────────────
    @fish.command(name="global", description="Cross-server fishing leaderboard.")
    @app_commands.describe(
        stat="Which stat to rank by", page="Page number (10 per page)"
    )
    @app_commands.choices(
        stat=[
            app_commands.Choice(name=v["label"], value=k)
            for k, v in db.GLOBAL_STATS.items()
        ]
    )
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def fish_global(
        self, ctx: commands.Context, stat: str = "earned", page: int = 1
    ):
        if stat not in db.GLOBAL_STATS:
            valid = ", ".join(db.GLOBAL_STATS)
            return await ctx.reply(
                embed=h.err(f"I don't know the stat **{stat}**. Try: {valid}."),
                ephemeral=True,
            )
        # The stat is this board's second dimension the way scope is the other
        # boards': six of them, so it's a dropdown rather than a button row.
        await paginator.send(
            ctx,
            lambda p, s: self._global_page(ctx, p, s or "earned"),
            page=page,
            switch=paginator.Switch(
                options=[(k, v["label"]) for k, v in db.GLOBAL_STATS.items()],
                current=stat,
                placeholder="Rank by another stat…",
            ),
        )

    async def _global_page(
        self, ctx: commands.Context, page: int, stat: str
    ) -> paginator.Page:
        label = db.GLOBAL_STATS[stat]["label"]
        econ = await db.get_econ_config(ctx.guild.id)
        page = max(1, page)
        per = 10
        total = await db.count_global_leaderboard(stat)
        if total == 0:
            return paginator.Page(
                h.info(f"No one has any {label} yet.", "🌐 Global Leaderboard")
            )
        pages = (total + per - 1) // per
        page = min(page, pages)
        offset = (page - 1) * per
        rows = await db.get_global_leaderboard(stat, per, offset)

        medals = {1: "🥇", 2: "🥈", 3: "🥉"}
        lines = []
        for i, row in enumerate(rows):
            pos = offset + i + 1
            user = self.bot.get_user(row["user_id"])
            name = user.display_name if user else f"User {row['user_id']}"
            badge = medals.get(pos, f"`#{pos}`")
            if stat == "earned":
                display = self._money(econ, int(row["value"]))
            elif stat == "best_weight":
                display = fmt_weight(row["value"])
            else:
                display = f"{int(row['value']):,}"
            lines.append(f"{badge} **{name}** — {display}")
        embed = h.embed(f"🌐 Global {label}", "\n".join(lines), h.BLUE)
        embed.set_footer(
            text=f"Page {page}/{pages} · {total} anglers · every server combined"
        )
        return paginator.Page(embed, page, pages)

    # ── /fish buy ────────────────────────────────────────────────────────────
    @fish.command(name="buy", description="Buy bait or fishing consumables with coins.")
    @app_commands.describe(
        item="Pick an item from the bait shop list",
        qty="How many to buy (default 1)",
    )
    async def fish_buy(self, ctx: commands.Context, item: str, qty: int = 1):
        await self._send(ctx, await self.buy_items(ctx.guild, ctx.author, item, qty))

    async def buy_items(
        self, guild: discord.Guild, member: discord.abc.User, item: str, qty: int = 1
    ) -> "Screen":
        d = item_catalog.find(item) or self._match_stock(item)
        if d is None or not d.key.startswith(("bait_", "fish_")) or d.price <= 0:
            stock = ", ".join(
                f"{s.name} ({s.price:,})" for s in self._bait_shop_items()
            )
            return Screen(
                h.err(f"**{item}** isn't sold at the bait shop. In stock: {stock}."),
                ok=False,
            )
        qty = max(1, min(qty, 100))
        total = d.price * qty
        econ = await db.get_econ_config(guild.id)
        new_qty = balance = 0
        async with self._lock(member.id):
            paid = await db.try_debit_coins(member.id, total)
            if paid:
                new_qty = await db.add_item(member.id, d.key, qty)
            else:
                balance = await db.get_balance(member.id)
        if not paid:
            return Screen(
                h.err(
                    f"{d.emoji} **{d.name}** ×{qty} costs "
                    f"{self._money(econ, total)} — you have "
                    f"{self._money(econ, balance)}."
                ),
                ok=False,
            )
        # A trap is set at a spot rather than armed as an effect, so pointing at
        # /inventory use would send the buyer somewhere that can't help them.
        how = (
            "Set it with `/fish trap`."
            if d.key == FISH_TRAP
            else f"Arm it with `/inventory use {d.name}`."
        )
        return Screen(
            h.ok(
                f"Bought **{qty}** {d.emoji} **{d.name}** for "
                f"{self._money(econ, total)}.\nYou now have **{new_qty}**. {how}",
                "🛒 Bought",
            )
        )

    def _match_stock(self, query: str):
        """Loose match against this shop's own stock, for prefix users.

        The catalogue's `find` wants a key or a full name, which is right for a
        global lookup and wrong at a counter: someone typing `!fish buy trap`
        means the Fish Trap, and there is nothing else on the shelf it could
        be. Slash users never reach this — their picker hands over the key.
        Ambiguity refuses rather than guessing.
        """
        q = (query or "").strip().lower()
        if not q:
            return None
        hits = [d for d in self._bait_shop_items() if q in d.name.lower() or q in d.key]
        return hits[0] if len(hits) == 1 else None

    def _bait_shop_items(self) -> list:
        """Everything /fish buy sells, cheapest first."""
        return sorted(
            (
                d
                for d in item_catalog.ITEMS.values()
                if d.key.startswith(("bait_", "fish_")) and d.price > 0
            ),
            key=lambda d: d.price,
        )

    @fish_buy.autocomplete("item")
    async def _fish_buy_ac(self, interaction: discord.Interaction, current: str):
        """Bait shop as a tap-to-pick list, priced and marked by affordability."""
        q = (current or "").strip().lower()
        balance = (
            await db.get_balance(interaction.user.id) if interaction.guild_id else 0
        )
        choices: list[app_commands.Choice[str]] = []
        for d in self._bait_shop_items():
            if q and q not in d.key.lower() and q not in d.name.lower():
                continue
            mark = "✅" if balance >= d.price else "🔒"
            choices.append(
                app_commands.Choice(
                    name=f"{mark} {d.emoji} {d.name} — {d.price:,} coins"[:100],
                    value=d.key,
                )
            )
        return choices[:25]

    # ── /fish bait ───────────────────────────────────────────────────────────
    @fish.command(
        name="bait", description="Your owned bait/consumables and what's armed."
    )
    async def fish_bait(self, ctx: commands.Context):
        inv = await db.get_inventory(ctx.author.id)
        owned_lines = [
            f"{d.emoji} **{d.name}** ×{row['qty']}"
            for row in inv
            if (d := item_catalog.get(row["item_key"]))
            and d.key.startswith(("bait_", "fish_"))
        ]
        effects = await db.get_active_effects(ctx.author.id)
        armed_lines = []
        if "fish_bait" in effects:
            eff = effects["fish_bait"]
            armed_lines.append(
                f"🪱 Bait armed: +{eff['magnitude']:.0%} luck, "
                f"**{eff['uses_left']}** cast(s) left"
            )
        if "fish_xp" in effects:
            eff = effects["fish_xp"]
            remaining = max(0, int(eff["expires_at"] - time.time()))
            armed_lines.append(
                f"⭐ XP boost armed: **{eff['magnitude']:g}x** XP, "
                f"{h.fmt_duration(remaining)} left"
            )
        if not owned_lines and not armed_lines:
            return await ctx.reply(
                embed=h.info(
                    "You don't have any bait yet. Buy some with `/fish buy`!",
                    "🪱 Bait",
                )
            )
        embed = h.embed("🪱 Bait", color=h.BLUE)
        embed.add_field(
            name="Owned",
            value="\n".join(owned_lines) if owned_lines else "Nothing owned yet.",
            inline=False,
        )
        embed.add_field(
            name="Armed",
            value=(
                "\n".join(armed_lines)
                if armed_lines
                else "Nothing armed. Use `/inventory use <bait>` before casting."
            ),
            inline=False,
        )
        await ctx.reply(embed=embed)

    # ── /fish quest ──────────────────────────────────────────────────────────
    @fish.command(name="quest", description="Today's fishing quest and your progress.")
    async def fish_quest(self, ctx: commands.Context):
        today = int(time.time() // 86400)
        qgen = generate_quest(ctx.author.id, today)
        quest = await db.get_or_create_quest(
            ctx.author.id, today, qgen["quest_key"], qgen["target"]
        )
        label = quest_label(quest["quest_key"], quest["target"])
        econ = await db.get_econ_config(ctx.guild.id)
        coins, xp = quest_reward(quest["quest_key"], quest["target"])
        if quest["claimed"]:
            desc = (
                f"**{label}**\n✅ Complete! You claimed "
                f"{self._money(econ, coins)} + **{xp}** XP today."
            )
        else:
            desc = (
                f"**{label}**\nProgress: **{quest['progress']}/{quest['target']}**\n"
                f"Reward: {self._money(econ, coins)} + **{xp}** XP"
            )
        await ctx.reply(embed=h.embed("🗺️ Today's Quest", desc, h.BLUE))

    # ── /fish events ─────────────────────────────────────────────────────────
    @fish.command(name="events", description="Active fishing events and time left.")
    async def fish_events(self, ctx: commands.Context):
        events = await db.get_active_events(ctx.guild.id)
        if not events:
            return await ctx.reply(
                embed=h.info("No fishing events running right now.", "🌊 Events")
            )
        now = time.time()
        lines = []
        for ev in events:
            remaining = max(0, int(ev["ends_at"] - now))
            label = EVENT_LABELS.get(ev["event_key"], ev["event_key"])
            lines.append(f"{label} — **{h.fmt_duration(remaining)}** left")
        embed = h.embed("🌊 Active Events", "\n".join(lines), RARITIES["treasure"][1])
        await ctx.reply(embed=embed)

    # ══════════════════════════════════════════════════════════════════════════
    #  Admin subcommands (Manage Server)
    # ══════════════════════════════════════════════════════════════════════════
    @fish.command(name="toggle", description="Turn fishing on or off for this server.")
    @commands.has_permissions(manage_guild=True)
    async def fish_toggle(self, ctx: commands.Context):
        cfg = await db.get_fishing_config(ctx.guild.id)
        enabled = not cfg["enabled"]
        await db.set_fishing_config(ctx.guild.id, enabled=enabled)
        if enabled:
            await ctx.reply(embed=h.ok("Fishing is now **enabled**. 🎣"))
        else:
            await ctx.reply(embed=h.ok("Fishing is now **disabled**."))

    # Bot-owner only, not Manage Server. `frenzy` doubles catch *value* and
    # `lucky_waters` shifts the rarity table toward the expensive tiers, so
    # starting one is turning up the coin faucet — and the coins it mints spend
    # in every server, not just the one that ran the command. Nothing stopped a
    # mod running back-to-back 3-hour frenzies forever. Events still fire on
    # their own (~0.4% per cast) for everybody, which is the fun part; this is
    # only the manual override. Mods keep `/fish toggle`.
    # Prefix-only for the same reason it is owner-only: it is a lever on the
    # coin faucet, not a fishing feature, and it has no business occupying a row
    # in the /fish picker that every angler scrolls past. `n!fish event frenzy 30`.
    @fish.command(
        name="event",
        description="Force-start a fishing event (bot owner).",
        with_app_command=False,
    )
    @commands.is_owner()
    async def fish_event(self, ctx: commands.Context, key: str, minutes: int = 15):
        key = key.strip().lower()
        event_def = next((e for e in EVENT_POOL if e["key"] == key), None)
        if event_def is None:
            valid = ", ".join(e["key"] for e in EVENT_POOL)
            return await ctx.reply(
                embed=h.err(f"Unknown event **{key}**. Choose from: {valid}."),
                ephemeral=True,
            )
        minutes = max(1, min(180, minutes))
        await db.start_event(
            ctx.guild.id, event_def["key"], event_def["magnitude"], minutes * 60
        )
        label = EVENT_LABELS.get(event_def["key"], event_def["key"])
        await ctx.reply(embed=h.ok(f"{label} started for **{minutes}** minute(s)."))

    @fish.command(name="config", description="Show the fishing settings.")
    @commands.has_permissions(manage_guild=True)
    async def fish_config(self, ctx: commands.Context):
        cfg = await db.get_fishing_config(ctx.guild.id)
        embed = h.embed("🎣 Fishing Settings", color=h.BLUE)
        embed.add_field(
            name="Enabled", value="✅ Yes" if cfg["enabled"] else "❌ No", inline=True
        )
        embed.add_field(
            name="Cast cooldown",
            value=f"{h.fmt_duration(CAST_COOLDOWN)} (fixed, bot-wide)",
            inline=True,
        )
        embed.add_field(name="Species", value=str(len(FISH)), inline=True)
        embed.add_field(
            name="Rod tiers",
            value=" → ".join(f"{r['emoji']} {r['name']}" for r in RODS),
            inline=False,
        )
        await ctx.reply(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(Fishing(bot))
