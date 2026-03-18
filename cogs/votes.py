"""
cogs/votes.py — v2.3.0
Bot list integration for top.gg, discordbotlist.com, and discord.bots.gg.

Features:
  - Posts server count to all sites every 12 hours
  - Receives vote webhooks via an aiohttp HTTP server
  - DMs the user when their vote cooldown resets (opt-out with /vote notify off)
  - Extra reminder slots for voters (50 vs 25)
  - /vote command — links, status, and streak

Config keys (config.json):
  topgg_v1_token       — top.gg v1 API token (Bearer, from Integrations & API settings)
  dbl_token            — discordbotlist.com bot token
  discordbotsgg_token  — discord.bots.gg bot token
  vote_webhook_port    — port to listen on (default 5000)
  vote_webhook_secret  — shared secret for webhook verification
                         top.gg:             HMAC-SHA256 (x-topgg-signature header)
                         DBL:                plain Authorization header match
                         discord.bots.gg:    plain Authorization header match

Webhook URLs to register on each site:
  top.gg:             http://YOUR_IP:PORT/webhook/topgg
  discordbotlist.com: http://YOUR_IP:PORT/webhook/dbl
  discord.bots.gg:    http://YOUR_IP:PORT/webhook/botsgg
"""

import asyncio
import hashlib
import hmac
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
_TOPGG_API_V1 = "https://top.gg/api/v1"
_DBL_API = "https://discordbotlist.com/api/v1"
_BOTSGG_API = "https://discord.bots.gg/api/v1"

_TOPGG_VOTE = "https://top.gg/bot/{bot_id}/vote"
_DBL_VOTE = "https://discordbotlist.com/bots/{bot_id}/upvote"
_BOTSGG_VOTE = "https://discord.bots.gg/bots/{bot_id}/vote"

# Cooldowns in seconds
_COOLDOWNS = {
    "topgg": 12 * 3600,  # 12 hours
    "dbl": 24 * 3600,  # 24 hours
    "botsgg": 12 * 3600,  # 12 hours
}

# Extra reminders granted to voters
VOTER_REMINDER_MAX = 50
DEFAULT_REMINDER_MAX = 25

_SITE_NAMES = {
    "topgg": "top.gg",
    "dbl": "discordbotlist.com",
    "botsgg": "discord.bots.gg",
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
        self.bot = bot
        self.topgg_v1_token: str | None = cfg.get("topgg_v1_token")
        self.dbl_token: str | None = cfg.get("dbl_token")
        self.botsgg_token: str | None = cfg.get("discordbotsgg_token")
        self.webhook_port: int = int(cfg.get("vote_webhook_port", 5000))
        self.webhook_secret: str | None = cfg.get("vote_webhook_secret")
        self._http_runner: aiohttp.web.AppRunner | None = None
        self._session: aiohttp.ClientSession | None = None

    # ── Lifecycle ──────────────────────────────────────────────────────────────
    async def cog_load(self):
        self._session = aiohttp.ClientSession()
        await self._start_webhook_server()
        self.post_stats.start()
        self.notify_loop.start()
        # Sync commands to applicable sites once the bot is ready — fire-and-forget
        self.bot.loop.create_task(self._sync_dbl_commands())
        self.bot.loop.create_task(self._sync_topgg_commands())
        log.info("Votes cog loaded — webhook server started, stat loop running")

    async def _fetch_discord_commands(self) -> list | None:
        """Fetch globally synced commands from Discord's API. Returns None on failure."""
        await self.bot.wait_until_ready()
        try:
            cmds = await self.bot.http.get_global_commands(self.bot.user.id)
            if not cmds:
                log.info("Commands sync: no global commands found — skipping")
                return None
            return cmds
        except Exception as exc:
            log.warning(f"Commands sync: failed to fetch from Discord: {exc}")
            return None

    async def _sync_dbl_commands(self):
        """POST the bot's slash commands to discordbotlist.com once on startup."""
        if not self.dbl_token:
            return

        cmds = await self._fetch_discord_commands()
        if not cmds:
            return

        bot_id = self.bot.user.id
        try:
            async with self._session.post(
                f"{_DBL_API}/bots/{bot_id}/commands",
                headers={"Authorization": self.dbl_token},
                json=cmds,
            ) as r:
                if r.status == 200:
                    log.info(f"DBL commands synced: {len(cmds)} command(s) posted")
                else:
                    body = await r.text()
                    log.warning(
                        f"DBL commands sync failed: HTTP {r.status} — {body[:200]}"
                    )
        except Exception as exc:
            log.warning(f"DBL commands sync error: {exc}")

    async def _sync_topgg_commands(self):
        """POST the bot's slash commands to top.gg using the v1 API."""
        if not self.topgg_v1_token:
            return

        cmds = await self._fetch_discord_commands()
        if not cmds:
            return

        # top.gg v1 API — endpoint: POST /api/v1/projects/@me/commands
        # Requires: Authorization: Bearer <v1_token>
        try:
            async with self._session.post(
                f"{_TOPGG_API_V1}/projects/@me/commands",
                headers={"Authorization": f"Bearer {self.topgg_v1_token}"},
                json=cmds,
            ) as r:
                if r.status in (200, 204):
                    log.info(f"top.gg commands synced: {len(cmds)} command(s) posted")
                else:
                    body = await r.text()
                    log.warning(
                        f"top.gg commands sync failed: HTTP {r.status} — {body[:200]}"
                    )
        except Exception as exc:
            log.warning(f"top.gg commands sync error: {exc}")

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
        app.router.add_post("/webhook/dbl", self._handle_dbl)
        app.router.add_post("/webhook/botsgg", self._handle_botsgg)

        runner = aiohttp.web.AppRunner(app)
        await runner.setup()
        site = aiohttp.web.TCPSite(runner, "0.0.0.0", self.webhook_port)
        await site.start()
        self._http_runner = runner
        log.info(f"Vote webhook server listening on :{self.webhook_port}")

    def _check_auth(self, request: aiohttp.web.Request) -> bool:
        """Validate the Authorization header against the configured secret (DBL / discord.bots.gg)."""
        if not self.webhook_secret:
            return True
        auth = request.headers.get("Authorization", "")
        return auth == self.webhook_secret

    def _verify_topgg_signature(self, raw_body: bytes, sig_header: str) -> bool:
        """Verify top.gg v1 HMAC-SHA256 signature.

        Header format: t={unix_timestamp},v1={hmac_sha256_hex}
        Message:       {timestamp}.{raw_body}
        """
        if not self.webhook_secret:
            return True  # No secret configured — accept all (not recommended for prod)

        try:
            parts = dict(part.split("=", 1) for part in sig_header.split(","))
            timestamp = parts["t"]
            expected = parts["v1"]
        except (KeyError, ValueError):
            return False

        message = f"{timestamp}.".encode() + raw_body
        computed = hmac.new(
            self.webhook_secret.encode(),
            message,
            hashlib.sha256,
        ).hexdigest()

        return hmac.compare_digest(computed, expected)

    async def _handle_topgg(self, request: aiohttp.web.Request) -> aiohttp.web.Response:
        raw_body = await request.read()

        sig_header = request.headers.get("x-topgg-signature", "")
        if not self._verify_topgg_signature(raw_body, sig_header):
            log.warning("top.gg webhook: rejected — signature verification failed")
            return aiohttp.web.Response(status=401)

        try:
            data = json.loads(raw_body)
        except Exception:
            return aiohttp.web.Response(status=400)

        event_type = data.get("type")

        # top.gg v1 payload: {"type": "vote.create"|"webhook.test", "data": {...}}
        if event_type == "webhook.test":
            log.info("top.gg test webhook received — not recording vote")
            return aiohttp.web.Response(status=200)

        if event_type == "vote.create":
            try:
                user_id = int(data["data"]["user"]["platform_id"])
                expires_at = data["data"].get("expires_at")  # ISO8601 — for future use
            except (KeyError, ValueError, TypeError):
                log.warning("top.gg vote.create: malformed payload")
                return aiohttp.web.Response(status=400)

            log.info(f"top.gg vote received: user={user_id}")
            await self._process_vote(user_id, "topgg")

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

    async def _handle_botsgg(
        self, request: aiohttp.web.Request
    ) -> aiohttp.web.Response:
        if not self._check_auth(request):
            log.warning("discord.bots.gg webhook: rejected — bad Authorization header")
            return aiohttp.web.Response(status=401)

        try:
            data = await request.json()
        except Exception:
            return aiohttp.web.Response(status=400)

        # discord.bots.gg payload: {"userId": "...", "botId": "...", "type": "vote"}
        user_id = int(data.get("userId", 0))

        if user_id:
            log.info(f"discord.bots.gg vote received: user={user_id}")
            await self._process_vote(user_id, "botsgg")

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
    @tasks.loop(minutes=720)
    async def post_stats(self):
        await self.bot.wait_until_ready()
        guild_count = len(self.bot.guilds)
        bot_id = self.bot.user.id

        if self.topgg_v1_token:
            try:
                async with self._session.post(
                    f"https://top.gg/api/bots/{bot_id}/stats",
                    headers={"Authorization": self.topgg_v1_token},
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

        if self.botsgg_token:
            try:
                async with self._session.post(
                    f"{_BOTSGG_API}/bots/{bot_id}/stats",
                    headers={"Authorization": self.botsgg_token},
                    json={"guildCount": guild_count},
                ) as r:
                    if r.status == 200:
                        log.info(f"discord.bots.gg stats posted: {guild_count} servers")
                    else:
                        log.warning(
                            f"discord.bots.gg stats post failed: HTTP {r.status}"
                        )
            except Exception as exc:
                log.warning(f"discord.bots.gg stats post error: {exc}")

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
            site = record["site"]
            cooldown = _COOLDOWNS.get(site)
            if cooldown is None:
                continue
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
                if site == "topgg":
                    vote_url = _TOPGG_VOTE.format(bot_id=self.bot.user.id)
                elif site == "dbl":
                    vote_url = _DBL_VOTE.format(bot_id=self.bot.user.id)
                else:
                    vote_url = _BOTSGG_VOTE.format(bot_id=self.bot.user.id)

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
    extras={
        'category': '🗳️ Voting',
        'short': 'Vote for NanoBot and see your voting status',
        'usage': 'vote [notify [on|off]]',
        'desc': 'Shows vote links for top.gg (12h cooldown) and discordbotlist.com (24h cooldown), your current cooldown countdown on each site, and your vote streak.\nVoter perk: active voters get 50 reminder slots instead of 25.\nNanoBot will DM you when your cooldown resets. Turn pings off with /vote notify off.',
        'args': [
            ('notify on/off', 'Enable or disable cooldown DM pings (omit to view links and status)'),
        ],
        'perms': 'None',
        'example': '!vote\n!vote notify off',
    },
    )
    @app_commands.describe(action="Optional: 'notify' to toggle cooldown pings")
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def vote(self, ctx: commands.Context, action: Optional[str] = None):
        """
        /vote              — show voting links and your current status
        /vote notify       — show notification setting
        /vote notify on    — enable cooldown pings (default)
        /vote notify off   — disable cooldown pings
        """
        bot_id = self.bot.user.id
        user = ctx.author

        # ── notify subcommand ──────────────────────────────────────────────────
        if action and action.lower().startswith("notify"):
            parts = action.lower().split()
            # "notify" alone → show current state
            if len(parts) == 1:
                topgg_row = await db.get_vote(user.id, "topgg")
                dbl_row = await db.get_vote(user.id, "dbl")
                botsgg_row = await db.get_vote(user.id, "botsgg")
                topgg_on = topgg_row["notify"] if topgg_row else True
                dbl_on = dbl_row["notify"] if dbl_row else True
                botsgg_on = botsgg_row["notify"] if botsgg_row else True
                await ctx.reply(
                    embed=h.info(
                        f"**top.gg cooldown pings:** {'on ✅' if topgg_on else 'off ❌'}\n"
                        f"**DBL cooldown pings:** {'on ✅' if dbl_on else 'off ❌'}\n"
                        f"**discord.bots.gg cooldown pings:** {'on ✅' if botsgg_on else 'off ❌'}\n\n"
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
            await db.set_vote_notify(user.id, "dbl", enabled)
            await db.set_vote_notify(user.id, "botsgg", enabled)
            status = "on ✅" if enabled else "off ❌"
            await ctx.reply(
                embed=h.ok(
                    f"Vote cooldown pings turned **{status}** for all sites.",
                    "🔔 Notifications Updated",
                ),
                ephemeral=True,
            )
            return

        if action:
            await ctx.reply(
                embed=h.err(
                    f"Unknown option `{action}`. Try `/vote` or `/vote notify`."
                ),
                ephemeral=True,
            )
            return

        # ── main /vote embed ───────────────────────────────────────────────────
        topgg_url = _TOPGG_VOTE.format(bot_id=bot_id)
        dbl_url = _DBL_VOTE.format(bot_id=bot_id)
        botsgg_url = _BOTSGG_VOTE.format(bot_id=bot_id)

        topgg_row = await db.get_vote(user.id, "topgg")
        dbl_row = await db.get_vote(user.id, "dbl")
        botsgg_row = await db.get_vote(user.id, "botsgg")

        def _status_line(row: dict | None, site: str) -> str:
            if not row or row["voted_at"] == 0:
                return "✅ Ready to vote!"
            remaining = _cooldown_remaining(row["voted_at"], site)
            if remaining <= 0:
                return "✅ Ready to vote!"
            return f"⏳ Cooldown: **{_fmt_cooldown(remaining)}** left"

        topgg_status = _status_line(topgg_row, "topgg")
        dbl_status = _status_line(dbl_row, "dbl")
        botsgg_status = _status_line(botsgg_row, "botsgg")

        topgg_streak = topgg_row["streak"] if topgg_row and topgg_row["voted_at"] else 0
        dbl_streak = dbl_row["streak"] if dbl_row and dbl_row["voted_at"] else 0
        botsgg_streak = (
            botsgg_row["streak"] if botsgg_row and botsgg_row["voted_at"] else 0
        )

        # Voter status — active on any site
        is_voter = (
            await db.has_voted_recently(user.id, "topgg")
            or await db.has_voted_recently(user.id, "dbl")
            or await db.has_voted_recently(user.id, "botsgg")
        )

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
            name="🏆 discord.bots.gg",
            value=(
                f"[**Vote →**]({botsgg_url})\n"
                f"{botsgg_status}\n"
                f"Streak: **{botsgg_streak}** vote(s)  ·  Resets every 12h"
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
    Voters (on any site) get VOTER_REMINDER_MAX. Everyone else gets DEFAULT_REMINDER_MAX.
    """
    topgg_active = await db.has_voted_recently(user_id, "topgg")
    dbl_active = await db.has_voted_recently(user_id, "dbl")
    botsgg_active = await db.has_voted_recently(user_id, "botsgg")
    return (
        VOTER_REMINDER_MAX
        if (topgg_active or dbl_active or botsgg_active)
        else DEFAULT_REMINDER_MAX
    )


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
