"""
cogs/economy.py
Per-guild NanoCoin economy.

Members hold a coin balance, claim a daily reward (with a consecutive-day
streak bonus), and pay each other. They also reward co-op activity: /report
tags a partner who confirms with a button, and /raid opens a join board for a
whole group (the host or a mod presses Finish to pay the party) — both grant
spendable coins and a lifetime contribution stat that drives a separate
contributor leaderboard and rank titles. Coins are spent in a per-guild shop on
Discord roles (granted instantly) or custom rewards (queued for a mod to
fulfil), with optional stock counts, per-user limits, and cooldowns. Admins
grant/take coins, view a rich list, and customise the currency name, emoji,
daily amount, streak bonus, co-op reward, and raid reward/party size.

Slash command budget: five flat commands (/balance, /daily, /pay, /report,
/raid) plus two groups (/coin …, /shop …) whose subcommands cost no extra
top-level slots.

──────────────────────────────────────────────────────
Commands
──────────────────────────────────────────────────────
  /balance [member]              → check a balance (+ contribution rank)
  /daily                         → claim the daily reward
  /pay <member> <amount>         → send coins to someone
  /report <member> [activity]    → co-op reward, partner confirms (alias: coop)
  /raid [activity]               → group co-op join board (alias: event)
  /coin top [page]               → richest members
  /coin contrib [page]           → top contributors (alias: contributions)
  /coin gamble <amount>          → bet coins to double them (alias: bet)
  /coin grant <member> <amount>  → add coins        (Manage Server)
  /coin take <member> <amount>   → remove coins     (Manage Server)
  /coin reset [member]           → wipe balances    (Manage Server)
  /coin daily <amount>           → set daily reward (Manage Server)
  /coin streakbonus <amount>     → per-day bonus    (Manage Server)
  /coin coop <amount>            → set co-op reward (Manage Server)
  /coin raid <amount>            → set raid reward  (Manage Server)
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

log = logging.getLogger("NanoBot.economy")

DAILY_COOLDOWN = 86_400  # 24h between claims
STREAK_WINDOW = 172_800  # claim within 48h of the last to keep the streak

# A daily reject arriving within this many seconds of the last successful claim
# isn't a genuine "come back tomorrow" — it's the same claim dispatched twice
# (e.g. a gateway redelivery, or two bot processes briefly online during a
# restart). Flagged as a duplicate so the command can swallow the contradictory
# second reply instead of telling the user they already claimed.
DAILY_DUP_WINDOW = 10

# Gamble odds: win chance under 0.5 gives the "house" a slight edge so coins
# aren't trivially farmed. A win pays the bet back plus (multiplier - 1)x.
GAMBLE_WIN_CHANCE = 0.45
GAMBLE_MULTIPLIER = 2.0

# Sanity ceiling for any single admin coin amount (grant / daily reward / streak
# bonus). Stops a fat-fingered "give 1e18" from wrecking the economy or pushing
# values toward integer limits. A billion is far above any real use.
COIN_MAX = 1_000_000_000

# How long a /report co-op reward waits for the partner to confirm.
COOP_CONFIRM_TIMEOUT = 120

# How long an open /raid board stays joinable before it auto-expires unpaid.
RAID_TIMEOUT = 1800  # 30 min

# Starter shop catalogue. A fresh guild's shop is empty, which reads as broken,
# so /shop seed drops in this curated set of generic community rewards. They're
# all `custom` kind (a mod fulfils them) because role rewards need a guild's own
# role id, which we can't know ahead of time. Prices are scaled to the default
# 100-coin daily reward (a few days' to a couple weeks' saving). Mods are meant
# to edit prices, remove ones that don't fit, and add their own role rewards.
_DEFAULT_SHOP_ITEMS = [
    {
        "name": "Custom Color Role",
        "price": 1500,
        "description": "Pick your own name color.",
        "reward": "Tell a mod the hex color you want and they'll set up your "
        "personal colored role.",
        "limit": 1,
    },
    {
        "name": "Custom Nickname",
        "price": 300,
        "description": "Request a nickname a mod will apply.",
        "reward": "Reply with the nickname you'd like and a mod will set it for you.",
    },
    {
        "name": "Server Shoutout",
        "price": 500,
        "description": "Get a shoutout in the announcements channel.",
        "reward": "A mod will post a shoutout for you in the announcements channel.",
    },
    {
        "name": "Pin a Message",
        "price": 400,
        "description": "Pin one message of your choice for a week.",
        "reward": "Link the message you want pinned and a mod will pin it for a week.",
    },
    {
        "name": "VIP for a Day",
        "price": 750,
        "description": "24 hours of VIP perks.",
        "reward": "A mod will grant you VIP perks for the next 24 hours.",
    },
    {
        "name": "Pick the Next Event",
        "price": 1000,
        "description": "Choose the theme of the next server event.",
        "reward": "Share your event idea — the next server event will run with "
        "your theme.",
    },
    {
        "name": "Movie Night Pick",
        "price": 600,
        "description": "Choose the next watch-party title.",
        "reward": "Tell a mod your pick and it becomes the next movie/watch-party.",
    },
    {
        "name": "Add a Server Emoji",
        "price": 2000,
        "description": "Submit an emoji to be added to the server.",
        "reward": "Send a mod the image and name for an emoji to add to the server.",
        "limit": 1,
    },
]


# Contribution rank titles, awarded by leaderboard position. The first match
# (lowest threshold the rank meets) wins; everyone ranked gets at least Member.
_RANK_TITLES = [
    (1, "🏆 Guild Legend"),
    (3, "💎 Veteran"),
    (10, "⭐ Trusted"),
    (25, "🤝 Contributor"),
]


# ══════════════════════════════════════════════════════════════════════════════
#  Pure helpers (no Discord deps — covered by tests/test_economy_helpers.py)
# ══════════════════════════════════════════════════════════════════════════════


def fmt_coins(amount: int, name: str, emoji: str) -> str:
    """Render a coin amount, e.g. '🪙 1,234 NanoCoins'."""
    label = name if abs(amount) == 1 else f"{name}s"
    return f"{emoji} **{amount:,}** {label}"


def compute_daily(
    now: float, last_daily: float, streak: int, base: int, bonus: int
) -> dict:
    """Decide a daily claim.

    Returns {"ok": False, "retry_after": secs} if still on cooldown, else
    {"ok": True, "total": coins, "streak": new_streak}.
    """
    elapsed = now - last_daily
    if last_daily and elapsed < DAILY_COOLDOWN:
        return {
            "ok": False,
            "retry_after": int(DAILY_COOLDOWN - elapsed),
            "duplicate": elapsed < DAILY_DUP_WINDOW,
        }
    if last_daily and elapsed < STREAK_WINDOW:
        new_streak = streak + 1
    else:
        new_streak = 1
    total = base + bonus * (new_streak - 1)
    return {"ok": True, "total": total, "streak": new_streak}


def resolve_gamble(
    amount: int,
    roll: float,
    win_chance: float = GAMBLE_WIN_CHANCE,
    multiplier: float = GAMBLE_MULTIPLIER,
) -> dict:
    """Resolve a bet given a roll in [0, 1).

    Returns {"won": bool, "delta": net_coin_change}. A win nets
    +round(amount × (multiplier - 1)); a loss nets -amount.
    """
    if roll < win_chance:
        return {"won": True, "delta": round(amount * (multiplier - 1))}
    return {"won": False, "delta": -amount}


def _rank_title(position: int) -> str:
    """Title for a contribution-leaderboard position (1 = top)."""
    for threshold, title in _RANK_TITLES:
        if position <= threshold:
            return title
    return "🌱 Member"


# ══════════════════════════════════════════════════════════════════════════════
#  /report  co-op confirmation view
# ══════════════════════════════════════════════════════════════════════════════
class ReportView(discord.ui.View):
    """Partner-confirm gate for a co-op activity reward.

    Only the named partner can confirm; either party can decline. Coins +
    contribution are awarded to both members on confirm. Short-lived (no
    persistence) — a pending report simply expires on bot restart.
    """

    def __init__(self, cog: "Economy", author_id: int, partner_id: int, activity: str):
        super().__init__(timeout=COOP_CONFIRM_TIMEOUT)
        self.cog = cog
        self.author_id = author_id
        self.partner_id = partner_id
        self.activity = activity
        self.message: Optional[discord.Message] = None
        self.resolved = False

    async def on_timeout(self):
        if self.resolved or self.message is None:
            return
        for child in self.children:
            child.disabled = True
        try:
            await self.message.edit(
                embed=h.warn(
                    "Co-op report expired — partner didn't confirm in time.",
                    "⏳ Expired",
                ),
                view=self,
            )
        except discord.HTTPException:
            pass

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.success, emoji="🤝")
    async def confirm(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if interaction.user.id != self.partner_id:
            return await interaction.response.send_message(
                embed=h.err("Only the tagged partner can confirm this report."),
                ephemeral=True,
            )
        self.resolved = True
        guild_id = interaction.guild.id
        cfg = await self.cog._cfg(guild_id)
        reward = cfg["coop_reward"]
        # Award coins + lifetime contribution to both members.
        for uid in (self.author_id, self.partner_id):
            await db.add_coins(guild_id, uid, reward)
            await db.add_contribution(guild_id, uid, reward)
        for child in self.children:
            child.disabled = True
        activity = f" for **{self.activity}**" if self.activity else ""
        await interaction.response.edit_message(
            embed=h.ok(
                f"🤝 <@{self.author_id}> and <@{self.partner_id}> teamed up{activity}!\n"
                f"Both earned {self.cog._money(cfg, reward)} and "
                f"**+{reward:,}** contribution.",
                "Co-op Confirmed",
            ),
            view=self,
        )
        self.stop()

    @discord.ui.button(label="Decline", style=discord.ButtonStyle.secondary, emoji="✖️")
    async def decline(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if interaction.user.id not in (self.author_id, self.partner_id):
            return await interaction.response.send_message(
                embed=h.err("Only the people involved can decline this report."),
                ephemeral=True,
            )
        self.resolved = True
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(
            embed=h.warn("Co-op report declined.", "✖️ Declined"), view=self
        )
        self.stop()


# ══════════════════════════════════════════════════════════════════════════════
#  /raid  group-co-op join board
# ══════════════════════════════════════════════════════════════════════════════
class RaidView(discord.ui.View):
    """Open join board for a group co-op (raid, event, big dungeon).

    Anyone in the server can Join (clicking is their own confirmation) up to the
    guild's party cap; the host or a Manage-Server mod presses Finish to pay
    everyone who joined, or Cancel to scrap it. Short-lived and in-memory — an
    open board simply expires on bot restart or after RAID_TIMEOUT.
    """

    def __init__(self, cog: "Economy", host_id: int, activity: str):
        super().__init__(timeout=RAID_TIMEOUT)
        self.cog = cog
        self.host_id = host_id
        self.activity = activity
        # Host counts as the first participant; dict keeps stable join order.
        self.participants: dict[int, None] = {host_id: None}
        self.message: Optional[discord.Message] = None
        self.resolved = False

    def _can_manage(self, user: discord.Member) -> bool:
        return user.id == self.host_id or user.guild_permissions.manage_guild

    async def _embed(self, cfg: dict) -> discord.Embed:
        what = f"\n**Activity:** {self.activity}" if self.activity else ""
        names = "\n".join(f"• <@{uid}>" for uid in self.participants)
        reward = self.cog._money(cfg, cfg["raid_reward"])
        need = cfg["raid_min"]
        body = (
            f"Hosted by <@{self.host_id}>.{what}\n\n"
            f"Press **Join** to take part — everyone who joins earns {reward} "
            f"+ contribution when the host presses **Finish**.\n"
            f"*Need at least {need} members · {len(self.participants)}/"
            f"{cfg['raid_max']} joined.*\n\n"
            f"**Party ({len(self.participants)}):**\n{names}"
        )
        return h.embed("⚔️ Raid Party", body, h.BLUE)

    async def _refresh(self, interaction: discord.Interaction, cfg: dict):
        await interaction.response.edit_message(embed=await self._embed(cfg), view=self)

    async def on_timeout(self):
        if self.resolved or self.message is None:
            return
        for child in self.children:
            child.disabled = True
        try:
            await self.message.edit(
                embed=h.warn(
                    "Raid expired — host didn't finish it in time.", "⏳ Expired"
                ),
                view=self,
            )
        except discord.HTTPException:
            pass

    @discord.ui.button(label="Join", style=discord.ButtonStyle.primary, emoji="⚔️")
    async def join(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.bot:
            return
        cfg = await self.cog._cfg(interaction.guild.id)
        if interaction.user.id in self.participants:
            return await interaction.response.send_message(
                embed=h.warn("You're already in the party."), ephemeral=True
            )
        if len(self.participants) >= cfg["raid_max"]:
            return await interaction.response.send_message(
                embed=h.err(f"Party is full ({cfg['raid_max']})."), ephemeral=True
            )
        self.participants[interaction.user.id] = None
        await self._refresh(interaction, cfg)

    @discord.ui.button(label="Leave", style=discord.ButtonStyle.secondary, emoji="🚪")
    async def leave(self, interaction: discord.Interaction, button: discord.ui.Button):
        cfg = await self.cog._cfg(interaction.guild.id)
        if interaction.user.id == self.host_id:
            return await interaction.response.send_message(
                embed=h.warn("The host can't leave — use **Cancel** to scrap it."),
                ephemeral=True,
            )
        if interaction.user.id not in self.participants:
            return await interaction.response.send_message(
                embed=h.warn("You're not in the party."), ephemeral=True
            )
        del self.participants[interaction.user.id]
        await self._refresh(interaction, cfg)

    @discord.ui.button(label="Finish", style=discord.ButtonStyle.success, emoji="✅")
    async def finish(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._can_manage(interaction.user):
            return await interaction.response.send_message(
                embed=h.err("Only the host or a server manager can finish the raid."),
                ephemeral=True,
            )
        cfg = await self.cog._cfg(interaction.guild.id)
        if len(self.participants) < cfg["raid_min"]:
            return await interaction.response.send_message(
                embed=h.err(
                    f"Need at least {cfg['raid_min']} members to pay out "
                    f"(only {len(self.participants)} joined)."
                ),
                ephemeral=True,
            )
        self.resolved = True
        reward = cfg["raid_reward"]
        guild_id = interaction.guild.id
        for uid in self.participants:
            await db.add_coins(guild_id, uid, reward)
            await db.add_contribution(guild_id, uid, reward)
        for child in self.children:
            child.disabled = True
        what = f" for **{self.activity}**" if self.activity else ""
        roster = ", ".join(f"<@{uid}>" for uid in self.participants)
        await interaction.response.edit_message(
            embed=h.ok(
                f"⚔️ Raid complete{what}! **{len(self.participants)}** members "
                f"each earned {self.cog._money(cfg, reward)} + "
                f"**{reward:,}** contribution.\n\n{roster}",
                "Raid Rewards Paid",
            ),
            view=self,
        )
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger, emoji="✖️")
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._can_manage(interaction.user):
            return await interaction.response.send_message(
                embed=h.err("Only the host or a server manager can cancel the raid."),
                ephemeral=True,
            )
        self.resolved = True
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(
            embed=h.warn("Raid cancelled — no coins awarded.", "✖️ Cancelled"),
            view=self,
        )
        self.stop()


# ══════════════════════════════════════════════════════════════════════════════
class Economy(commands.Cog):
    """NanoCoin balances, daily rewards, and transfers — per server."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Per-(guild, user) locks serialize the read-check-write in /daily so two
        # concurrent invocations can't both pass the cooldown check and
        # double-claim. Created lazily; entries are tiny and bounded by the
        # active user set.
        self._daily_locks: dict[tuple[int, int], asyncio.Lock] = {}

    def _daily_lock(self, guild_id: int, user_id: int) -> asyncio.Lock:
        key = (guild_id, user_id)
        lock = self._daily_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._daily_locks[key] = lock
        return lock

    async def _cfg(self, guild_id: int) -> dict:
        return await db.get_econ_config(guild_id)

    def _money(self, cfg: dict, amount: int) -> str:
        return fmt_coins(amount, cfg["currency_name"], cfg["currency_emoji"])

    # ══════════════════════════════════════════════════════════════════════════
    #  /balance  — flat
    # ══════════════════════════════════════════════════════════════════════════
    @commands.hybrid_command(
        name="balance",
        aliases=["bal"],
        description="Check your NanoCoin balance, or someone else's.",
        extras={
            "category": "🪙 Economy",
            "short": "Check a coin balance",
            "usage": "balance [member]",
            "desc": "Shows the coin balance and server wealth rank for you or another member.",
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
        res = await db.get_econ_rank(ctx.guild.id, member.id)
        coins = res[1] if res else 0
        rank_pos = res[0] if res else None
        contrib = await db.get_contrib_rank(ctx.guild.id, member.id)

        embed = h.embed(f"{cfg['currency_emoji']} {member.display_name}", color=h.BLUE)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="Balance", value=self._money(cfg, coins), inline=True)
        embed.add_field(
            name="Rank",
            value=f"**#{rank_pos}**" if rank_pos else "Unranked",
            inline=True,
        )
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
        async with self._daily_lock(ctx.guild.id, ctx.author.id):
            last_daily, streak = await db.get_daily_state(ctx.guild.id, ctx.author.id)
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

            new_bal = await db.add_coins(ctx.guild.id, ctx.author.id, res["total"])
            await db.set_daily_state(
                ctx.guild.id, ctx.author.id, time.time(), res["streak"]
            )
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

        ok = await db.transfer_coins(ctx.guild.id, ctx.author.id, member.id, amount)
        if not ok:
            bal = await db.get_balance(ctx.guild.id, ctx.author.id)
            return await ctx.reply(
                embed=h.err(f"Not enough coins. You have {self._money(cfg, bal)}."),
                ephemeral=True,
            )
        new_bal = await db.get_balance(ctx.guild.id, ctx.author.id)
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
            "short": "Rich list + economy admin settings",
            "usage": "coin [subcommand]",
            "desc": "View the richest members with /coin top. Admins grant/take coins "
            "and customise the daily reward, streak bonus, currency name, and emoji.",
            "args": [],
            "perms": "Admin subcommands require Manage Server",
            "example": "{prefix}coin top\n{prefix}coin grant @User 500",
        },
    )
    @commands.guild_only()
    async def coin(self, ctx: commands.Context):
        await self._show_leaderboard(ctx, 1)

    # ── /coin top ───────────────────────────────────────────────────────────
    @coin.command(name="top", description="Show the richest members.")
    @app_commands.describe(page="Page number (10 per page)")
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def coin_top(self, ctx: commands.Context, page: int = 1):
        await self._show_leaderboard(ctx, page)

    async def _show_leaderboard(self, ctx: commands.Context, page: int):
        cfg = await self._cfg(ctx.guild.id)
        page = max(1, page)
        per = 10
        total = await db.count_econ(ctx.guild.id)
        if total == 0:
            return await ctx.reply(
                embed=h.info(
                    "No one has any coins yet. Try `/daily`!",
                    f"{cfg['currency_emoji']} Rich List",
                )
            )
        pages = (total + per - 1) // per
        page = min(page, pages)
        offset = (page - 1) * per
        rows = await db.get_econ_leaderboard(ctx.guild.id, per, offset)

        medals = {1: "🥇", 2: "🥈", 3: "🥉"}
        lines = []
        for i, row in enumerate(rows):
            pos = offset + i + 1
            member = ctx.guild.get_member(row["user_id"])
            name = member.display_name if member else f"User {row['user_id']}"
            badge = medals.get(pos, f"`#{pos}`")
            lines.append(f"{badge} **{name}** — {self._money(cfg, row['coins'])}")

        embed = h.embed(f"{cfg['currency_emoji']} Rich List", "\n".join(lines), h.BLUE)
        embed.set_footer(text=f"Page {page}/{pages} · {total} members")
        await ctx.reply(embed=embed)

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
        if not await db.try_debit_coins(ctx.guild.id, ctx.author.id, amount):
            balance = await db.get_balance(ctx.guild.id, ctx.author.id)
            return await ctx.reply(
                embed=h.err(f"Not enough coins. You have {self._money(cfg, balance)}."),
                ephemeral=True,
            )

        res = resolve_gamble(amount, random.random())
        if res["won"]:
            new_bal = await db.add_coins(
                ctx.guild.id, ctx.author.id, amount + res["delta"]
            )
        else:
            new_bal = await db.get_balance(ctx.guild.id, ctx.author.id)
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
    @coin.command(name="grant", description="Add coins to a member's balance.")
    @app_commands.describe(member="Member to credit", amount="Coins to add")
    @commands.has_permissions(manage_guild=True)
    async def coin_grant(
        self, ctx: commands.Context, member: discord.Member, amount: int
    ):
        cfg = await self._cfg(ctx.guild.id)
        if member.bot:
            return await ctx.reply(
                embed=h.err("Bots don't hold coins."), ephemeral=True
            )
        if amount <= 0:
            return await ctx.reply(
                embed=h.err("Amount must be positive."), ephemeral=True
            )
        if amount > COIN_MAX:
            return await ctx.reply(
                embed=h.err(f"Amount can't exceed {COIN_MAX:,}."), ephemeral=True
            )
        new_bal = await db.add_coins(ctx.guild.id, member.id, amount)
        await ctx.reply(
            embed=h.ok(
                f"Granted {self._money(cfg, amount)} to {member.mention}.\n"
                f"New balance: {self._money(cfg, new_bal)}"
            )
        )

    # ── /coin take ─────────────────────────────────────────────────────────────
    @coin.command(name="take", description="Remove coins from a member's balance.")
    @app_commands.describe(member="Member to debit", amount="Coins to remove")
    @commands.has_permissions(manage_guild=True)
    async def coin_take(
        self, ctx: commands.Context, member: discord.Member, amount: int
    ):
        cfg = await self._cfg(ctx.guild.id)
        if amount <= 0:
            return await ctx.reply(
                embed=h.err("Amount must be positive."), ephemeral=True
            )
        new_bal = await db.add_coins(ctx.guild.id, member.id, -amount)
        await ctx.reply(
            embed=h.ok(
                f"Took {self._money(cfg, amount)} from {member.mention}.\n"
                f"New balance: {self._money(cfg, new_bal)}"
            )
        )

    # ── /coin reset ─────────────────────────────────────────────────────────────
    @coin.command(
        name="reset", description="Reset coins for one member, or the whole server."
    )
    @app_commands.describe(member="Member to reset (omit to reset everyone)")
    @commands.has_permissions(manage_guild=True)
    async def coin_reset(
        self, ctx: commands.Context, member: Optional[discord.Member] = None
    ):
        if member:
            await db.reset_economy(ctx.guild.id, member.id)
            return await ctx.reply(
                embed=h.ok(f"Reset coins for **{member.display_name}**.")
            )
        removed = await db.reset_economy(ctx.guild.id)
        await ctx.reply(
            embed=h.ok(f"Reset the whole economy ({removed} accounts cleared).")
        )

    # ── /coin daily ─────────────────────────────────────────────────────────────
    @coin.command(name="daily", description="Set the daily reward amount.")
    @app_commands.describe(amount="Coins given per daily claim")
    @commands.has_permissions(manage_guild=True)
    async def coin_daily(self, ctx: commands.Context, amount: int):
        if amount < 0:
            return await ctx.reply(
                embed=h.err("Amount can't be negative."), ephemeral=True
            )
        if amount > COIN_MAX:
            return await ctx.reply(
                embed=h.err(f"Amount can't exceed {COIN_MAX:,}."), ephemeral=True
            )
        await db.set_econ_config(ctx.guild.id, daily_amount=amount)
        cfg = await self._cfg(ctx.guild.id)
        await ctx.reply(embed=h.ok(f"Daily reward set to {self._money(cfg, amount)}."))

    # ── /coin streakbonus ─────────────────────────────────────────────────────────
    @coin.command(
        name="streakbonus",
        description="Set the bonus coins added per consecutive daily streak day.",
    )
    @app_commands.describe(amount="Bonus coins per extra streak day")
    @commands.has_permissions(manage_guild=True)
    async def coin_streakbonus(self, ctx: commands.Context, amount: int):
        if amount < 0:
            return await ctx.reply(
                embed=h.err("Bonus can't be negative."), ephemeral=True
            )
        if amount > COIN_MAX:
            return await ctx.reply(
                embed=h.err(f"Bonus can't exceed {COIN_MAX:,}."), ephemeral=True
            )
        await db.set_econ_config(ctx.guild.id, streak_bonus=amount)
        cfg = await self._cfg(ctx.guild.id)
        await ctx.reply(
            embed=h.ok(f"Streak bonus set to {self._money(cfg, amount)} per extra day.")
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
        accounts = await db.count_econ(ctx.guild.id)
        embed = h.embed(f"{cfg['currency_emoji']} Economy Settings", color=h.BLUE)
        embed.add_field(
            name="Currency",
            value=f"{cfg['currency_emoji']} {cfg['currency_name']}",
            inline=True,
        )
        embed.add_field(
            name="Daily reward",
            value=f"{cfg['daily_amount']:,}",
            inline=True,
        )
        embed.add_field(
            name="Streak bonus",
            value=f"{cfg['streak_bonus']:,}/day",
            inline=True,
        )
        embed.add_field(name="Accounts", value=str(accounts), inline=True)
        embed.add_field(
            name="Co-op reward",
            value=f"{cfg['coop_reward']:,}/person",
            inline=True,
        )
        embed.add_field(
            name="Raid reward",
            value=f"{cfg['raid_reward']:,}/person",
            inline=True,
        )
        embed.add_field(
            name="Raid party",
            value=f"{cfg['raid_min']}–{cfg['raid_max']} members",
            inline=True,
        )
        await ctx.reply(embed=embed)

    # ── /coin contrib ─────────────────────────────────────────────────────────────
    @coin.command(
        name="contrib",
        aliases=["contributions"],
        description="Show the top guild contributors (co-op leaderboard).",
    )
    @app_commands.describe(page="Page number (10 per page)")
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def coin_contrib(self, ctx: commands.Context, page: int = 1):
        page = max(1, page)
        per = 10
        total = await db.count_contrib(ctx.guild.id)
        if total == 0:
            return await ctx.reply(
                embed=h.info(
                    "No co-op contributions yet. Team up and use `/report`!",
                    "🤝 Top Contributors",
                )
            )
        pages = (total + per - 1) // per
        page = min(page, pages)
        offset = (page - 1) * per
        rows = await db.get_contrib_leaderboard(ctx.guild.id, per, offset)

        medals = {1: "🥇", 2: "🥈", 3: "🥉"}
        lines = []
        for i, row in enumerate(rows):
            pos = offset + i + 1
            member = ctx.guild.get_member(row["user_id"])
            name = member.display_name if member else f"User {row['user_id']}"
            badge = medals.get(pos, f"`#{pos}`")
            lines.append(
                f"{badge} **{name}** — {row['contribution']:,} pts "
                f"· {_rank_title(pos)}"
            )

        embed = h.embed("🤝 Top Contributors", "\n".join(lines), h.BLUE)
        embed.set_footer(text=f"Page {page}/{pages} · {total} contributors")
        await ctx.reply(embed=embed)

    # ── /coin coop (set co-op reward) ─────────────────────────────────────────────
    @coin.command(
        name="coop",
        description="Set the coins each member earns per confirmed /report.",
    )
    @app_commands.describe(amount="Coins awarded to EACH partner per confirmed co-op")
    @commands.has_permissions(manage_guild=True)
    async def coin_coop(self, ctx: commands.Context, amount: int):
        if amount < 0:
            return await ctx.reply(
                embed=h.err("Amount can't be negative."), ephemeral=True
            )
        if amount > COIN_MAX:
            return await ctx.reply(
                embed=h.err(f"Amount can't exceed {COIN_MAX:,}."), ephemeral=True
            )
        await db.set_econ_config(ctx.guild.id, coop_reward=amount)
        cfg = await self._cfg(ctx.guild.id)
        await ctx.reply(
            embed=h.ok(
                f"Co-op reward set to {self._money(cfg, amount)} per person.\n"
                f"Set to **0** to disable `/report`."
            )
        )

    # ── /coin raid (set raid reward) ──────────────────────────────────────────────
    @coin.command(
        name="raid",
        description="Set the coins each member earns per finished /raid.",
    )
    @app_commands.describe(amount="Coins awarded to EACH participant per finished raid")
    @commands.has_permissions(manage_guild=True)
    async def coin_raid(self, ctx: commands.Context, amount: int):
        if amount < 0:
            return await ctx.reply(
                embed=h.err("Amount can't be negative."), ephemeral=True
            )
        if amount > COIN_MAX:
            return await ctx.reply(
                embed=h.err(f"Amount can't exceed {COIN_MAX:,}."), ephemeral=True
            )
        await db.set_econ_config(ctx.guild.id, raid_reward=amount)
        cfg = await self._cfg(ctx.guild.id)
        await ctx.reply(
            embed=h.ok(
                f"Raid reward set to {self._money(cfg, amount)} per person.\n"
                f"Set to **0** to disable `/raid`."
            )
        )

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
    #  /report  — flat, co-op activity reward
    # ══════════════════════════════════════════════════════════════════════════
    @commands.hybrid_command(
        name="report",
        aliases=["coop"],
        description="Report doing a guild activity with someone — both earn coins!",
        extras={
            "category": "🪙 Economy",
            "short": "Reward a co-op activity",
            "usage": "report <member> [activity]",
            "desc": "Tag a partner you did something with (a dungeon, a raid, an event). "
            "They confirm with a button, then you both earn coins and contribution points.",
            "args": [
                "member — who you teamed up with",
                "activity — what you did together (optional)",
            ],
            "perms": "None",
            "example": "{prefix}report @Friend cleared a dungeon",
        },
    )
    @commands.guild_only()
    @app_commands.describe(
        member="Who you teamed up with", activity="What you did together (optional)"
    )
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def report(
        self,
        ctx: commands.Context,
        member: discord.Member,
        *,
        activity: Optional[str] = None,
    ):
        cfg = await self._cfg(ctx.guild.id)
        if cfg["coop_reward"] <= 0:
            return await ctx.reply(
                embed=h.warn(
                    "Co-op rewards are disabled. An admin can enable them with "
                    "`/coin coop <amount>`.",
                    "Disabled",
                ),
                ephemeral=True,
            )
        if member.bot:
            return await ctx.reply(
                embed=h.err("You can't report a co-op with a bot."), ephemeral=True
            )
        if member.id == ctx.author.id:
            return await ctx.reply(
                embed=h.err("Tag the partner you teamed up with, not yourself."),
                ephemeral=True,
            )
        activity = (activity or "").strip()[:200]
        what = f"\n**Activity:** {activity}" if activity else ""
        view = ReportView(self, ctx.author.id, member.id, activity)
        embed = h.embed(
            "🤝 Co-op Report",
            f"{ctx.author.mention} says they teamed up with {member.mention}!{what}\n\n"
            f"{member.mention}, press **Confirm** to award you both "
            f"{self._money(cfg, cfg['coop_reward'])} + contribution points.",
            h.BLUE,
        )
        msg = await ctx.reply(content=member.mention, embed=embed, view=view)
        view.message = msg

    # ══════════════════════════════════════════════════════════════════════════
    #  /raid  — flat, group-co-op join board
    # ══════════════════════════════════════════════════════════════════════════
    @commands.hybrid_command(
        name="raid",
        aliases=["event"],
        description="Start a group co-op anyone can join — the whole party earns coins!",
        extras={
            "category": "🪙 Economy",
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
                    "Raid rewards are disabled. An admin can enable them with "
                    "`/coin raid <amount>`.",
                    "Disabled",
                ),
                ephemeral=True,
            )
        activity = (activity or "").strip()[:200]
        view = RaidView(self, ctx.author.id, activity)
        msg = await ctx.reply(embed=await view._embed(cfg), view=view)
        view.message = msg

    # ══════════════════════════════════════════════════════════════════════════
    #  /shop  group
    # ══════════════════════════════════════════════════════════════════════════
    @commands.hybrid_group(
        name="shop",
        description="Browse and redeem rewards with your coins. See /shop list.",
        invoke_without_command=True,
        extras={
            "category": "🪙 Economy",
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
            desc = item["description"] or "—"
            embed.add_field(
                name=f"`#{item['id']}` {item['name']} — {self._money(cfg, item['price'])}",
                value=f"{desc}\n*{' · '.join(meta)}*",
                inline=False,
            )
        embed.set_footer(text=f"Page {page}/{pages} · buy with /shop buy <id or name>")
        await ctx.reply(embed=embed)

    # ── /shop buy ─────────────────────────────────────────────────────────────────
    @shop.command(name="buy", description="Redeem an item by its id or name.")
    @app_commands.describe(item="Item id (e.g. 3) or its exact name")
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

        item_row = res["item"]
        if record["kind"] == "role":
            try:
                await ctx.author.add_roles(
                    role, reason=f"Shop purchase: {item_row['name']}"
                )
            except discord.HTTPException:
                # Grant failed after charge — refund coins and restore stock.
                await db.add_coins(ctx.guild.id, ctx.author.id, item_row["price"])
                if item_row["stock"] != -1:
                    await db.edit_shop_item(
                        ctx.guild.id, item_row["id"], stock=item_row["stock"]
                    )
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
                f"Added **{name}** (`#{item_id}`) for {self._money(cfg, price)}.",
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
                spec["price"],
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
        item="Item id or name",
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

    # ── /shop remove ──────────────────────────────────────────────────────────────
    @shop.command(name="remove", description="Delete a shop item (Manage Server).")
    @app_commands.describe(item="Item id or name")
    @commands.has_permissions(manage_guild=True)
    async def shop_remove(self, ctx: commands.Context, *, item: str):
        record = await self._resolve_item(ctx.guild.id, item)
        if not record:
            return await ctx.reply(
                embed=h.err(f"No shop item matches `{item}`."), ephemeral=True
            )
        await db.remove_shop_item(ctx.guild.id, record["id"])
        await ctx.reply(embed=h.ok(f"Removed **{record['name']}** from the shop."))

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
    @app_commands.describe(purchase_id="The pending purchase id from /shop pending")
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

    # ── shop helpers ──────────────────────────────────────────────────────────────
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
