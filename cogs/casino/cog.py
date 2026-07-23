"""
cogs/casino/cog.py
Casino minigames — a NanoCoin economy sink/faucet with a shared progressive
jackpot.

Five bet-and-resolve games (flip, dice, slots, roulette, blackjack) all debit
the bet up front via the atomic economy API, resolve the outcome from an
explicit random roll (or, for blackjack, an explicit shuffled shoe) through
pure helpers in helpers.py, then credit any payout. A 3+ consecutive win
streak adds a small payout bonus (capped at +25%); losing resets it. 20% of
every net loss a player takes feeds a per-guild progressive jackpot, which a
triple-7️⃣ slots spin claims outright.

Slash command budget: one group (/casino …) — subcommands cost no extra
top-level slots.

──────────────────────────────────────────────────────
Commands
──────────────────────────────────────────────────────
  /casino                          → overview: games, limits, jackpot, your stats
  /casino flip <bet> <side>        → 50/50 coin flip (heads/tails), pays 1.92x
  /casino dice <bet>               → 2d6 vs the dealer, higher wins, pays 2.1x
  /casino slots <bet>              → 3-reel slots, triple 7️⃣ also wins the jackpot
  /casino roulette <bet> <space>   → European wheel: color/parity/range or a number
  /casino blackjack <bet>          → interactive Hit/Stand vs the dealer
  /casino jackpot                  → see the progressive jackpot pool
  /casino stats [member]           → games, wagered, won, net, biggest win, streak
  /casino top [page]               → leaderboard by net winnings
  /casino limit <min> <max>        → set bet bounds              (Manage Server)
  /casino toggle                   → enable/disable casino games (Manage Server)
  /casino config                   → show settings                (Manage Server)
"""

import asyncio
import logging
import random
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from utils import db
from utils import helpers as h

from .constants import BLACKJACK_DECKS, JACKPOT_FEED_RATE, SLOT_JACKPOT_SYMBOL
from .helpers import (
    apply_streak_bonus,
    is_blackjack,
    new_shoe,
    parse_roulette_space,
    resolve_dice,
    resolve_flip,
    resolve_roulette,
    resolve_slots,
    settle_blackjack,
    spin_number,
)
from .views import BlackjackView, _bj_embed

log = logging.getLogger("NanoBot.casino")


class Casino(commands.Cog):
    """Bet-and-resolve minigames on the NanoCoin economy, plus a shared jackpot."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Per-(guild, user) locks serialize debit → resolve → credit → record so
        # a double-send can't be charged twice or corrupt the win-streak read
        # (the /daily / fishing pattern).
        self._locks: dict[tuple[int, int], asyncio.Lock] = {}

    def _lock(self, guild_id: int, user_id: int) -> asyncio.Lock:
        key = (guild_id, user_id)
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        return lock

    def _money(self, econ: dict, amount: int) -> str:
        name = econ["currency_name"]
        label = name if abs(amount) == 1 else f"{name}s"
        return f"{econ['currency_emoji']} **{amount:,}** {label}"

    def _bet_error(self, cfg: dict, bet: int) -> Optional[str]:
        if bet <= 0:
            return "Bet must be a positive number of coins."
        if bet < cfg["min_bet"] or bet > cfg["max_bet"]:
            return f"Bet must be between **{cfg['min_bet']:,}** and **{cfg['max_bet']:,}**."
        return None

    async def _settle_common(
        self, guild_id: int, user_id: int, bet: int, streak: int, raw_payout: int
    ) -> tuple[int, dict]:
        """Apply the streak bonus (on a true win), feed the jackpot on a net
        loss, record the game, and return (final_payout, updated_stats).

        Caller must already hold `self._lock(guild_id, user_id)` and must have
        already debited `bet`.
        """
        final_payout = raw_payout
        if raw_payout > bet:
            new_streak = streak + 1
            final_payout = apply_streak_bonus(raw_payout, new_streak)
        if final_payout > 0:
            await db.add_coins(guild_id, user_id, final_payout)
        net = final_payout - bet
        if net < 0:
            await db.add_to_jackpot(guild_id, round(-net * JACKPOT_FEED_RATE))
        stats = await db.record_casino_game(guild_id, user_id, bet, final_payout)
        return final_payout, stats

    # ══════════════════════════════════════════════════════════════════════════
    #  /casino  group
    # ══════════════════════════════════════════════════════════════════════════
    @commands.hybrid_group(
        name="casino",
        description="Casino minigames — bet NanoCoins on flip, dice, slots, "
        "roulette, or blackjack.",
        invoke_without_command=True,
        extras={
            "category": "🪙 Economy",
            "short": "Bet coins on flip, dice, slots, roulette, and blackjack",
            "usage": "casino [subcommand]",
            "desc": "Bet NanoCoins across five games, chase a shared progressive "
            "jackpot (triple 7️⃣ on /casino slots claims it), and build a "
            "win streak for a payout bonus. Admins can toggle the casino "
            "and set bet limits.",
            "args": [],
            "perms": "Admin subcommands require Manage Server",
            "example": "{prefix}casino\n{prefix}casino flip 50 heads\n"
            "{prefix}casino slots 100",
        },
    )
    @commands.guild_only()
    async def casino(self, ctx: commands.Context):
        if ctx.invoked_subcommand is None:
            await self._do_overview(ctx)

    async def _do_overview(self, ctx: commands.Context):
        cfg = await db.get_casino_config(ctx.guild.id)
        econ = await db.get_econ_config(ctx.guild.id)
        stats = await db.get_casino_stats(ctx.guild.id, ctx.author.id)
        desc = (
            "🪙 **flip** <bet> <heads|tails> — 50/50, pays 1.92x\n"
            "🎲 **dice** <bet> — 2d6 vs the dealer, pays 2.1x\n"
            "🎰 **slots** <bet> — 3 reels, triple 7️⃣ wins the jackpot\n"
            "🎡 **roulette** <bet> <space> — color/parity/range 2x, number 35x\n"
            "🃏 **blackjack** <bet> — Hit/Stand vs the dealer, blackjack pays 3:2"
        )
        embed = h.embed("🎰 Casino", desc, h.BLUE)
        embed.add_field(
            name="Bet limits",
            value=f"{self._money(econ, cfg['min_bet'])} – {self._money(econ, cfg['max_bet'])}",
            inline=True,
        )
        embed.add_field(
            name="Jackpot", value=self._money(econ, cfg["jackpot_pool"]), inline=True
        )
        embed.add_field(
            name="Status",
            value="✅ Open" if cfg["enabled"] else "❌ Closed",
            inline=True,
        )
        if stats["games"]:
            net = stats["won"] - stats["wagered"]
            embed.add_field(
                name="Your stats",
                value=f"{stats['games']:,} games · net {self._money(econ, net)} · "
                f"streak {stats['streak']}",
                inline=False,
            )
        embed.set_footer(text="Try /casino flip, /casino slots, or /casino blackjack")
        await ctx.reply(embed=embed)

    # ── /casino flip ─────────────────────────────────────────────────────────
    @casino.command(name="flip", description="Bet on a 50/50 coin flip.")
    @app_commands.describe(bet="How many coins to bet", side="heads or tails")
    async def casino_flip(self, ctx: commands.Context, bet: int, side: str):
        cfg = await db.get_casino_config(ctx.guild.id)
        if not cfg["enabled"]:
            return await ctx.reply(
                embed=h.err("Casino games are disabled on this server."), ephemeral=True
            )
        choice = side.strip().lower()
        if choice not in ("heads", "tails"):
            return await ctx.reply(
                embed=h.err("Pick **heads** or **tails**."), ephemeral=True
            )
        err = self._bet_error(cfg, bet)
        if err:
            return await ctx.reply(embed=h.err(err), ephemeral=True)

        econ = await db.get_econ_config(ctx.guild.id)
        async with self._lock(ctx.guild.id, ctx.author.id):
            if not await db.try_debit_coins(ctx.guild.id, ctx.author.id, bet):
                balance = await db.get_balance(ctx.guild.id, ctx.author.id)
                return await ctx.reply(
                    embed=h.err(
                        f"You need {self._money(econ, bet)} to play — you have "
                        f"{self._money(econ, balance)}."
                    ),
                    ephemeral=True,
                )
            stats = await db.get_casino_stats(ctx.guild.id, ctx.author.id)
            outcome = resolve_flip(bet, choice, random.random())
            final_payout, new_stats = await self._settle_common(
                ctx.guild.id, ctx.author.id, bet, stats["streak"], outcome["payout"]
            )

        if outcome["won"]:
            desc = (
                f"The coin landed on **{outcome['result']}**! You won "
                f"{self._money(econ, final_payout)}."
            )
            title, color = "🪙 You Won!", h.GREEN
        else:
            desc = f"The coin landed on **{outcome['result']}**. You lost {self._money(econ, bet)}."
            title, color = "🪙 You Lost", h.RED
        embed = h.embed(title, desc, color)
        if new_stats["streak"] >= 3:
            embed.set_footer(text=f"🔥 {new_stats['streak']}-win streak!")
        await ctx.reply(embed=embed)

    # ── /casino dice ─────────────────────────────────────────────────────────
    @casino.command(name="dice", description="Roll 2d6 against the dealer.")
    @app_commands.describe(bet="How many coins to bet")
    async def casino_dice(self, ctx: commands.Context, bet: int):
        cfg = await db.get_casino_config(ctx.guild.id)
        if not cfg["enabled"]:
            return await ctx.reply(
                embed=h.err("Casino games are disabled on this server."), ephemeral=True
            )
        err = self._bet_error(cfg, bet)
        if err:
            return await ctx.reply(embed=h.err(err), ephemeral=True)

        econ = await db.get_econ_config(ctx.guild.id)
        async with self._lock(ctx.guild.id, ctx.author.id):
            if not await db.try_debit_coins(ctx.guild.id, ctx.author.id, bet):
                balance = await db.get_balance(ctx.guild.id, ctx.author.id)
                return await ctx.reply(
                    embed=h.err(
                        f"You need {self._money(econ, bet)} to play — you have "
                        f"{self._money(econ, balance)}."
                    ),
                    ephemeral=True,
                )
            stats = await db.get_casino_stats(ctx.guild.id, ctx.author.id)
            outcome = resolve_dice(
                bet,
                (random.random(), random.random()),
                (random.random(), random.random()),
            )
            final_payout, new_stats = await self._settle_common(
                ctx.guild.id, ctx.author.id, bet, stats["streak"], outcome["payout"]
            )

        rolls_desc = (
            f"You rolled **{' + '.join(map(str, outcome['player_rolls']))} = "
            f"{outcome['player_total']}**\n"
            f"Dealer rolled **{' + '.join(map(str, outcome['dealer_rolls']))} = "
            f"{outcome['dealer_total']}**"
        )
        if outcome["outcome"] == "win":
            desc = f"{rolls_desc}\n\nYou won {self._money(econ, final_payout)}!"
            title, color = "🎲 You Won!", h.GREEN
        elif outcome["outcome"] == "push":
            desc = f"{rolls_desc}\n\nIt's a push — your {self._money(econ, bet)} bet is back."
            title, color = "🎲 Push", h.YELLOW
        else:
            desc = f"{rolls_desc}\n\nYou lost {self._money(econ, bet)}."
            title, color = "🎲 You Lost", h.RED
        embed = h.embed(title, desc, color)
        if new_stats["streak"] >= 3:
            embed.set_footer(text=f"🔥 {new_stats['streak']}-win streak!")
        await ctx.reply(embed=embed)

    # ── /casino slots ────────────────────────────────────────────────────────
    @casino.command(name="slots", description="Spin the 3-reel slot machine.")
    @app_commands.describe(bet="How many coins to bet")
    async def casino_slots(self, ctx: commands.Context, bet: int):
        cfg = await db.get_casino_config(ctx.guild.id)
        if not cfg["enabled"]:
            return await ctx.reply(
                embed=h.err("Casino games are disabled on this server."), ephemeral=True
            )
        err = self._bet_error(cfg, bet)
        if err:
            return await ctx.reply(embed=h.err(err), ephemeral=True)

        econ = await db.get_econ_config(ctx.guild.id)
        jackpot_award = 0
        async with self._lock(ctx.guild.id, ctx.author.id):
            if not await db.try_debit_coins(ctx.guild.id, ctx.author.id, bet):
                balance = await db.get_balance(ctx.guild.id, ctx.author.id)
                return await ctx.reply(
                    embed=h.err(
                        f"You need {self._money(econ, bet)} to play — you have "
                        f"{self._money(econ, balance)}."
                    ),
                    ephemeral=True,
                )
            stats = await db.get_casino_stats(ctx.guild.id, ctx.author.id)
            outcome = resolve_slots(
                bet, (random.random(), random.random(), random.random())
            )
            final_payout, new_stats = await self._settle_common(
                ctx.guild.id, ctx.author.id, bet, stats["streak"], outcome["payout"]
            )
            if outcome["jackpot_win"]:
                jackpot_award = await db.try_claim_jackpot(ctx.guild.id)
                if jackpot_award:
                    await db.add_coins(ctx.guild.id, ctx.author.id, jackpot_award)

        reels = " ".join(outcome["reels"])
        if outcome["outcome"] == "triple":
            desc = (
                f"[ {reels} ] — **Triple!** You won {self._money(econ, final_payout)}."
            )
            title, color = "🎰 Jackpot Symbols!", h.GREEN
        elif outcome["outcome"] == "pair":
            desc = f"[ {reels} ] — pair. You won {self._money(econ, final_payout)}."
            title, color = "🎰 Small Win", h.GREEN
        else:
            desc = f"[ {reels} ] — no match. You lost {self._money(econ, bet)}."
            title, color = "🎰 No Match", h.RED
        embed = h.embed(title, desc, color)
        if jackpot_award:
            embed.add_field(
                name="💰 PROGRESSIVE JACKPOT!",
                value=f"Triple {SLOT_JACKPOT_SYMBOL} paid out the whole pot: "
                f"{self._money(econ, jackpot_award)}!",
                inline=False,
            )
        if new_stats["streak"] >= 3:
            embed.set_footer(text=f"🔥 {new_stats['streak']}-win streak!")
        await ctx.reply(embed=embed)

    # ── /casino roulette ─────────────────────────────────────────────────────
    @casino.command(name="roulette", description="Spin the European roulette wheel.")
    @app_commands.describe(
        bet="How many coins to bet",
        space="red / black / odd / even / high / low, or an exact number 0-36",
    )
    async def casino_roulette(self, ctx: commands.Context, bet: int, space: str):
        cfg = await db.get_casino_config(ctx.guild.id)
        if not cfg["enabled"]:
            return await ctx.reply(
                embed=h.err("Casino games are disabled on this server."), ephemeral=True
            )
        parsed_space = parse_roulette_space(space)
        if parsed_space is None:
            return await ctx.reply(
                embed=h.err(
                    "Bet on **red**, **black**, **odd**, **even**, **high** "
                    "(19-36), **low** (1-18), or an exact number **0-36**."
                ),
                ephemeral=True,
            )
        err = self._bet_error(cfg, bet)
        if err:
            return await ctx.reply(embed=h.err(err), ephemeral=True)

        econ = await db.get_econ_config(ctx.guild.id)
        async with self._lock(ctx.guild.id, ctx.author.id):
            if not await db.try_debit_coins(ctx.guild.id, ctx.author.id, bet):
                balance = await db.get_balance(ctx.guild.id, ctx.author.id)
                return await ctx.reply(
                    embed=h.err(
                        f"You need {self._money(econ, bet)} to play — you have "
                        f"{self._money(econ, balance)}."
                    ),
                    ephemeral=True,
                )
            stats = await db.get_casino_stats(ctx.guild.id, ctx.author.id)
            number = spin_number(random.random())
            outcome = resolve_roulette(bet, parsed_space, number)
            final_payout, new_stats = await self._settle_common(
                ctx.guild.id, ctx.author.id, bet, stats["streak"], outcome["payout"]
            )

        color_emoji = {"red": "🔴", "black": "⚫", "green": "🟢"}[outcome["color"]]
        desc = f"The ball landed on **{outcome['number']}** {color_emoji}\n\n"
        if outcome["won"]:
            desc += f"You won {self._money(econ, final_payout)}!"
            title, color = "🎡 You Won!", h.GREEN
        else:
            desc += f"You lost {self._money(econ, bet)}."
            title, color = "🎡 You Lost", h.RED
        embed = h.embed(title, desc, color)
        if new_stats["streak"] >= 3:
            embed.set_footer(text=f"🔥 {new_stats['streak']}-win streak!")
        await ctx.reply(embed=embed)

    # ── /casino blackjack ────────────────────────────────────────────────────
    @casino.command(name="blackjack", description="Play blackjack against the dealer.")
    @app_commands.describe(bet="How many coins to bet")
    async def casino_blackjack(self, ctx: commands.Context, bet: int):
        cfg = await db.get_casino_config(ctx.guild.id)
        if not cfg["enabled"]:
            return await ctx.reply(
                embed=h.err("Casino games are disabled on this server."), ephemeral=True
            )
        err = self._bet_error(cfg, bet)
        if err:
            return await ctx.reply(embed=h.err(err), ephemeral=True)

        econ = await db.get_econ_config(ctx.guild.id)
        async with self._lock(ctx.guild.id, ctx.author.id):
            if not await db.try_debit_coins(ctx.guild.id, ctx.author.id, bet):
                balance = await db.get_balance(ctx.guild.id, ctx.author.id)
                return await ctx.reply(
                    embed=h.err(
                        f"You need {self._money(econ, bet)} to play — you have "
                        f"{self._money(econ, balance)}."
                    ),
                    ephemeral=True,
                )
            stats = await db.get_casino_stats(ctx.guild.id, ctx.author.id)
            streak = stats["streak"]
            shoe = new_shoe(BLACKJACK_DECKS)
            random.shuffle(shoe)
            player_cards = [shoe.pop(), shoe.pop()]
            dealer_cards = [shoe.pop(), shoe.pop()]

            if is_blackjack(player_cards) or is_blackjack(dealer_cards):
                # Already holding the lock from the debit above — settle
                # in-place rather than recursing into settle_blackjack_hand
                # (which acquires the same non-reentrant lock).
                result = await self._settle_blackjack_locked(
                    ctx.guild.id, ctx.author.id, bet, streak, player_cards, dealer_cards
                )
                embed = _bj_embed(
                    player_cards=player_cards,
                    dealer_cards=dealer_cards,
                    hide_dealer=False,
                    title=result["title"],
                    color=result["color"],
                    footer=result["footer"],
                )
                return await ctx.reply(embed=embed)

        view = BlackjackView(
            self,
            guild_id=ctx.guild.id,
            user_id=ctx.author.id,
            bet=bet,
            shoe=shoe,
            player_cards=player_cards,
            dealer_cards=dealer_cards,
            econ=econ,
            streak=streak,
        )
        msg = await ctx.reply(embed=view.render(), view=view)
        view.message = msg

    async def settle_blackjack_hand(
        self,
        guild_id: int,
        user_id: int,
        bet: int,
        streak: int,
        player_cards: list,
        dealer_cards: list,
    ) -> dict:
        """Settle a finished (stood/busted) blackjack hand: apply the streak
        bonus, feed the jackpot, record the game, and return render info.

        Acquires its own per-user lock — used by BlackjackView once the
        original command's lock has been released. The instant-natural path
        in casino_blackjack already holds the lock, so it calls
        _settle_blackjack_locked directly instead.
        """
        async with self._lock(guild_id, user_id):
            return await self._settle_blackjack_locked(
                guild_id, user_id, bet, streak, player_cards, dealer_cards
            )

    async def _settle_blackjack_locked(
        self,
        guild_id: int,
        user_id: int,
        bet: int,
        streak: int,
        player_cards: list,
        dealer_cards: list,
    ) -> dict:
        """Same as settle_blackjack_hand, assuming the caller already holds
        self._lock(guild_id, user_id)."""
        outcome = settle_blackjack(bet, player_cards, dealer_cards)
        econ = await db.get_econ_config(guild_id)
        final_payout, new_stats = await self._settle_common(
            guild_id, user_id, bet, streak, outcome["payout"]
        )

        labels = {
            "blackjack": (
                "🃏 Blackjack!",
                h.GREEN,
                f"You won {self._money(econ, final_payout)}!",
            ),
            "win": (
                "🃏 You Won!",
                h.GREEN,
                f"You won {self._money(econ, final_payout)}!",
            ),
            "push": (
                "🃏 Push",
                h.YELLOW,
                f"Your {self._money(econ, bet)} bet is back.",
            ),
            "lose": ("🃏 You Lost", h.RED, f"You lost {self._money(econ, bet)}."),
            "bust": (
                "🃏 Bust!",
                h.RED,
                f"You went over 21 — lost {self._money(econ, bet)}.",
            ),
        }
        title, color, footer = labels[outcome["outcome"]]
        if new_stats["streak"] >= 3:
            footer += f" 🔥 {new_stats['streak']}-win streak!"
        return {
            "outcome": outcome["outcome"],
            "payout": final_payout,
            "title": title,
            "color": color,
            "footer": footer,
        }

    # ── /casino jackpot ──────────────────────────────────────────────────────
    @casino.command(name="jackpot", description="See the progressive jackpot pool.")
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def casino_jackpot(self, ctx: commands.Context):
        cfg = await db.get_casino_config(ctx.guild.id)
        econ = await db.get_econ_config(ctx.guild.id)
        embed = h.embed(
            "💰 Progressive Jackpot",
            f"The pot is currently {self._money(econ, cfg['jackpot_pool'])}.\n"
            f"Land a triple {SLOT_JACKPOT_SYMBOL} on `/casino slots` to win it all!",
            h.YELLOW,
        )
        await ctx.reply(embed=embed)

    # ── /casino stats ────────────────────────────────────────────────────────
    @casino.command(name="stats", description="Casino stats for you or another member.")
    @app_commands.describe(member="Whose stats to show (defaults to you)")
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def casino_stats(
        self, ctx: commands.Context, member: Optional[discord.Member] = None
    ):
        member = member or ctx.author
        if member.bot:
            return await ctx.reply(embed=h.err("Bots don't gamble."), ephemeral=True)
        stats = await db.get_casino_stats(ctx.guild.id, member.id)
        econ = await db.get_econ_config(ctx.guild.id)
        rank = await db.get_casino_rank(ctx.guild.id, member.id)
        net = stats["won"] - stats["wagered"]

        embed = h.embed(f"🎰 {member.display_name}", color=h.BLUE)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(
            name="Games played", value=f"**{stats['games']:,}**", inline=True
        )
        embed.add_field(
            name="Wagered", value=self._money(econ, stats["wagered"]), inline=True
        )
        embed.add_field(name="Won", value=self._money(econ, stats["won"]), inline=True)
        embed.add_field(name="Net", value=self._money(econ, net), inline=True)
        embed.add_field(
            name="Biggest win",
            value=self._money(econ, stats["biggest_win"]),
            inline=True,
        )
        embed.add_field(
            name="Win streak",
            value=f"**{stats['streak']}** (best **{stats['best_streak']}**)",
            inline=True,
        )
        embed.add_field(
            name="Rank", value=f"**#{rank[0]}**" if rank else "Unranked", inline=True
        )
        await ctx.reply(embed=embed)

    # ── /casino top ──────────────────────────────────────────────────────────
    @casino.command(name="top", description="The server's top net winners.")
    @app_commands.describe(page="Page number (10 per page)")
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def casino_top(self, ctx: commands.Context, page: int = 1):
        econ = await db.get_econ_config(ctx.guild.id)
        page = max(1, page)
        per = 10
        total = await db.count_casino_players(ctx.guild.id)
        if total == 0:
            return await ctx.reply(
                embed=h.info("No one has played yet. Try `/casino`!", "🎰 Players")
            )
        pages = (total + per - 1) // per
        page = min(page, pages)
        offset = (page - 1) * per
        rows = await db.get_casino_leaderboard(ctx.guild.id, per, offset)

        medals = {1: "🥇", 2: "🥈", 3: "🥉"}
        lines = []
        for i, row in enumerate(rows):
            pos = offset + i + 1
            member = ctx.guild.get_member(row["user_id"])
            name = member.display_name if member else f"User {row['user_id']}"
            badge = medals.get(pos, f"`#{pos}`")
            lines.append(
                f"{badge} **{name}** — net {self._money(econ, row['net'])} "
                f"({row['games']:,} games)"
            )
        embed = h.embed("🎰 Top Winners", "\n".join(lines), h.BLUE)
        embed.set_footer(text=f"Page {page}/{pages} · {total} players")
        await ctx.reply(embed=embed)

    # ══════════════════════════════════════════════════════════════════════════
    #  Admin subcommands (Manage Server)
    # ══════════════════════════════════════════════════════════════════════════
    @casino.command(
        name="toggle", description="Turn casino games on or off for this server."
    )
    @commands.has_permissions(manage_guild=True)
    async def casino_toggle(self, ctx: commands.Context):
        cfg = await db.get_casino_config(ctx.guild.id)
        enabled = not cfg["enabled"]
        await db.set_casino_config(ctx.guild.id, enabled=enabled)
        if enabled:
            await ctx.reply(embed=h.ok("Casino games are now **enabled**. 🎰"))
        else:
            await ctx.reply(embed=h.ok("Casino games are now **disabled**."))

    @casino.command(name="limit", description="Set the minimum and maximum bet.")
    @app_commands.describe(
        minimum="Smallest allowed bet", maximum="Largest allowed bet"
    )
    @commands.has_permissions(manage_guild=True)
    async def casino_limit(self, ctx: commands.Context, minimum: int, maximum: int):
        if minimum <= 0 or maximum <= 0:
            return await ctx.reply(
                embed=h.err("Both limits must be positive numbers of coins."),
                ephemeral=True,
            )
        if maximum < minimum:
            return await ctx.reply(
                embed=h.err("Maximum bet must be at least the minimum bet."),
                ephemeral=True,
            )
        await db.set_casino_config(ctx.guild.id, min_bet=minimum, max_bet=maximum)
        await ctx.reply(
            embed=h.ok(f"Bet limits set to **{minimum:,}** – **{maximum:,}**.")
        )

    @casino.command(name="config", description="Show the casino settings.")
    @commands.has_permissions(manage_guild=True)
    async def casino_config(self, ctx: commands.Context):
        cfg = await db.get_casino_config(ctx.guild.id)
        econ = await db.get_econ_config(ctx.guild.id)
        embed = h.embed("🎰 Casino Settings", color=h.BLUE)
        embed.add_field(
            name="Enabled", value="✅ Yes" if cfg["enabled"] else "❌ No", inline=True
        )
        embed.add_field(
            name="Bet limits",
            value=f"{self._money(econ, cfg['min_bet'])} – {self._money(econ, cfg['max_bet'])}",
            inline=True,
        )
        embed.add_field(
            name="Jackpot", value=self._money(econ, cfg["jackpot_pool"]), inline=True
        )
        await ctx.reply(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(Casino(bot))
