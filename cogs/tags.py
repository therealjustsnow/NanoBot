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

import io
import json
import logging
import re
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from utils import db
from utils import helpers as h

log = logging.getLogger("NanoBot.tags")

_CDN_HOSTS = ("cdn.discordapp.com", "media.discordapp.net", "attachments.discord.media")


# ── Data helpers ───────────────────────────────────────────────────────────────


def _norm(v) -> dict:
    """Upgrade legacy plain-string tags to dict shape."""
    if isinstance(v, str):
        return {"content": v, "image_url": None}
    return v


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
        title=f"{prefix}  [{guild_name}]  {name}",
        description=tag.get("content") or None,
        color=h.BLUE,
    )
    if tag.get("image_url"):
        e.set_image(url=tag["image_url"])
    e.set_footer(text="NanoBot Tags")
    return e


async def _send_tag(
    target,
    tag: dict,
    name: str,
    guild_name: str,
    *,
    reply: bool = True,
):
    """
    Send a tag. Content <= 1500 chars uses a rich embed.
    Content 1501-2000 falls back to plain text so nothing gets cut off.
    """
    text = tag.get("content") or ""
    img_url = tag.get("image_url")
    send = getattr(target, "reply" if reply else "send", None) or target.send

    if len(text) > 1500:
        nl = chr(10)
        header = "📌 **[" + guild_name + "]  " + name + "**"
        body = header + nl + nl + text
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
        extras={
            "category": "🏷️ Tags",
            "short": "Post text snippets in channel or DM — personal or server-wide",
            "usage": "tag [shorthand or subcommand]",
            "desc": "Tags let you save text (and images) and fire them instantly.\nShorthand: !tag <n> — post tag, !<n> — same shorter, !tag + <n> | <content> — create, !tag - <n> — delete\nSubcommands: /tag create, /tag global, /tag use, /tag preview, /tag edit, /tag delete, /tag list, /tag export, /tag import",
            "args": [],
            "perms": "None (global creation requires Manage Messages)",
            "example": "!tag + rules | Read #rules before posting!\n!rules",
        },
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
        # Split only on the first space to isolate the verb; rest stays raw.
        parts = args.split(None, 1) if args else []

        if not parts:
            return await self._show_list(ctx)

        verb = parts[0].lower()
        rest = parts[1] if len(parts) > 1 else ""

        # ── Create: !tag + <name> | <content> ─────────────────────────────────
        # The | separates name from content — lets both sides have spaces.
        # e.g.  !tag + server rules | Read #rules before posting!
        if verb in ("+", "add", "create"):
            if "|" not in rest:
                return await ctx.reply(
                    embed=h.err(
                        f"Usage: `{ctx.prefix}tag + <name> | <content>`\n"
                        f"Use **|** to separate the tag name from its content.\n"
                        f"Example: `{ctx.prefix}tag + server rules | Read #rules first!`"
                    ),
                    ephemeral=True,
                )
            tag_name, _, tag_content = rest.partition("|")
            await self._do_create(ctx, tag_name.strip(), tag_content.strip() or None)

        # ── Delete: !tag - <name>  (full remaining text is the name) ──────────
        elif verb in ("-", "remove", "delete"):
            if not rest:
                return await ctx.reply(
                    embed=h.err(
                        f"Usage: `{ctx.prefix}tag - <name>`\n"
                        f"Example: `{ctx.prefix}tag - server rules`"
                    ),
                    ephemeral=True,
                )
            await self._do_delete(ctx, rest)

        # ── Global create: !tag g+ <name> | <content> ─────────────────────────
        elif verb in ("global+", "g+", "gadd"):
            if not ctx.author.guild_permissions.manage_messages:
                return await ctx.reply(
                    embed=h.err("You need **Manage Messages** to create global tags."),
                    ephemeral=True,
                )
            if "|" not in rest:
                return await ctx.reply(
                    embed=h.err(
                        f"Usage: `{ctx.prefix}tag g+ <name> | <content>`\n"
                        f"Example: `{ctx.prefix}tag g+ server rules | Read #rules first!`"
                    ),
                    ephemeral=True,
                )
            tag_name, _, tag_content = rest.partition("|")
            await self._do_create_global(
                ctx, tag_name.strip(), tag_content.strip() or None
            )

        # ── Fallback: full args is the tag name (supports spaces) ─────────────
        # !tag server rules  →  looks up tag named "server rules"
        else:
            await self._do_use(ctx, args)

    # ══════════════════════════════════════════════════════════════════════════
    #  /tag create
    # ══════════════════════════════════════════════════════════════════════════
    @tag.command(
        name="create",
        description="Create a personal tag with optional image.",
    )
    @app_commands.describe(
        name="Tag name (max 32 chars)",
        content="Tag text (max 2000 chars) — optional if an image is provided",
        image="Attach an image file",
        image_url="Or paste a direct image URL (https://...)",
    )
    async def tag_create(
        self,
        ctx: commands.Context,
        name: str,
        content: Optional[str] = None,
        image: Optional[discord.Attachment] = None,
        image_url: Optional[str] = None,
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
        name="Tag name (max 32 chars)",
        content="Tag content (max 2000 chars) — optional if an image is provided",
        image="Attach an image file",
        image_url="Or paste a direct image URL",
    )
    @commands.has_permissions(manage_messages=True)
    async def tag_global(
        self,
        ctx: commands.Context,
        name: str,
        content: Optional[str] = None,
        image: Optional[discord.Attachment] = None,
        image_url: Optional[str] = None,
    ):
        img_url, img_warn = _resolve_image(ctx, image, image_url)
        if img_url is None and img_warn:
            return await ctx.reply(embed=h.err(img_warn), ephemeral=True)
        await self._do_create_global(
            ctx, name, content, img_url=img_url, img_warn=img_warn
        )

    # ══════════════════════════════════════════════════════════════════════════
    #  /tag use
    # ══════════════════════════════════════════════════════════════════════════
    @tag.command(
        name="use",
        description="Post a tag in this channel, or DM it to a specific user.",
    )
    @app_commands.describe(
        name="Tag name",
        dm_user="DM the tag to this user instead of posting it here",
    )
    async def tag_use(
        self,
        ctx: commands.Context,
        name: str,
        dm_user: Optional[discord.Member] = None,
    ):
        await self._do_use(ctx, name.lower().strip(), dm_user=dm_user)

    # ══════════════════════════════════════════════════════════════════════════
    #  /tag preview
    # ══════════════════════════════════════════════════════════════════════════
    @tag.command(
        name="preview",
        description="Preview a tag — only you see this response.",
    )
    @app_commands.describe(name="Tag name to preview")
    async def tag_preview(self, ctx: commands.Context, name: str):
        name = name.lower().strip()
        tag = await db.get_tag(ctx.guild.id, name, ctx.author.id)
        if not tag:
            return await ctx.reply(
                embed=h.err(f"No tag named `{name}` found."), ephemeral=True
            )
        # Always ephemeral so only the invoker sees the preview
        text = tag.get("content") or ""
        img_url = tag.get("image_url")
        if len(text) > 1500:
            await ctx.reply(
                f"📌 **[{ctx.guild.name}]  {name}**\n\n{text}"
                + (f"\n{img_url}" if img_url else ""),
                ephemeral=True,
            )
        else:
            await ctx.reply(
                embed=_tag_embed(tag, name, ctx.guild.name, prefix="👁️"), ephemeral=True
            )

    # ══════════════════════════════════════════════════════════════════════════
    #  /tag edit
    # ══════════════════════════════════════════════════════════════════════════
    @tag.command(
        name="edit",
        description="Edit a tag's content and/or image.",
    )
    @app_commands.describe(
        name="Tag name",
        new_content="New text content (max 2000 chars — leave blank to keep existing)",
        image="New image attachment (leave blank to keep existing)",
        image_url="New image URL — or type 'remove' to clear the image",
    )
    async def tag_edit(
        self,
        ctx: commands.Context,
        name: str,
        new_content: Optional[str] = None,
        image: Optional[discord.Attachment] = None,
        image_url: Optional[str] = None,
    ):
        name = name.lower().strip()
        uid = str(ctx.author.id)

        # Determine scope
        if await db.tag_exists(ctx.guild.id, uid, name):
            scope = uid
        elif ctx.author.guild_permissions.manage_messages and await db.tag_exists(
            ctx.guild.id, "global", name
        ):
            scope = "global"
        else:
            return await ctx.reply(
                embed=h.err(
                    f"Tag `{name}` not found or you don't have permission to edit it."
                ),
                ephemeral=True,
            )

        if new_content is not None and len(new_content) > 2000:
            return await ctx.reply(
                embed=h.err("Content must be 2000 characters or fewer."), ephemeral=True
            )

        changes = []

        if new_content is not None:
            await db.update_tag_content(ctx.guild.id, scope, name, new_content)
            changes.append("📝 Text updated")

        # Image handling
        remove_img = image_url and image_url.strip().lower() == "remove"
        if remove_img:
            await db.update_tag_image(ctx.guild.id, scope, name, None)
            changes.append("🖼️ Image removed")
        elif image or image_url:
            img_url, img_warn = _resolve_image(ctx, image, image_url)
            if img_url:
                await db.update_tag_image(ctx.guild.id, scope, name, img_url)
                changes.append("🖼️ Image updated")
                if img_warn:
                    await ctx.send(
                        embed=h.warn(img_warn, "⚠️ CDN Warning"), ephemeral=True
                    )
            elif img_warn:
                return await ctx.reply(embed=h.err(img_warn), ephemeral=True)

        if not changes:
            return await ctx.reply(
                embed=h.warn(
                    "Nothing to change — provide new_content, image, or image_url."
                ),
                ephemeral=True,
            )

        scope_label = "personal" if scope == uid else "global"
        await ctx.reply(
            embed=h.ok(
                f"{scope_label.title()} tag **{name}** updated.\n"
                + "  ·  ".join(changes),
                "✏️ Tag Edited",
            ),
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
    @tag.command(
        name="list", description="List your personal tags and global server tags."
    )
    async def tag_list(self, ctx: commands.Context):
        await self._show_list(ctx)

    # ══════════════════════════════════════════════════════════════════════════
    #  Internal implementation methods (called by both shorthands and subcommands)
    # ══════════════════════════════════════════════════════════════════════════

    async def _do_create(
        self,
        ctx: commands.Context,
        name: str,
        content: str | None,
        *,
        img_url: str | None = None,
        img_warn: str | None = None,
    ):
        name = name.lower().strip()
        content = content.strip() if content else None

        if len(name) > 32:
            return await ctx.reply(
                embed=h.err("Tag name must be 32 characters or fewer."), ephemeral=True
            )
        if not content and not img_url:
            return await ctx.reply(
                embed=h.err(
                    "A tag needs at least some text or an image — you can't have both empty."
                ),
                ephemeral=True,
            )
        if content and len(content) > 2000:
            return await ctx.reply(
                embed=h.err("Tag content must be 2000 characters or fewer."),
                ephemeral=True,
            )

        uid = str(ctx.author.id)

        if await db.tag_exists(ctx.guild.id, uid, name):
            return await ctx.reply(
                embed=h.err(
                    f"Tag `{name}` already exists.\n"
                    f"Use `{ctx.prefix}tag edit {name} <content>` to update it."
                ),
                ephemeral=True,
            )

        await db.set_tag(ctx.guild.id, uid, name, content, img_url)

        img_line = "\n🖼️ Image attached." if img_url else ""
        await ctx.reply(
            embed=h.ok(
                f"Personal tag **{name}** created.{img_line}\n"
                f"Only you can see it. Use `/tag use {name}` or `{ctx.prefix}{name}` to post it.",
                "🏷️ Tag Created",
            ),
            ephemeral=True,
        )
        if img_warn:
            await ctx.send(embed=h.warn(img_warn, "⚠️ CDN Warning"), ephemeral=True)

    async def _do_create_global(
        self,
        ctx: commands.Context,
        name: str,
        content: str | None,
        *,
        img_url: str | None = None,
        img_warn: str | None = None,
    ):
        name = name.lower().strip()
        content = content.strip() if content else None

        if len(name) > 32:
            return await ctx.reply(
                embed=h.err("Tag name must be 32 characters or fewer."), ephemeral=True
            )
        if not content and not img_url:
            return await ctx.reply(
                embed=h.err(
                    "A tag needs at least some text or an image — you can't have both empty."
                ),
                ephemeral=True,
            )
        if content and len(content) > 2000:
            return await ctx.reply(
                embed=h.err("Tag content must be 2000 characters or fewer."),
                ephemeral=True,
            )

        if await db.tag_exists(ctx.guild.id, "global", name):
            return await ctx.reply(
                embed=h.err(
                    f"Global tag `{name}` already exists. Use `/tag edit {name}` to update it."
                ),
                ephemeral=True,
            )

        await db.set_tag(
            ctx.guild.id,
            "global",
            name,
            content,
            img_url,
            by_id=str(ctx.author.id),
            by_name=str(ctx.author),
        )

        img_line = "\n🖼️ Image attached." if img_url else ""
        await ctx.reply(
            embed=h.ok(
                f"Global tag **{name}** created.{img_line}\n"
                f"Anyone can use it: `/tag use {name}` or `{ctx.prefix}{name}`.",
                "🌐 Global Tag Created",
            ),
            ephemeral=True,
        )
        if img_warn:
            await ctx.send(embed=h.warn(img_warn, "⚠️ CDN Warning"), ephemeral=True)

    async def _do_use(
        self,
        ctx: commands.Context,
        name: str,
        *,
        dm_user: Optional[discord.Member] = None,
    ):
        name = name.lower().strip()
        tag = await db.get_tag(ctx.guild.id, name, ctx.author.id)
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
                    embed=h.err(
                        f"Couldn't DM **{dm_user.display_name}** — their DMs may be closed."
                    ),
                    ephemeral=True,
                )
            await ctx.reply(
                embed=h.ok(
                    f"Tag `{name}` sent to **{dm_user.display_name}** via DM. 📨",
                    "📨 Tag Sent",
                ),
                ephemeral=True,
            )
        else:
            # Default: post the tag in the channel (embed or plain text depending on length)
            await _send_tag(ctx, tag, name, ctx.guild.name)

    async def _do_delete(self, ctx: commands.Context, name: str):
        name = name.lower().strip()
        uid = str(ctx.author.id)

        if await db.delete_tag(ctx.guild.id, uid, name):
            return await ctx.reply(
                embed=h.ok(f"Personal tag **{name}** deleted.", "🗑️ Deleted"),
                ephemeral=True,
            )

        if ctx.author.guild_permissions.manage_messages:
            if await db.delete_tag(ctx.guild.id, "global", name):
                return await ctx.reply(
                    embed=h.ok(f"Global tag **{name}** deleted.", "🗑️ Deleted"),
                    ephemeral=True,
                )

        await ctx.reply(
            embed=h.err(
                f"Tag `{name}` not found or you don't have permission to delete it."
            ),
            ephemeral=True,
        )

    async def _show_list(self, ctx: commands.Context):
        personal_tags = await db.get_personal_tags(ctx.guild.id, ctx.author.id)
        global_tags = await db.get_global_tags(ctx.guild.id)

        prefix = self.bot.prefixes.get(str(ctx.guild.id), self.bot.default_prefix)

        e = h.embed(title="🏷️ Tag List", color=h.BLUE)
        e.description = (
            f"`{prefix}tag <name>` or `{prefix}<name>` → post it in the channel\n"
            f"`{prefix}tag + <name> <content>` → create  ·  `{prefix}tag - <name>` → delete\n"
            f"🖼️ = has image\n\u200b"
        )

        p_lines = (
            "  ".join(_list_entry(n, t) for n, t in sorted(personal_tags.items()))
            or "_None yet_"
        )
        g_lines = (
            "  ".join(_list_entry(n, t) for n, t in sorted(global_tags.items()))
            or "_None yet_"
        )

        e.add_field(
            name=f"🔒 Your Tags ({len(personal_tags)})",
            value=p_lines + "\n\u200b",
            inline=False,
        )
        e.add_field(
            name=f"🌐 Server Tags ({len(global_tags)})",
            value=g_lines + "\n\u200b",
            inline=False,
        )
        e.set_footer(text="Personal tags are only visible to you  ·  NanoBot")
        await ctx.reply(embed=e, ephemeral=True)

    # ══════════════════════════════════════════════════════════════════════════
    #  /tag export
    # ══════════════════════════════════════════════════════════════════════════
    @tag.command(
        name="export",
        description="Download all your personal tags as a JSON file you can re-import later.",
    )
    async def tag_export(self, ctx: commands.Context):
        personal = await db.get_personal_tags(ctx.guild.id, ctx.author.id)
        if not personal:
            return await ctx.reply(
                embed=h.info("You have no personal tags to export.", "📦 Export"),
                ephemeral=True,
            )

        payload = json.dumps(
            {"exported_by": str(ctx.author), "tags": personal},
            indent=2,
            ensure_ascii=False,
        )

        buf = io.BytesIO(payload.encode("utf-8"))
        file = discord.File(buf, filename=f"tags_{ctx.author.id}.json")

        await ctx.reply(
            embed=h.ok(
                f"**{len(personal)}** tag(s) exported.\n"
                "Use `/tag import` with this file to restore them in any server.",
                "📦 Tags Exported",
            ),
            file=file,
            ephemeral=True,
        )

    #  /tag import
    # ══════════════════════════════════════════════════════════════════════════
    @tag.command(
        name="import",
        description="Import personal tags from a file exported by /tag export.",
    )
    @app_commands.describe(file="The JSON file produced by /tag export.")
    async def tag_import(
        self, ctx: commands.Context, file: Optional[discord.Attachment] = None
    ):
        # Prefix fallback: check message attachments
        if file is None:
            if ctx.message.attachments:
                file = ctx.message.attachments[0]
            else:
                return await ctx.reply(
                    embed=h.err(
                        "Attach your exported tags JSON file.\n"
                        "Slash: pick a file in the `file` field.\n"
                        "Prefix: attach the file to your message.",
                        "📦 Import",
                    ),
                    ephemeral=True,
                )

        attachment = file

        # Basic sanity checks on the attachment
        if not attachment.filename.endswith(".json"):
            return await ctx.reply(
                embed=h.err(
                    "That doesn't look like a tags export file (expected a .json).",
                    "📦 Import",
                ),
                ephemeral=True,
            )
        if attachment.size > 512_000:  # 512 KB — very generous for tags
            return await ctx.reply(
                embed=h.err("That file is too large to be a tags export.", "📦 Import"),
                ephemeral=True,
            )

        # Download and parse
        try:
            raw = await attachment.read()
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return await ctx.reply(
                embed=h.err(
                    "Couldn't read that file — is it a valid tags export?", "📦 Import"
                ),
                ephemeral=True,
            )

        tags = data.get("tags") if isinstance(data, dict) else None
        if not isinstance(tags, dict) or not tags:
            return await ctx.reply(
                embed=h.err(
                    "No tags found in that file, or the format isn't recognised.",
                    "📦 Import",
                ),
                ephemeral=True,
            )

        scope = str(ctx.author.id)
        imported = 0
        skipped = 0
        bad = 0

        for name, meta in tags.items():
            # Validate each tag entry
            if not isinstance(name, str) or not isinstance(meta, dict):
                bad += 1
                continue

            content = meta.get("content")
            image_url = meta.get("image_url")

            # Must have at least one of content or image_url, and name must be valid
            if not name or len(name) > 64:
                bad += 1
                continue
            if not content and not image_url:
                bad += 1
                continue
            if content and len(content) > 2000:
                bad += 1
                continue

            # Skip if the user already has a tag with this name in this guild
            if await db.tag_exists(ctx.guild.id, scope, name):
                skipped += 1
                continue

            await db.set_tag(
                guild_id=ctx.guild.id,
                scope=scope,
                name=name,
                content=content,
                image_url=image_url,
                by_id=str(ctx.author.id),
                by_name=str(ctx.author),
            )
            imported += 1

        # Build result summary
        parts = [f"**{imported}** tag(s) imported."]
        if skipped:
            parts.append(
                f"**{skipped}** skipped (already exist — use `/tag edit` to update them)."
            )
        if bad:
            parts.append(f"**{bad}** skipped (invalid entries in the file).")

        embed = (
            h.ok("\n".join(parts), "📦 Tags Imported")
            if imported
            else h.info("\n".join(parts), "📦 Tags Imported")
        )
        await ctx.reply(embed=embed, ephemeral=True)


# ── Registration ───────────────────────────────────────────────────────────────
async def setup(bot: commands.Bot):
    await bot.add_cog(Tags(bot))
