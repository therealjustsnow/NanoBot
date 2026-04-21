"""
cogs/welcome.py
Per-server welcome and leave messages.

Supports channel messages and DMs, custom embed titles, content,
image URLs, footer text, thumbnail control, embed color, and optional
text-on-image overlays (requires Pillow).

Variables supported everywhere (title, content, footer_text, image_text):
  {user}     — display name
  {mention}  — ping
  {server}   — server name
  {count}    — member count
  {username} — full username (user#0000 style)

Commands:
  welcome        — configure or view welcome settings
  leave          — configure or view leave settings
  testwelcome    — preview the welcome message
  testleave      — preview the leave message
"""

import io
import logging
import os
from typing import Optional

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

from utils import db
from utils import helpers as h
from utils.checks import has_admin_perms

log = logging.getLogger("NanoBot.welcome")

# ---------------------------------------------------------------------------
# Optional Pillow import — image overlay is silently skipped if not installed
# ---------------------------------------------------------------------------
try:
    from PIL import Image, ImageDraw, ImageFont

    _PILLOW_OK = True
except ImportError:  # pragma: no cover
    _PILLOW_OK = False
    log.warning(
        "Pillow not installed — image text overlay will be disabled. "
        "Run: pip install Pillow>=10.0.0"
    )

_VARS_HELP = (
    "`{user}` — display name  ·  `{mention}` — ping  ·  "
    "`{server}` — server name  ·  `{count}` — member count  ·  `{username}` — full username"
)

# Common system font paths tried in order; falls back to PIL built-in.
_FONT_PATHS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    "/System/Library/Fonts/Helvetica.ttc",  # macOS
    "C:/Windows/Fonts/arialbd.ttf",  # Windows
]


# ── Template helpers ──────────────────────────────────────────────────────────


def _fill(template: str, member: discord.Member) -> str:
    return (
        template.replace("{user}", member.display_name)
        .replace("{mention}", member.mention)
        .replace("{server}", member.guild.name)
        .replace("{count}", str(member.guild.member_count or "?"))
        .replace("{username}", str(member))
    )


def _is_valid_hex(color: str) -> bool:
    c = color.lstrip("#")
    if len(c) != 6:
        return False
    try:
        int(c, 16)
        return True
    except ValueError:
        return False


def _parse_color(color_str: str | None) -> int:
    """Return an integer embed color from a hex string, or the default blue."""
    if color_str:
        try:
            return int(color_str.lstrip("#"), 16)
        except ValueError:
            pass
    return h.BLUE


# ── Image overlay helpers ─────────────────────────────────────────────────────


def _load_font(size: int):
    for path in _FONT_PATHS:
        if os.path.isfile(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    # Built-in bitmap font — no size argument in Pillow < 10.1
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _wrap_text(draw: "ImageDraw.ImageDraw", text: str, font, max_width: int) -> str:
    """Word-wrap *text* so each line fits within *max_width* pixels."""
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = (current + " " + word).strip()
        bbox = draw.textbbox((0, 0), candidate, font=font)
        if bbox[2] - bbox[0] <= max_width:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return "\n".join(lines)


async def _make_overlay_image(image_url: str, text: str) -> discord.File | None:
    """
    Download *image_url*, render *text* on it, and return a discord.File.
    Returns None on any error (network, decode, draw) so the caller can
    gracefully fall back to embedding the raw URL.
    """
    if not _PILLOW_OK:
        return None

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                image_url, timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status != 200:
                    log.warning(
                        f"Image overlay: HTTP {resp.status} fetching {image_url}"
                    )
                    return None
                raw = await resp.read()

        img = Image.open(io.BytesIO(raw)).convert("RGBA")
        w, h_px = img.size

        font_size = max(24, h_px // 10)
        font = _load_font(font_size)

        # Measure & wrap text
        probe_draw = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
        wrapped = _wrap_text(probe_draw, text, font, w - 40)

        draw_tmp = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
        bbox = draw_tmp.textbbox((0, 0), wrapped, font=font)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]

        padding = 16
        text_x = (w - text_w) // 2
        text_y = h_px - text_h - padding * 3

        # Semi-transparent dark pill behind the text for readability
        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        ov_draw = ImageDraw.Draw(overlay)
        ov_draw.rounded_rectangle(
            [
                text_x - padding,
                text_y - padding,
                text_x + text_w + padding,
                text_y + text_h + padding,
            ],
            radius=10,
            fill=(0, 0, 0, 160),
        )
        img = Image.alpha_composite(img, overlay)

        final_draw = ImageDraw.Draw(img)
        final_draw.text((text_x, text_y), wrapped, font=font, fill=(255, 255, 255, 255))

        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="PNG")
        buf.seek(0)
        return discord.File(buf, filename="welcome_banner.png")

    except Exception as exc:
        log.warning(f"Image overlay failed for {image_url!r}: {exc}")
        return None


# ── Core delivery ─────────────────────────────────────────────────────────────


async def _send_event(
    bot: commands.Bot,
    member: discord.Member,
    cfg: dict,
    event: str,  # "welcome" | "leave"
):
    """Build and deliver a welcome or leave embed."""
    default_title = "👋 Welcome!" if event == "welcome" else "👋 Goodbye!"
    default_content = (
        f"Welcome to **{member.guild.name}**, {member.mention}! We're glad to have you."
        if event == "welcome"
        else f"**{member.display_name}** has left the server. Farewell!"
    )

    title = _fill(cfg["title"] or default_title, member)
    content = _fill(cfg["content"] or default_content, member)

    e = discord.Embed(
        title=title,
        description=content,
        color=_parse_color(cfg.get("color")),
    )

    # Thumbnail: "avatar" (default) | "none" | https://... URL
    thumbnail = cfg.get("thumbnail")
    if thumbnail is None or thumbnail == "avatar":
        e.set_thumbnail(url=member.display_avatar.url)
    elif thumbnail.startswith("https://"):
        e.set_thumbnail(url=thumbnail)
    # "none" → no thumbnail set

    # Image with optional text overlay
    image_file: discord.File | None = None
    if cfg.get("image_url"):
        raw_image_text = cfg.get("image_text") or ""
        image_text = _fill(raw_image_text.replace("\\n", "\n"), member)
        if image_text:
            image_file = await _make_overlay_image(cfg["image_url"], image_text)

        if image_file:
            e.set_image(url="attachment://welcome_banner.png")
        else:
            e.set_image(url=cfg["image_url"])

    # Footer
    footer_raw = cfg.get("footer_text") or member.guild.name
    e.set_footer(text=_fill(footer_raw, member))
    e.timestamp = discord.utils.utcnow()

    send_kwargs: dict = {"embed": e}
    if image_file:
        send_kwargs["file"] = image_file

    # DM delivery
    if cfg["dm"]:
        try:
            await member.send(**send_kwargs)
            log.info(f"{event} DM sent to {member} ({member.id}) in {member.guild}")
            return
        except discord.Forbidden:
            log.debug(f"{event} DM failed for {member} ({member.id}) — closed DMs")

    # Channel delivery
    channel: discord.TextChannel | None = None
    if cfg.get("channel_id"):
        channel = member.guild.get_channel(int(cfg["channel_id"]))  # type: ignore
    if not channel:
        channel = member.guild.system_channel  # type: ignore

    if channel:
        try:
            await channel.send(**send_kwargs)
            log.info(f"{event} message sent to #{channel} in {member.guild}")
        except discord.Forbidden:
            log.warning(f"Can't send {event} message to #{channel} in {member.guild}")


# ══════════════════════════════════════════════════════════════════════════════
class Welcome(commands.Cog):
    """Welcome and leave message configuration."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ── Events ─────────────────────────────────────────────────────────────────
    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if member.bot:
            return
        cfg = await db.get_welcome_config(member.guild.id)
        if cfg and cfg["enabled"]:
            await _send_event(self.bot, member, cfg, "welcome")

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        if member.bot:
            return
        cfg = await db.get_leave_config(member.guild.id)
        if cfg and cfg["enabled"]:
            await _send_event(self.bot, member, cfg, "leave")

    # ══════════════════════════════════════════════════════════════════════════
    #  /welcome group
    # ══════════════════════════════════════════════════════════════════════════
    @commands.hybrid_group(
        name="welcome",
        description="Configure or view welcome message settings.",
        invoke_without_command=True,
        extras={
            "category": "👋 Welcome & Leave",
            "short": "Configure join welcome messages",
            "usage": "welcome [set|test]",
            "desc": (
                "No args: show current welcome config.\n"
                "`welcome set` — configure channel, message, image, color, and more.\n"
                "`welcome test` — preview the welcome message as if you just joined.\n\n"
                "Template variables: `{user}`, `{mention}`, `{server}`, `{count}`"
            ),
            "args": [
                ("set [options]", "Configure the welcome message (see /welcome set)"),
                ("test", "Preview the welcome message"),
            ],
            "perms": "Administrator",
            "example": "/welcome set enabled:True channel:#welcome\n/welcome test",
        },
    )
    async def welcome(self, ctx: commands.Context):
        await self._show_config(ctx, "welcome")

    @welcome.command(name="set", description="Configure the welcome message.")
    @app_commands.describe(
        enabled="Enable or disable welcome messages",
        channel="Channel to post in (defaults to current channel if not set)",
        title="Embed title — supports {user}, {server}, etc.",
        content="Message body — supports {user}, {mention}, {server}, {count}",
        image_url="Background image URL (https://...)",
        image_text="Text to draw on the image itself — supports all vars",
        footer_text="Footer text — supports all vars (default: server name)",
        thumbnail='Member avatar by default. Set to "none" to hide, or an https:// URL',
        color="Embed color as a hex value, e.g. #5865F2",
        dm="DM the joining user instead of posting in a channel",
    )
    @has_admin_perms()
    async def welcome_set(
        self,
        ctx: commands.Context,
        enabled: Optional[bool] = None,
        channel: Optional[discord.TextChannel] = None,
        title: Optional[str] = None,
        content: Optional[str] = None,
        image_url: Optional[str] = None,
        image_text: Optional[str] = None,
        footer_text: Optional[str] = None,
        thumbnail: Optional[str] = None,
        color: Optional[str] = None,
        dm: Optional[bool] = None,
    ):
        await self._do_set(
            ctx,
            "welcome",
            enabled,
            channel,
            title,
            content,
            image_url,
            image_text,
            footer_text,
            thumbnail,
            color,
            dm,
        )

    @welcome.command(
        name="test", description="Preview the welcome message as if you just joined."
    )
    @has_admin_perms()
    async def welcome_test(self, ctx: commands.Context):
        cfg = await db.get_welcome_config(ctx.guild.id)
        if not cfg or not cfg.get("enabled"):
            return await ctx.reply(
                embed=h.warn(
                    "Welcome messages are not enabled. Use `/welcome set enabled:True` first."
                ),
                ephemeral=True,
            )
        await ctx.reply(
            embed=h.info("Sending test welcome message...", "🧪 Test"), ephemeral=True
        )
        await _send_event(self.bot, ctx.author, cfg, "welcome")  # type: ignore

    # ══════════════════════════════════════════════════════════════════════════
    #  /leave group
    # ══════════════════════════════════════════════════════════════════════════
    @commands.hybrid_group(
        name="leave",
        description="Configure or view leave message settings.",
        invoke_without_command=True,
        extras={
            "category": "👋 Welcome & Leave",
            "short": "Configure member leave messages",
            "usage": "leave [set|test]",
            "desc": (
                "No args: show current leave config.\n"
                "`leave set` — configure channel, message, image, color, and more.\n"
                "`leave test` — preview the leave message.\n\n"
                "Template variables: `{user}`, `{mention}`, `{server}`, `{count}`"
            ),
            "args": [
                ("set [options]", "Configure the leave message (see /leave set)"),
                ("test", "Preview the leave message"),
            ],
            "perms": "Administrator",
            "example": "/leave set enabled:True channel:#goodbye\n/leave test",
        },
    )
    async def leave(self, ctx: commands.Context):
        await self._show_config(ctx, "leave")

    @leave.command(name="set", description="Configure the leave message.")
    @app_commands.describe(
        enabled="Enable or disable leave messages",
        channel="Channel to post in (defaults to current channel if not set)",
        title="Embed title — supports {user}, {server}, etc.",
        content="Message body — supports {user}, {mention}, {server}, {count}",
        image_url="Background image URL (https://...)",
        image_text="Text to draw on the image itself — supports all vars",
        footer_text="Footer text — supports all vars (default: server name)",
        thumbnail='Member avatar by default. Set to "none" to hide, or an https:// URL',
        color="Embed color as a hex value, e.g. #FF5733",
        dm="DM the leaving user instead of posting in a channel",
    )
    @has_admin_perms()
    async def leave_set(
        self,
        ctx: commands.Context,
        enabled: Optional[bool] = None,
        channel: Optional[discord.TextChannel] = None,
        title: Optional[str] = None,
        content: Optional[str] = None,
        image_url: Optional[str] = None,
        image_text: Optional[str] = None,
        footer_text: Optional[str] = None,
        thumbnail: Optional[str] = None,
        color: Optional[str] = None,
        dm: Optional[bool] = None,
    ):
        await self._do_set(
            ctx,
            "leave",
            enabled,
            channel,
            title,
            content,
            image_url,
            image_text,
            footer_text,
            thumbnail,
            color,
            dm,
        )

    @leave.command(
        name="test", description="Preview the leave message as if you just left."
    )
    @has_admin_perms()
    async def leave_test(self, ctx: commands.Context):
        cfg = await db.get_leave_config(ctx.guild.id)
        if not cfg or not cfg.get("enabled"):
            return await ctx.reply(
                embed=h.warn(
                    "Leave messages are not enabled. Use `/leave set enabled:True` first."
                ),
                ephemeral=True,
            )
        await ctx.reply(
            embed=h.info("Sending test leave message...", "🧪 Test"), ephemeral=True
        )
        await _send_event(self.bot, ctx.author, cfg, "leave")  # type: ignore

    # ── Shared implementation ──────────────────────────────────────────────────
    async def _show_config(self, ctx: commands.Context, event: str):
        getter = db.get_welcome_config if event == "welcome" else db.get_leave_config
        cfg = await getter(ctx.guild.id)
        emoji = "👋" if event == "welcome" else "🚪"

        e = h.embed(title=f"{emoji} {event.title()} Config", color=h.BLUE)

        if not cfg:
            e.description = f"No {event} config set. Use `/{event} set` to configure."
        else:
            ch = (
                ctx.guild.get_channel(int(cfg["channel_id"]))
                if cfg.get("channel_id")
                else None
            )
            e.add_field(
                name="✅ Enabled", value="Yes" if cfg["enabled"] else "No", inline=True
            )
            e.add_field(
                name="📢 Channel",
                value=ch.mention if ch else "_System channel_",
                inline=True,
            )
            e.add_field(
                name="📨 DM Mode", value="Yes" if cfg["dm"] else "No", inline=True
            )
            e.add_field(
                name="📝 Title", value=cfg.get("title") or "_Default_", inline=False
            )
            e.add_field(
                name="💬 Content",
                value=(cfg.get("content") or "_Default_")[:500],
                inline=False,
            )
            e.add_field(
                name="🎨 Color",
                value=(
                    f"`#{cfg['color'].lstrip('#').upper()}`"
                    if cfg.get("color")
                    else "_Default (NanoBot blue)_"
                ),
                inline=True,
            )
            e.add_field(
                name="👤 Thumbnail",
                value=(
                    cfg["thumbnail"]
                    if cfg.get("thumbnail")
                    else "_Member avatar (default)_"
                ),
                inline=True,
            )
            e.add_field(
                name="📄 Footer",
                value=cfg.get("footer_text") or "_Server name (default)_",
                inline=False,
            )
            if cfg.get("image_url"):
                e.add_field(name="🖼️ Image URL", value=cfg["image_url"], inline=False)
            if cfg.get("image_text"):
                e.add_field(
                    name="✍️ Image Text",
                    value=f"`{cfg['image_text'][:200]}`",
                    inline=False,
                )

        e.set_footer(text=f"{_VARS_HELP}  ·  NanoBot")
        await ctx.reply(embed=e, ephemeral=True)

    async def _do_set(
        self,
        ctx: commands.Context,
        event: str,
        enabled: Optional[bool],
        channel: Optional[discord.TextChannel],
        title: Optional[str],
        content: Optional[str],
        image_url: Optional[str],
        image_text: Optional[str],
        footer_text: Optional[str],
        thumbnail: Optional[str],
        color: Optional[str],
        dm: Optional[bool],
    ):
        # ── Validate inputs ────────────────────────────────────────────────
        if image_url and not image_url.startswith("https://"):
            return await ctx.reply(
                embed=h.err("Image URL must start with `https://`."), ephemeral=True
            )

        if color and not _is_valid_hex(color):
            return await ctx.reply(
                embed=h.err(
                    "Color must be a hex value like `#5865F2` or `FF0000` (6 hex digits)."
                ),
                ephemeral=True,
            )

        if thumbnail is not None:
            thumbnail = thumbnail.strip()
            tl = thumbnail.lower()
            if tl in ("avatar", "none"):
                thumbnail = tl
            elif not thumbnail.startswith("https://"):
                return await ctx.reply(
                    embed=h.err(
                        "Thumbnail must be `avatar`, `none`, or an `https://` URL."
                    ),
                    ephemeral=True,
                )

        # ── Merge with existing config ─────────────────────────────────────
        getter = db.get_welcome_config if event == "welcome" else db.get_leave_config
        setter = db.set_welcome_config if event == "welcome" else db.set_leave_config

        existing = await getter(ctx.guild.id) or {}

        if channel is None and not existing.get("channel_id"):
            channel = ctx.channel  # type: ignore[assignment]

        new_cfg = {
            "enabled": (
                enabled if enabled is not None else existing.get("enabled", False)
            ),
            "channel_id": str(channel.id) if channel else existing.get("channel_id"),
            "title": title if title is not None else existing.get("title"),
            "content": content if content is not None else existing.get("content"),
            "image_url": (
                image_url if image_url is not None else existing.get("image_url")
            ),
            "image_text": (
                image_text if image_text is not None else existing.get("image_text")
            ),
            "footer_text": (
                footer_text if footer_text is not None else existing.get("footer_text")
            ),
            "thumbnail": (
                thumbnail if thumbnail is not None else existing.get("thumbnail")
            ),
            "color": color if color is not None else existing.get("color"),
            "dm": dm if dm is not None else existing.get("dm", False),
        }

        await setter(ctx.guild.id, **new_cfg)
        log.info(
            f"{event} config updated in {ctx.guild} ({ctx.guild.id}) by {ctx.author}"
        )

        ch = (
            ctx.guild.get_channel(int(new_cfg["channel_id"]))
            if new_cfg.get("channel_id")
            else None
        )
        lines = [
            f"✅ Enabled: **{'Yes' if new_cfg['enabled'] else 'No'}**",
            f"📢 Channel: {ch.mention if ch else '_System channel_'}",
            f"📨 DM Mode: **{'Yes' if new_cfg['dm'] else 'No'}**",
        ]
        if new_cfg.get("title"):
            lines.append(f"📝 Title: {new_cfg['title'][:100]}")
        if new_cfg.get("color"):
            lines.append(f"🎨 Color: `#{new_cfg['color'].lstrip('#').upper()}`")
        if new_cfg.get("thumbnail"):
            lines.append(f"👤 Thumbnail: `{new_cfg['thumbnail']}`")
        if new_cfg.get("footer_text"):
            lines.append(f"📄 Footer: {new_cfg['footer_text'][:100]}")
        if new_cfg.get("image_url"):
            lines.append("🖼️ Image: set")
        if new_cfg.get("image_text"):
            if not new_cfg.get("image_url"):
                lines.append(
                    "⚠️ Image text is set but no image URL is configured — it won't appear."
                )
            elif not _PILLOW_OK:
                lines.append(
                    "⚠️ Image text is set but Pillow is not installed — overlay disabled."
                )
            else:
                lines.append(f"✍️ Image text: `{new_cfg['image_text'][:80]}`")

        emoji = "👋" if event == "welcome" else "🚪"
        await ctx.reply(
            embed=h.ok(
                "\n".join(lines) + f"\n\nTest it with `/{event} test`.",
                f"{emoji} {event.title()} Config Updated",
            ),
            ephemeral=True,
        )


# ── Registration ───────────────────────────────────────────────────────────────
async def setup(bot: commands.Bot):
    await bot.add_cog(Welcome(bot))
