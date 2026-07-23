"""
cogs/casino/cog.py
Casino minigames — a NanoCoin economy sink/faucet with a shared progressive
jackpot and a pair of daily challenges.

Five bet-and-resolve games (flip, dice, slots, roulette, blackjack) all debit
the bet up front via the atomic economy API, resolve the outcome from an
explicit random roll (or, for blackjack, an explicit shuffled shoe) through
pure helpers in helpers.py, then credit any payout. A 3+ consecutive win
streak adds a small payout bonus (capped at +25%); losing resets it. 20% of
every net loss a player takes feeds a per-guild progressive jackpot, which a
triple-7️⃣ slots spin claims outright.

/casino blackjack persists its open hand (bet already debited) in SQLite the
moment it's dealt, updates it on every Hit, and deletes it the instant it's
settled — so a restart mid-hand resumes exactly where it left off (or, if the
message is gone, a cog-owned timer auto-stands it) instead of silently losing
the bet. The Hit/Stand view is persistent with stable per-hand custom_ids;
settlement claims the row atomically first so a button press racing the
expiry timer (or a restart-restored expiry) can never pay out twice.

Two deterministic daily challenges per member (win/play N games, wager N
coins, or land a big payout) auto-claim a modest coin reward the moment
they're completed — see constants.py for the reward-vs-house-edge sizing.

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
  /casino blackjack <bet>          → interactive Hit/Stand vs the dealer, restart-safe
  /casino jackpot                  → see the progressive jackpot pool
  /casino challenge                → today's 2 daily challenges + your progress
  /casino stats [member]           → games, wins, wagered, net, biggest win, streak
  /casino top [page]               → leaderboard by net winnings
  /casino limit <min> <max>        → set bet bounds              (Manage Server)
  /casino toggle                   → enable/disable casino games (Manage Server)
  /casino config                   → show settings                (Manage Server)
"""

import asyncio
import logging
import random
import time
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from utils import db
from utils import helpers as h

from .constants import (
    BJ_TIMEOUT,
    BLACKJACK_DECKS,
    JACKPOT_FEED_RATE,
    SLOT_JACKPOT_SYMBOL,
)
from .helpers import (
    apply_streak_bonus,
    challenge_short_label,
    dealer_should_hit,
    generate_challenges,
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
        self._locks = h.KeyedLocks()
        # Open /casino blackjack hands, keyed by hand_id: the live view + its
        # BJ_TIMEOUT expiry timer. Persisted in SQLite (casino_blackjack) and
        # restored on startup (on_restore_schedules) so a restart never
        # orphans the buttons or loses the already-debited bet — the same
        # persisted-view + cog-owned-timer pattern as economy's /raid.
        self._bj_hands: dict[int, BlackjackView] = {}
        self._bj_tasks: dict[int, asyncio.Task] = {}

    async def cog_load(self):
        # restore_schedules only fires from on_ready, so a hot-reload after
        # the bot is already up would leave any persisted hand un-rearmed.
        # Re-run the restore here when the gateway is already ready (initial
        # startup still waits for the on_ready dispatch).
        if self.bot.is_ready():
            self.bot.loop.create_task(self.on_restore_schedules())

    def cog_unload(self):
        for task in self._bj_tasks.values():
            task.cancel()
        self._bj_tasks.clear()

    def _lock(self, guild_id: int, user_id: int):
        # Returns an async context manager; existing `async with self._lock(...)`
        # call sites are unchanged. KeyedLocks refcounts holder + waiters and
        # drops an entry when the last interested task releases, so the map no
        # longer grows for the lifetime of the process.
        return self._locks.hold((guild_id, user_id))

    def _money(self, econ: dict, amount: int) -> str:
        return h.fmt_coins(amount, econ["currency_name"], econ["currency_emoji"])

    def _bet_error(self, cfg: dict, bet: int) -> Optional[str]:
        if bet <= 0:
            return "Bet must be a positive number of coins."
        if bet < cfg["min_bet"] or bet > cfg["max_bet"]:
            return f"Bet must be between **{cfg['min_bet']:,}** and **{cfg['max_bet']:,}**."
        return None

    async def _settle_common(
        self, guild_id: int, user_id: int, bet: int, streak: int, raw_payout: int
    ) -> tuple[int, dict, Optional[str]]:
        """Apply the streak bonus (on a true win), feed the jackpot on a net
        loss, record the game, bump today's challenges, and return
        (final_payout, updated_stats, challenge_footer_hint).

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
        hint = await self._bump_challenges(guild_id, user_id, bet, final_payout)
        return final_payout, stats, hint

    def _set_result_footer(
        self, embed: discord.Embed, stats: dict, hint: Optional[str]
    ) -> None:
        """Append the win-streak line and/or a challenge hint to a result embed."""
        parts = []
        if stats["streak"] >= 3:
            parts.append(f"🔥 {stats['streak']}-win streak!")
        if hint:
            parts.append(hint)
        if parts:
            embed.set_footer(text=" · ".join(parts))

    # ── Daily challenges ─────────────────────────────────────────────────────
    async def _bump_challenges(
        self, guild_id: int, user_id: int, bet: int, payout: int
    ) -> Optional[str]:
        """Advance today's 2 daily challenges for one settled game and
        auto-claim any that just completed.

        Returns a short footer hint — a completion announcement takes
        priority, else the first still-open challenge's progress — or None if
        neither challenge is relevant to mention right now.
        """
        won = payout > bet
        today = int(time.time() // 86400)
        challenges = generate_challenges(guild_id, user_id, today)
        completed_notes: list[str] = []
        progress_notes: list[str] = []
        for chal in challenges:
            key, target, reward, label = (
                chal["chal_key"],
                chal["target"],
                chal["reward"],
                chal["label"],
            )
            current = await db.get_challenge_progress(guild_id, user_id, today, key)
            if current["claimed"]:
                continue
            amount, mode = 0, "add"
            if key == "win_games":
                amount = 1 if won else 0
            elif key == "play_games":
                amount = 1
            elif key == "wager_coins":
                amount = bet
            elif key == "big_payout":
                amount, mode = payout, "max"
            if mode == "add" and amount <= 0:
                continue
            row = await db.bump_challenge_progress(
                guild_id, user_id, today, key, target, amount, mode=mode
            )
            if row["progress"] >= target and not row["claimed"]:
                if await db.try_claim_challenge(guild_id, user_id, today, key):
                    await db.add_coins(guild_id, user_id, reward)
                    econ = await db.get_econ_config(guild_id)
                    completed_notes.append(
                        f"🏆 Challenge done — {label}! +{self._money(econ, reward)}"
                    )
                    continue
            progress_notes.append(
                f"Challenge: {min(row['progress'], target)}/{target} "
                f"{challenge_short_label(key)}"
            )
        if completed_notes:
            return " ".join(completed_notes)
        return progress_notes[0] if progress_notes else None

    # ── Blackjack hand persistence / lifecycle ──────────────────────────────
    def _arm_blackjack(self, view: BlackjackView) -> None:
        """Track a live blackjack hand and schedule its BJ_TIMEOUT auto-stand."""
        self._bj_hands[view.hand_id] = view
        delay = max(0.0, view.created_at + BJ_TIMEOUT - time.time())
        self._bj_tasks[view.hand_id] = asyncio.create_task(self._bj_expiry(view, delay))

    async def _bj_expiry(self, view: BlackjackView, delay: float) -> None:
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return
        await view.expire()

    async def _end_blackjack(self, hand_id: int) -> None:
        """Drop a settled hand: cancel its timer and stop tracking it.

        The persisted row itself is removed by db.claim_blackjack_hand at
        settle time — this only tidies up in-memory bookkeeping.
        """
        task = self._bj_tasks.pop(hand_id, None)
        if task is not None and task is not asyncio.current_task():
            task.cancel()
        self._bj_hands.pop(hand_id, None)

    @commands.Cog.listener()
    async def on_restore_schedules(self):
        """Rebuild open blackjack hands after a restart."""
        for row in await db.get_open_blackjack_hands():
            hand_id = row["hand_id"]
            if hand_id in self._bj_hands:
                # on_ready re-fired (gateway re-identify) — this hand is
                # already live; rebuilding it would leak the old timer.
                continue
            if row["message_id"] is None:
                # Crash beat the message-id write — nothing to bind buttons
                # to. Settle it anyway so the debited bet isn't lost forever.
                await self._settle_orphaned_hand(row)
                continue
            econ = await db.get_econ_config(row["guild_id"])
            view = BlackjackView(
                self,
                hand_id=hand_id,
                guild_id=row["guild_id"],
                user_id=row["user_id"],
                bet=row["bet"],
                shoe=row["shoe"],
                player_cards=row["player"],
                dealer_cards=row["dealer"],
                econ=econ,
                streak=row["streak"],
                created_at=row["created_at"],
            )
            self.bot.add_view(view, message_id=row["message_id"])
            channel = self.bot.get_channel(row["channel_id"])
            if channel is not None:
                view.message = channel.get_partial_message(row["message_id"])
            self._arm_blackjack(view)

    async def _settle_orphaned_hand(self, row: dict) -> None:
        """Settle a persisted hand that never got a message_id (crash between
        the row insert and the reply being sent) — there's nothing to edit,
        but the bet must still be resolved rather than left debited forever."""
        claimed = await db.claim_blackjack_hand(row["hand_id"])
        if claimed is None:
            return
        shoe, player_cards, dealer_cards = (
            claimed["shoe"],
            claimed["player"],
            claimed["dealer"],
        )
        while dealer_should_hit(dealer_cards):
            dealer_cards.append(shoe.pop())
        await self.settle_blackjack_hand(
            claimed["guild_id"],
            claimed["user_id"],
            claimed["bet"],
            claimed["streak"],
            player_cards,
            dealer_cards,
        )

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
            "jackpot (triple 7️⃣ on /casino slots claims it), build a win "
            "streak for a payout bonus, and complete 2 daily challenges "
            "(/casino challenge) for a coin reward. Admins can toggle the "
            "casino and set bet limits.",
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
            "🃏 **blackjack** <bet> — Hit/Stand vs the dealer, blackjack pays 3:2\n"
            "🎯 **challenge** — today's 2 daily challenges + your progress"
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
            final_payout, new_stats, chal_hint = await self._settle_common(
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
        self._set_result_footer(embed, new_stats, chal_hint)
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
            final_payout, new_stats, chal_hint = await self._settle_common(
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
        self._set_result_footer(embed, new_stats, chal_hint)
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
            final_payout, new_stats, chal_hint = await self._settle_common(
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
        self._set_result_footer(embed, new_stats, chal_hint)
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
            final_payout, new_stats, chal_hint = await self._settle_common(
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
        self._set_result_footer(embed, new_stats, chal_hint)
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
                # (which acquires the same non-reentrant lock). Resolves in
                # one round trip, so there's no open hand to persist.
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

            # Persist the open hand now — bet already debited — before ever
            # showing the buttons, so a restart mid-hand can resume or
            # auto-settle it instead of orphaning the bet.
            created_at = time.time()
            hand_id = await db.create_blackjack_hand(
                ctx.guild.id,
                ctx.channel.id,
                ctx.author.id,
                bet,
                streak,
                shoe,
                player_cards,
                dealer_cards,
                created_at,
            )

        view = BlackjackView(
            self,
            hand_id=hand_id,
            guild_id=ctx.guild.id,
            user_id=ctx.author.id,
            bet=bet,
            shoe=shoe,
            player_cards=player_cards,
            dealer_cards=dealer_cards,
            econ=econ,
            streak=streak,
            created_at=created_at,
        )
        msg = await ctx.reply(embed=view.render(), view=view)
        view.message = msg
        await db.set_blackjack_message(hand_id, msg.id)
        self._arm_blackjack(view)

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
        final_payout, new_stats, chal_hint = await self._settle_common(
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
        if chal_hint:
            footer += f" · {chal_hint}"
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

    # ── /casino challenge ────────────────────────────────────────────────────
    @casino.command(
        name="challenge", description="See today's daily challenges and your progress."
    )
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def casino_challenge(self, ctx: commands.Context):
        econ = await db.get_econ_config(ctx.guild.id)
        today = int(time.time() // 86400)
        challenges = generate_challenges(ctx.guild.id, ctx.author.id, today)
        embed = h.embed(
            "🎯 Daily Casino Challenges",
            "Play flip, dice, slots, roulette, or blackjack to make progress. "
            "Rewards pay out automatically the moment you complete one.",
            h.BLUE,
        )
        for chal in challenges:
            row = await db.get_challenge_progress(
                ctx.guild.id, ctx.author.id, today, chal["chal_key"]
            )
            progress = min(row["progress"], chal["target"])
            if row["claimed"]:
                status = "✅ Claimed"
            elif progress >= chal["target"]:
                status = "🎉 Complete!"
            else:
                status = f"{progress:,}/{chal['target']:,}"
            embed.add_field(
                name=chal["label"],
                value=f"{status} · reward {self._money(econ, chal['reward'])}",
                inline=False,
            )
        embed.set_footer(text="Resets daily at 00:00 UTC")
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
        embed.add_field(name="Wins", value=f"**{stats['wins']:,}**", inline=True)
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
