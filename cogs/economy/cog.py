"""
cogs/economy.py
NanoCoin economy — GLOBAL wallets, per-guild settings.

A member's wallet, daily streak, and lifetime contribution belong to the
*user*: coins earned in one server spend in every other one, and /daily is a
single claim per day across the whole bot (not one per server).

That splits the settings cleanly into faucets and sinks. The reward *amounts*
(/daily, its streak bonus, /squad, /raid) MINT into that one global wallet, so
they are bot-wide and owner-only — `!econ` in cogs/admin — because a server
raising its daily would be paying its members coins that spend in every other
server too. What a guild owns is what only touches its own members: currency
name/emoji, raid party size, its /squad and /raid boards, and above all its
**shop**, which is a pure sink — a purchase destroys coins in exchange for that
guild's own role or mod-fulfilled perk, so no price it sets can affect anyone
outside it. To make those prices pickable rather than guesswork, /shop shows
each one as a time to earn. See docs/global-economy.md for the full scope split.

Members hold a coin balance, claim a daily reward (with a consecutive-day
streak bonus), and pay each other. They also reward co-op activity: /squad
tags teammates who each confirm with a button (tag up to five directly, or tag
no one to open a picker menu for a squad of up to 25), and /raid opens a join
board for a whole group (the host or a mod presses Finish to pay the party) —
both grant spendable coins and a lifetime contribution stat that drives a
separate contributor leaderboard and rank titles. Coins are spent in a
per-guild shop on Discord roles (granted instantly) or custom rewards (queued
for a mod to fulfil), with optional stock counts, per-user limits, and
cooldowns. Server admins view the rich list and customise the currency name,
emoji, and raid party size; moving coins in or out of a wallet is the bot
owner's, since the wallet is global.

Slash command budget: five flat commands (/balance, /daily, /pay, /squad,
/raid) plus two groups (/coin …, /shop …) whose subcommands cost no extra
top-level slots.

──────────────────────────────────────────────────────
Commands
──────────────────────────────────────────────────────
  /balance [member]              → check a balance (+ contribution rank)
  /daily                         → claim the daily reward
  /pay <member> <amount>         → send coins to someone
  /squad [member…] [activity]    → co-op reward, teammates confirm (alias: coop)
  /raid [activity]               → group co-op join board (alias: event)
  /coin top [page] [scope]       → richest members (this server or global)
  /coin contrib [page] [scope]   → top contributors (alias: contributions)
  /coin gamble <amount>          → bet coins to double them (alias: bet)
  /coin grant <amount> [member…] → add coins        (tag up to 5, or blank for
                                                       a 25-member picker) (bot owner)
  /coin take <amount> [member…]  → remove coins     (tag up to 5, or blank for
                                                       a 25-member picker) (bot owner)
  /coin reset [member]           → wipe a global wallet   (bot owner)
  /coin raidsize <min> <max>     → set party size   (Manage Server)
  /coin name <text>              → currency name    (Manage Server)
  /coin emoji <emoji>            → currency emoji   (Manage Server)
  /coin config                   → show settings    (Manage Server)
  /shop list [page]              → browse rewards
  /shop buy <id|name>            → redeem an item
  /shop seed                     → add starter rewards (Manage Server)
  /shop add …                    → create an item   (Manage Server)
  /shop edit <id|name> …         → edit an item     (Manage Server)
  /shop remove <id|name>         → delete an item   (Manage Server)
  /shop pending                  → custom-reward queue (Manage Server)
  /shop fulfill <id>             → mark reward delivered (Manage Server)

The reward amounts are not here: they mint into global wallets, so `!econ`
lives in the owner-only admin cog.
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
from utils import globalxp
from utils import helpers as h
from utils.helpers import SCOPE_CHOICES

from .constants import (
    COIN_MAX,
    COOP_CONFIRM_TIMEOUT,
    RAID_TIMEOUT,
    REWARD_DEFAULTS,
    _DEFAULT_SHOP_ITEMS,
)
from cogs.activities.helpers import effective_cooldown

from .helpers import (
    compute_daily,
    fmt_coins,
    resolve_gamble,
    seconds_to_afford,
    _rank_title,
    _scaled_price,
)
from .views import MassCoinPickerView, RaidView, SquadBuilderView, SquadView

log = logging.getLogger("NanoBot.economy")


class Economy(commands.Cog):
    """NanoCoin balances, daily rewards, and transfers — one wallet per user,
    shared by every server the bot is in."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Per-user locks serialize the read-check-write in /daily. The claim
        # is global, so the two racing invocations this guards against can be
        # in two different servers. Entries free themselves once idle.
        self._daily_locks = h.KeyedLocks()
        # Open /raid boards + pending /squad confirms, keyed by id: the live view
        # + its expiry timer. Persisted in SQLite (economy_raids/economy_squads)
        # and restored on startup so a restart doesn't orphan the buttons ("This
        # interaction failed") or drop a payout mid-confirm.
        self._raids: dict[int, RaidView] = {}
        self._raid_tasks: dict[int, asyncio.Task] = {}
        self._squads: dict[int, SquadView] = {}
        self._squad_tasks: dict[int, asyncio.Task] = {}

    async def cog_load(self):
        # restore_schedules only fires from on_ready, so a hot-reload after the
        # bot is up would bring the persisted boards back with no expiry timers.
        # Re-run the restore here when the gateway is already ready (initial
        # startup still waits for the on_ready dispatch — the guild/channel
        # cache isn't populated this early).
        if self.bot.is_ready():
            self.bot.loop.create_task(self.on_restore_schedules())

    def cog_unload(self):
        for task in (*self._raid_tasks.values(), *self._squad_tasks.values()):
            task.cancel()
        self._raid_tasks.clear()
        self._squad_tasks.clear()

    def _daily_lock(self, user_id: int):
        # Returns an async context manager; existing `async with` call sites
        # are unchanged. KeyedLocks refcounts holder + waiters and drops an
        # entry when the last interested task releases.
        return self._daily_locks.hold(user_id)

    # Merged cfg key ← bot-wide reward key. The cfg key names are unchanged from
    # when these were guild columns, so every call site reads the same dict it
    # always did.
    _REWARD_KEYS = {
        "daily_amount": "daily",
        "streak_bonus": "streak_bonus",
        "coop_reward": "coop",
        "raid_reward": "raid",
    }

    async def _cfg(self, guild_id: int) -> dict:
        """This server's economy settings, with the bot-wide faucets folded in.

        The guild row holds only what affects its own members — currency
        name/emoji and raid party size. The reward *amounts* mint into a global
        wallet (one /daily per account, one wallet everywhere), so they are
        bot-wide and owner-set (`!econ`); see REWARD_DEFAULTS in constants.py.
        Shop prices are untouched by all of this and stay the guild's: a
        purchase is a sink, so what it costs affects nobody outside the server.
        """
        cfg = dict(await db.get_econ_config(guild_id))
        overrides = await db.get_reward_amounts()
        for cfg_key, reward_key in self._REWARD_KEYS.items():
            cfg[cfg_key] = overrides.get(reward_key, REWARD_DEFAULTS[reward_key])
        return cfg

    def _money(self, cfg: dict, amount: int) -> str:
        return fmt_coins(amount, cfg["currency_name"], cfg["currency_emoji"])

    # ── co-op payouts ────────────────────────────────────────────────────────
    async def _coop_cooldown(self, key: str) -> int:
        """The bot-wide claim cooldown for `key` ("coop" or "raid")."""
        overrides = await db.get_activity_cooldowns()
        return effective_cooldown(key, overrides.get(key))

    async def _check_coop_ready(self, ctx, key: str, label: str) -> bool:
        """Refuse to open a board the host can't be paid for.

        The real guard is the per-member claim in _pay_party; this is only so a
        host finds out now rather than after five people have pressed Confirm.
        Read-only — it never claims.
        """
        stats = await db.get_activity_stats(ctx.author.id)
        last = stats.get(f"last_{key}", 0) or 0
        if not last:
            return True
        left = int(await self._coop_cooldown(key) - (time.time() - last))
        if left <= 0:
            return True
        await ctx.reply(
            embed=h.warn(
                f"You've already been paid for a {label} recently. "
                f"Your next one is ready in **{h.fmt_duration(left)}**.",
                "⏳ On cooldown",
            ),
            ephemeral=True,
        )
        return False

    async def _pay_party(self, party: list[int], key: str, reward: int):
        """Pay everyone in `party` who is off cooldown. Returns (paid, skipped).

        The reward is a faucet — coins minted into a global wallet — and until
        this existed it had no rate limit at all: only the *invoker* carried a
        few seconds of command cooldown, so two members could confirm a squad
        every half-minute, forever, risk-free. Each member now takes the same
        atomic per-user claim an activity does, so the payout is bounded per
        person however many boards they are tagged in.

        Someone on cooldown is skipped rather than blocking the board, so one
        member's timing can't cost the rest of the party their reward.
        """
        now = time.time()
        cooldown = await self._coop_cooldown(key)
        paid: list[int] = []
        skipped: list[int] = []
        for uid in party:
            if await db.try_claim_activity(uid, key, now, cooldown):
                skipped.append(uid)
                continue
            await db.add_coins(uid, reward)
            await db.add_contribution(uid, reward)
            await globalxp.award(uid, "coop")
            paid.append(uid)
        return paid, skipped

    def _effort(self, price: int) -> str:
        """ "~25m to earn" — what a shop price costs in play time.

        A price on its own tells a mod nothing about how hard they've just made
        a reward to get, which is why shop pricing was guesswork. This converts
        it against the bot-wide income rate (see helpers.seconds_to_afford):
        fishing, because at ~5,000 coins/hour it dwarfs every other faucet by an
        order of magnitude, so it is the only honest denominator. It's a floor —
        nobody fishes non-stop — and the footers say as much.
        """
        seconds = seconds_to_afford(price)
        return "free" if seconds <= 0 else f"~{h.fmt_duration(seconds)} to earn"

    # ══════════════════════════════════════════════════════════════════════════
    #  /balance  — flat
    # ══════════════════════════════════════════════════════════════════════════
    @commands.hybrid_command(
        name="balance",
        aliases=["bal"],
        description="Check your NanoCoin balance, or someone else's.",
        extras={
            "category": "🪙 Economy",
            "sub": "💰 Wallet & Shop",
            "short": "Check a coin balance",
            "usage": "balance [member]",
            "desc": "Shows the coin balance, global wealth rank, and contribution "
            "rank for you or another member. Balances are global — the same "
            "wallet in every server.",
            "args": ["member — whose balance to show (defaults to you)"],
            "perms": "None",
            "example": "{prefix}balance\n{prefix}bal @Friend",
        },
    )
    @commands.guild_only()
    @app_commands.describe(member="Whose balance to show (defaults to you)")
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def balance(
        self, ctx: commands.Context, member: Optional[discord.Member] = None
    ):
        member = member or ctx.author
        if member.bot:
            return await ctx.reply(
                embed=h.err("Bots don't hold coins."), ephemeral=True
            )
        cfg = await self._cfg(ctx.guild.id)
        res = await db.get_econ_rank(member.id)
        coins = res[1] if res else 0
        rank_pos = res[0] if res else None
        contrib = await db.get_contrib_rank(member.id)

        embed = h.embed(f"{cfg['currency_emoji']} {member.display_name}", color=h.BLUE)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="Balance", value=self._money(cfg, coins), inline=True)
        embed.add_field(
            name="Global rank",
            value=f"**#{rank_pos}**" if rank_pos else "Unranked",
            inline=True,
        )
        embed.set_footer(text="One wallet — the same balance in every server.")
        if contrib:
            embed.add_field(
                name="🤝 Contribution",
                value=f"**{contrib[1]:,}** pts · {_rank_title(contrib[0])} (#{contrib[0]})",
                inline=False,
            )
        await ctx.reply(embed=embed)

    # ══════════════════════════════════════════════════════════════════════════
    #  /daily  — flat
    # ══════════════════════════════════════════════════════════════════════════
    @commands.hybrid_command(
        name="daily",
        description="Claim your daily NanoCoins. Keep a streak for bonus coins!",
        extras={
            "category": "🪙 Economy",
            "sub": "💰 Wallet & Shop",
            "short": "Claim daily coins",
            "usage": "daily",
            "desc": "Grants the daily reward. Claim within 48h of your last to grow a streak bonus.",
            "args": [],
            "perms": "None",
            "example": "{prefix}daily",
        },
    )
    @commands.guild_only()
    async def daily(self, ctx: commands.Context):
        cfg = await self._cfg(ctx.guild.id)
        async with self._daily_lock(ctx.author.id):
            last_daily, streak = await db.get_daily_state(ctx.author.id)
            res = compute_daily(
                time.time(),
                last_daily,
                streak,
                cfg["daily_amount"],
                cfg["streak_bonus"],
            )
            if not res["ok"]:
                if res.get("duplicate"):
                    # Same claim dispatched twice (one user action delivered to
                    # the bot more than once — e.g. a restart left two processes
                    # briefly online). Drop the contradictory "already claimed"
                    # so one /daily yields exactly one reply.
                    return
                return await ctx.reply(
                    embed=h.warn(
                        f"You've already claimed today. Come back in "
                        f"**{h.fmt_duration(res['retry_after'])}**.",
                        "⏳ Not Yet",
                    ),
                    ephemeral=True,
                )

            new_bal = await db.add_coins(ctx.author.id, res["total"])
            await db.set_daily_state(ctx.author.id, time.time(), res["streak"])
            # Account-wide XP: one flat award per claim, the same in every
            # server no matter what this guild pays in coins.
            await globalxp.award(ctx.author.id, "daily")
        desc = f"You claimed {self._money(cfg, res['total'])}!"
        if res["streak"] > 1:
            desc += f"\n🔥 **{res['streak']}-day streak**"
        desc += f"\nBalance: {self._money(cfg, new_bal)}"
        await ctx.reply(embed=h.ok(desc, "🪙 Daily Reward"))

    # ══════════════════════════════════════════════════════════════════════════
    #  /pay  — flat
    # ══════════════════════════════════════════════════════════════════════════
    @commands.hybrid_command(
        name="pay",
        description="Send some of your NanoCoins to another member.",
        extras={
            "category": "🪙 Economy",
            "sub": "💰 Wallet & Shop",
            "short": "Send coins to someone",
            "usage": "pay <member> <amount>",
            "desc": "Transfers coins from your balance to another member's.",
            "args": ["member — who to pay", "amount — how many coins"],
            "perms": "None",
            "example": "{prefix}pay @Friend 50",
        },
    )
    @commands.guild_only()
    @app_commands.describe(member="Who to pay", amount="How many coins to send")
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def pay(self, ctx: commands.Context, member: discord.Member, amount: int):
        cfg = await self._cfg(ctx.guild.id)
        if member.bot:
            return await ctx.reply(embed=h.err("You can't pay a bot."), ephemeral=True)
        if member.id == ctx.author.id:
            return await ctx.reply(
                embed=h.err("You can't pay yourself."), ephemeral=True
            )
        if amount <= 0:
            return await ctx.reply(
                embed=h.err("Amount must be positive."), ephemeral=True
            )

        ok = await db.transfer_coins(ctx.author.id, member.id, amount)
        if not ok:
            bal = await db.get_balance(ctx.author.id)
            return await ctx.reply(
                embed=h.err(f"Not enough coins. You have {self._money(cfg, bal)}."),
                ephemeral=True,
            )
        new_bal = await db.get_balance(ctx.author.id)
        await ctx.reply(
            embed=h.ok(
                f"Sent {self._money(cfg, amount)} to {member.mention}.\n"
                f"Your balance: {self._money(cfg, new_bal)}"
            )
        )

    # ══════════════════════════════════════════════════════════════════════════
    #  /coin  group
    # ══════════════════════════════════════════════════════════════════════════
    @commands.hybrid_group(
        name="coin",
        description="Economy: rich list and admin settings. See /coin top.",
        invoke_without_command=True,
        extras={
            "category": "🪙 Economy",
            "sub": "💰 Wallet & Shop",
            "short": "Rich list + economy admin settings",
            "usage": "coin [subcommand]",
            "desc": "View the richest members with /coin top. Admins grant/take coins "
            "and customise the daily reward, streak bonus, currency name, and emoji.",
            "args": [],
            "perms": "Admin subcommands require Manage Server",
            "example": "{prefix}coin top\n{prefix}coin grant 500 @User",
        },
    )
    @commands.guild_only()
    async def coin(self, ctx: commands.Context):
        await self._show_leaderboard(ctx, 1, "server")

    # ── /coin top ───────────────────────────────────────────────────────────
    @coin.command(name="top", description="Show the richest members.")
    @app_commands.describe(
        page="Page number (10 per page)",
        scope="This server's members, or every server (wallets are global)",
    )
    @app_commands.choices(scope=SCOPE_CHOICES)
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def coin_top(
        self, ctx: commands.Context, page: int = 1, scope: str = "server"
    ):
        await self._show_leaderboard(ctx, page, scope)

    async def _show_leaderboard(self, ctx: commands.Context, page: int, scope: str):
        """Rich list. Wallets are global, so "this server" is that same table
        filtered to the guild's members — both views rank the same coins."""
        cfg = await self._cfg(ctx.guild.id)
        per = 10
        if scope == "global":
            total = await db.count_econ()
            pages = max(1, (total + per - 1) // per)
            page = max(1, min(page, pages))
            offset = (page - 1) * per
            rows = await db.get_econ_leaderboard(per, offset)
        else:
            all_rows = await db.get_econ_leaderboard_for(h.member_ids(ctx.guild))
            rows, page, pages, total = h.page_rows(
                all_rows, lambda r: (-r["coins"], r["user_id"]), page, per
            )
            offset = (page - 1) * per
        if total == 0:
            return await ctx.reply(
                embed=h.info(
                    "No one has any coins yet. Try `/daily`!",
                    f"{cfg['currency_emoji']} Rich List",
                )
            )

        medals = {1: "🥇", 2: "🥈", 3: "🥉"}
        lines = []
        for i, row in enumerate(rows):
            pos = offset + i + 1
            badge = medals.get(pos, f"`#{pos}`")
            lines.append(
                f"{badge} **{self._name_for(ctx, row['user_id'])}** — "
                f"{self._money(cfg, row['coins'])}"
            )

        title = f"{cfg['currency_emoji']} Rich List"
        if scope == "global":
            title += " (Global)"
        embed = h.embed(title, "\n".join(lines), h.BLUE)
        embed.set_footer(
            text=f"Page {page}/{pages} · {total} "
            + ("wallets across every server" if scope == "global" else "members here")
        )
        await ctx.reply(embed=embed)

    def _name_for(self, ctx: commands.Context, user_id: int) -> str:
        """Display name for a leaderboard row — a global board can list people
        who aren't in this server, so fall back to the bot-wide user cache."""
        member = ctx.guild.get_member(user_id) if ctx.guild else None
        if member:
            return member.display_name
        user = self.bot.get_user(user_id)
        return user.display_name if user else f"User {user_id}"

    # ── /coin gamble ─────────────────────────────────────────────────────────────
    @coin.command(
        name="gamble",
        aliases=["bet"],
        description="Bet some coins for a chance to double them.",
    )
    @app_commands.describe(amount="How many coins to bet")
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def coin_gamble(self, ctx: commands.Context, amount: int):
        cfg = await self._cfg(ctx.guild.id)
        if amount <= 0:
            return await ctx.reply(embed=h.err("Bet must be positive."), ephemeral=True)
        # Atomically reserve the stake so two rapid bets can't spend the same
        # coins. On a win we hand back the stake plus the net winnings.
        if not await db.try_debit_coins(ctx.author.id, amount):
            balance = await db.get_balance(ctx.author.id)
            return await ctx.reply(
                embed=h.err(f"Not enough coins. You have {self._money(cfg, balance)}."),
                ephemeral=True,
            )

        res = resolve_gamble(amount, random.random())
        if res["won"]:
            new_bal = await db.add_coins(ctx.author.id, amount + res["delta"])
        else:
            new_bal = await db.get_balance(ctx.author.id)
        if res["won"]:
            embed = h.ok(
                f"🎰 You won {self._money(cfg, res['delta'])}!\n"
                f"Balance: {self._money(cfg, new_bal)}",
                "🎉 Winner",
            )
        else:
            embed = h.embed(
                "💸 Bust",
                f"🎰 You lost {self._money(cfg, amount)}.\n"
                f"Balance: {self._money(cfg, new_bal)}",
                h.RED,
            )
        await ctx.reply(embed=embed)

    # ── /coin grant ─────────────────────────────────────────────────────────────
    # Granting coins is the most direct faucet there is: it mints straight into
    # a wallet that spends in every server. That makes it the bot owner's, for
    # the same reason /coin reset already is — the difference between granting
    # and resetting is only the sign. See docs/global-economy.md.
    @coin.command(
        name="grant",
        description="Add coins to one or more members' (global) balances (bot owner).",
    )
    @app_commands.describe(
        amount="Coins to add to each member's global wallet",
        member="Member to credit (leave every member blank to open a 25-member picker)",
        member2="Another member (optional)",
        member3="Another member (optional)",
        member4="Another member (optional)",
        member5="Another member (optional)",
    )
    @commands.is_owner()
    async def coin_grant(
        self,
        ctx: commands.Context,
        amount: int,
        member: Optional[discord.Member] = None,
        member2: Optional[discord.Member] = None,
        member3: Optional[discord.Member] = None,
        member4: Optional[discord.Member] = None,
        member5: Optional[discord.Member] = None,
    ):
        await self._mass_coin(
            ctx, "grant", amount, (member, member2, member3, member4, member5)
        )

    # ── /coin take ─────────────────────────────────────────────────────────────
    # And taking them destroys value earned in servers this one has never seen
    # — the objection that made /coin reset owner-only, at a smaller scale.
    @coin.command(
        name="take",
        description="Remove coins from members' (global) balances (bot owner).",
    )
    @app_commands.describe(
        amount="Coins to remove from each member's global wallet",
        member="Member to debit (leave every member blank to open a 25-member picker)",
        member2="Another member (optional)",
        member3="Another member (optional)",
        member4="Another member (optional)",
        member5="Another member (optional)",
    )
    @commands.is_owner()
    async def coin_take(
        self,
        ctx: commands.Context,
        amount: int,
        member: Optional[discord.Member] = None,
        member2: Optional[discord.Member] = None,
        member3: Optional[discord.Member] = None,
        member4: Optional[discord.Member] = None,
        member5: Optional[discord.Member] = None,
    ):
        await self._mass_coin(
            ctx, "take", amount, (member, member2, member3, member4, member5)
        )

    async def _mass_coin(
        self,
        ctx: commands.Context,
        action: str,
        amount: int,
        tagged: tuple[Optional[discord.Member], ...],
    ) -> None:
        """Shared body for /coin grant and /coin take.

        Directly-tagged members (up to 5) are applied immediately, matching
        /squad's "tag up to 5" shortcut. If none are tagged, opens a
        MassCoinPickerView so the mod can pick up to 25 members via a menu
        instead — Discord has no way to accept an arbitrary-length member list
        as slash command arguments, so the picker is the only path past 5.
        """
        cfg = await self._cfg(ctx.guild.id)
        if amount <= 0:
            return await ctx.reply(
                embed=h.err("Amount must be positive."), ephemeral=True
            )
        if amount > COIN_MAX:
            return await ctx.reply(
                embed=h.err(f"Amount can't exceed {COIN_MAX:,}."), ephemeral=True
            )

        members: list[discord.Member] = []
        for m in tagged:
            if m is None:
                continue
            if m.bot:
                return await ctx.reply(
                    embed=h.err("Bots don't hold coins."), ephemeral=True
                )
            if m.id not in {x.id for x in members}:
                members.append(m)

        if not members:
            builder = MassCoinPickerView(self, ctx.author.id, action, amount)
            msg = await ctx.reply(embed=builder._embed(cfg), view=builder)
            builder.message = msg
            return

        delta = amount if action == "grant" else -amount
        lines = []
        for m in members:
            new_bal = await db.add_coins(m.id, delta)
            lines.append(f"{m.mention} → {self._money(cfg, new_bal)}")

        verb = "Granted" if action == "grant" else "Took"
        prep = "to" if action == "grant" else "from"
        embed = h.ok(
            f"{verb} {self._money(cfg, amount)} {prep} "
            f"**{len(members)}** member(s).\n\n" + "\n".join(lines)
        )
        # Wallets are global — say so, so nobody mistakes this for play money
        # confined to this server.
        embed.set_footer(text="Wallets are global — this applies in every server.")
        await ctx.reply(embed=embed)

    # ── /coin reset ─────────────────────────────────────────────────────────────
    @coin.command(
        name="reset",
        description="Wipe a wallet, or every wallet (bot owner only).",
    )
    @app_commands.describe(member="Member whose wallet to wipe (omit to wipe ALL)")
    @commands.is_owner()
    async def coin_reset(
        self, ctx: commands.Context, member: Optional[discord.Member] = None
    ):
        # Owner-only since wallets went global: this is no longer "clear this
        # server's ledger" — it deletes coins the member earned in every other
        # server too, with no way to put them back. Server staff still adjust
        # balances with /coin grant and /coin take.
        if member:
            await db.reset_economy(member.id)
            return await ctx.reply(
                embed=h.warn(
                    f"Wiped **{member.display_name}**'s global wallet — every "
                    "server, not just this one.",
                    "Wallet Reset",
                )
            )
        removed = await db.reset_economy()
        await ctx.reply(
            embed=h.warn(
                f"Wiped every wallet on the bot ({removed} accounts cleared).",
                "Economy Reset",
            )
        )

    # ── /coin name ─────────────────────────────────────────────────────────────
    @coin.command(name="name", description="Set the currency name (e.g. NanoCoin).")
    @app_commands.describe(name="Currency name (max 32 chars)")
    @commands.has_permissions(manage_guild=True)
    async def coin_name(self, ctx: commands.Context, *, name: str):
        name = name.strip()
        if not name or len(name) > 32:
            return await ctx.reply(
                embed=h.err("Name must be 1–32 characters."), ephemeral=True
            )
        await db.set_econ_config(ctx.guild.id, currency_name=name)
        await ctx.reply(embed=h.ok(f"Currency name set to **{name}**."))

    # ── /coin emoji ─────────────────────────────────────────────────────────────
    @coin.command(name="emoji", description="Set the currency emoji.")
    @app_commands.describe(emoji="Emoji to represent the currency")
    @commands.has_permissions(manage_guild=True)
    async def coin_emoji(self, ctx: commands.Context, emoji: str):
        emoji = emoji.strip()
        if not emoji or len(emoji) > 32:
            return await ctx.reply(
                embed=h.err("Provide a single emoji."), ephemeral=True
            )
        await db.set_econ_config(ctx.guild.id, currency_emoji=emoji)
        await ctx.reply(embed=h.ok(f"Currency emoji set to {emoji}."))

    # ── /coin config ─────────────────────────────────────────────────────────────
    @coin.command(name="config", description="Show the current economy settings.")
    @commands.has_permissions(manage_guild=True)
    async def coin_config(self, ctx: commands.Context):
        cfg = await self._cfg(ctx.guild.id)
        accounts = await db.count_econ()
        embed = h.embed(f"{cfg['currency_emoji']} Economy Settings", color=h.BLUE)
        embed.add_field(
            name="Currency",
            value=f"{cfg['currency_emoji']} {cfg['currency_name']}",
            inline=True,
        )
        embed.add_field(
            name="Daily reward",
            value=f"{cfg['daily_amount']:,} (bot-wide)",
            inline=True,
        )
        embed.add_field(
            name="Streak bonus",
            value=f"{cfg['streak_bonus']:,}/day (bot-wide)",
            inline=True,
        )
        # Wallets are global, so this is every funded account on the bot — the
        # old call passed a guild id to a function that takes none and raised.
        embed.add_field(name="Wallets (global)", value=str(accounts), inline=True)
        embed.add_field(
            name="Co-op reward",
            value=f"{cfg['coop_reward']:,}/person (bot-wide)",
            inline=True,
        )
        embed.add_field(
            name="Raid reward",
            value=f"{cfg['raid_reward']:,}/person (bot-wide)",
            inline=True,
        )
        embed.add_field(
            name="Raid party",
            value=f"{cfg['raid_min']}–{cfg['raid_max']} members",
            inline=True,
        )
        embed.set_footer(
            text="Reward amounts are bot-wide — they pay into wallets that spend "
            "in every server, so only the bot owner can change them. Your shop "
            "prices are yours."
        )
        await ctx.reply(embed=embed)

    # ── /coin contrib ─────────────────────────────────────────────────────────────
    @coin.command(
        name="contrib",
        aliases=["contributions"],
        description="Show the top guild contributors (co-op leaderboard).",
    )
    @app_commands.describe(
        page="Page number (10 per page)",
        scope="This server's members, or every server",
    )
    @app_commands.choices(scope=SCOPE_CHOICES)
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def coin_contrib(
        self, ctx: commands.Context, page: int = 1, scope: str = "server"
    ):
        per = 10
        if scope == "global":
            total = await db.count_contrib()
            pages = max(1, (total + per - 1) // per)
            page = max(1, min(page, pages))
            offset = (page - 1) * per
            rows = await db.get_contrib_leaderboard(per, offset)
        else:
            all_rows = await db.get_contrib_leaderboard_for(h.member_ids(ctx.guild))
            rows, page, pages, total = h.page_rows(
                all_rows, lambda r: (-r["contribution"], r["user_id"]), page, per
            )
            offset = (page - 1) * per
        if total == 0:
            return await ctx.reply(
                embed=h.info(
                    "No co-op contributions yet. Team up and use `/squad`!",
                    "🤝 Top Contributors",
                )
            )

        medals = {1: "🥇", 2: "🥈", 3: "🥉"}
        lines = []
        for i, row in enumerate(rows):
            pos = offset + i + 1
            badge = medals.get(pos, f"`#{pos}`")
            lines.append(
                f"{badge} **{self._name_for(ctx, row['user_id'])}** — "
                f"{row['contribution']:,} pts · {_rank_title(pos)}"
            )

        title = "🤝 Top Contributors"
        if scope == "global":
            title += " (Global)"
        embed = h.embed(title, "\n".join(lines), h.BLUE)
        embed.set_footer(text=f"Page {page}/{pages} · {total} contributors")
        await ctx.reply(embed=embed)

    # ── /coin raidsize (set party bounds) ─────────────────────────────────────────
    @coin.command(
        name="raidsize",
        description="Set the minimum and maximum raid party size.",
    )
    @app_commands.describe(
        minimum="Fewest members needed to pay out a raid",
        maximum="Most members who can join a raid",
    )
    @commands.has_permissions(manage_guild=True)
    async def coin_raidsize(self, ctx: commands.Context, minimum: int, maximum: int):
        if minimum < 2:
            return await ctx.reply(
                embed=h.err("Minimum must be at least 2."), ephemeral=True
            )
        if maximum < minimum:
            return await ctx.reply(
                embed=h.err("Maximum can't be below the minimum."), ephemeral=True
            )
        if maximum > 100:
            return await ctx.reply(
                embed=h.err("Maximum can't exceed 100."), ephemeral=True
            )
        await db.set_econ_config(ctx.guild.id, raid_min=minimum, raid_max=maximum)
        await ctx.reply(
            embed=h.ok(f"Raid party size set to **{minimum}–{maximum}** members.")
        )

    # ══════════════════════════════════════════════════════════════════════════
    #  /squad  — flat, co-op activity reward (tag up to 5, or menu-pick up to 25)
    # ══════════════════════════════════════════════════════════════════════════
    @commands.hybrid_command(
        name="squad",
        aliases=["coop"],
        description="Squad up on a guild activity — everyone in the squad earns coins!",
        extras={
            "category": "🪙 Economy",
            "sub": "🤝 Co-op",
            "short": "Reward a co-op activity",
            "usage": "squad [member] [member2] [member3] [member4] [member5] [activity]",
            "desc": "Reward a group activity (a dungeon, a raid, an event). Tag up to 5 "
            "teammates directly, or tag no one to open a picker menu and build a "
            "squad of up to 25. Each teammate confirms with a button, then the "
            "whole squad earns coins and contribution points.",
            "args": [
                "member — a teammate (tag up to 5, or omit all to open the picker)",
                "activity — what you did together (optional)",
            ],
            "perms": "None",
            "example": "{prefix}squad @Ann @Bo @Cy cleared a dungeon\n{prefix}squad",
        },
    )
    @commands.guild_only()
    @app_commands.describe(
        member="A teammate (leave everyone blank to open the squad picker)",
        member2="Another teammate (optional)",
        member3="Another teammate (optional)",
        member4="Another teammate (optional)",
        member5="Another teammate (optional)",
        activity="What you did together (optional)",
    )
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def squad(
        self,
        ctx: commands.Context,
        member: Optional[discord.Member] = None,
        member2: Optional[discord.Member] = None,
        member3: Optional[discord.Member] = None,
        member4: Optional[discord.Member] = None,
        member5: Optional[discord.Member] = None,
        *,
        activity: Optional[str] = None,
    ):
        cfg = await self._cfg(ctx.guild.id)
        if cfg["coop_reward"] <= 0:
            return await ctx.reply(
                embed=h.warn(
                    "Co-op rewards are switched off on this bot.",
                    "Disabled",
                ),
                ephemeral=True,
            )
        if not await self._check_coop_ready(ctx, "coop", "squad"):
            return
        # Collect any directly-tagged teammates, dropping bots/self/duplicates.
        partner_ids: list[int] = []
        for m in (member, member2, member3, member4, member5):
            if m is None:
                continue
            if m.bot:
                return await ctx.reply(
                    embed=h.err("You can't squad up with a bot."), ephemeral=True
                )
            if m.id == ctx.author.id:
                return await ctx.reply(
                    embed=h.err("Tag the teammates you played with, not yourself."),
                    ephemeral=True,
                )
            if m.id not in partner_ids:
                partner_ids.append(m.id)

        activity = (activity or "").strip()[:200]

        # No one tagged → open the host-only picker menu (up to 25 teammates).
        if not partner_ids:
            builder = SquadBuilderView(self, ctx.author.id, activity)
            msg = await ctx.reply(embed=builder._embed(), view=builder)
            builder.message = msg
            return

        view = await self._make_squad(
            ctx.guild.id, ctx.channel.id, ctx.author.id, partner_ids, activity
        )
        pings = " ".join(f"<@{uid}>" for uid in partner_ids)
        msg = await ctx.reply(content=pings, embed=await view._embed(cfg), view=view)
        view.message = msg
        await db.set_squad_message(view.squad_id, msg.id)
        self._arm_squad(view)

    # ── Squad persistence / lifecycle ────────────────────────────────────────────
    async def _make_squad(
        self,
        guild_id: int,
        channel_id: int,
        author_id: int,
        partner_ids: list[int],
        activity: str,
    ) -> SquadView:
        """Persist a new pending squad confirm and build its (unsent) view."""
        created_at = time.time()
        squad_id = await db.create_squad(
            guild_id, channel_id, author_id, partner_ids, activity, created_at
        )
        return SquadView(
            self, squad_id, author_id, partner_ids, activity, created_at=created_at
        )

    def _arm_squad(self, view: SquadView) -> None:
        """Track a pending squad confirm and schedule its auto-expire."""
        self._squads[view.squad_id] = view
        delay = max(0.0, view.created_at + COOP_CONFIRM_TIMEOUT - time.time())
        self._squad_tasks[view.squad_id] = asyncio.create_task(
            self._squad_expiry(view, delay)
        )

    async def _squad_expiry(self, view: SquadView, delay: float) -> None:
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return
        await view.expire()

    async def _end_squad(self, squad_id: int) -> None:
        """Drop a confirmed/declined/expired squad: cancel timer + delete the row."""
        task = self._squad_tasks.pop(squad_id, None)
        if task is not None and task is not asyncio.current_task():
            task.cancel()
        self._squads.pop(squad_id, None)
        await db.delete_squad(squad_id)

    # ══════════════════════════════════════════════════════════════════════════
    #  /raid  — flat, group-co-op join board
    # ══════════════════════════════════════════════════════════════════════════
    @commands.hybrid_command(
        name="raid",
        aliases=["event"],
        description="Start a group co-op anyone can join — the whole party earns coins!",
        extras={
            "category": "🪙 Economy",
            "sub": "🤝 Co-op",
            "short": "Group co-op reward board",
            "usage": "raid [activity]",
            "desc": "Opens a join board for a big group activity (a raid, an event, a "
            "world boss). Members press Join to take part; you (the host) or a "
            "server manager press Finish to pay the whole party coins + contribution.",
            "args": ["activity — what the group is doing (optional)"],
            "perms": "None to start; Finish/Cancel is host or Manage Server",
            "example": "{prefix}raid molten core run",
        },
    )
    @commands.guild_only()
    @app_commands.describe(activity="What the group is doing (optional)")
    @commands.cooldown(1, 15, commands.BucketType.user)
    async def raid(self, ctx: commands.Context, *, activity: Optional[str] = None):
        cfg = await self._cfg(ctx.guild.id)
        if cfg["raid_reward"] <= 0:
            return await ctx.reply(
                embed=h.warn(
                    "Raid rewards are switched off on this bot.",
                    "Disabled",
                ),
                ephemeral=True,
            )
        if not await self._check_coop_ready(ctx, "raid", "raid"):
            return
        activity = (activity or "").strip()[:200]
        created_at = time.time()
        raid_id = await db.create_raid(
            ctx.guild.id,
            ctx.channel.id,
            ctx.author.id,
            activity,
            [ctx.author.id],
            created_at,
        )
        view = RaidView(self, raid_id, ctx.author.id, activity, created_at=created_at)
        msg = await ctx.reply(embed=await view._embed(cfg), view=view)
        view.message = msg
        await db.set_raid_message(raid_id, msg.id)
        self._arm_raid(view)

    # ── Raid persistence / lifecycle ─────────────────────────────────────────────
    def _arm_raid(self, view: RaidView) -> None:
        """Track a live raid and schedule its RAID_TIMEOUT auto-expire."""
        self._raids[view.raid_id] = view
        delay = max(0.0, view.created_at + RAID_TIMEOUT - time.time())
        self._raid_tasks[view.raid_id] = asyncio.create_task(
            self._raid_expiry(view, delay)
        )

    async def _raid_expiry(self, view: RaidView, delay: float) -> None:
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return
        await view.expire()

    async def _end_raid(self, raid_id: int) -> None:
        """Drop a finished/cancelled/expired raid: cancel its timer + delete the row."""
        task = self._raid_tasks.pop(raid_id, None)
        if task is not None and task is not asyncio.current_task():
            task.cancel()
        self._raids.pop(raid_id, None)
        await db.delete_raid(raid_id)

    @commands.Cog.listener()
    async def on_restore_schedules(self):
        """Rebuild open raid boards + pending squad confirms after a restart."""
        for row in await db.get_open_raids():
            raid_id = row["raid_id"]
            if raid_id in self._raids:
                # on_ready re-fired (gateway re-identify) — this raid is already
                # live; rebuilding it would leak the old expiry timer.
                continue
            if row["message_id"] is None:
                # Crash beat the message-id write — nothing to bind buttons to.
                await db.delete_raid(raid_id)
                continue
            participants = {uid: None for uid in row["participants"]}
            participants.setdefault(row["host_id"], None)
            view = RaidView(
                self,
                raid_id,
                row["host_id"],
                row["activity"],
                participants=participants,
                created_at=row["created_at"],
            )
            self.bot.add_view(view, message_id=row["message_id"])
            channel = self.bot.get_channel(row["channel_id"])
            if channel is not None:
                view.message = channel.get_partial_message(row["message_id"])
            self._arm_raid(view)

        for row in await db.get_open_squads():
            squad_id = row["squad_id"]
            if squad_id in self._squads:
                continue  # already live — same re-identify guard as raids
            if row["message_id"] is None:
                await db.delete_squad(squad_id)
                continue
            view = SquadView(
                self,
                squad_id,
                row["author_id"],
                row["partner_ids"],
                row["activity"],
                confirmed=set(row["confirmed"]),
                created_at=row["created_at"],
            )
            self.bot.add_view(view, message_id=row["message_id"])
            channel = self.bot.get_channel(row["channel_id"])
            if channel is not None:
                view.message = channel.get_partial_message(row["message_id"])
            self._arm_squad(view)

    # ══════════════════════════════════════════════════════════════════════════
    #  /shop  group
    # ══════════════════════════════════════════════════════════════════════════
    @commands.hybrid_group(
        name="shop",
        description="Browse and redeem rewards with your coins. See /shop list.",
        invoke_without_command=True,
        extras={
            "category": "🪙 Economy",
            "sub": "💰 Wallet & Shop",
            "short": "Redeem coins for rewards",
            "usage": "shop [subcommand]",
            "desc": "Spend your coins on rewards mods set up: Discord roles or custom "
            "rewards (in-game loot, perks, anything). Admins manage items with "
            "Manage Server.",
            "args": [],
            "perms": "Admin subcommands require Manage Server",
            "example": "{prefix}shop\n{prefix}shop buy Personal Role",
        },
    )
    @commands.guild_only()
    async def shop(self, ctx: commands.Context):
        await self._show_shop(ctx, 1)

    # ── /shop list ────────────────────────────────────────────────────────────────
    @shop.command(name="list", description="Browse the items you can buy.")
    @app_commands.describe(page="Page number (8 per page)")
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def shop_list(self, ctx: commands.Context, page: int = 1):
        await self._show_shop(ctx, page)

    async def _show_shop(self, ctx: commands.Context, page: int):
        cfg = await self._cfg(ctx.guild.id)
        page = max(1, page)
        per = 8
        total = await db.count_shop_items(ctx.guild.id, enabled_only=True)
        if total == 0:
            return await ctx.reply(
                embed=h.info(
                    "The shop is empty. Admins can drop in starter rewards with "
                    "`/shop seed`, or add their own with `/shop add`.",
                    "🛒 Shop",
                )
            )
        pages = (total + per - 1) // per
        page = min(page, pages)
        offset = (page - 1) * per
        items = await db.list_shop_items(
            ctx.guild.id, enabled_only=True, limit=per, offset=offset
        )

        embed = h.embed("🛒 Shop", color=h.BLUE)
        for item in items:
            tag = "🎭 Role" if item["kind"] == "role" else "🎁 Custom"
            meta = [tag]
            if item["stock"] != -1:
                meta.append(f"{item['stock']} left")
            if item["per_user_limit"] > 0:
                meta.append(f"limit {item['per_user_limit']}/user")
            meta.append(self._effort(item["price"]))
            desc = item["description"] or "—"
            embed.add_field(
                name=f"`#{item['id']}` {item['name']} — {self._money(cfg, item['price'])}",
                value=f"{desc}\n*{' · '.join(meta)}*",
                inline=False,
            )
        embed.set_footer(
            text=f"Page {page}/{pages} · buy with /shop buy <id or name> · "
            "times assume non-stop fishing, so real ones run longer"
        )
        await ctx.reply(embed=embed)

    # ── /shop buy ─────────────────────────────────────────────────────────────────
    @shop.command(name="buy", description="Redeem an item by its id or name.")
    @app_commands.describe(item="Pick a reward from the shop list")
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def shop_buy(self, ctx: commands.Context, *, item: str):
        cfg = await self._cfg(ctx.guild.id)
        record = await self._resolve_item(ctx.guild.id, item)
        if not record or not record["enabled"]:
            return await ctx.reply(
                embed=h.err(f"No shop item matches `{item}`. Try `/shop list`."),
                ephemeral=True,
            )

        # For role rewards, validate grantability BEFORE charging the buyer.
        role = None
        if record["kind"] == "role":
            role = ctx.guild.get_role(record["role_id"]) if record["role_id"] else None
            err = self._role_grant_error(ctx, role)
            if err:
                return await ctx.reply(embed=h.err(err), ephemeral=True)
            if role in ctx.author.roles:
                return await ctx.reply(
                    embed=h.warn(f"You already have **{role.name}**."), ephemeral=True
                )

        res = await db.purchase_item(ctx.guild.id, record["id"], ctx.author.id)
        if not res["ok"]:
            return await ctx.reply(
                embed=h.err(self._buy_failure_text(res, cfg)), ephemeral=True
            )

        await globalxp.award(ctx.author.id, "shop")
        item_row = res["item"]
        if record["kind"] == "role":
            try:
                await ctx.author.add_roles(
                    role, reason=f"Shop purchase: {item_row['name']}"
                )
            except discord.HTTPException:
                # Grant failed after charge — refund coins and restore stock
                # (relative increment: writing back the stale pre-purchase
                # snapshot would clobber a concurrent buyer's decrement).
                await db.add_coins(ctx.author.id, item_row["price"])
                if item_row["stock"] != -1:
                    await db.restock_shop_item(ctx.guild.id, item_row["id"])
                return await ctx.reply(
                    embed=h.err("Couldn't grant the role — refunded. Tell an admin."),
                    ephemeral=True,
                )
            return await ctx.reply(
                embed=h.ok(
                    f"Redeemed **{item_row['name']}** — you got the {role.mention} role!\n"
                    f"Balance: {self._money(cfg, res['new_balance'])}",
                    "🎭 Reward Unlocked",
                )
            )

        # Custom reward: tell the buyer, queue it for a mod to fulfil.
        payload = item_row["payload"] or "A mod will follow up with your reward."
        await ctx.reply(
            embed=h.ok(
                f"Redeemed **{item_row['name']}**!\n\n{payload}\n\n"
                f"*Mods have been notified to fulfil this.*\n"
                f"Balance: {self._money(cfg, res['new_balance'])}",
                "🎁 Reward Redeemed",
            )
        )

    @shop_buy.autocomplete("item")
    async def _shop_buy_ac(self, interaction: discord.Interaction, current: str):
        return await self._shop_choices(interaction, current, enabled_only=True)

    # ── /shop add ─────────────────────────────────────────────────────────────────
    @shop.command(name="add", description="Add a shop item (Manage Server).")
    @app_commands.describe(
        name="Item name (unique, max 80 chars)",
        price="Coin cost",
        kind="role = grants a Discord role · custom = manual reward",
        role="Role to grant (required for a role item)",
        reward="Custom reward text shown to the buyer (custom items)",
        description="Short description shown in the shop",
        stock="Total available (omit or -1 for unlimited)",
        limit="Max purchases per user (0 = unlimited)",
        cooldown="Seconds a user must wait between buying this (0 = none)",
    )
    @app_commands.choices(
        kind=[
            app_commands.Choice(name="role", value="role"),
            app_commands.Choice(name="custom", value="custom"),
        ]
    )
    @commands.has_permissions(manage_guild=True)
    async def shop_add(
        self,
        ctx: commands.Context,
        name: str,
        price: int,
        kind: str,
        role: Optional[discord.Role] = None,
        reward: Optional[str] = None,
        description: Optional[str] = None,
        stock: int = -1,
        limit: int = 0,
        cooldown: int = 0,
    ):
        name = name.strip()
        kind = kind.lower().strip()
        if not name or len(name) > 80:
            return await ctx.reply(
                embed=h.err("Name must be 1–80 characters."), ephemeral=True
            )
        if kind not in ("role", "custom"):
            return await ctx.reply(
                embed=h.err("Kind must be `role` or `custom`."), ephemeral=True
            )
        if price < 0 or price > COIN_MAX:
            return await ctx.reply(
                embed=h.err(f"Price must be 0–{COIN_MAX:,}."), ephemeral=True
            )
        if limit < 0 or cooldown < 0:
            return await ctx.reply(
                embed=h.err("Limit and cooldown can't be negative."), ephemeral=True
            )
        if kind == "role":
            if not role:
                return await ctx.reply(
                    embed=h.err("A role item needs a `role`."), ephemeral=True
                )
            err = self._role_grant_error(ctx, role)
            if err:
                return await ctx.reply(embed=h.err(err), ephemeral=True)
        if kind == "custom" and not reward:
            return await ctx.reply(
                embed=h.err("A custom item needs `reward` text."), ephemeral=True
            )

        item_id = await db.add_shop_item(
            ctx.guild.id,
            name,
            price,
            kind,
            description=(description or "").strip()[:200],
            role_id=role.id if role else None,
            payload=(reward or "").strip()[:500],
            stock=stock if stock >= 0 else -1,
            per_user_limit=limit,
            cooldown=cooldown,
        )
        if item_id is None:
            return await ctx.reply(
                embed=h.err(f"An item named **{name}** already exists."), ephemeral=True
            )
        cfg = await self._cfg(ctx.guild.id)
        await ctx.reply(
            embed=h.ok(
                f"Added **{name}** (`#{item_id}`) for {self._money(cfg, price)} "
                f"— about **{self._effort(price)}** at full tilt.\n"
                "Adjust with `/shop edit` if that's not the effort you wanted.",
                "🛒 Item Added",
            )
        )

    # ── /shop seed ────────────────────────────────────────────────────────────────
    @shop.command(
        name="seed",
        description="Fill an empty shop with starter rewards (Manage Server).",
    )
    @commands.has_permissions(manage_guild=True)
    async def shop_seed(self, ctx: commands.Context):
        cfg = await self._cfg(ctx.guild.id)
        added, skipped = 0, 0
        for spec in _DEFAULT_SHOP_ITEMS:
            item_id = await db.add_shop_item(
                ctx.guild.id,
                spec["name"],
                _scaled_price(spec["price"], cfg["daily_amount"]),
                "custom",
                description=spec["description"],
                payload=spec["reward"],
                per_user_limit=spec.get("limit", 0),
            )
            if item_id is None:
                skipped += 1  # name already exists — leave the mod's version alone
            else:
                added += 1
        if added == 0:
            return await ctx.reply(
                embed=h.warn(
                    "All starter items already exist — nothing added. "
                    "Use `/shop add` to create your own.",
                    "🛒 Shop",
                )
            )
        note = f" ({skipped} already existed, left as-is)" if skipped else ""
        await ctx.reply(
            embed=h.ok(
                f"Added **{added}** starter reward(s) to the shop{note}.\n\n"
                "These are generic examples a mod fulfils by hand — edit prices "
                "with `/shop edit`, remove ones you don't want with `/shop remove`, "
                "and add role rewards with `/shop add`.",
                "🛒 Shop Seeded",
            )
        )

    # ── /shop edit ────────────────────────────────────────────────────────────────
    @shop.command(name="edit", description="Edit a shop item's fields (Manage Server).")
    @app_commands.describe(
        item="Pick the item to edit",
        price="New price",
        description="New description",
        reward="New custom reward text",
        stock="New stock (-1 = unlimited)",
        limit="New per-user limit (0 = unlimited)",
        cooldown="New per-user cooldown seconds (0 = none)",
        enabled="Show (true) or hide (false) the item",
    )
    @commands.has_permissions(manage_guild=True)
    async def shop_edit(
        self,
        ctx: commands.Context,
        item: str,
        price: Optional[int] = None,
        description: Optional[str] = None,
        reward: Optional[str] = None,
        stock: Optional[int] = None,
        limit: Optional[int] = None,
        cooldown: Optional[int] = None,
        enabled: Optional[bool] = None,
    ):
        record = await self._resolve_item(ctx.guild.id, item)
        if not record:
            return await ctx.reply(
                embed=h.err(f"No shop item matches `{item}`."), ephemeral=True
            )
        fields: dict = {}
        if price is not None:
            if price < 0 or price > COIN_MAX:
                return await ctx.reply(
                    embed=h.err(f"Price must be 0–{COIN_MAX:,}."), ephemeral=True
                )
            fields["price"] = price
        if description is not None:
            fields["description"] = description.strip()[:200]
        if reward is not None:
            fields["payload"] = reward.strip()[:500]
        if stock is not None:
            fields["stock"] = stock if stock >= 0 else -1
        if limit is not None:
            if limit < 0:
                return await ctx.reply(
                    embed=h.err("Limit can't be negative."), ephemeral=True
                )
            fields["per_user_limit"] = limit
        if cooldown is not None:
            if cooldown < 0:
                return await ctx.reply(
                    embed=h.err("Cooldown can't be negative."), ephemeral=True
                )
            fields["cooldown"] = cooldown
        if enabled is not None:
            fields["enabled"] = enabled
        if not fields:
            return await ctx.reply(
                embed=h.warn("Nothing to change — pass at least one field."),
                ephemeral=True,
            )
        await db.edit_shop_item(ctx.guild.id, record["id"], **fields)
        await ctx.reply(
            embed=h.ok(f"Updated **{record['name']}** (`#{record['id']}`).")
        )

    @shop_edit.autocomplete("item")
    async def _shop_edit_ac(self, interaction: discord.Interaction, current: str):
        return await self._shop_choices(interaction, current, enabled_only=False)

    # ── /shop remove ──────────────────────────────────────────────────────────────
    @shop.command(name="remove", description="Delete a shop item (Manage Server).")
    @app_commands.describe(item="Pick the item to delete")
    @commands.has_permissions(manage_guild=True)
    async def shop_remove(self, ctx: commands.Context, *, item: str):
        record = await self._resolve_item(ctx.guild.id, item)
        if not record:
            return await ctx.reply(
                embed=h.err(f"No shop item matches `{item}`."), ephemeral=True
            )
        await db.remove_shop_item(ctx.guild.id, record["id"])
        await ctx.reply(embed=h.ok(f"Removed **{record['name']}** from the shop."))

    @shop_remove.autocomplete("item")
    async def _shop_remove_ac(self, interaction: discord.Interaction, current: str):
        return await self._shop_choices(interaction, current, enabled_only=False)

    # ── /shop pending ─────────────────────────────────────────────────────────────
    @shop.command(
        name="pending", description="List custom rewards awaiting fulfilment."
    )
    @commands.has_permissions(manage_guild=True)
    async def shop_pending(self, ctx: commands.Context):
        total = await db.count_pending_purchases(ctx.guild.id)
        if total == 0:
            return await ctx.reply(
                embed=h.info("No custom rewards waiting.", "🎁 Pending")
            )
        rows = await db.list_pending_purchases(ctx.guild.id, limit=25)
        lines = []
        for r in rows:
            member = ctx.guild.get_member(r["user_id"])
            who = member.mention if member else f"<@{r['user_id']}>"
            lines.append(f"`#{r['id']}` {who} → **{r['item_name']}**")
        embed = h.embed("🎁 Pending Rewards", "\n".join(lines), h.BLUE)
        embed.set_footer(text=f"{total} pending · fulfil with /shop fulfill <id>")
        await ctx.reply(embed=embed)

    # ── /shop fulfill ─────────────────────────────────────────────────────────────
    @shop.command(
        name="fulfill",
        aliases=["fulfil"],
        description="Mark a pending custom reward as delivered (Manage Server).",
    )
    @app_commands.describe(purchase_id="Pick a pending reward to mark delivered")
    @commands.has_permissions(manage_guild=True)
    async def shop_fulfill(self, ctx: commands.Context, purchase_id: int):
        res = await db.fulfill_purchase(ctx.guild.id, purchase_id, ctx.author.id)
        if not res:
            return await ctx.reply(
                embed=h.err(f"No pending purchase `#{purchase_id}`."), ephemeral=True
            )
        member = ctx.guild.get_member(res["user_id"])
        who = member.mention if member else f"<@{res['user_id']}>"
        await ctx.reply(
            embed=h.ok(f"Marked **{res['item_name']}** for {who} as fulfilled.")
        )

    @shop_fulfill.autocomplete("purchase_id")
    async def _shop_fulfill_ac(self, interaction: discord.Interaction, current: str):
        """The fulfil queue as a pick list — the id is a bookkeeping detail mods
        shouldn't have to copy out of /shop pending."""
        if not interaction.guild_id:
            return []
        q = (current or "").strip().lower()
        rows = await db.list_pending_purchases(interaction.guild_id, limit=25)
        choices: list[app_commands.Choice[int]] = []
        for r in rows:
            member = (
                interaction.guild.get_member(r["user_id"])
                if interaction.guild
                else None
            )
            who = member.display_name if member else f"User {r['user_id']}"
            if q and q not in r["item_name"].lower() and q not in who.lower():
                continue
            choices.append(
                app_commands.Choice(
                    name=f"#{r['id']} · {r['item_name']} → {who}"[:100], value=r["id"]
                )
            )
        return choices[:25]

    # ── shop helpers ──────────────────────────────────────────────────────────────
    async def _shop_choices(
        self,
        interaction: discord.Interaction,
        current: str,
        *,
        enabled_only: bool,
    ) -> list[app_commands.Choice[str]]:
        """Tap-to-pick shop list: nobody should have to remember an item id or
        type a reward's exact name. Values are ids, which _resolve_item takes."""
        if not interaction.guild_id:
            return []
        q = (current or "").strip().lower()
        items = await db.list_shop_items(
            interaction.guild_id, enabled_only=enabled_only, limit=100
        )
        cfg = await self._cfg(interaction.guild_id)
        balance = await db.get_balance(interaction.user.id) if enabled_only else 0
        choices: list[app_commands.Choice[str]] = []
        for item in items:
            if (
                q
                and q not in item["name"].lower()
                and not str(item["id"]).startswith(q)
            ):
                continue
            label = f"{item['name']} — {item['price']:,} {cfg['currency_name']}"
            if enabled_only:
                label = ("✅ " if balance >= item["price"] else "🔒 ") + label
            elif not item["enabled"]:
                label += " (hidden)"
            if item["stock"] == 0:
                label += " · sold out"
            elif item["stock"] > 0:
                label += f" · {item['stock']} left"
            choices.append(app_commands.Choice(name=label[:100], value=str(item["id"])))
        return choices[:25]

    async def _resolve_item(self, guild_id: int, ref: str) -> Optional[dict]:
        """Resolve a shop item by numeric id, else by exact (case-insensitive) name."""
        ref = ref.strip()
        if ref.lstrip("#").isdigit():
            item = await db.get_shop_item(guild_id, int(ref.lstrip("#")))
            if item:
                return item
        return await db.get_shop_item_by_name(guild_id, ref)

    def _role_grant_error(
        self, ctx: commands.Context, role: Optional[discord.Role]
    ) -> Optional[str]:
        """Return an error string if the bot can't grant `role`, else None."""
        if role is None:
            return "That role no longer exists. Tell an admin to fix the item."
        me = ctx.guild.me
        if not me.guild_permissions.manage_roles:
            return "I need the Manage Roles permission to grant shop roles."
        if role.managed or role.is_default():
            return "That role can't be assigned by a bot."
        if role >= me.top_role:
            return "That role is above my highest role, so I can't grant it."
        return None

    def _buy_failure_text(self, res: dict, cfg: dict) -> str:
        reason = res.get("reason")
        item = res.get("item", {})
        if reason == "funds":
            return f"Not enough coins — **{item.get('name', 'this')}** costs {self._money(cfg, item.get('price', 0))}."
        if reason == "out_of_stock":
            return f"**{item.get('name', 'This item')}** is sold out."
        if reason == "limit":
            return f"You've hit the purchase limit for **{item.get('name', 'this item')}**."
        if reason == "cooldown":
            return (
                f"You bought **{item.get('name', 'this')}** recently. Try again in "
                f"**{h.fmt_duration(res.get('retry_after', 0))}**."
            )
        if reason == "disabled":
            return "That item isn't available right now."
        return "That item couldn't be purchased."


async def setup(bot: commands.Bot):
    await bot.add_cog(Economy(bot))
