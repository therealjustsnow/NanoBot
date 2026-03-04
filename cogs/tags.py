"""
cogs/tags.py
Tag system — save text snippets (with optional images) and retrieve them fast.

Personal tags: visible only to you.
Global tags:   usable by anyone (mod-only creation).

──────────────────────────────────────────────────────
Shorthand (prefix commands — fastest on mobile)
──────────────────────────────────────────────────────
  !tag                              → list all tags
  !tag hello                        → send (DM) the tag named "hello"
  !tag + hello Hello world!         → create personal tag "hello"
  !tag add hello Hello world!       → same
  !tag - hello                      → delete tag "hello"
  !tag remove hello                 → same
  !hello                            → also sends tag "hello" (handled in main.py)

Full subcommands (work as slash AND prefix)
──────────────────────────────────────────────────────
  /tag create   <name> <content> [image] [image_url]
  /tag global   <name> <content> [image] [image_url]   (mods only)
  /tag use      <name> [dm_user]
  /tag preview  <name>
  /tag image    <name> [image] [image_url]
  /tag edit     <name> <new_content>
  /tag delete   <name>
  /tag list

Tag data shape:
  { "content": str, "image_url": str|null, "by_id": str, "by_name": str }
  Legacy plain strings are normalised silently on read.
"""

import logging
import re
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from utils import helpers as h
from utils import storage

log = logging.getLogger("NanoBot.tags")

_FILE     = "tags.json"
_CDN_HOSTS = ("cdn.discordapp.com", "media.discordapp.net", "attachments.discord.media")


# ── Data helpers ───────────────────────────────────────────────────────────────

def _norm(v) -> dict:
    """Upgrade legacy plain-string tags to dict shape."""
    if isinstance(v, str):
        return {"content": v, "image_url": None}
    return v


def _get_data(guild_id: int) -> tuple[dict, str]:
    data = storage.read(_FILE)
    gid  = str(guild_id)
    if gid not in data:
        data[gid] = {"global": {}, "personal": {}}
    return data, gid


def _find(guild_id: int, user_id: int, name: str) -> dict | None:
    """Personal first, then global. Returns normalised dict or None."""
    data = storage.read(_FILE)
    gid  = str(guild_id)
    uid  = str(user_id)
    v = data.get(gid, {}).get("personal", {}).get(uid, {}).get(name)
    if v is not None:
        return _norm(v)
    v = data.get(gid, {}).get("global", {}).get(name)
    if v is not None:
        return _norm(v)
    return None


def _cdn_warn(url: str) -> str | None:
    if any(h_ in url for h_ in _CDN_HOSTS):
        return (
            "**Heads up:** This image is hosted on Discord's CDN and may expire.\n"
            "For permanent tags, upload to [Imgur](https://imgur.com) and paste the direct URL."
        )
    return None


def _resolve_image(ctx, attachment, image_url) -> tuple[str | None, str | None]:
    """Returns (url, warning_or_None)."""
    url = None
    if attachment:
        if not (attachment.content_type or "").startswith("image/"):
            return None, "That file doesn't look like an image. No image saved."
        url = attachment.url
    elif image_url:
        if not image_url.startswith("https://"):
            return None, "Image URL must start with `https://`. No image saved."
        url = image_url
    elif ctx.message.attachments:
        for a in ctx.message.attachments:
            if (a.content_type or "").startswith("image/"):
                url = a.url
                break
    warn = _cdn_warn(url) if url else None
    return url, warn


def _tag_embed(tag: dict, name: str, guild_name: str, *, prefix="📌") -> discord.Embed:
    """Build a rich embed for tags whose content fits (≤1500 chars)."""
    e = discord.Embed(
        title       = f"{prefix}  [{guild_name}]  {name}",
        description = tag.get("content") or None,
        color       = h.BLUE,
    )
    if tag.get("image_url"):
        e.set_image(url=tag["image_url"])
    e.set_footer(text="NanoBot Tags")
    return e


async def _send_tag(
    target,
    tag:   dict,
    name:  str,
    guild_name: str,
    *,
    reply: bool = True,
):
    """
    Send a tag. Content <= 1500 chars uses a rich embed.
    Content 1501-2000 falls back to plain text so nothing gets cut off.
    """
    text    = tag.get("content") or ""
    img_url = tag.get("image_url")
    send    = getattr(target, "reply" if reply else "send", None) or target.send

    if len(text) > 1500:
        nl     = chr(10)
        header = "📌 **[" + guild_name + "]  " + name + "**"
        body   = header + nl + nl + text
        if img_url:
            body += nl + img_url
        await send(body)
    else:
        await send(embed=_tag_embed(tag, name, guild_name))


def _list_entry(name: str, tag: dict) -> str:
    return f"`{name}`{'  🖼️' if tag.get('image_url') else ''}"


# ══════════════════════════════════════════════════════════════════════════════
class Tags(commands.Cog):
    """Quick-access text snippets with optional images — personal or server-wide."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ══════════════════════════════════════════════════════════════════════════
    #  /tag  root — handles all prefix shorthands
    # ══════════════════════════════════════════════════════════════════════════
    @commands.hybrid_group(
        name="tag",
        description="Manage and use tags. /tag list, /tag create, /tag use, etc.",
        invoke_without_command=True,
    )
    async def tag(self, ctx: commands.Context, *, args: str = ""):
        """
        Slash: shows the tag list (subcommands handle the rest).
        Prefix shorthand:
          !tag                        → list
          !tag <name>                 → use/DM tag
          !tag + <name> <content>     → create personal tag
          !tag add <name> <content>   → create personal tag
          !tag - <name>               → delete tag
          !tag remove <name>          → delete tag
        """
        # Slash commands should use the proper subcommands — show list
        if ctx.interaction:
            return await self._show_list(ctx)

        # Prefix shorthand parsing
        parts = args.split(None, 2) if args else []

        if not parts:
            return await self._show_list(ctx)

        verb = parts[0].lower()

        # ── Create shorthand: !tag + name content | !tag add name content ─────
        if verb in ("+", "add", "create"):
            if len(parts) < 3:
                return await ctx.reply(
                    embed=h.err(
                        f"Usage: `{ctx.prefix}tag + <name> <content>`\n"
                        f"Example: `{ctx.prefix}tag + rules Read the rules before posting!`"
                    ),
                    ephemeral=True,
                )
            await self._do_create(ctx, parts[1], parts[2])

        # ── Delete shorthand: !tag - name | !tag remove name ──────────────────
        elif verb in ("-", "remove", "delete"):
            if len(parts) < 2:
                return await ctx.reply(
                    embed=h.err(f"Usage: `{ctx.prefix}tag - <name>`"),
                    ephemeral=True,
                )
            await self._do_delete(ctx, parts[1])

        # ── Global shorthand: !tag global+ name content ────────────────────────
        elif verb in ("global+", "g+", "gadd"):
            if not ctx.author.guild_permissions.manage_messages:
                return await ctx.reply(
                    embed=h.err("You need **Manage Messages** to create global tags."),
                    ephemeral=True,
                )
            if len(parts) < 3:
                return await ctx.reply(
                    embed=h.err(f"Usage: `{ctx.prefix}tag g+ <name> <content>`"),
                    ephemeral=True,
                )
            await self._do_create_global(ctx, parts[1], parts[2])

        # ── Fallback: treat as tag name → use it ──────────────────────────────
        else:
            await self._do_use(ctx, verb)

    # ══════════════════════════════════════════════════════════════════════════
    #  /tag create
    # ══════════════════════════════════════════════════════════════════════════
    @tag.command(
        name="create",
        description="Create a personal tag with optional image.",
    )
    @app_commands.describe(
        name      = "Tag name (no spaces, max 32 chars)",
        content   = "Tag text (max 2000 chars) — optional if an image is provided",
        image     = "Attach an image file",
        image_url = "Or paste a direct image URL (https://...)",
    )
    async def tag_create(
        self,
        ctx:       commands.Context,
        name:      str,
        content:   Optional[str]               = None,
        image:     Optional[discord.Attachment] = None,
        image_url: Optional[str]               = None,
    ):
        img_url, img_warn = _resolve_image(ctx, image, image_url)
        if img_url is None and img_warn:
            return await ctx.reply(embed=h.err(img_warn), ephemeral=True)
        await self._do_create(ctx, name, content, img_url=img_url, img_warn=img_warn)

    # ══════════════════════════════════════════════════════════════════════════
    #  /tag global
    # ══════════════════════════════════════════════════════════════════════════
    @tag.command(
        name="global",
        description="Create a global server tag usable by anyone. Mods only.",
    )
    @app_commands.describe(
        name      = "Tag name (no spaces, max 32 chars)",
        content   = "Tag content (max 2000 chars) — optional if an image is provided",
        image     = "Attach an image file",
        image_url = "Or paste a direct image URL",
    )
    @commands.has_permissions(manage_messages=True)
    async def tag_global(
        self,
        ctx:       commands.Context,
        name:      str,
        content:   Optional[str]               = None,
        image:     Optional[discord.Attachment] = None,
        image_url: Optional[str]               = None,
    ):
        img_url, img_warn = _resolve_image(ctx, image, image_url)
        if img_url is None and img_warn:
            return await ctx.reply(embed=h.err(img_warn), ephemeral=True)
        await self._do_create_global(ctx, name, content, img_url=img_url, img_warn=img_warn)

    # ══════════════════════════════════════════════════════════════════════════
    #  /tag use
    # ══════════════════════════════════════════════════════════════════════════
    @tag.command(
        name="use",
        description="Post a tag in this channel, or DM it to a specific user.",
    )
    @app_commands.describe(
        name    = "Tag name",
        dm_user = "DM the tag to this user instead of posting it here",
    )
    async def tag_use(
        self,
        ctx:     commands.Context,
        name:    str,
        dm_user: Optional[discord.Member] = None,
    ):
        await self._do_use(ctx, name.lower().strip(), dm_user=dm_user)

    # ══════════════════════════════════════════════════════════════════════════
    #  /tag preview
    # ══════════════════════════════════════════════════════════════════════════
    @tag.command(
        name="preview",
        description="Preview a tag here (only you see it) without DMing anyone.",
    )
    @app_commands.describe(name="Tag name to preview")
    async def tag_preview(self, ctx: commands.Context, name: str):
        name = name.lower().strip()
        tag  = _find(ctx.guild.id, ctx.author.id, name)
        if not tag:
            return await ctx.reply(embed=h.err(f"No tag named `{name}` found."), ephemeral=True)
        await _send_tag(ctx, tag, name, ctx.guild.name)
        # Note: preview always uses reply; plain-text tags won't be ephemeral but that's fine

    # ══════════════════════════════════════════════════════════════════════════
    #  /tag image
    # ══════════════════════════════════════════════════════════════════════════
    @tag.command(
        name="image",
        description="Add, replace, or remove the image on a tag.",
    )
    @app_commands.describe(
        name      = "Tag name",
        image     = "New image (leave both blank to REMOVE the image)",
        image_url = "New image URL — or type 'remove' to clear it",
    )
    async def tag_image(
        self,
        ctx:       commands.Context,
        name:      str,
        image:     Optional[discord.Attachment] = None,
        image_url: Optional[str]               = None,
    ):
        name     = name.lower().strip()
        data     = storage.read(_FILE)
        gid, uid = str(ctx.guild.id), str(ctx.author.id)

        tag_ref   = None
        is_global = False

        personal = data.get(gid, {}).get("personal", {}).get(uid, {})
        if name in personal:
            data[gid]["personal"][uid][name] = _norm(personal[name])
            tag_ref = data[gid]["personal"][uid][name]
        elif ctx.author.guild_permissions.manage_messages:
            glob = data.get(gid, {}).get("global", {})
            if name in glob:
                data[gid]["global"][name] = _norm(glob[name])
                tag_ref = data[gid]["global"][name]
                is_global = True

        if tag_ref is None:
            return await ctx.reply(
                embed=h.err(f"Tag `{name}` not found or you don't have permission to edit it."),
                ephemeral=True,
            )

        removing = (image is None and image_url is None) or (
            image_url and image_url.strip().lower() == "remove"
        )
        if removing:
            had = bool(tag_ref.get("image_url"))
            tag_ref["image_url"] = None
            storage.write(_FILE, data)
            msg = f"Image removed from **{name}**." if had else f"**{name}** had no image anyway."
            return await ctx.reply(embed=h.ok(msg, "🖼️ Image Cleared"), ephemeral=True)

        img_url, img_warn = _resolve_image(ctx, image, image_url)
        if not img_url:
            if img_warn:
                return await ctx.reply(embed=h.err(img_warn), ephemeral=True)
            return await ctx.reply(
                embed=h.err(
                    "No image found.\n"
                    "• Attach an image file, or paste a `https://` URL\n"
                    "• Type `remove` in the `image_url` field to clear the image"
                ),
                ephemeral=True,
            )

        tag_ref["image_url"] = img_url
        storage.write(_FILE, data)
        scope = "global" if is_global else "personal"
        await ctx.reply(embed=h.ok(f"Image updated on {scope} tag **{name}**.", "🖼️ Image Updated"), ephemeral=True)
        if img_warn:
            await ctx.send(embed=h.warn(img_warn, "⚠️ CDN Warning"), ephemeral=True)

    # ══════════════════════════════════════════════════════════════════════════
    #  /tag edit
    # ══════════════════════════════════════════════════════════════════════════
    @tag.command(
        name="edit",
        description="Edit the text content of a tag. Use /tag image to change the image.",
    )
    @app_commands.describe(name="Tag name", new_content="New content (max 1500 chars)")
    async def tag_edit(self, ctx: commands.Context, name: str, *, new_content: str):
        name = name.lower().strip()
        if len(new_content) > 1500:
            return await ctx.reply(embed=h.err("Content must be 1500 characters or fewer."), ephemeral=True)
        data     = storage.read(_FILE)
        gid, uid = str(ctx.guild.id), str(ctx.author.id)

        personal = data.get(gid, {}).get("personal", {}).get(uid, {})
        if name in personal:
            t = _norm(personal[name]); t["content"] = new_content
            data[gid]["personal"][uid][name] = t
            storage.write(_FILE, data)
            return await ctx.reply(embed=h.ok(f"Personal tag **{name}** updated.", "✏️ Edited"), ephemeral=True)

        if ctx.author.guild_permissions.manage_messages:
            glob = data.get(gid, {}).get("global", {})
            if name in glob:
                t = _norm(glob[name]); t["content"] = new_content
                data[gid]["global"][name] = t
                storage.write(_FILE, data)
                return await ctx.reply(embed=h.ok(f"Global tag **{name}** updated.", "✏️ Edited"), ephemeral=True)

        await ctx.reply(
            embed=h.err(f"Tag `{name}` not found or you don't have permission to edit it."),
            ephemeral=True,
        )

    # ══════════════════════════════════════════════════════════════════════════
    #  /tag delete
    # ══════════════════════════════════════════════════════════════════════════
    @tag.command(
        name="delete",
        description="Delete a tag (personal, or global if you're a mod).",
    )
    @app_commands.describe(name="Tag name to delete")
    async def tag_delete(self, ctx: commands.Context, name: str):
        await self._do_delete(ctx, name)

    # ══════════════════════════════════════════════════════════════════════════
    #  /tag list
    # ══════════════════════════════════════════════════════════════════════════
    @tag.command(name="list", description="List your personal tags and global server tags.")
    async def tag_list(self, ctx: commands.Context):
        await self._show_list(ctx)

    # ══════════════════════════════════════════════════════════════════════════
    #  Internal implementation methods (called by both shorthands and subcommands)
    # ══════════════════════════════════════════════════════════════════════════

    async def _do_create(
        self,
        ctx:      commands.Context,
        name:     str,
        content:  str | None,
        *,
        img_url:  str | None = None,
        img_warn: str | None = None,
    ):
        name    = name.lower().strip()
        content = content.strip() if content else None

        if len(name) > 32:
            return await ctx.reply(embed=h.err("Tag name must be 32 characters or fewer."), ephemeral=True)
        if not content and not img_url:
            return await ctx.reply(
                embed=h.err("A tag needs at least some text or an image — you can't have both empty."),
                ephemeral=True,
            )
        if content and len(content) > 2000:
            return await ctx.reply(embed=h.err("Tag content must be 2000 characters or fewer."), ephemeral=True)

        data, gid = _get_data(ctx.guild.id)
        uid = str(ctx.author.id)

        if data[gid].setdefault("personal", {}).setdefault(uid, {}).get(name):
            return await ctx.reply(
                embed=h.err(
                    f"Tag `{name}` already exists.\n"
                    f"Use `{ctx.prefix}tag edit {name} <content>` to update it."
                ),
                ephemeral=True,
            )

        data[gid]["personal"][uid][name] = {"content": content, "image_url": img_url}
        storage.write(_FILE, data)

        img_line = "\n🖼️ Image saved." if img_url else ""
        await ctx.reply(
            embed=h.ok(
                f"Tag **{name}** saved.{img_line}\n"
                f"Use `{ctx.prefix}tag {name}` or `{ctx.prefix}{name}` to send it anytime.",
                "🏷️ Tag Created",
            ),
            ephemeral=True,
        )
        if img_warn:
            await ctx.send(embed=h.warn(img_warn, "⚠️ CDN Warning"), ephemeral=True)

    async def _do_create_global(
        self,
        ctx:      commands.Context,
        name:     str,
        content:  str | None,
        *,
        img_url:  str | None = None,
        img_warn: str | None = None,
    ):
        name    = name.lower().strip()
        content = content.strip() if content else None

        if len(name) > 32:
            return await ctx.reply(embed=h.err("Tag name must be 32 characters or fewer."), ephemeral=True)
        if not content and not img_url:
            return await ctx.reply(
                embed=h.err("A tag needs at least some text or an image — you can't have both empty."),
                ephemeral=True,
            )
        if content and len(content) > 2000:
            return await ctx.reply(embed=h.err("Tag content must be 2000 characters or fewer."), ephemeral=True)

        data, gid = _get_data(ctx.guild.id)
        if name in data[gid].get("global", {}):
            return await ctx.reply(
                embed=h.err(f"Global tag `{name}` already exists. Use `/tag edit {name}` to update it."),
                ephemeral=True,
            )

        data[gid].setdefault("global", {})[name] = {
            "content":   content,
            "image_url": img_url,
            "by_id":     str(ctx.author.id),
            "by_name":   str(ctx.author),
        }
        storage.write(_FILE, data)

        img_line = "\n🖼️ Image saved." if img_url else ""
        await ctx.reply(
            embed=h.ok(
                f"Global tag **{name}** saved.{img_line}\n"
                f"Anyone can use `{ctx.prefix}tag use {name}` or `{ctx.prefix}{name}` to send it.",
                "🌐 Global Tag Created",
            ),
            ephemeral=True,
        )
        if img_warn:
            await ctx.send(embed=h.warn(img_warn, "⚠️ CDN Warning"), ephemeral=True)

    async def _do_use(
        self,
        ctx:     commands.Context,
        name:    str,
        *,
        dm_user: Optional[discord.Member] = None,
    ):
        name = name.lower().strip()
        tag  = _find(ctx.guild.id, ctx.author.id, name)
        if not tag:
            return await ctx.reply(
                embed=h.err(
                    f"No tag named `{name}` found.\n"
                    f"Use `{ctx.prefix}tag list` to see all available tags."
                ),
                ephemeral=True,
            )

        if dm_user:
            # Explicit user target → DM them
            try:
                await _send_tag(dm_user, tag, name, ctx.guild.name, reply=False)
            except discord.Forbidden:
                return await ctx.reply(
                    embed=h.err(f"Couldn't DM **{dm_user.display_name}** — their DMs may be closed."),
                    ephemeral=True,
                )
            await ctx.reply(
                embed=h.ok(f"Tag `{name}` sent to **{dm_user.display_name}** via DM. 📨", "📨 Tag Sent"),
                ephemeral=True,
            )
        else:
            # Default: post the tag in the channel (embed or plain text depending on length)
            await _send_tag(ctx, tag, name, ctx.guild.name)

    async def _do_delete(self, ctx: commands.Context, name: str):
        name     = name.lower().strip()
        data     = storage.read(_FILE)
        gid, uid = str(ctx.guild.id), str(ctx.author.id)

        personal = data.get(gid, {}).get("personal", {}).get(uid, {})
        if name in personal:
            del personal[name]
            data[gid]["personal"][uid] = personal
            storage.write(_FILE, data)
            return await ctx.reply(embed=h.ok(f"Personal tag **{name}** deleted.", "🗑️ Deleted"), ephemeral=True)

        if ctx.author.guild_permissions.manage_messages:
            glob = data.get(gid, {}).get("global", {})
            if name in glob:
                del glob[name]
                data[gid]["global"] = glob
                storage.write(_FILE, data)
                return await ctx.reply(embed=h.ok(f"Global tag **{name}** deleted.", "🗑️ Deleted"), ephemeral=True)

        await ctx.reply(
            embed=h.err(f"Tag `{name}` not found or you don't have permission to delete it."),
            ephemeral=True,
        )

    async def _show_list(self, ctx: commands.Context):
        data = storage.read(_FILE)
        gid  = str(ctx.guild.id)
        uid  = str(ctx.author.id)

        personal_tags = {n: _norm(v) for n, v in data.get(gid, {}).get("personal", {}).get(uid, {}).items()}
        global_tags   = {n: _norm(v) for n, v in data.get(gid, {}).get("global", {}).items()}

        prefix = self.bot.prefixes.get(gid, self.bot.default_prefix)

        e = h.embed(title="🏷️ Tag List", color=h.BLUE)
        e.description = (
            f"`{prefix}tag <name>` or `{prefix}<name>` → DM it to yourself\n"
            f"`{prefix}tag + <name> <content>` → create  ·  `{prefix}tag - <name>` → delete\n"
            f"🖼️ = has image\n\u200b"
        )

        p_lines = "  ".join(_list_entry(n, t) for n, t in sorted(personal_tags.items())) or "_None yet_"
        g_lines = "  ".join(_list_entry(n, t) for n, t in sorted(global_tags.items()))   or "_None yet_"

        e.add_field(name=f"🔒 Your Tags ({len(personal_tags)})",    value=p_lines + "\n\u200b", inline=False)
        e.add_field(name=f"🌐 Server Tags ({len(global_tags)})", value=g_lines + "\n\u200b", inline=False)
        e.set_footer(text="Personal tags are only visible to you  ·  NanoBot")
        await ctx.reply(embed=e, ephemeral=True)


# ── Registration ───────────────────────────────────────────────────────────────
async def setup(bot: commands.Bot):
    await bot.add_cog(Tags(bot))
