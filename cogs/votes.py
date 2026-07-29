"""
cogs/votes.py — v2.3.0
Bot list integration for top.gg, discordbotlist.com, and discord.bots.gg.

Features:
  - Posts server count to all sites every 12 hours
  - Receives vote webhooks via an aiohttp HTTP server
  - DMs the user when their vote cooldown resets (opt-out with /vote notify off)
  - Vote rewards: coins (streak-scaled), a timed coin boost, a timed luck
    boost, extra reminder slots (50 vs 25), and a milestone chest
  - /vote command — links, status, and streak

discord.bots.gg is stats-only — it has no voting or vote-webhook API, so only
its server-count posting is wired up (no vote link, cooldown, or webhook).

Config keys (config.ini → [votes]):
  topgg_v1_token       — top.gg v1 API token (Bearer, from Integrations & API settings)
  dbl_token            — discordbotlist.com bot token
  discordbotsgg_token  — discord.bots.gg bot token (server-count posting only)
  vote_webhook_port    — port to listen on (default 5000)
  vote_webhook_secret  — shared secret for webhook verification
                         top.gg:             HMAC-SHA256 (x-topgg-signature header)
                         DBL:                plain Authorization header match

Webhook URLs to register on each site:
  top.gg:             http://YOUR_IP:PORT/webhook/topgg
  discordbotlist.com: http://YOUR_IP:PORT/webhook/dbl
"""

import asyncio
import hashlib
import hmac
import json
import logging
import time
from typing import Optional

import aiohttp
import aiohttp.web
import discord
from discord import app_commands
from discord.ext import commands, tasks

from utils import db
from utils import helpers as h
from utils import items as item_catalogue

log = logging.getLogger("NanoBot.votes")

# ── Constants ──────────────────────────────────────────────────────────────────
_TOPGG_API_V1 = "https://top.gg/api/v1"
_DBL_API = "https://discordbotlist.com/api/v1"
_BOTSGG_API = "https://discord.bots.gg/api/v1"

_TOPGG_VOTE = "https://top.gg/bot/{bot_id}/vote"
_DBL_VOTE = "https://discordbotlist.com/bots/{bot_id}/upvote"

# Cooldowns in seconds. discord.bots.gg has no voting, so it isn't listed here.
_COOLDOWNS = {
    "topgg": 12 * 3600,  # 12 hours
    "dbl": 12 * 3600,  # 12 hours
}

# Extra reminders granted to voters
VOTER_REMINDER_MAX = 50
DEFAULT_REMINDER_MAX = 25

# ══════════════════════════════════════════════════════════════════════════════
#  What a vote is actually worth
# ══════════════════════════════════════════════════════════════════════════════
# Voting used to pay in reminder slots, which is a reward for a feature most
# voters don't use — you were being thanked in a currency you had no use for.
# A vote costs a real thirty seconds on someone else's website, so it should
# pay in the things the bot is actually about: coins, and a window where
# everything you do pays better.
#
# All three rewards are **global**, like the wallet they land in, and all three
# are sized against a ~8,000-coin day (see cogs/activities/constants.py): the
# full package is a nice thank-you rather than a way to skip the game. Two
# sites on 12-hour cooldowns cap it at four votes a day (~1,600 coins at a
# maxed streak, under a fifth of a day's play), and the realistic case of one
# or two votes is nearer 8%.
VOTE_COINS = 250

# Votes in a row multiply the coins, capped — the same shape as every other
# streak in the bot, and for the same reason: an uncapped per-day bonus quietly
# becomes the biggest faucet there is.
VOTE_STREAK_BONUS = 0.10  # +10% per consecutive vote
VOTE_STREAK_CAP = 0.6  # ...up to +60%

# The timed buffs, both written into the shared `user_effects` vocabulary
# (docs/economy-design.md) so nothing here needs its own storage:
#   coin_boost — every coin you *earn* is worth more (activities, fishing sales)
#   luck       — better fish, better ore, better odds on a robbery
VOTE_BOOST_HOURS = 6
VOTE_COIN_BOOST = 1.25
VOTE_LUCK = 0.10

# Every Nth vote also drops something to open. A milestone rather than a
# per-vote item, so it stays a small event instead of inventory clutter.
VOTE_MILESTONE_EVERY = 5
VOTE_MILESTONE_ITEM = "treasure_chest"

_SITE_NAMES = {
    "topgg": "top.gg",
    "dbl": "discordbotlist.com",
}


# ── Helpers ────────────────────────────────────────────────────────────────────
def _now() -> float:
    return time.time()


def vote_coins(streak: int, base: int = VOTE_COINS) -> int:
    """Coins for one vote, scaled by the consecutive-vote streak and capped.

    Pure and roll-free — a vote reward has nothing to gamble on, and the point
    of the streak is that it is predictable enough to be worth protecting.
    """
    bonus = min(VOTE_STREAK_CAP, max(0, streak - 1) * VOTE_STREAK_BONUS)
    return max(1, round(base * (1 + bonus)))


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


# Registration key used with the shared HttpServer (utils/webserver.py).
WEBHOOK_OWNER = "votes_webhook"


# ══════════════════════════════════════════════════════════════════════════════
class Votes(commands.Cog):
    """Bot list integrations — stat posting, vote webhooks, rewards."""

    def __init__(self, bot: commands.Bot, cfg: dict):
        self.bot = bot
        self.topgg_v1_token: str | None = cfg.get("topgg_v1_token")
        self.dbl_token: str | None = cfg.get("dbl_token")
        self.botsgg_token: str | None = cfg.get("discordbotsgg_token")
        self.webhook_port: int = int(cfg.get("vote_webhook_port", 5000))
        self.webhook_host: str = str(cfg.get("vote_webhook_host") or "0.0.0.0")
        self.webhook_secret: str | None = cfg.get("vote_webhook_secret")
        self._session: aiohttp.ClientSession | None = None
        self._startup_tasks: list[asyncio.Task] = []

    # ── Lifecycle ──────────────────────────────────────────────────────────────
    async def cog_load(self):
        self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10))
        await self._register_webhook()
        self.post_stats.start()
        self.notify_loop.start()
        # Sync commands to applicable sites once the bot is ready — fire-and-forget
        self._startup_tasks = [
            asyncio.create_task(self._sync_dbl_commands()),
            asyncio.create_task(self._sync_topgg_commands()),
        ]
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
        for task in self._startup_tasks:
            task.cancel()
        self._startup_tasks.clear()
        # Drop our webhook routes from the shared server and rebind so the port
        # reflects the change (no-op if the server isn't running).
        self.bot.web.unregister(WEBHOOK_OWNER)
        if self.bot.web.is_running:
            try:
                await self.bot.web.restart()
            except Exception as exc:
                log.debug("Web server restart on votes unload failed: %s", exc)
        if self._session and not self._session.closed:
            await self._session.close()
        log.info("Votes cog unloaded")

    # ── Webhook HTTP server ────────────────────────────────────────────────────
    @commands.Cog.listener()
    async def on_config_reloaded(self, cfg: dict):
        self.webhook_secret = cfg.get("vote_webhook_secret")
        self.topgg_v1_token = cfg.get("topgg_v1_token")
        self.dbl_token = cfg.get("dbl_token")
        self.botsgg_token = cfg.get("discordbotsgg_token")
        self.webhook_host = str(cfg.get("vote_webhook_host") or "0.0.0.0")
        self.webhook_port = int(cfg.get("vote_webhook_port", 5000))
        # Re-register and rebind so host/port/secret changes apply without a
        # cog reload (shares the port with /health as needed).
        await self._register_webhook()
        log.info("Votes config reloaded")

    async def _register_webhook(self):
        """Register webhook routes with the shared HTTP server (or drop them).

        Refuses to expose an unauthenticated webhook: with no secret anyone
        reaching the port could forge vote payloads. Only binds if the shared
        server is already running (a cog reload) — at first boot the bot
        starts the shared server once after all cogs load.
        """
        if not self.webhook_secret:
            self.bot.web.unregister(WEBHOOK_OWNER)
            log.warning(
                "Vote webhook NOT enabled: no vote_webhook_secret configured. "
                "Set one to enable vote webhooks."
            )
            if self.bot.web.is_running:
                await self.bot.web.restart()
            return

        routes = [
            aiohttp.web.post("/webhook/topgg", self._handle_topgg),
            aiohttp.web.post("/webhook/dbl", self._handle_dbl),
        ]
        self.bot.web.register(
            WEBHOOK_OWNER,
            self.webhook_host,
            self.webhook_port,
            routes,
        )
        log.info(
            f"Vote webhook routes registered for {self.webhook_host}:{self.webhook_port}"
        )
        if self.bot.web.is_running:
            await self.bot.web.restart()

    def _check_auth(self, request: aiohttp.web.Request) -> bool:
        """Validate the Authorization header against the configured secret (DBL)."""
        if not self.webhook_secret:
            return True
        auth = request.headers.get("Authorization", "")
        # Constant-time comparison so the secret can't be recovered byte-by-byte
        # via response-timing measurement.
        return hmac.compare_digest(auth, self.webhook_secret)

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
                expires_at = data["data"].get(  # noqa: F841  ISO8601 — for future use
                    "expires_at"
                )
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
        try:
            user_id = int(data.get("id", 0))
        except (ValueError, TypeError):
            return aiohttp.web.Response(status=400)

        if user_id:
            log.info(f"DBL vote received: user={user_id}")
            await self._process_vote(user_id, "dbl")

        return aiohttp.web.Response(status=200)

    async def _process_vote(self, user_id: int, site: str):
        """Record the vote, pay for it, thank the user by DM, log it."""
        record = await db.record_vote(user_id, site)
        streak = record["streak"]
        site_name = _SITE_NAMES[site]
        reward = await self._grant_vote_rewards(user_id, streak)

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
                        + "\n".join(reward["lines"])
                        + "\n\nI'll ping you when you can vote again. "
                        "Use `/vote notify` to turn that off."
                    ),
                    color=h.GREEN,
                )
                await user.send(embed=e)
            except discord.Forbidden:
                pass  # DMs closed — silently skip
            except Exception as exc:
                log.warning(f"Failed to DM vote thanks to {user_id}: {exc}")

        log.info(
            f"Vote processed: user={user_id} site={site} streak={streak} "
            f"coins={reward['coins']}"
        )

    async def _grant_vote_rewards(self, user_id: int, streak: int) -> dict:
        """Pay for one vote. Returns {"coins": int, "lines": [str]}.

        Separate from the DM on purpose: the rewards must land whether or not
        the member has DMs open, and a thank-you that failed to send is not a
        reason to withhold the coins.
        """
        coins = vote_coins(streak)
        balance = await db.add_coins(user_id, coins)
        await db.grant_effect(
            user_id, "coin_boost", VOTE_COIN_BOOST, duration=VOTE_BOOST_HOURS * 3600
        )
        await db.grant_effect(
            user_id, "luck", VOTE_LUCK, duration=VOTE_BOOST_HOURS * 3600
        )
        lines = [
            f"🪙 **{coins:,}** coins — balance now **{balance:,}**",
            f"📈 **+{VOTE_COIN_BOOST - 1:.0%} coins** from everything you earn, "
            f"for **{VOTE_BOOST_HOURS}h**",
            f"🍀 **+{VOTE_LUCK:.0%} luck** — better fish, better ore, better "
            f"heists — for **{VOTE_BOOST_HOURS}h**",
            f"⏰ **{VOTER_REMINDER_MAX}** reminder slots "
            f"(up from {DEFAULT_REMINDER_MAX})",
        ]
        if streak and streak % VOTE_MILESTONE_EVERY == 0:
            await db.add_item(user_id, VOTE_MILESTONE_ITEM, 1)
            lines.append(
                f"🎁 **{streak}-vote milestone:** "
                f"{item_catalogue.display(VOTE_MILESTONE_ITEM)} — open it with "
                f"`/inventory use`"
            )
        return {"coins": coins, "lines": lines}

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
                else:
                    vote_url = _DBL_VOTE.format(bot_id=self.bot.user.id)

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
            "category": "🗳️ Voting",
            "short": "Vote for NanoBot and see your voting status",
            "usage": "vote [notify [on|off]]",
            "desc": "Shows vote links for top.gg (12h cooldown) and discordbotlist.com (12h cooldown), your current cooldown countdown on each site, and your vote streak.\nEvery vote pays coins, a 6-hour coin boost and a 6-hour luck boost, plus 50 reminder slots instead of 25 — and every 5th vote drops a treasure chest.\nNanoBot will DM you when your cooldown resets. Turn pings off with /vote notify off.",
            "args": [
                (
                    "notify on/off",
                    "Enable or disable cooldown DM pings (omit to view links and status)",
                ),
            ],
            "perms": "None",
            "example": "{prefix}vote\n{prefix}vote notify off",
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
                topgg_on = topgg_row["notify"] if topgg_row else True
                dbl_on = dbl_row["notify"] if dbl_row else True
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
            await db.set_vote_notify(user.id, "dbl", enabled)
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

        topgg_row = await db.get_vote(user.id, "topgg")
        dbl_row = await db.get_vote(user.id, "dbl")

        def _status_line(row: dict | None, site: str) -> str:
            if not row or row["voted_at"] == 0:
                return "✅ Ready to vote!"
            remaining = _cooldown_remaining(row["voted_at"], site)
            if remaining <= 0:
                return "✅ Ready to vote!"
            return f"⏳ Cooldown: **{_fmt_cooldown(remaining)}** left"

        topgg_status = _status_line(topgg_row, "topgg")
        dbl_status = _status_line(dbl_row, "dbl")

        topgg_streak = topgg_row["streak"] if topgg_row and topgg_row["voted_at"] else 0
        dbl_streak = dbl_row["streak"] if dbl_row and dbl_row["voted_at"] else 0

        # Voter status — active on any site
        is_voter = await db.has_voted_recently(
            user.id, "topgg"
        ) or await db.has_voted_recently(user.id, "dbl")

        e = h.embed(title="🗳️ Vote for NanoBot", color=h.BLUE)
        e.description = (
            "Voting helps more people discover NanoBot — it takes about thirty "
            "seconds and you can do it twice a day.\n\u200b"
        )
        e.add_field(
            name="🎁 What you get, every time",
            value=(
                f"🪙 **{VOTE_COINS:,}+** coins (more with a streak, up to "
                f"**{vote_coins(999):,}**)\n"
                f"📈 **+{VOTE_COIN_BOOST - 1:.0%} coins** from everything you "
                f"earn for **{VOTE_BOOST_HOURS}h**\n"
                f"🍀 **+{VOTE_LUCK:.0%} luck** for **{VOTE_BOOST_HOURS}h** — "
                f"better fish, better ore, better heists\n"
                f"⏰ **{VOTER_REMINDER_MAX}** reminder slots instead of "
                f"{DEFAULT_REMINDER_MAX}\n"
                f"🎁 Every **{VOTE_MILESTONE_EVERY}th** vote: a "
                f"{item_catalogue.display(VOTE_MILESTONE_ITEM)}\n\u200b"
            ),
            inline=False,
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
                f"Streak: **{dbl_streak}** vote(s)  ·  Resets every 12h"
            ),
            inline=True,
        )
        e.add_field(
            name="\u200b",
            value=(
                f"**Your status:** {'🟢 Active voter — perks running' if is_voter else '⚪ Not an active voter'}\n"
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
    return VOTER_REMINDER_MAX if (topgg_active or dbl_active) else DEFAULT_REMINDER_MAX


# ── Registration ───────────────────────────────────────────────────────────────
async def setup(bot: commands.Bot):
    # Prefer the already-loaded config attached to the bot by main.py; fall
    # back to a fresh read (e.g. when this cog is reloaded standalone).
    cfg = getattr(bot, "config", None)
    if cfg is None:
        from utils import config as cfg_mod

        cfg = cfg_mod.load()
    await bot.add_cog(Votes(bot, cfg))
