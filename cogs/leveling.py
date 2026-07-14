"""
cogs/leveling.py
Per-guild message XP and leveling.

Members earn a random amount of XP per message (rate-limited by a cooldown so
chat spam can't farm it). XP maps to a level via a Mee6-style curve. Admins can
configure XP rates, set up role rewards granted on level-up, designate an
announcement channel, and exclude channels from earning XP.

Slash command budget: one flat command (/rank) plus one group (/level …) whose
subcommands cost no extra top-level slots.

──────────────────────────────────────────────────────
Commands
──────────────────────────────────────────────────────
  /rank [member]                 → your (or someone's) level card
  /level top [page]              → server XP leaderboard
  /level set <member> <amount>   → set a member's XP        (Manage Server)
  /level give <member> <amount>  → add XP to a member       (Manage Server)
  /level reset [member]          → wipe XP (one or all)     (Manage Server)
  /level toggle <on|off>         → enable/disable leveling  (Manage Server)
  /level rate <min> <max> [cd]   → XP per message + cooldown(Manage Server)
  /level announce [channel]      → level-up message channel (Manage Server)
  /level reward <add|remove|list> [level] [role]            (Manage Server)
  /level ignore <add|remove|list> [channel]                 (Manage Server)
  /level config                  → show current settings    (Manage Server)
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
from utils.converters import SafeTextChannel

log = logging.getLogger("NanoBot.leveling")

# Per-level coin-reward ceiling. The reward is multiplied by the level reached,
# so this keeps even high-level payouts reasonable and clear of integer limits.
_COIN_REWARD_MAX = 1_000_000


# ══════════════════════════════════════════════════════════════════════════════
#  Pure level math (no Discord deps — covered by tests/test_leveling_helpers.py)
# ══════════════════════════════════════════════════════════════════════════════


def xp_to_advance(level: int) -> int:
    """XP needed to go from `level` to `level + 1` (Mee6 curve)."""
    return 5 * level * level + 50 * level + 100


def xp_for_level(level: int) -> int:
    """Cumulative XP required to reach the start of `level`."""
    return sum(xp_to_advance(n) for n in range(max(0, level)))


def level_for_xp(xp: int) -> int:
    """Highest level fully reached for a given total XP."""
    level = 0
    total = 0
    while True:
        need = xp_to_advance(level)
        if xp < total + need:
            return level
        total += need
        level += 1


def level_progress(xp: int) -> tuple[int, int, int]:
    """Return (level, xp_into_level, xp_span_for_next)."""
    level = level_for_xp(xp)
    base = xp_for_level(level)
    span = xp_to_advance(level)
    return level, xp - base, span


def render_bar(into: int, span: int, width: int = 12) -> str:
    """Render a unicode progress bar for `into`/`span`."""
    if span <= 0:
        filled = width
    else:
        filled = int(width * into / span)
    filled = max(0, min(width, filled))
    return "█" * filled + "░" * (width - filled)


# ══════════════════════════════════════════════════════════════════════════════
class Leveling(commands.Cog):
    """Message-based XP, levels, and role rewards — per server."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # (guild_id, user_id) → monotonic timestamp of last XP grant
        self._cooldowns: dict[tuple[int, int], float] = {}

    # ── XP granting ─────────────────────────────────────────────────────────────
    @commands.Cog.listener("on_message")
    async def _grant_xp(self, message: discord.Message):
        if not message.guild or message.author.bot:
            return
        if not isinstance(message.author, discord.Member):
            return

        cfg = await db.get_level_config(message.guild.id)
        if not cfg["enabled"]:
            return

        ignored = await db.get_level_ignored_channels(message.guild.id)
        if message.channel.id in ignored:
            return

        key = (message.guild.id, message.author.id)
        now = time.monotonic()
        if now - self._cooldowns.get(key, 0.0) < cfg["cooldown"]:
            return
        self._cooldowns[key] = now

        gain = random.randint(cfg["xp_min"], cfg["xp_max"])
        new_xp = await db.add_xp(message.guild.id, message.author.id, gain)
        old_level = level_for_xp(new_xp - gain)
        new_level = level_for_xp(new_xp)
        if new_level > old_level:
            await self._on_level_up(message, message.author, new_level, cfg)

    async def _on_level_up(
        self,
        message: discord.Message,
        member: discord.Member,
        new_level: int,
        cfg: dict,
    ):
        rewards = await db.get_level_rewards(message.guild.id)
        granted = await self._apply_rewards(member, new_level, rewards)

        coins = cfg["coin_reward"] * new_level if cfg["coin_reward"] else 0
        econ = None
        if coins:
            await db.add_coins(message.guild.id, member.id, coins)
            econ = await db.get_econ_config(message.guild.id)

        if not cfg["announce"]:
            return

        channel: Optional[discord.abc.Messageable] = None
        if cfg["announce_channel"]:
            channel = message.guild.get_channel(cfg["announce_channel"])
        if channel is None:
            channel = message.channel

        desc = f"{member.mention} reached **level {new_level}**!"
        if coins and econ:
            desc += (
                f"\nEarned {econ['currency_emoji']} **{coins:,}** "
                f"{econ['currency_name']}{'' if coins == 1 else 's'}"
            )
        if granted:
            roles = ", ".join(r.mention for r in granted)
            desc += f"\nUnlocked: {roles}"
        embed = h.embed("🎉 Level Up", desc, h.GREEN)
        embed.set_thumbnail(url=member.display_avatar.url)
        try:
            await channel.send(embed=embed)
        except discord.Forbidden:
            pass

    async def _apply_rewards(
        self, member: discord.Member, level: int, rewards: dict[int, int]
    ) -> list[discord.Role]:
        """Grant any reward roles at or below `level` the member is missing."""
        guild = member.guild
        me = guild.me
        if not me.guild_permissions.manage_roles:
            return []
        granted: list[discord.Role] = []
        for lvl, role_id in rewards.items():
            if lvl > level:
                continue
            role = guild.get_role(role_id)
            if not role or role in member.roles:
                continue
            if role >= me.top_role or role.managed:
                continue
            try:
                await member.add_roles(role, reason=f"Level {lvl} reward")
                granted.append(role)
            except discord.HTTPException:
                pass
        return granted

    # ══════════════════════════════════════════════════════════════════════════
    #  /rank  — flat, hottest verb
    # ══════════════════════════════════════════════════════════════════════════
    @commands.hybrid_command(
        name="rank",
        aliases=["lvl"],
        description="Show your level, XP, and rank on this server.",
        extras={
            "category": "📈 Leveling",
            "short": "See your level and XP progress",
            "usage": "rank [member]",
            "desc": "Shows level, total XP, progress to the next level, and server rank.",
            "args": ["member — whose card to show (defaults to you)"],
            "perms": "None",
            "example": "{prefix}rank\n{prefix}rank @Friend",
        },
    )
    @commands.guild_only()
    @app_commands.describe(member="Whose rank to show (defaults to you)")
    async def rank(
        self, ctx: commands.Context, member: Optional[discord.Member] = None
    ):
        member = member or ctx.author
        if member.bot:
            return await ctx.reply(embed=h.err("Bots don't earn XP."), ephemeral=True)

        res = await db.get_rank(ctx.guild.id, member.id)
        xp = res[1] if res else 0
        rank_pos = res[0] if res else None
        level, into, span = level_progress(xp)
        bar = render_bar(into, span)

        embed = h.embed(f"📈 {member.display_name}", color=h.BLUE)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="Level", value=f"**{level}**", inline=True)
        embed.add_field(
            name="Rank",
            value=f"**#{rank_pos}**" if rank_pos else "Unranked",
            inline=True,
        )
        embed.add_field(name="Total XP", value=f"**{xp:,}**", inline=True)
        embed.add_field(
            name=f"Progress to level {level + 1}",
            value=f"`{bar}`  {into:,} / {span:,} XP",
            inline=False,
        )
        await ctx.reply(embed=embed)

    # ══════════════════════════════════════════════════════════════════════════
    #  /level  group  (subcommands are free of the top-level slash budget)
    # ══════════════════════════════════════════════════════════════════════════
    @commands.hybrid_group(
        name="level",
        description="Leveling: leaderboard and admin settings. See /level top.",
        invoke_without_command=True,
        extras={
            "category": "📈 Leveling",
            "short": "Leaderboard + leveling admin settings",
            "usage": "level [subcommand]",
            "desc": "View the leaderboard with /level top. Admins configure XP rate, "
            "role rewards, the announcement channel, and ignored channels.",
            "args": [],
            "perms": "Admin subcommands require Manage Server",
            "example": "{prefix}level top\n{prefix}level reward add 5 @Regular",
        },
    )
    @commands.guild_only()
    async def level(self, ctx: commands.Context):
        if ctx.interaction:
            return await self._show_leaderboard(ctx, 1)
        await self._show_leaderboard(ctx, 1)

    # ── /level top ───────────────────────────────────────────────────────────
    @level.command(name="top", description="Show the server XP leaderboard.")
    @app_commands.describe(page="Page number (10 per page)")
    async def level_top(self, ctx: commands.Context, page: int = 1):
        await self._show_leaderboard(ctx, page)

    async def _show_leaderboard(self, ctx: commands.Context, page: int):
        page = max(1, page)
        per = 10
        total = await db.count_ranked(ctx.guild.id)
        if total == 0:
            return await ctx.reply(
                embed=h.info(
                    "No one has earned XP yet. Start chatting!",
                    "📈 Leaderboard",
                )
            )
        pages = (total + per - 1) // per
        page = min(page, pages)
        offset = (page - 1) * per
        rows = await db.get_leaderboard(ctx.guild.id, per, offset)

        medals = {1: "🥇", 2: "🥈", 3: "🥉"}
        lines = []
        for i, row in enumerate(rows):
            pos = offset + i + 1
            member = ctx.guild.get_member(row["user_id"])
            name = member.display_name if member else f"User {row['user_id']}"
            lvl = level_for_xp(row["xp"])
            badge = medals.get(pos, f"`#{pos}`")
            lines.append(f"{badge} **{name}** — level {lvl} ({row['xp']:,} XP)")

        embed = h.embed("📈 XP Leaderboard", "\n".join(lines), h.BLUE)
        embed.set_footer(text=f"Page {page}/{pages} · {total} ranked")
        await ctx.reply(embed=embed)

    # ── /level set ─────────────────────────────────────────────────────────────
    @level.command(name="set", description="Set a member's XP to an exact amount.")
    @app_commands.describe(member="Member to adjust", amount="New XP total (>= 0)")
    @commands.has_permissions(manage_guild=True)
    async def level_set(
        self, ctx: commands.Context, member: discord.Member, amount: int
    ):
        if member.bot:
            return await ctx.reply(embed=h.err("Bots don't earn XP."), ephemeral=True)
        if amount < 0:
            return await ctx.reply(embed=h.err("XP can't be negative."), ephemeral=True)
        await db.set_xp(ctx.guild.id, member.id, amount)
        lvl = level_for_xp(amount)
        await ctx.reply(
            embed=h.ok(
                f"Set **{member.display_name}** to **{amount:,} XP** (level {lvl})."
            )
        )

    # ── /level give ─────────────────────────────────────────────────────────────
    @level.command(name="give", description="Add (or subtract) XP for a member.")
    @app_commands.describe(
        member="Member to adjust", amount="XP to add (negative to remove)"
    )
    @commands.has_permissions(manage_guild=True)
    async def level_give(
        self, ctx: commands.Context, member: discord.Member, amount: int
    ):
        if member.bot:
            return await ctx.reply(embed=h.err("Bots don't earn XP."), ephemeral=True)
        new_xp = await db.add_xp(ctx.guild.id, member.id, amount)
        lvl = level_for_xp(new_xp)
        verb = "Added" if amount >= 0 else "Removed"
        await ctx.reply(
            embed=h.ok(
                f"{verb} **{abs(amount):,} XP** for **{member.display_name}** — "
                f"now **{new_xp:,} XP** (level {lvl})."
            )
        )

    # ── /level reset ─────────────────────────────────────────────────────────────
    @level.command(
        name="reset", description="Reset XP for one member, or the whole server."
    )
    @app_commands.describe(member="Member to reset (omit to reset everyone)")
    @commands.has_permissions(manage_guild=True)
    async def level_reset(
        self, ctx: commands.Context, member: Optional[discord.Member] = None
    ):
        if member:
            await db.reset_levels(ctx.guild.id, member.id)
            self._cooldowns.pop((ctx.guild.id, member.id), None)
            return await ctx.reply(
                embed=h.ok(f"Reset XP for **{member.display_name}**.")
            )
        removed = await db.reset_levels(ctx.guild.id)
        for k in [k for k in self._cooldowns if k[0] == ctx.guild.id]:
            self._cooldowns.pop(k, None)
        await ctx.reply(
            embed=h.ok(f"Reset XP for the whole server ({removed} members cleared).")
        )

    # ── /level toggle ─────────────────────────────────────────────────────────────
    @level.command(name="toggle", description="Turn leveling on or off.")
    @app_commands.describe(state="on or off")
    @commands.has_permissions(manage_guild=True)
    async def level_toggle(self, ctx: commands.Context, state: str):
        s = state.strip().lower()
        if s not in ("on", "off", "enable", "disable", "true", "false"):
            return await ctx.reply(embed=h.err("Use `on` or `off`."), ephemeral=True)
        enabled = s in ("on", "enable", "true")
        await db.set_level_config(ctx.guild.id, enabled=enabled)
        await ctx.reply(
            embed=h.ok(f"Leveling is now **{'on' if enabled else 'off'}**.")
        )

    # ── /level rate ─────────────────────────────────────────────────────────────
    @level.command(
        name="rate", description="Set XP earned per message and the cooldown."
    )
    @app_commands.describe(
        xp_min="Minimum XP per message",
        xp_max="Maximum XP per message",
        cooldown="Seconds between XP grants (default 60)",
    )
    @commands.has_permissions(manage_guild=True)
    async def level_rate(
        self,
        ctx: commands.Context,
        xp_min: int,
        xp_max: int,
        cooldown: Optional[int] = None,
    ):
        if xp_min < 0 or xp_max < 0 or xp_min > xp_max:
            return await ctx.reply(
                embed=h.err("Need `0 <= min <= max`."), ephemeral=True
            )
        kwargs = {"xp_min": xp_min, "xp_max": xp_max}
        if cooldown is not None:
            if cooldown < 0:
                return await ctx.reply(
                    embed=h.err("Cooldown can't be negative."), ephemeral=True
                )
            kwargs["cooldown"] = cooldown
        await db.set_level_config(ctx.guild.id, **kwargs)
        cfg = await db.get_level_config(ctx.guild.id)
        await ctx.reply(
            embed=h.ok(
                f"XP per message: **{cfg['xp_min']}–{cfg['xp_max']}**, "
                f"cooldown **{cfg['cooldown']}s**."
            )
        )

    # ── /level coinreward ─────────────────────────────────────────────────────────
    @level.command(
        name="coinreward",
        description="Coins awarded per level on level-up (amount × new level). 0 = off.",
    )
    @app_commands.describe(amount="Coins per level (multiplied by the new level)")
    @commands.has_permissions(manage_guild=True)
    async def level_coinreward(self, ctx: commands.Context, amount: int):
        if amount < 0:
            return await ctx.reply(
                embed=h.err("Amount can't be negative."), ephemeral=True
            )
        # Reward is multiplied by the new level, so cap the per-level rate to keep
        # high-level payouts sane and well clear of integer limits.
        if amount > _COIN_REWARD_MAX:
            return await ctx.reply(
                embed=h.err(f"Amount can't exceed {_COIN_REWARD_MAX:,} per level."),
                ephemeral=True,
            )
        await db.set_level_config(ctx.guild.id, coin_reward=amount)
        if amount == 0:
            return await ctx.reply(embed=h.ok("Level-up coin rewards turned off."))
        await ctx.reply(
            embed=h.ok(
                f"Level-ups now award **{amount:,}** coins × the new level "
                f"(e.g. level 5 → {amount * 5:,} coins)."
            )
        )

    # ── /level announce ───────────────────────────────────────────────────────────
    @level.command(
        name="announce",
        description="Set the level-up announcement channel (omit to use the current channel).",
    )
    @app_commands.describe(
        channel="Channel for level-up messages (omit to clear / announce in place)"
    )
    @commands.has_permissions(manage_guild=True)
    async def level_announce(
        self,
        ctx: commands.Context,
        channel: Optional[SafeTextChannel] = None,
    ):
        if channel:
            await db.set_level_config(
                ctx.guild.id, announce=True, announce_channel=channel.id
            )
            return await ctx.reply(
                embed=h.ok(f"Level-ups will be announced in {channel.mention}.")
            )
        await db.set_level_config(ctx.guild.id, announce=True, announce_channel=None)
        await ctx.reply(
            embed=h.ok("Level-ups will be announced in the channel where they happen.")
        )

    # ── /level reward ───────────────────────────────────────────────────────────
    @level.command(
        name="reward",
        description="Manage role rewards: reward add <level> <role> | remove <level> | list",
    )
    @app_commands.describe(
        action="add, remove, or list",
        level="Level the reward unlocks at",
        role="Role to grant (for add)",
    )
    @commands.has_permissions(manage_guild=True)
    async def level_reward(
        self,
        ctx: commands.Context,
        action: str,
        level: Optional[int] = None,
        role: Optional[discord.Role] = None,
    ):
        act = action.strip().lower()
        if act == "list":
            rewards = await db.get_level_rewards(ctx.guild.id)
            if not rewards:
                return await ctx.reply(
                    embed=h.info("No role rewards set up.", "🎁 Rewards")
                )
            lines = []
            for lvl, role_id in rewards.items():
                r = ctx.guild.get_role(role_id)
                lines.append(f"Level **{lvl}** → {r.mention if r else f'`{role_id}`'}")
            return await ctx.reply(
                embed=h.embed("🎁 Role Rewards", "\n".join(lines), h.BLUE)
            )

        if act == "add":
            if level is None or level < 1 or role is None:
                return await ctx.reply(
                    embed=h.err("Usage: `reward add <level> <role>` (level >= 1)."),
                    ephemeral=True,
                )
            if role >= ctx.guild.me.top_role:
                return await ctx.reply(
                    embed=h.err(
                        f"I can't assign {role.mention} — it's above my top role."
                    ),
                    ephemeral=True,
                )
            await db.add_level_reward(ctx.guild.id, level, role.id)
            return await ctx.reply(
                embed=h.ok(f"{role.mention} will be granted at **level {level}**.")
            )

        if act == "remove":
            if level is None:
                return await ctx.reply(
                    embed=h.err("Usage: `reward remove <level>`."), ephemeral=True
                )
            removed = await db.remove_level_reward(ctx.guild.id, level)
            if removed:
                return await ctx.reply(
                    embed=h.ok(f"Removed the reward for level **{level}**.")
                )
            return await ctx.reply(
                embed=h.err(f"No reward set for level {level}."), ephemeral=True
            )

        await ctx.reply(
            embed=h.err("Action must be `add`, `remove`, or `list`."), ephemeral=True
        )

    # ── /level ignore ───────────────────────────────────────────────────────────
    @level.command(
        name="ignore",
        description="Manage no-XP channels: ignore add <channel> | remove <channel> | list",
    )
    @app_commands.describe(
        action="add, remove, or list",
        channel="Channel to ignore (for add/remove)",
    )
    @commands.has_permissions(manage_guild=True)
    async def level_ignore(
        self,
        ctx: commands.Context,
        action: str,
        channel: Optional[SafeTextChannel] = None,
    ):
        act = action.strip().lower()
        if act == "list":
            ids = await db.get_level_ignored_channels(ctx.guild.id)
            if not ids:
                return await ctx.reply(
                    embed=h.info("No channels are ignored.", "🚫 Ignored Channels")
                )
            mentions = []
            for cid in ids:
                ch = ctx.guild.get_channel(cid)
                mentions.append(ch.mention if ch else f"`{cid}`")
            return await ctx.reply(
                embed=h.embed("🚫 Ignored Channels", ", ".join(mentions), h.BLUE)
            )

        if channel is None:
            return await ctx.reply(
                embed=h.err("Specify a channel: `ignore add #channel`."),
                ephemeral=True,
            )
        if act == "add":
            await db.add_level_ignored_channel(ctx.guild.id, channel.id)
            return await ctx.reply(
                embed=h.ok(f"{channel.mention} will no longer earn XP.")
            )
        if act == "remove":
            removed = await db.remove_level_ignored_channel(ctx.guild.id, channel.id)
            if removed:
                return await ctx.reply(embed=h.ok(f"{channel.mention} earns XP again."))
            return await ctx.reply(
                embed=h.err(f"{channel.mention} wasn't ignored."), ephemeral=True
            )

        await ctx.reply(
            embed=h.err("Action must be `add`, `remove`, or `list`."), ephemeral=True
        )

    # ── /level config ───────────────────────────────────────────────────────────
    @level.command(name="config", description="Show the current leveling settings.")
    @commands.has_permissions(manage_guild=True)
    async def level_config(self, ctx: commands.Context):
        cfg = await db.get_level_config(ctx.guild.id)
        rewards = await db.get_level_rewards(ctx.guild.id)
        ignored = await db.get_level_ignored_channels(ctx.guild.id)

        announce = "in place"
        if cfg["announce_channel"]:
            ch = ctx.guild.get_channel(cfg["announce_channel"])
            announce = ch.mention if ch else f"`{cfg['announce_channel']}`"
        if not cfg["announce"]:
            announce = "off"

        embed = h.embed("📈 Leveling Settings", color=h.BLUE)
        embed.add_field(
            name="Status",
            value="**On**" if cfg["enabled"] else "**Off**",
            inline=True,
        )
        embed.add_field(
            name="XP / message",
            value=f"{cfg['xp_min']}–{cfg['xp_max']}",
            inline=True,
        )
        embed.add_field(name="Cooldown", value=f"{cfg['cooldown']}s", inline=True)
        embed.add_field(name="Announce", value=announce, inline=True)
        embed.add_field(name="Role rewards", value=str(len(rewards)), inline=True)
        embed.add_field(name="Ignored channels", value=str(len(ignored)), inline=True)
        coin = f"{cfg['coin_reward']:,}/level" if cfg["coin_reward"] else "off"
        embed.add_field(name="Coin reward", value=coin, inline=True)
        await ctx.reply(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(Leveling(bot))
