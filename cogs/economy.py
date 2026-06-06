"""
cogs/economy.py
Per-guild NanoCoin economy.

Members hold a coin balance, claim a daily reward (with a consecutive-day
streak bonus), and pay each other. Admins grant/take coins, view a rich list,
and customise the currency name, emoji, daily amount, and streak bonus.

Slash command budget: three flat commands (/balance, /daily, /pay) plus one
group (/coin …) whose subcommands cost no extra top-level slots.

──────────────────────────────────────────────────────
Commands
──────────────────────────────────────────────────────
  /balance [member]              → check a balance
  /daily                         → claim the daily reward
  /pay <member> <amount>         → send coins to someone
  /coin top [page]               → richest members
  /coin gamble <amount>          → bet coins to double them (alias: bet)
  /coin grant <member> <amount>  → add coins        (Manage Server)
  /coin take <member> <amount>   → remove coins     (Manage Server)
  /coin reset [member]           → wipe balances    (Manage Server)
  /coin daily <amount>           → set daily reward (Manage Server)
  /coin streakbonus <amount>     → per-day bonus    (Manage Server)
  /coin name <text>              → currency name    (Manage Server)
  /coin emoji <emoji>            → currency emoji   (Manage Server)
  /coin config                   → show settings    (Manage Server)
"""

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

# Gamble odds: win chance under 0.5 gives the "house" a slight edge so coins
# aren't trivially farmed. A win pays the bet back plus (multiplier - 1)x.
GAMBLE_WIN_CHANCE = 0.45
GAMBLE_MULTIPLIER = 2.0


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
        return {"ok": False, "retry_after": int(DAILY_COOLDOWN - elapsed)}
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


# ══════════════════════════════════════════════════════════════════════════════
class Economy(commands.Cog):
    """NanoCoin balances, daily rewards, and transfers — per server."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

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

        embed = h.embed(f"{cfg['currency_emoji']} {member.display_name}", color=h.BLUE)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="Balance", value=self._money(cfg, coins), inline=True)
        embed.add_field(
            name="Rank",
            value=f"**#{rank_pos}**" if rank_pos else "Unranked",
            inline=True,
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
        last_daily, streak = await db.get_daily_state(ctx.guild.id, ctx.author.id)
        res = compute_daily(
            time.time(),
            last_daily,
            streak,
            cfg["daily_amount"],
            cfg["streak_bonus"],
        )
        if not res["ok"]:
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
    async def coin_gamble(self, ctx: commands.Context, amount: int):
        cfg = await self._cfg(ctx.guild.id)
        if amount <= 0:
            return await ctx.reply(embed=h.err("Bet must be positive."), ephemeral=True)
        balance = await db.get_balance(ctx.guild.id, ctx.author.id)
        if balance < amount:
            return await ctx.reply(
                embed=h.err(f"Not enough coins. You have {self._money(cfg, balance)}."),
                ephemeral=True,
            )

        res = resolve_gamble(amount, random.random())
        new_bal = await db.add_coins(ctx.guild.id, ctx.author.id, res["delta"])
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
        await ctx.reply(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(Economy(bot))
