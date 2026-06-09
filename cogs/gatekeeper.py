"""
cogs/gatekeeper.py
New-account gatekeeper: role-based muting + math-captcha verification.

On member join the bot mutes (via a "Muted (NanoBot)" role) accounts that look
risky:
  • account younger than a configurable threshold (default 30 days)
  • no profile picture (Discord's auto-assigned logo avatar)
  • a pickable "stock" avatar matched against a shared, system-wide image catalog

How the age and avatar checks combine is per-guild (`match_mode`): "or" mutes on
either signal (original behaviour), "and" mutes only when an account is both too
young AND has a bad avatar. The stock-avatar catalog itself is global — every
guild reads the same `assets/`+`data/gatekeeper_avatars/` images and any guild's
`/gatekeeper learnavatar` contributes to it, so quiet servers benefit from busy
ones.

Muted members are sent a verification message (DM first, falling back to a
quarantine channel) with a button. Pressing it opens a modal with a simple
addition problem; a correct answer removes the mute role immediately. Members
who don't verify within a configurable window (default 7 days) are kicked.
Account-age mutes also auto-lift once the account is old enough (default 35
days / 5 weeks) even without verifying.

All schedules (auto-unmute + auto-kick) persist in SQLite and are restored on
restart via the on_restore_schedules event, mirroring cogs/moderation/.

The mute role itself only restricts members if it denies sending in every
channel. `/gatekeeper setup` creates the role and applies those overwrites.
Note: place the role high in the list (just below NanoBot's own role) so the
bot can assign it — the bot cannot manage a role above its own.
"""

import asyncio
import io
import ipaddress
import logging
import os
import random
import socket
from typing import Optional
from urllib.parse import urlparse

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

from utils import db
from utils import helpers as h

log = logging.getLogger("NanoBot.gatekeeper")

try:
    from PIL import Image

    _PILLOW_OK = True
except ImportError:  # pragma: no cover
    _PILLOW_OK = False
    log.warning("Pillow not installed — stock-avatar detection disabled.")

# Reference images for stock-avatar detection. Two folders are scanned:
#   • assets/gatekeeper_avatars/ — bundled seeds shipped with the repo (read-only)
#   • data/gatekeeper_avatars/   — runtime additions from /gatekeeper learnavatar
# Each file's perceptual hash is matched against joining members.
BUNDLED_AVATAR_DIR = os.path.join("assets", "gatekeeper_avatars")
AVATAR_CATALOG_DIR = os.path.join("data", "gatekeeper_avatars")

# Perceptual (difference) hash size: 8x8 comparison grid → 64-bit hash.
# A joining avatar within this Hamming distance of any catalog hash is a match.
# Stock avatars are visually distinct, so a small distance avoids false hits.
_DHASH_THRESHOLD = 8

MUTE_ROLE_NAME = "Muted (NanoBot)"

_VERIFY_BUTTON_CID = "gk:verify:button"

DEFAULT_VERIFY_MESSAGE = (
    "You've been temporarily muted in **{server}** while we check new accounts.\n\n"
    "To get unmuted, press the **Verify** button below and solve the quick math "
    "problem. If you don't verify in time you'll be removed from the server."
)


def _is_safe_public_url(url: str) -> bool:
    """True only for an http(s) URL whose host resolves entirely to public IPs.

    Blocks SSRF via /gatekeeper learnavatar: a Manage-Server user could otherwise
    point the fetch at loopback, link-local (cloud metadata at 169.254.169.254),
    or private-range addresses to probe the host's internal network. Every
    resolved address must be global, so a hostname that maps to a private IP is
    rejected too.
    """
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return False
    try:
        infos = socket.getaddrinfo(parsed.hostname, parsed.port or 0)
    except (socket.gaierror, UnicodeError, ValueError):
        return False
    if not infos:
        return False
    for info in infos:
        addr = info[4][0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            return False
        if not ip.is_global or ip.is_reserved:
            return False
    return True


# ══════════════════════════════════════════════════════════════════════════════
#  Perceptual hashing (pure Pillow — no extra dependency)
# ══════════════════════════════════════════════════════════════════════════════


def _dhash(image_bytes: bytes) -> Optional[int]:
    """Return a 64-bit difference hash of an image, or None on failure."""
    if not _PILLOW_OK:
        return None
    try:
        im = (
            Image.open(io.BytesIO(image_bytes))
            .convert("L")
            .resize((9, 8), Image.LANCZOS)
        )
    except Exception as exc:  # pragma: no cover - defensive
        log.debug(f"dhash decode failed: {exc}")
        return None
    px = im.tobytes()  # 9 wide * 8 tall, row-major, one byte per pixel ("L")
    bits = 0
    for row in range(8):
        base = row * 9
        for col in range(8):
            bits = (bits << 1) | (1 if px[base + col] > px[base + col + 1] else 0)
    return bits


def _hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


# ══════════════════════════════════════════════════════════════════════════════
#  Verification UI (persistent button → math captcha modal)
# ══════════════════════════════════════════════════════════════════════════════


class VerifyModal(discord.ui.Modal):
    """Modal that poses a random addition problem."""

    def __init__(self, cog: "Gatekeeper", a: int, b: int):
        super().__init__(title="Verify you're human")
        self.cog = cog
        self.answer = a + b
        self.response = discord.ui.TextInput(
            label=f"What is {a} + {b}?",
            placeholder="Type the number…",
            max_length=4,
            required=True,
        )
        self.add_item(self.response)

    async def on_submit(self, interaction: discord.Interaction):
        raw = self.response.value.strip()
        try:
            given = int(raw)
        except ValueError:
            given = None
        if given != self.answer:
            await interaction.response.send_message(
                embed=h.err(
                    "That's not the right answer. Press **Verify** to try again.",
                    "❌ Incorrect",
                ),
                ephemeral=True,
            )
            return
        await self.cog.complete_verification(interaction)


class VerifyView(discord.ui.View):
    """Persistent view holding the single Verify button."""

    def __init__(self, cog: "Gatekeeper"):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(
        label="Verify",
        style=discord.ButtonStyle.success,
        emoji="✅",
        custom_id=_VERIFY_BUTTON_CID,
    )
    async def verify(
        self, interaction: discord.Interaction, _button: discord.ui.Button
    ):
        if interaction.guild is None:
            # Button pressed in a DM — resolve the pending guild from the DB.
            pending = await self.cog._find_pending_for_user(interaction.user.id)
            if pending is None:
                await interaction.response.send_message(
                    embed=h.info("You have nothing to verify right now.", "✅ All Set"),
                    ephemeral=True,
                )
                return
        else:
            key = f"{interaction.guild.id}:{interaction.user.id}"
            if await db.get_gatekeeper_pending(key) is None:
                await interaction.response.send_message(
                    embed=h.info("You're already verified here.", "✅ All Set"),
                    ephemeral=True,
                )
                return
        a, b = random.randint(2, 9), random.randint(2, 9)
        await interaction.response.send_modal(VerifyModal(self.cog, a, b))


# ══════════════════════════════════════════════════════════════════════════════
class Gatekeeper(commands.Cog):
    """New-account muting and captcha verification."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._session: Optional[aiohttp.ClientSession] = None
        self._unmute_tasks: dict[str, asyncio.Task] = {}
        self._kick_tasks: dict[str, asyncio.Task] = {}
        self._catalog: list[int] = []

    async def cog_load(self):
        self._session = aiohttp.ClientSession()
        self.reload_catalog()
        self.bot.add_view(VerifyView(self))

    def cog_unload(self):
        for task in list(self._unmute_tasks.values()):
            task.cancel()
        for task in list(self._kick_tasks.values()):
            task.cancel()
        self._unmute_tasks.clear()
        self._kick_tasks.clear()
        if self._session and not self._session.closed:
            # Schedule the close; cog_unload is sync.
            asyncio.create_task(self._session.close())

    # ── Avatar catalog ──────────────────────────────────────────────────────────
    def reload_catalog(self) -> int:
        """Recompute perceptual hashes for every image in the catalog folder."""
        self._catalog = []
        if not _PILLOW_OK:
            return 0
        os.makedirs(AVATAR_CATALOG_DIR, exist_ok=True)
        for folder in (BUNDLED_AVATAR_DIR, AVATAR_CATALOG_DIR):
            if not os.path.isdir(folder):
                continue
            for name in os.listdir(folder):
                path = os.path.join(folder, name)
                if not os.path.isfile(path):
                    continue
                try:
                    with open(path, "rb") as fh:
                        digest = _dhash(fh.read())
                except OSError:
                    continue
                if digest is not None:
                    self._catalog.append(digest)
        log.info(f"Gatekeeper avatar catalog: {len(self._catalog)} reference image(s)")
        return len(self._catalog)

    async def _fetch_avatar_bytes(self, asset: discord.Asset) -> Optional[bytes]:
        """Download an avatar at a fixed size/format for stable hashing."""
        if self._session is None:
            return None
        url = asset.replace(format="png", size=128).url
        try:
            async with self._session.get(url, timeout=aiohttp.ClientTimeout(10)) as r:
                if r.status != 200:
                    return None
                return await r.read()
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            log.debug(f"avatar fetch failed: {exc}")
            return None

    async def _matches_stock_avatar(
        self, member: discord.Member, threshold: int = _DHASH_THRESHOLD
    ) -> bool:
        """True if the member's custom avatar matches a catalog reference image.

        `threshold` is the max perceptual-hash Hamming distance counted as a
        match — higher is looser (catches recolours/near-variants of a known
        avatar) at the cost of more false positives.
        """
        if not self._catalog or member.avatar is None:
            return False
        data = await self._fetch_avatar_bytes(member.avatar)
        if data is None:
            return False
        digest = _dhash(data)
        if digest is None:
            return False
        return any(_hamming(digest, ref) <= threshold for ref in self._catalog)

    # ── Logging ───────────────────────────────────────────────────────────────
    async def _log(self, guild: discord.Guild, cfg: dict, embed: discord.Embed):
        chan_id = cfg.get("log_channel_id")
        if not chan_id:
            return
        channel = guild.get_channel(int(chan_id))
        if channel is None:
            return
        try:
            await channel.send(embed=embed)
        except (discord.Forbidden, discord.HTTPException):
            pass

    # ── Schedule restoration ────────────────────────────────────────────────────
    @commands.Cog.listener()
    async def on_restore_schedules(self):
        data = await db.get_all_gatekeeper_pending()
        now = discord.utils.utcnow().timestamp()
        for key, info in data.items():
            guild_id = int(info["guild_id"])
            user_id = int(info["user_id"])
            unmute_at = info["unmute_at"]
            kick_at = info["kick_at"]

            if unmute_at is not None:
                remaining = unmute_at - now
                if remaining <= 0:
                    # Overdue: lift now. This clears the row and any pending kick,
                    # so don't arm a kick for this member.
                    await self._auto_unmute(guild_id, user_id, 0, key)
                    continue
                self._unmute_tasks[key] = asyncio.create_task(
                    self._auto_unmute(guild_id, user_id, remaining, key)
                )
            # Arm the kick whenever one is scheduled. Both timers may run at once
            # (e.g. an age mute with verification on); whichever fires first
            # cancels the other.
            if kick_at is not None:
                self._kick_tasks[key] = asyncio.create_task(
                    self._auto_kick(guild_id, user_id, max(0, kick_at - now), key)
                )

    # ── Scheduling helpers ──────────────────────────────────────────────────────
    @staticmethod
    async def _chunked_sleep(seconds: float) -> None:
        remaining = seconds
        while remaining > 0:
            await asyncio.sleep(min(remaining, 3600))
            remaining -= 3600

    def _cancel_tasks(self, key: str) -> None:
        for table in (self._unmute_tasks, self._kick_tasks):
            task = table.pop(key, None)
            if task:
                task.cancel()

    async def _auto_unmute(self, guild_id: int, user_id: int, delay: float, key: str):
        await self._chunked_sleep(delay)
        guild = self.bot.get_guild(guild_id)
        if guild:
            cfg = await db.get_gatekeeper_config(guild_id)
            member = guild.get_member(user_id)
            role = (
                guild.get_role(int(cfg["mute_role_id"]))
                if cfg["mute_role_id"]
                else None
            )
            if member and role and role in member.roles:
                try:
                    await member.remove_roles(
                        role, reason="Gatekeeper: account aged out"
                    )
                    log.info(f"Gatekeeper auto-unmute: {h.user_log(member)} in {guild}")
                    await self._log(
                        guild,
                        cfg,
                        h.mod_action_embed(
                            "🔊 Gatekeeper Unmute",
                            member,
                            "Account aged past the threshold — auto-unmuted.",
                            color=h.GREEN,
                        ),
                    )
                except (discord.Forbidden, discord.HTTPException) as exc:
                    log.warning(f"Gatekeeper auto-unmute failed for {user_id}: {exc}")
        # Aging out clears the whole hold, including any pending kick.
        kick = self._kick_tasks.pop(key, None)
        if kick:
            kick.cancel()
        self._unmute_tasks.pop(key, None)
        await db.remove_gatekeeper_pending(key)

    async def _auto_kick(self, guild_id: int, user_id: int, delay: float, key: str):
        await self._chunked_sleep(delay)
        guild = self.bot.get_guild(guild_id)
        # Re-check the hold still exists — verification may have cleared it.
        if await db.get_gatekeeper_pending(key) is None:
            self._kick_tasks.pop(key, None)
            return
        if guild:
            member = guild.get_member(user_id)
            if member:
                try:
                    try:
                        await member.send(
                            embed=h.warn(
                                f"You were removed from **{guild.name}** for not "
                                "verifying in time. You're welcome to rejoin and verify.",
                                "👋 Removed",
                            )
                        )
                    except (discord.Forbidden, discord.HTTPException):
                        pass
                    await member.kick(reason="Gatekeeper: failed to verify in time")
                    log.info(f"Gatekeeper auto-kick: {h.user_log(member)} in {guild}")
                    cfg = await db.get_gatekeeper_config(guild_id)
                    await self._log(
                        guild,
                        cfg,
                        h.mod_action_embed(
                            "🚪 Gatekeeper Kick",
                            member,
                            "Did not verify in time.",
                        ),
                    )
                except (discord.Forbidden, discord.HTTPException) as exc:
                    log.warning(f"Gatekeeper auto-kick failed for {user_id}: {exc}")
        unmute = self._unmute_tasks.pop(key, None)
        if unmute:
            unmute.cancel()
        self._kick_tasks.pop(key, None)
        await db.remove_gatekeeper_pending(key)

    # ── Verification completion ──────────────────────────────────────────────────
    async def _find_pending_for_user(self, user_id: int) -> Optional[dict]:
        """Find a pending hold for a user across guilds (used for DM verification)."""
        data = await db.get_all_gatekeeper_pending()
        for info in data.values():
            if int(info["user_id"]) == user_id:
                return info
        return None

    async def complete_verification(self, interaction: discord.Interaction):
        """Correct captcha answer: remove the mute role and clear the hold."""
        if interaction.guild is not None:
            guild = interaction.guild
        else:
            pending = await self._find_pending_for_user(interaction.user.id)
            guild = self.bot.get_guild(int(pending["guild_id"])) if pending else None
        if guild is None:
            await interaction.response.send_message(
                embed=h.err("Couldn't resolve the server. Verify from within it."),
                ephemeral=True,
            )
            return

        key = f"{guild.id}:{interaction.user.id}"
        if await db.get_gatekeeper_pending(key) is None:
            await interaction.response.send_message(
                embed=h.info("You're already verified.", "✅ All Set"), ephemeral=True
            )
            return

        cfg = await db.get_gatekeeper_config(guild.id)
        member = guild.get_member(interaction.user.id)
        role = guild.get_role(int(cfg["mute_role_id"])) if cfg["mute_role_id"] else None
        if member and role and role in member.roles:
            try:
                await member.remove_roles(role, reason="Gatekeeper: verified")
            except (discord.Forbidden, discord.HTTPException) as exc:
                log.warning(f"Gatekeeper unmute-on-verify failed: {exc}")

        self._cancel_tasks(key)
        await db.remove_gatekeeper_pending(key)
        log.info(f"Gatekeeper verified: {interaction.user} in {guild}")
        await interaction.response.send_message(
            embed=h.ok(
                f"You're verified and unmuted in **{guild.name}**. Welcome!",
                "✅ Verified",
            ),
            ephemeral=True,
        )
        if member:
            await self._log(
                guild,
                cfg,
                h.mod_action_embed(
                    "✅ Gatekeeper Verify",
                    member,
                    "Passed verification captcha.",
                    color=h.GREEN,
                ),
            )

    # ── Join handling ─────────────────────────────────────────────────────────
    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if member.bot:
            return
        guild = member.guild
        cfg = await db.get_gatekeeper_config(guild.id)
        if not cfg["enabled"]:
            return

        reasons = await self._evaluate(member, cfg)
        if not reasons:
            return

        role = self._resolve_mute_role(guild, cfg)
        if role is None:
            log.warning(
                f"Gatekeeper enabled in {guild} but mute role missing/unusable — "
                "run /gatekeeper setup."
            )
            await self._log(
                guild,
                cfg,
                h.mod_action_embed(
                    "⚠️ Gatekeeper Misconfigured",
                    member,
                    f"Tried to mute ({', '.join(reasons)}) but the mute role is "
                    "missing or above my highest role. Run `/gatekeeper setup` or "
                    "move my role up.",
                    color=h.RED,
                ),
            )
            return

        try:
            await member.add_roles(role, reason=f"Gatekeeper: {', '.join(reasons)}")
        except (discord.Forbidden, discord.HTTPException) as exc:
            log.warning(f"Gatekeeper mute failed for {h.user_log(member)}: {exc}")
            return

        # Auto-unmute whenever account age is one of the mute reasons and the
        # guild leaves age-out on: once the account crosses unmute_age it's no
        # longer "young", so the hold no longer applies. Turn age_unmute off to
        # force every held member to verify instead.
        unmute_at: Optional[float] = None
        if "new account" in reasons and cfg["age_unmute_enabled"]:
            unmute_at = member.created_at.timestamp() + cfg["unmute_age"]
        kick_at: Optional[float] = None
        if cfg["verify_enabled"]:
            kick_at = discord.utils.utcnow().timestamp() + cfg["kick_timeout"]

        key = f"{guild.id}:{member.id}"
        await db.set_gatekeeper_pending(
            key, guild.id, member.id, ", ".join(reasons), unmute_at, kick_at
        )

        now = discord.utils.utcnow().timestamp()
        if unmute_at is not None:
            self._unmute_tasks[key] = asyncio.create_task(
                self._auto_unmute(guild.id, member.id, max(0, unmute_at - now), key)
            )
        if kick_at is not None:
            self._kick_tasks[key] = asyncio.create_task(
                self._auto_kick(guild.id, member.id, max(0, kick_at - now), key)
            )

        log.info(
            f"Gatekeeper muted {h.user_log(member)} in {guild} "
            f"({', '.join(reasons)}); verify={cfg['verify_enabled']}"
        )
        await self._log(
            guild,
            cfg,
            h.mod_action_embed(
                "🔇 Gatekeeper Mute",
                member,
                ", ".join(reasons),
            ),
        )
        if cfg["verify_enabled"]:
            await self._send_verification(member, cfg)

    async def _evaluate(self, member: discord.Member, cfg: dict) -> list[str]:
        """Return the list of reasons this member should be muted (empty = allow).

        Two checks run: account age ("young") and avatar ("no profile picture" or
        a catalogued "default profile picture"). How they combine is per-guild via
        ``match_mode``:
          • "or"  → mute if young OR bad-avatar (original behaviour)
          • "and" → mute only if young AND bad-avatar
        """
        mode = cfg.get("match_mode", "or")

        young = False
        if cfg["mute_new_accounts"]:
            age = (discord.utils.utcnow() - member.created_at).total_seconds()
            young = age < cfg["min_account_age"]

        # In AND mode an old account can never be gated — skip the avatar check
        # (and its network fetch) entirely.
        if mode == "and" and not young:
            return []

        avatar_reason: Optional[str] = None
        if cfg["mute_default_avatar"] and member.avatar is None:
            avatar_reason = "no profile picture"
        elif cfg["mute_stock_avatar"] and member.avatar is not None:
            threshold = cfg.get("stock_threshold", _DHASH_THRESHOLD)
            if await self._matches_stock_avatar(member, threshold):
                avatar_reason = "default profile picture"

        avatar_bad = avatar_reason is not None
        gated = (young and avatar_bad) if mode == "and" else (young or avatar_bad)
        if not gated:
            return []

        reasons: list[str] = []
        if young:
            reasons.append("new account")
        if avatar_reason:
            reasons.append(avatar_reason)
        return reasons

    def _resolve_mute_role(
        self, guild: discord.Guild, cfg: dict
    ) -> Optional[discord.Role]:
        if not cfg["mute_role_id"]:
            return None
        role = guild.get_role(int(cfg["mute_role_id"]))
        if role is None:
            return None
        me = guild.me
        if me is None or role >= me.top_role or not me.guild_permissions.manage_roles:
            return None
        return role

    async def _send_verification(self, member: discord.Member, cfg: dict):
        template = cfg["verify_message"] or DEFAULT_VERIFY_MESSAGE
        text = template.replace("{server}", member.guild.name)
        embed = h.warn(text, "🔒 Verification Required")
        view = VerifyView(self)

        # DM first.
        try:
            await member.send(embed=embed, view=view)
            return
        except (discord.Forbidden, discord.HTTPException):
            pass

        # Fall back to the quarantine channel.
        chan_id = cfg.get("quarantine_channel_id")
        if not chan_id:
            log.debug(
                f"Gatekeeper: couldn't DM {h.user_log(member)} and no quarantine "
                "channel set."
            )
            return
        channel = member.guild.get_channel(int(chan_id))
        if channel is None:
            return
        try:
            await channel.send(content=member.mention, embed=embed, view=view)
        except (discord.Forbidden, discord.HTTPException) as exc:
            log.warning(f"Gatekeeper verification post failed in {channel}: {exc}")

    # ── Member leave → clear hold ────────────────────────────────────────────────
    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        key = f"{member.guild.id}:{member.id}"
        if await db.get_gatekeeper_pending(key) is not None:
            self._cancel_tasks(key)
            await db.remove_gatekeeper_pending(key)

    # ══════════════════════════════════════════════════════════════════════════
    #  /gatekeeper command group
    # ══════════════════════════════════════════════════════════════════════════
    gk = app_commands.Group(
        name="gatekeeper",
        description="Mute and verify suspicious new accounts.",
        default_permissions=discord.Permissions(manage_guild=True),
        guild_only=True,
    )

    @gk.command(name="setup", description="Create the Muted role and lock it down.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def gk_setup(self, interaction: discord.Interaction):
        guild = interaction.guild
        me = guild.me
        if not me.guild_permissions.manage_roles:
            await interaction.response.send_message(
                embed=h.err("I need the **Manage Roles** permission first."),
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True)

        cfg = await db.get_gatekeeper_config(guild.id)
        role = guild.get_role(int(cfg["mute_role_id"])) if cfg["mute_role_id"] else None
        if role is None:
            role = discord.utils.get(guild.roles, name=MUTE_ROLE_NAME)
        if role is None:
            try:
                role = await guild.create_role(
                    name=MUTE_ROLE_NAME,
                    reason="Gatekeeper mute role",
                    permissions=discord.Permissions.none(),
                )
            except discord.Forbidden:
                await interaction.followup.send(
                    embed=h.err("I couldn't create the role — check my permissions."),
                    ephemeral=True,
                )
                return

        # Position the role as high as the bot can (just below its own top role).
        try:
            target_pos = max(1, me.top_role.position - 1)
            await role.edit(position=target_pos, reason="Gatekeeper: raise mute role")
        except (discord.Forbidden, discord.HTTPException):
            pass

        # Deny sending / speaking for the role in every channel.
        denied = 0
        overwrite = discord.PermissionOverwrite(
            send_messages=False,
            send_messages_in_threads=False,
            create_public_threads=False,
            create_private_threads=False,
            add_reactions=False,
            speak=False,
        )
        for channel in guild.channels:
            try:
                await channel.set_permissions(
                    role, overwrite=overwrite, reason="Gatekeeper mute lockdown"
                )
                denied += 1
            except (discord.Forbidden, discord.HTTPException):
                continue

        await db.set_gatekeeper_config(guild.id, mute_role_id=str(role.id))

        note = ""
        if role >= me.top_role:
            note = (
                "\n\n⚠️ The role is **at or above my own** — I won't be able to assign "
                "it. Move my role above it in **Server Settings → Roles**."
            )
        await interaction.followup.send(
            embed=h.ok(
                f"Mute role {role.mention} ready — send/speak denied in **{denied}** "
                f"channel(s).\n\nEnable gatekeeping with `/gatekeeper enable`, and set a "
                "fallback channel with `/gatekeeper channel` for members who have DMs "
                f"closed.{note}",
                "🔧 Gatekeeper Setup",
            ),
            ephemeral=True,
        )

    @gk.command(name="status", description="Show the current gatekeeper settings.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def gk_status(self, interaction: discord.Interaction):
        cfg = await db.get_gatekeeper_config(interaction.guild_id)
        guild = interaction.guild

        def chan(cid):
            if not cid:
                return "_not set_"
            c = guild.get_channel(int(cid))
            return c.mention if c else f"⚠️ Unknown (`{cid}`)"

        role = guild.get_role(int(cfg["mute_role_id"])) if cfg["mute_role_id"] else None
        role_str = role.mention if role else "_not set — run `/gatekeeper setup`_"
        pending = await db.get_all_gatekeeper_pending()
        held = sum(1 for p in pending.values() if p["guild_id"] == str(guild.id))

        lines = [
            f"**Status:** {'🟢 Enabled' if cfg['enabled'] else '🔴 Disabled'}",
            f"**Mute role:** {role_str}",
            f"**Quarantine channel:** {chan(cfg['quarantine_channel_id'])}",
            f"**Log channel:** {chan(cfg['log_channel_id'])}",
            "",
            f"**Match mode:** {'🔗 AND' if cfg.get('match_mode', 'or') == 'and' else '➕ OR'} "
            f"(age {'AND' if cfg.get('match_mode', 'or') == 'and' else 'OR'} avatar)",
            f"**Mute new accounts:** {'✅' if cfg['mute_new_accounts'] else '❌'} "
            f"(younger than {h.fmt_duration(cfg['min_account_age'])})",
            f"**Auto-unmute age:** {'✅' if cfg.get('age_unmute_enabled', True) else '❌'} "
            f"(at {h.fmt_duration(cfg['unmute_age'])})",
            f"**Mute no-avatar (logo):** {'✅' if cfg['mute_default_avatar'] else '❌'}",
            f"**Mute stock avatars:** {'✅' if cfg['mute_stock_avatar'] else '❌'} "
            f"({len(self._catalog)} reference image(s), sensitivity "
            f"{cfg.get('stock_threshold', 8)})",
            f"**Verification:** {'✅' if cfg['verify_enabled'] else '❌'} "
            f"(kick after {h.fmt_duration(cfg['kick_timeout'])})",
            "",
            f"**Members currently held:** {held}",
        ]
        await interaction.response.send_message(
            embed=h.info("\n".join(lines), "🛡️ Gatekeeper Status"), ephemeral=True
        )

    @gk.command(name="enable", description="Enable the gatekeeper.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def gk_enable(self, interaction: discord.Interaction):
        cfg = await db.get_gatekeeper_config(interaction.guild_id)
        if not cfg["mute_role_id"] or not self._resolve_mute_role(
            interaction.guild, cfg
        ):
            await interaction.response.send_message(
                embed=h.err(
                    "No usable mute role. Run `/gatekeeper setup` first (and make sure "
                    "my role sits above it)."
                ),
                ephemeral=True,
            )
            return
        await db.set_gatekeeper_config(interaction.guild_id, enabled=True)
        await interaction.response.send_message(
            embed=h.ok("Gatekeeper is now **enabled**.", "🛡️ Enabled"), ephemeral=True
        )

    @gk.command(name="disable", description="Disable the gatekeeper.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def gk_disable(self, interaction: discord.Interaction):
        await db.set_gatekeeper_config(interaction.guild_id, enabled=False)
        await interaction.response.send_message(
            embed=h.ok(
                "Gatekeeper is now **disabled**. Existing holds remain.", "🛡️ Disabled"
            ),
            ephemeral=True,
        )

    @gk.command(name="role", description="Use an existing role as the mute role.")
    @app_commands.describe(role="The role to assign to muted members")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def gk_role(self, interaction: discord.Interaction, role: discord.Role):
        await db.set_gatekeeper_config(interaction.guild_id, mute_role_id=str(role.id))
        warning = ""
        if role >= interaction.guild.me.top_role:
            warning = "\n\n⚠️ This role is above mine — I won't be able to assign it."
        await interaction.response.send_message(
            embed=h.ok(f"Mute role set to {role.mention}.{warning}", "🔧 Mute Role"),
            ephemeral=True,
        )

    @gk.command(name="channel", description="Set the fallback quarantine channel.")
    @app_commands.describe(channel="Channel for verification when DMs are closed")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def gk_channel(
        self, interaction: discord.Interaction, channel: discord.TextChannel
    ):
        await db.set_gatekeeper_config(
            interaction.guild_id, quarantine_channel_id=str(channel.id)
        )
        await interaction.response.send_message(
            embed=h.ok(f"Quarantine channel set to {channel.mention}.", "🔧 Channel"),
            ephemeral=True,
        )

    @gk.command(name="logchannel", description="Set the gatekeeper log channel.")
    @app_commands.describe(channel="Channel for mute/verify/kick logs")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def gk_logchannel(
        self, interaction: discord.Interaction, channel: discord.TextChannel
    ):
        await db.set_gatekeeper_config(
            interaction.guild_id, log_channel_id=str(channel.id)
        )
        await interaction.response.send_message(
            embed=h.ok(f"Log channel set to {channel.mention}.", "🔧 Log Channel"),
            ephemeral=True,
        )

    @gk.command(name="minage", description="Mute accounts younger than this.")
    @app_commands.describe(
        duration="e.g. 30d, 2w, 1mo-ish — blank to disable age muting"
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def gk_minage(self, interaction: discord.Interaction, duration: str):
        secs = h.parse_duration(duration)
        if secs is None or secs <= 0:
            await interaction.response.send_message(
                embed=h.err("Couldn't parse that duration. Try `30d`, `2w`, `720h`."),
                ephemeral=True,
            )
            return
        await db.set_gatekeeper_config(
            interaction.guild_id, min_account_age=secs, mute_new_accounts=True
        )
        await interaction.response.send_message(
            embed=h.ok(
                f"Now muting accounts younger than **{h.fmt_duration(secs)}**.",
                "🔧 Min Age",
            ),
            ephemeral=True,
        )

    @gk.command(
        name="unmuteage", description="Auto-unmute once accounts reach this age."
    )
    @app_commands.describe(duration="e.g. 35d, 5w")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def gk_unmuteage(self, interaction: discord.Interaction, duration: str):
        secs = h.parse_duration(duration)
        if secs is None or secs <= 0:
            await interaction.response.send_message(
                embed=h.err("Couldn't parse that duration. Try `35d`, `5w`."),
                ephemeral=True,
            )
            return
        await db.set_gatekeeper_config(interaction.guild_id, unmute_age=secs)
        await interaction.response.send_message(
            embed=h.ok(
                f"Account-age mutes auto-lift at **{h.fmt_duration(secs)}** old.",
                "🔧 Unmute Age",
            ),
            ephemeral=True,
        )

    @gk.command(
        name="kicktimeout", description="Kick unverified members after this long."
    )
    @app_commands.describe(duration="e.g. 7d, 48h")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def gk_kicktimeout(self, interaction: discord.Interaction, duration: str):
        secs = h.parse_duration(duration)
        if secs is None or secs <= 0:
            await interaction.response.send_message(
                embed=h.err("Couldn't parse that duration. Try `7d`, `48h`."),
                ephemeral=True,
            )
            return
        await db.set_gatekeeper_config(interaction.guild_id, kick_timeout=secs)
        await interaction.response.send_message(
            embed=h.ok(
                f"Unverified members are kicked after **{h.fmt_duration(secs)}**.",
                "🔧 Kick Timeout",
            ),
            ephemeral=True,
        )

    @gk.command(name="newaccounts", description="Toggle muting by account age.")
    @app_commands.describe(enabled="Mute accounts younger than the min age?")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def gk_newaccounts(self, interaction: discord.Interaction, enabled: bool):
        await db.set_gatekeeper_config(interaction.guild_id, mute_new_accounts=enabled)
        await interaction.response.send_message(
            embed=h.ok(
                f"Account-age muting **{'on' if enabled else 'off'}**.", "🔧 Updated"
            ),
            ephemeral=True,
        )

    @gk.command(name="noavatar", description="Toggle muting members with no avatar.")
    @app_commands.describe(enabled="Mute the Discord logo (no-avatar) accounts?")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def gk_noavatar(self, interaction: discord.Interaction, enabled: bool):
        await db.set_gatekeeper_config(
            interaction.guild_id, mute_default_avatar=enabled
        )
        await interaction.response.send_message(
            embed=h.ok(
                f"No-avatar muting **{'on' if enabled else 'off'}**.", "🔧 Updated"
            ),
            ephemeral=True,
        )

    @gk.command(
        name="stockavatar", description="Toggle muting catalogued stock avatars."
    )
    @app_commands.describe(enabled="Mute members whose avatar matches the catalog?")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def gk_stockavatar(self, interaction: discord.Interaction, enabled: bool):
        await db.set_gatekeeper_config(interaction.guild_id, mute_stock_avatar=enabled)
        extra = ""
        if enabled and not self._catalog:
            extra = (
                "\n\n⚠️ Catalog is empty — add images with `/gatekeeper learnavatar`."
            )
        await interaction.response.send_message(
            embed=h.ok(
                f"Stock-avatar muting **{'on' if enabled else 'off'}**.{extra}",
                "🔧 Updated",
            ),
            ephemeral=True,
        )

    @gk.command(name="verify", description="Toggle captcha verification.")
    @app_commands.describe(enabled="Send a verify prompt and kick non-verifiers?")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def gk_verify(self, interaction: discord.Interaction, enabled: bool):
        await db.set_gatekeeper_config(interaction.guild_id, verify_enabled=enabled)
        await interaction.response.send_message(
            embed=h.ok(f"Verification **{'on' if enabled else 'off'}**.", "🔧 Updated"),
            ephemeral=True,
        )

    @gk.command(name="message", description="Set the verification prompt text.")
    @app_commands.describe(text="Use {server} for the server name. 'default' to reset.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def gk_message(self, interaction: discord.Interaction, text: str):
        value = None if text.strip().lower() == "default" else text.strip()
        await db.set_gatekeeper_config(interaction.guild_id, verify_message=value)
        preview = (value or DEFAULT_VERIFY_MESSAGE).replace(
            "{server}", interaction.guild.name
        )
        await interaction.response.send_message(
            embed=h.ok(f"Verification message updated.\n\n> {preview}", "🔧 Message"),
            ephemeral=True,
        )

    @gk.command(
        name="learnavatar",
        description="Add an avatar to the stock-avatar catalog.",
    )
    @app_commands.describe(
        user="Copy this member's current avatar into the catalog",
        url="…or a direct image URL to add instead",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def gk_learnavatar(
        self,
        interaction: discord.Interaction,
        user: Optional[discord.Member] = None,
        url: Optional[str] = None,
    ):
        if not _PILLOW_OK:
            await interaction.response.send_message(
                embed=h.err("Pillow isn't installed — stock-avatar detection is off."),
                ephemeral=True,
            )
            return
        if user is None and not url:
            await interaction.response.send_message(
                embed=h.err("Give me a **user** or an image **url**."), ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)

        data: Optional[bytes] = None
        label = ""
        if user is not None:
            if user.avatar is None:
                await interaction.followup.send(
                    embed=h.err(
                        f"{user.display_name} has no custom avatar (Discord logo "
                        "default) — toggle `/gatekeeper noavatar` to catch those."
                    ),
                    ephemeral=True,
                )
                return
            data = await self._fetch_avatar_bytes(user.avatar)
            label = f"{user.id}"
        else:
            if not _is_safe_public_url(url):
                await interaction.followup.send(
                    embed=h.err(
                        "That URL isn't allowed. Give a public http(s) image link "
                        "(local, private, and metadata addresses are blocked)."
                    ),
                    ephemeral=True,
                )
                return
            if self._session is None:
                self._session = aiohttp.ClientSession()
            try:
                async with self._session.get(
                    url, timeout=aiohttp.ClientTimeout(10), allow_redirects=False
                ) as r:
                    if r.status == 200:
                        data = await r.read()
            except (aiohttp.ClientError, asyncio.TimeoutError):
                data = None
            label = "url"

        if not data or _dhash(data) is None:
            await interaction.followup.send(
                embed=h.err("Couldn't download or decode that image."), ephemeral=True
            )
            return

        os.makedirs(AVATAR_CATALOG_DIR, exist_ok=True)
        # Filename by perceptual hash so the same image isn't stored twice.
        digest = _dhash(data)
        path = os.path.join(AVATAR_CATALOG_DIR, f"{digest:016x}.png")
        try:
            Image.open(io.BytesIO(data)).convert("RGB").save(path, format="PNG")
        except Exception as exc:
            await interaction.followup.send(
                embed=h.err(f"Couldn't save the image: {exc}"), ephemeral=True
            )
            return

        count = self.reload_catalog()
        await interaction.followup.send(
            embed=h.ok(
                f"Added to the stock-avatar catalog ({label}). "
                f"Catalog now holds **{count}** image(s).",
                "🖼️ Avatar Learned",
            ),
            ephemeral=True,
        )

    @gk.command(
        name="checkavatar",
        description="Test whether a member's avatar matches the catalog.",
    )
    @app_commands.describe(user="The member to test")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def gk_checkavatar(
        self, interaction: discord.Interaction, user: discord.Member
    ):
        await interaction.response.defer(ephemeral=True)
        cfg = await db.get_gatekeeper_config(interaction.guild_id)
        threshold = cfg.get("stock_threshold", _DHASH_THRESHOLD)
        if user.avatar is None:
            verdict = "Has the **Discord logo default** (no custom avatar)."
        elif not self._catalog:
            verdict = (
                "Catalog is empty — add references with `/gatekeeper learnavatar`."
            )
        elif await self._matches_stock_avatar(user, threshold):
            verdict = (
                f"✅ **Matches** a catalogued stock avatar (sensitivity {threshold})."
            )
        else:
            verdict = "❌ No catalog match (looks like a real custom avatar)."
        await interaction.followup.send(
            embed=h.info(f"{h.user_display(user)}\n\n{verdict}", "🖼️ Avatar Check"),
            ephemeral=True,
        )

    @gk.command(
        name="sensitivity",
        description="How close an avatar must match the catalog (0=exact, higher=looser).",
    )
    @app_commands.describe(distance="Max hash distance for a match (0–20, default 8)")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def gk_sensitivity(self, interaction: discord.Interaction, distance: int):
        if not 0 <= distance <= 20:
            await interaction.response.send_message(
                embed=h.err("Pick a distance between **0** and **20**."),
                ephemeral=True,
            )
            return
        await db.set_gatekeeper_config(interaction.guild_id, stock_threshold=distance)
        await interaction.response.send_message(
            embed=h.ok(
                f"Stock-avatar match distance set to **{distance}**.\n"
                "Lower = stricter (near-exact only). Higher = looser (catches "
                "recolours/variants, but more false positives).",
                "🔧 Sensitivity",
            ),
            ephemeral=True,
        )

    @gk.command(
        name="matchmode",
        description="How the account-age and avatar checks combine to mute.",
    )
    @app_commands.describe(mode="AND = young and bad-avatar; OR = either one")
    @app_commands.choices(
        mode=[
            app_commands.Choice(name="AND (young AND bad-avatar)", value="and"),
            app_commands.Choice(name="OR (young OR bad-avatar)", value="or"),
        ]
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def gk_matchmode(
        self, interaction: discord.Interaction, mode: app_commands.Choice[str]
    ):
        await db.set_gatekeeper_config(interaction.guild_id, match_mode=mode.value)
        if mode.value == "and":
            blurb = (
                "Members are muted only when the account is **younger than the min "
                "age** *and* has **no avatar or a stock avatar**."
            )
        else:
            blurb = (
                "Members are muted when the account is **too young** *or* has **no "
                "avatar / a stock avatar** (either one alone triggers a mute)."
            )
        await interaction.response.send_message(
            embed=h.ok(
                f"Match mode set to **{mode.value.upper()}**.\n{blurb}", "🔧 Match Mode"
            ),
            ephemeral=True,
        )

    @gk.command(
        name="ageunmute",
        description="Auto-unmute age-flagged members once they're old enough.",
    )
    @app_commands.describe(
        enabled="On = age out automatically; Off = must verify to get unmuted"
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def gk_ageunmute(self, interaction: discord.Interaction, enabled: bool):
        await db.set_gatekeeper_config(interaction.guild_id, age_unmute_enabled=enabled)
        if enabled:
            blurb = (
                "Members muted for account age will **auto-unmute** once they cross "
                "the unmute age, even without verifying."
            )
        else:
            blurb = (
                "Age-out is **off** — every held member must pass verification to be "
                "unmuted (or they're kicked at the timeout)."
            )
        await interaction.response.send_message(
            embed=h.ok(
                f"Age auto-unmute **{'on' if enabled else 'off'}**.\n{blurb}",
                "🔧 Age Unmute",
            ),
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Gatekeeper(bot))
