"""
cogs/votes.py — v2.2.0
Bot list integration for top.gg and discordbotlist.com.

Features:
  - Posts server count to both sites every 30 minutes
  - Receives vote webhooks via an aiohttp HTTP server
  - DMs the user when their vote cooldown resets (opt-out with /vote notify off)
  - Extra reminder slots for voters (50 vs 25)
  - /vote command — links, status, and streak

Config keys (config.json):
  topgg_token       — top.gg bot token (Authorization header)
  dbl_token         — discordbotlist.com bot token
  vote_webhook_port — port to listen on (default 5000)
  vote_webhook_secret — secret passed in webhook Authorization header
                        (set the same value in each site's webhook settings)

Webhook URLs to register on each site:
  top.gg:            http://YOUR_IP:PORT/webhook/topgg
  discordbotlist.com: http://YOUR_IP:PORT/webhook/dbl

Both sites' webhook payloads are normalised before processing, so the
reward logic is site-agnostic.
"""

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import Optional

import aiohttp
import aiohttp.web
import discord
from discord import app_commands
from discord.ext import commands, tasks

from utils import db
from utils import helpers as h

log = logging.getLogger("NanoBot.votes")

# ── Constants ──────────────────────────────────────────────────────────────────
_TOPGG_API   = "https://top.gg/api"
_DBL_API     = "https://discordbotlist.com/api/v1"

_TOPGG_VOTE  = "https://top.gg/bot/{bot_id}/vote"
_DBL_VOTE    = "https://discordbotlist.com/bots/{bot_id}/upvote"

# Cooldowns in seconds
_COOLDOWNS = {
    "topgg": 12 * 3600,   # 12 hours
    "dbl":   24 * 3600,   # 24 hours
}

# Extra reminders granted to voters
VOTER_REMINDER_MAX = 50
DEFAULT_REMINDER_MAX = 25

_SITE_NAMES = {
    "topgg": "top.gg",
    "dbl":   "discordbotlist.com",
}


# ── Helpers ────────────────────────────────────────────────────────────────────
def _now() -> float:
    return time.time()


def _cooldown_remaining(voted_at: float, site: str) -> float:
    """Seconds until the vote cooldown expires. 0 if already expired."""
    return max(0.0, voted_at + _COOLDOWNS[site] - _now())


def _fmt_cooldown(secs: float) -> str:
    secs = int(secs)
    h_part, m_part = divmod(secs, 3600)
    m_part //= 60
    if h_part and m_part:
        return f"{h_part}h {m_part}m"
    if h_part:
        return f"{h_part}h"
    return f"{m_part}m"


# ══════════════════════════════════════════════════════════════════════════════
class Votes(commands.Cog):
    """Bot list integrations — stat posting, vote webhooks, rewards."""

    def __init__(self, bot: commands.Bot, cfg: dict):
        self.bot             = bot
        self.topgg_token: str | None = cfg.get("topgg_token")
        self.dbl_token:   str | None = cfg.get("dbl_token")
        self.webhook_port: int       = int(cfg.get("vote_webhook_port", 5000))
        self.webhook_secret: str | None = cfg.get("vote_webhook_secret")
        self._http_runner: aiohttp.web.AppRunner | None = None
        self._session: aiohttp.ClientSession | None = None

    # ── Lifecycle ──────────────────────────────────────────────────────────────
    async def cog_load(self):
        self._session = aiohttp.ClientSession()
        await self._start_webhook_server()
        self.post_stats.start()
        self.notify_loop.start()
        # Sync commands to DBL once the bot is ready — fire-and-forget task
        self.bot.loop.create_task(self._sync_dbl_commands())
        log.info("Votes cog loaded — webhook server started, stat loop running")

    async def _sync_dbl_commands(self):
        """POST the bot's slash commands to discordbotlist.com once on startup."""
        if not self.dbl_token:
            return

        await self.bot.wait_until_ready()

        # Fetch the globally synced commands from Discord's own API
        try:
            app_commands_list = await self.bot.http.get_global_commands(self.bot.user.id)
        except Exception as exc:
            log.warning(f"DBL commands sync: failed to fetch commands from Discord: {exc}")
            return

        if not app_commands_list:
            log.info("DBL commands sync: no global commands found — skipping")
            return

        bot_id = self.bot.user.id
        try:
            async with self._session.post(
                f"{_DBL_API}/bots/{bot_id}/commands",
                headers={"Authorization": self.dbl_token},
                json=app_commands_list,
            ) as r:
                if r.status == 200:
                    log.info(f"DBL commands synced: {len(app_commands_list)} command(s) posted")
                else:
                    body = await r.text()
                    log.warning(f"DBL commands sync failed: HTTP {r.status} — {body[:200]}")
        except Exception as exc:
            log.warning(f"DBL commands sync error: {exc}")

    async def cog_unload(self):
        self.post_stats.cancel()
        self.notify_loop.cancel()
        if self._http_runner:
            await self._http_runner.cleanup()
        if self._session and not self._session.closed:
            await self._session.close()
        log.info("Votes cog unloaded")

    # ── Webhook HTTP server ────────────────────────────────────────────────────
    async def _start_webhook_server(self):
        app = aiohttp.web.Application()
        app.router.add_post("/webhook/topgg", self._handle_topgg)
        app.router.add_post("/webhook/dbl",   self._handle_dbl)

        runner = aiohttp.web.AppRunner(app)
        await runner.setup()
        site = aiohttp.web.TCPSite(runner, "0.0.0.0", self.webhook_port)
        await site.start()
        self._http_runner = runner
        log.info(f"Vote webhook server listening on :{self.webhook_port}")

    def _check_auth(self, request: aiohttp.web.Request) -> bool:
        """Validate the Authorization header against the configured secret."""
        if not self.webhook_secret:
            return True   # No secret configured — accept all (not recommended for prod)
        auth = request.headers.get("Authorization", "")
        return auth == self.webhook_secret

    async def _handle_topgg(self, request: aiohttp.web.Request) -> aiohttp.web.Response:
        if not self._check_auth(request):
            log.warning("top.gg webhook: rejected — bad Authorization header")
            return aiohttp.web.Response(status=401)

        try:
            data = await request.json()
        except Exception:
            return aiohttp.web.Response(status=400)

        # top.gg payload: {"user": "userid", "type": "upvote"|"test", ...}
        user_id = int(data.get("user", 0))
        is_test = data.get("type") == "test"

        if user_id:
            log.info(f"top.gg vote received: user={user_id} test={is_test}")
            if not is_test:
                await self._process_vote(user_id, "topgg")
            else:
                log.info("top.gg test webhook — not recording vote")

        return aiohttp.web.Response(status=200)

    async def _handle_dbl(self, request: aiohttp.web.Request) -> aiohttp.web.Response:
        if not self._check_auth(request):
            log.warning("DBL webhook: rejected — bad Authorization header")
            return aiohttp.web.Response(status=401)

        try:
            data = await request.json()
        except Exception:
            return aiohttp.web.Response(status=400)

        # DBL payload: {"id": "userid", "username": "...", ...}
        user_id = int(data.get("id", 0))

        if user_id:
            log.info(f"DBL vote received: user={user_id}")
            await self._process_vote(user_id, "dbl")

        return aiohttp.web.Response(status=200)

    async def _process_vote(self, user_id: int, site: str):
        """Record the vote, thank the user by DM, log it."""
        record = await db.record_vote(user_id, site)
        streak = record["streak"]
        site_name = _SITE_NAMES[site]

        user = self.bot.get_user(user_id)
        if user is None:
            try:
                user = await self.bot.fetch_user(user_id)
            except discord.NotFound:
                pass

        if user:
            streak_line = f"🔥 **{streak}-vote streak!**\n" if streak > 1 else ""
            try:
                e = h.embed(
                    title="🗳️ Thanks for voting!",
                    description=(
                        f"{streak_line}"
                        f"You voted for NanoBot on **{site_name}**.\n\n"
                        f"**Your reward:** {VOTER_REMINDER_MAX} reminder slots "
                        f"(up from {DEFAULT_REMINDER_MAX}) for the next "
                        f"{_fmt_cooldown(_COOLDOWNS[site])}.\n\n"
                        f"I'll ping you when you can vote again. "
                        f"Use `/vote notify` to turn that off."
                    ),
                    color=h.GREEN,
                )
                await user.send(embed=e)
            except discord.Forbidden:
                pass  # DMs closed — silently skip
            except Exception as exc:
                log.warning(f"Failed to DM vote thanks to {user_id}: {exc}")

        log.info(f"Vote processed: user={user_id} site={site} streak={streak}")

    # ── Stat posting loop ──────────────────────────────────────────────────────
    @tasks.loop(minutes=30)
    async def post_stats(self):
        await self.bot.wait_until_ready()
        guild_count = len(self.bot.guilds)
        bot_id = self.bot.user.id

        if self.topgg_token:
            try:
                async with self._session.post(
                    f"{_TOPGG_API}/bots/{bot_id}/stats",
                    headers={"Authorization": self.topgg_token},
                    json={"server_count": guild_count},
                ) as r:
                    if r.status == 200:
                        log.info(f"top.gg stats posted: {guild_count} servers")
                    else:
                        log.warning(f"top.gg stats post failed: HTTP {r.status}")
            except Exception as exc:
                log.warning(f"top.gg stats post error: {exc}")

        if self.dbl_token:
            try:
                async with self._session.post(
                    f"{_DBL_API}/bots/{bot_id}/stats",
                    headers={"Authorization": self.dbl_token},
                    json={"guilds": guild_count},
                ) as r:
                    if r.status == 200:
                        log.info(f"DBL stats posted: {guild_count} servers")
                    else:
                        log.warning(f"DBL stats post failed: HTTP {r.status}")
            except Exception as exc:
                log.warning(f"DBL stats post error: {exc}")

    @post_stats.before_loop
    async def before_post_stats(self):
        await self.bot.wait_until_ready()

    # ── Vote cooldown DM loop ──────────────────────────────────────────────────
    @tasks.loop(minutes=5)
    async def notify_loop(self):
        """Check every 5 minutes for votes whose cooldown just expired and DM the user."""
        await self.bot.wait_until_ready()
        now = _now()
        records = await db.get_all_votes_for_notify()

        for record in records:
            site     = record["site"]
            cooldown = _COOLDOWNS[site]
            voted_at = record["voted_at"]

            # Fire the notification in the 5-minute window after cooldown expires
            elapsed = now - voted_at
            if cooldown <= elapsed <= cooldown + 300:
                user_id = int(record["user_id"])
                user = self.bot.get_user(user_id)
                if user is None:
                    try:
                        user = await self.bot.fetch_user(user_id)
                    except discord.NotFound:
                        continue

                site_name = _SITE_NAMES[site]
                vote_url  = (
                    _TOPGG_VOTE.format(bot_id=self.bot.user.id)
                    if site == "topgg"
                    else _DBL_VOTE.format(bot_id=self.bot.user.id)
                )

                try:
                    e = h.embed(
                        title="🗳️ You can vote again!",
                        description=(
                            f"Your **{site_name}** vote cooldown has reset.\n\n"
                            f"[**Vote now →**]({vote_url})\n\n"
                            f"_Turn off these pings with `/vote notify off`._"
                        ),
                        color=h.BLUE,
                    )
                    await user.send(embed=e)
                    log.info(f"Vote cooldown ping sent: user={user_id} site={site}")
                except discord.Forbidden:
                    pass
                except Exception as exc:
                    log.warning(f"Failed to send cooldown ping to {user_id}: {exc}")

    @notify_loop.before_loop
    async def before_notify_loop(self):
        await self.bot.wait_until_ready()

    # ══════════════════════════════════════════════════════════════════════════
    #  /vote command
    # ══════════════════════════════════════════════════════════════════════════
    @commands.hybrid_command(
        name="vote",
        description="Vote for NanoBot on bot lists and see your voting status.",
    )
    @app_commands.describe(
        action="Optional: 'notify' to toggle cooldown pings"
    )
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def vote(self, ctx: commands.Context, action: Optional[str] = None):
        """
        /vote           — show voting links and your current status
        /vote notify    — show notification setting
        /vote notify on — enable cooldown pings (default)
        /vote notify off — disable cooldown pings
        """
        bot_id = self.bot.user.id
        user   = ctx.author

        # ── notify subcommand ──────────────────────────────────────────────────
        if action and action.lower().startswith("notify"):
            parts = action.lower().split()
            # "notify" alone → show current state
            if len(parts) == 1:
                topgg_row = await db.get_vote(user.id, "topgg")
                dbl_row   = await db.get_vote(user.id, "dbl")
                topgg_on  = topgg_row["notify"] if topgg_row else True
                dbl_on    = dbl_row["notify"]   if dbl_row   else True
                await ctx.reply(
                    embed=h.info(
                        f"**top.gg cooldown pings:** {'on ✅' if topgg_on else 'off ❌'}\n"
                        f"**DBL cooldown pings:** {'on ✅' if dbl_on else 'off ❌'}\n\n"
                        f"Use `/vote notify on` or `/vote notify off` to change.",
                        "🔔 Vote Notifications",
                    ),
                    ephemeral=True,
                )
                return

            setting_str = parts[1] if len(parts) > 1 else ""
            if setting_str not in ("on", "off"):
                await ctx.reply(
                    embed=h.err("Use `/vote notify on` or `/vote notify off`."),
                    ephemeral=True,
                )
                return

            enabled = setting_str == "on"
            await db.set_vote_notify(user.id, "topgg", enabled)
            await db.set_vote_notify(user.id, "dbl",   enabled)
            status = "on ✅" if enabled else "off ❌"
            await ctx.reply(
                embed=h.ok(
                    f"Vote cooldown pings turned **{status}** for both sites.",
                    "🔔 Notifications Updated",
                ),
                ephemeral=True,
            )
            return

        if action:
            await ctx.reply(
                embed=h.err(f"Unknown option `{action}`. Try `/vote` or `/vote notify`."),
                ephemeral=True,
            )
            return

        # ── main /vote embed ───────────────────────────────────────────────────
        topgg_url  = _TOPGG_VOTE.format(bot_id=bot_id)
        dbl_url    = _DBL_VOTE.format(bot_id=bot_id)

        topgg_row  = await db.get_vote(user.id, "topgg")
        dbl_row    = await db.get_vote(user.id, "dbl")

        def _status_line(row: dict | None, site: str) -> str:
            if not row or row["voted_at"] == 0:
                return "✅ Ready to vote!"
            remaining = _cooldown_remaining(row["voted_at"], site)
            if remaining <= 0:
                return "✅ Ready to vote!"
            return f"⏳ Cooldown: **{_fmt_cooldown(remaining)}** left"

        topgg_status = _status_line(topgg_row, "topgg")
        dbl_status   = _status_line(dbl_row,   "dbl")

        topgg_streak = topgg_row["streak"] if topgg_row and topgg_row["voted_at"] else 0
        dbl_streak   = dbl_row["streak"]   if dbl_row   and dbl_row["voted_at"]   else 0

        # Voter status — active on either site
        is_voter = await db.has_voted_recently(user.id, "topgg") or \
                   await db.has_voted_recently(user.id, "dbl")

        e = h.embed(title="🗳️ Vote for NanoBot", color=h.BLUE)
        e.description = (
            "Voting helps more people discover NanoBot.\n"
            "As a thank you, voters get **50 reminder slots** instead of 25.\n\u200b"
        )

        e.add_field(
            name="🏆 top.gg",
            value=(
                f"[**Vote →**]({topgg_url})\n"
                f"{topgg_status}\n"
                f"Streak: **{topgg_streak}** vote(s)  ·  Resets every 12h"
            ),
            inline=True,
        )
        e.add_field(
            name="🏆 discordbotlist.com",
            value=(
                f"[**Vote →**]({dbl_url})\n"
                f"{dbl_status}\n"
                f"Streak: **{dbl_streak}** vote(s)  ·  Resets every 24h"
            ),
            inline=True,
        )
        e.add_field(
            name="\u200b",
            value=(
                f"**Your status:** {'🟢 Active voter — 50 reminder slots!' if is_voter else '⚪ Not an active voter — 25 reminder slots'}\n"
                f"Cooldown pings: use `/vote notify off` to silence them."
            ),
            inline=False,
        )

        await ctx.reply(embed=e, ephemeral=True)


# ── Helper used by reminders.py ────────────────────────────────────────────────
async def get_reminder_limit(user_id: int) -> int:
    """
    Returns the active reminder limit for a user.
    Voters (on either site) get VOTER_REMINDER_MAX. Everyone else gets DEFAULT_REMINDER_MAX.
    """
    topgg_active = await db.has_voted_recently(user_id, "topgg")
    dbl_active   = await db.has_voted_recently(user_id, "dbl")
    return VOTER_REMINDER_MAX if (topgg_active or dbl_active) else DEFAULT_REMINDER_MAX


# ── Registration ───────────────────────────────────────────────────────────────
async def setup(bot: commands.Bot):
    import json, os
    cfg = {}
    if os.path.exists("config.json"):
        with open("config.json", encoding="utf-8") as f:
            try:
                cfg = json.load(f)
            except json.JSONDecodeError:
                pass
    await bot.add_cog(Votes(bot, cfg))
