"""
cogs/automod.py — v1.3.0
Passive auto-moderation — watches every message and enforces configurable rules.

Rules (all individually togglable, each with its own action):
  spam            — X messages from the same user within Y seconds
  invites         — Discord invite links (discord.gg / discord.com/invite)
  links           — Any external URL
  caps            — Messages above a configurable % uppercase (min length guard)
  mentions        — Too many @mentions in a single message
  badwords        — Configurable per-server word list
  regex           — Configurable per-server regex pattern list
  attachment_word — Word from a list AND N+ attachments in the same message

Actions (per rule):
  delete    — Silently delete the message
  warn      — Delete + add a formal warning (triggers warnconfig auto-kick/ban)
  timeout   — Delete + 10-minute Discord timeout
  kick      — Delete + kick member (optional DM)
  softban   — Delete + ban/unban member (optional DM; sent before ban)

Exempt channels and roles are ignored for all rules.

Logging:
  Every triggered action posts an embed to the automod log channel.
  Priority: dedicated channel set via /automod logchannel > audit log channel
  (if the automod_action event is enabled in /auditlog events).

Commands (all /automod, require Manage Server):
  /automod status               — Full config overview
  /automod enable               — Master on switch
  /automod disable              — Master off switch
  /automod logchannel [channel] — Set (or clear) the dedicated automod log channel
  /automod rule                 — Toggle a rule on/off, set its action, and optional DM to the user
  /automod spam                 — Set spam detection count + time window
  /automod caps                 — Set caps % threshold and minimum message length
  /automod mentions             — Set per-message mention limit
  /automod timeout              — Set the timeout duration for the "timeout" action (default 10 min)
  /automod attachments          — Set min attachment count for the Word + Attachment rule
  /automod badword add          — Add a word to the filter
  /automod badword remove       — Remove a word from the filter
  /automod badword list         — List all filtered words (ephemeral)
  /automod attachword add       — Add a word to the attachment-word filter
  /automod attachword remove    — Remove a word from the attachment-word filter
  /automod attachword list      — List all attachment-word filter words (ephemeral)
  /automod regex add            — Add a regex pattern to the filter
  /automod regex remove         — Remove a regex pattern (autocomplete by label)
  /automod regex list           — List all patterns with IDs and labels (ephemeral)
  /automod regex test           — Test a string against all active patterns (ephemeral)
  /automod ignore               — Add / remove exempt channels or roles
"""

import logging
import re
import time
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

from utils import db
from utils import helpers as h
from utils.checks import has_admin_perms

from .constants import ACTION_LABELS, RULE_LABELS, TIMEOUT_SECONDS, _spam_tracker
from .helpers import (
    _all_matches_regex_safe,
    _caps_percent,
    _check_spam,
    _clear_spam,
    _has_badword,
    _has_invite,
    _has_link,
    _is_risky_regex,
    _matches_regex_safe,
    _mention_count,
)
from .actions import _execute_action
from .autocomplete import (
    _action_autocomplete,
    _regex_pattern_autocomplete,
    _rule_autocomplete,
)

log = logging.getLogger("NanoBot.automod")


# ══════════════════════════════════════════════════════════════════════════════
class AutoMod(commands.Cog):
    """Passive auto-moderation rules."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Config cache: {guild_id: cfg_dict}  — refreshed on any config change
        self._cache: dict[int, dict] = {}

    async def _get_cfg(self, guild_id: int) -> dict | None:
        """Return automod config from cache, falling back to DB.

        The cached dict is augmented with two extra keys so the message
        listener never hits the database for per-guild word/pattern lists:
          _badwords        — list[str] from automod_badwords
          _regex_patterns  — list[dict] from automod_regex_patterns
        Both are invalidated together with the rest of the config via
        _invalidate(), which must be called after any mutation to these lists.
        """
        if guild_id not in self._cache:
            cfg = await db.get_automod_config(guild_id)
            if cfg:
                cfg["_badwords"] = await db.get_automod_badwords(guild_id)
                cfg["_regex_patterns"] = await db.get_automod_regex_patterns(guild_id)
                cfg["_attachment_words"] = await db.get_automod_attachment_words(
                    guild_id
                )
                self._cache[guild_id] = cfg
        return self._cache.get(guild_id)

    def _invalidate(self, guild_id: int) -> None:
        self._cache.pop(guild_id, None)

    async def cog_load(self) -> None:
        self._prune_spam_tracker.start()

    async def cog_unload(self) -> None:
        self._prune_spam_tracker.cancel()

    # ── Background: spam tracker pruning ──────────────────────────────────────
    @tasks.loop(minutes=5)
    async def _prune_spam_tracker(self) -> None:
        """Remove stale entries from the in-memory spam tracker.

        Runs every 5 minutes. Any timestamp older than 60 s (the maximum
        configurable spam window) is expired. Empty deques and empty guild
        dicts are then deleted so the dict doesn't grow unboundedly for bots
        serving many guilds over long runtimes.
        """
        cutoff = time.monotonic() - 60  # max possible spam window
        for guild_data in list(_spam_tracker.values()):
            for uid in list(guild_data.keys()):
                q = guild_data[uid]
                while q and q[0] < cutoff:
                    q.popleft()
                if not q:
                    del guild_data[uid]
        for gid in [g for g, d in list(_spam_tracker.items()) if not d]:
            del _spam_tracker[gid]

    # ── /automod group ─────────────────────────────────────────────────────────
    automod_group = app_commands.Group(
        name="automod",
        description="Configure automatic moderation rules.",
        default_permissions=discord.Permissions(manage_guild=True),
        guild_only=True,
    )

    # ── /automod status ────────────────────────────────────────────────────────
    @automod_group.command(
        name="status", description="Show the current AutoMod configuration."
    )
    @has_admin_perms()
    async def am_status(self, interaction: discord.Interaction):
        cfg = await db.get_automod_config(interaction.guild_id)

        if not cfg:
            await interaction.response.send_message(
                embed=h.info(
                    "AutoMod is **not configured** yet.\n"
                    "Use `/automod enable` to turn it on, then `/automod rule` to set up rules.",
                    "🛡️ AutoMod Status",
                ),
                ephemeral=True,
            )
            return

        status = "🟢 Enabled" if cfg["enabled"] else "🔴 Disabled"
        rules = cfg["rules"]
        tmo_secs = cfg.get("timeout_seconds", TIMEOUT_SECONDS)

        log_ch_id = cfg.get("log_channel_id")
        if log_ch_id:
            log_ch = interaction.guild.get_channel(int(log_ch_id))
            log_ch_str = log_ch.mention if log_ch else f"⚠️ Unknown (`{log_ch_id}`)"
        else:
            log_ch_str = "_not set — falls back to audit log_"

        lines = [
            f"**Status:** {status} · **Timeout duration:** {h.fmt_duration(tmo_secs)}",
            f"**Log channel:** {log_ch_str}\n",
        ]

        for key, label in RULE_LABELS.items():
            r = rules.get(key, {})
            if r.get("enabled"):
                action = ACTION_LABELS.get(
                    r.get("action", "delete"), r.get("action", "delete")
                )
                extra = ""
                if key == "spam":
                    extra = f" · {r.get('count', 5)} msgs / {r.get('seconds', 5)}s"
                elif key == "caps":
                    extra = f" · {r.get('percent', 70)}% caps, min {r.get('min_length', 10)} chars"
                elif key == "mentions":
                    extra = f" · limit {r.get('limit', 5)}"
                elif key == "badwords":
                    words = await db.get_automod_badwords(interaction.guild_id)
                    extra = f" · {len(words)} word(s)"
                elif key == "regex":
                    patterns = await db.get_automod_regex_patterns(interaction.guild_id)
                    extra = f" · {len(patterns)} pattern(s)"
                elif key == "attachment_word":
                    words = await db.get_automod_attachment_words(interaction.guild_id)
                    min_att = r.get("min_attachments", 1)
                    extra = f" · {len(words)} word(s), ≥{min_att} attachment(s)"
                if r.get("dm_message"):
                    extra += " · DM on trigger"
                lines.append(f"✅ **{label}** — {action}{extra}")
            else:
                lines.append(f"❌ ~~{label}~~")

        # Ignores
        ignore_chs = cfg.get("ignore_channels", [])
        ignore_rls = cfg.get("ignore_roles", [])
        if ignore_chs or ignore_rls:
            lines.append("")
            if ignore_chs:
                mentions = " ".join(f"<#{c}>" for c in ignore_chs)
                lines.append(f"**Exempt channels:** {mentions}")
            if ignore_rls:
                mentions = " ".join(f"<@&{r}>" for r in ignore_rls)
                lines.append(f"**Exempt roles:** {mentions}")

        e = h.embed("🛡️ AutoMod Status", "\n".join(lines), h.BLUE)
        await interaction.response.send_message(embed=e, ephemeral=True)

    # ── /automod enable ────────────────────────────────────────────────────────
    @automod_group.command(name="enable", description="Enable AutoMod for this server.")
    @has_admin_perms()
    async def am_enable(self, interaction: discord.Interaction):
        await db.set_automod_enabled(interaction.guild_id, True)
        self._invalidate(interaction.guild_id)
        await interaction.response.send_message(
            embed=h.ok(
                "AutoMod is now **enabled**.\nUse `/automod rule` to configure rules.",
                "🛡️ AutoMod On",
            ),
            ephemeral=True,
        )

    # ── /automod disable ───────────────────────────────────────────────────────
    @automod_group.command(
        name="disable", description="Disable AutoMod for this server."
    )
    @has_admin_perms()
    async def am_disable(self, interaction: discord.Interaction):
        await db.set_automod_enabled(interaction.guild_id, False)
        self._invalidate(interaction.guild_id)
        await interaction.response.send_message(
            embed=h.ok("AutoMod is now **disabled**.", "🛡️ AutoMod Off"),
            ephemeral=True,
        )

    # ── /automod logchannel ────────────────────────────────────────────────────
    @automod_group.command(
        name="logchannel",
        description="Set a dedicated channel for AutoMod action logs. Omit to clear (falls back to audit log).",
    )
    @app_commands.describe(
        channel="Channel to post AutoMod logs in. Leave blank to clear the dedicated channel."
    )
    @has_admin_perms()
    async def am_logchannel(
        self,
        interaction: discord.Interaction,
        channel: Optional[discord.TextChannel] = None,
    ):
        await db.set_automod_log_channel(
            interaction.guild_id, channel.id if channel else None
        )
        self._invalidate(interaction.guild_id)
        if channel:
            # Warn now if the bot can't actually post there — otherwise the first
            # time anyone notices is a silently-dropped AutoMod log.
            perms = channel.permissions_for(interaction.guild.me)
            missing = [
                name
                for name, has in (
                    ("View Channel", perms.view_channel),
                    ("Send Messages", perms.send_messages),
                    ("Embed Links", perms.embed_links),
                )
                if not has
            ]
            body = f"AutoMod action logs will be sent to {channel.mention}."
            if missing:
                body += (
                    "\n\n⚠️ **Heads up:** I'm missing "
                    + ", ".join(f"**{m}**" for m in missing)
                    + f" in {channel.mention}, so logs there will fail. "
                    "Grant those permissions or pick another channel."
                )
            embed = (
                h.warn(body, "🛡️ AutoMod Log Channel Set")
                if missing
                else h.ok(body, "🛡️ AutoMod Log Channel Set")
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(
                embed=h.ok(
                    "Dedicated AutoMod log channel cleared.\n"
                    "Logs will fall back to the audit log channel if the **AutoMod Action** "
                    "event is enabled there (`/auditlog events`).",
                    "🛡️ AutoMod Log Channel Cleared",
                ),
                ephemeral=True,
            )

    # ── /automod rule ──────────────────────────────────────────────────────────
    @automod_group.command(
        name="rule", description="Toggle a rule on/off and set its action."
    )
    @app_commands.describe(
        rule="Which rule to configure",
        enabled="Turn this rule on or off",
        action="What to do when the rule triggers",
        dm_message="Optional DM sent to the user when the rule triggers",
    )
    @app_commands.autocomplete(rule=_rule_autocomplete, action=_action_autocomplete)
    @has_admin_perms()
    async def am_rule(
        self,
        interaction: discord.Interaction,
        rule: str,
        enabled: bool,
        action: str = "delete",
        dm_message: Optional[str] = None,
    ):
        if rule not in RULE_LABELS:
            await interaction.response.send_message(
                embed=h.err(
                    f"Unknown rule `{rule}`. Valid rules: {', '.join(RULE_LABELS)}"
                ),
                ephemeral=True,
            )
            return
        if action not in ACTION_LABELS:
            await interaction.response.send_message(
                embed=h.err(
                    f"Unknown action `{action}`. Valid actions: {', '.join(ACTION_LABELS)}"
                ),
                ephemeral=True,
            )
            return

        updates = {"enabled": enabled, "action": action}
        if dm_message is not None:
            updates["dm_message"] = dm_message.strip() or None
        await db.set_automod_rule(interaction.guild_id, rule, **updates)
        self._invalidate(interaction.guild_id)

        state = "enabled" if enabled else "disabled"
        a_label = ACTION_LABELS[action]
        dm_note = "\nDM: enabled (sent on trigger)" if updates.get("dm_message") else ""
        await interaction.response.send_message(
            embed=h.ok(
                f"{RULE_LABELS[rule]} rule **{state}**.\nAction: {a_label}{dm_note}",
                "🛡️ Rule Updated",
            ),
            ephemeral=True,
        )

    # ── /automod spam ──────────────────────────────────────────────────────────
    @automod_group.command(
        name="spam",
        description="Set the spam detection threshold (messages per time window).",
    )
    @app_commands.describe(
        count="Number of messages that triggers the rule (e.g. 5)",
        seconds="Time window in seconds (e.g. 5)",
    )
    @has_admin_perms()
    async def am_spam(
        self,
        interaction: discord.Interaction,
        count: app_commands.Range[int, 2, 30],
        seconds: app_commands.Range[int, 2, 60],
    ):
        await db.set_automod_rule(
            interaction.guild_id, "spam", count=count, seconds=seconds
        )
        self._invalidate(interaction.guild_id)
        await interaction.response.send_message(
            embed=h.ok(
                f"Spam rule: **{count} messages** in **{seconds} seconds** → triggers.",
                "💬 Spam Config Updated",
            ),
            ephemeral=True,
        )

    # ── /automod caps ──────────────────────────────────────────────────────────
    @automod_group.command(
        name="caps",
        description="Configure the caps-abuse filter.",
    )
    @app_commands.describe(
        percent="Minimum uppercase % to trigger (e.g. 70)",
        min_length="Minimum message length in characters before checking caps (e.g. 10)",
    )
    @has_admin_perms()
    async def am_caps(
        self,
        interaction: discord.Interaction,
        percent: app_commands.Range[int, 10, 100] = 70,
        min_length: app_commands.Range[int, 5, 200] = 10,
    ):
        await db.set_automod_rule(
            interaction.guild_id,
            "caps",
            percent=percent,
            min_length=min_length,
        )
        self._invalidate(interaction.guild_id)
        await interaction.response.send_message(
            embed=h.ok(
                f"Caps rule: **{percent}%** uppercase, minimum **{min_length}** characters.",
                "🔠 Caps Config Updated",
            ),
            ephemeral=True,
        )

    # ── /automod mentions ──────────────────────────────────────────────────────
    @automod_group.command(
        name="mentions",
        description="Set the max @mentions allowed in a single message.",
    )
    @app_commands.describe(
        limit="Trigger if a message has this many mentions or more (e.g. 5)"
    )
    @has_admin_perms()
    async def am_mentions(
        self,
        interaction: discord.Interaction,
        limit: app_commands.Range[int, 2, 30] = 5,
    ):
        await db.set_automod_rule(interaction.guild_id, "mentions", limit=limit)
        self._invalidate(interaction.guild_id)
        await interaction.response.send_message(
            embed=h.ok(
                f"Mass-mention rule: triggers at **{limit}+** mentions in one message.",
                "📣 Mentions Config Updated",
            ),
            ephemeral=True,
        )

    # ── /automod timeout ──────────────────────────────────────────────────────
    @automod_group.command(
        name="timeout",
        description="Set how long the timeout action lasts (default: 10 minutes).",
    )
    @app_commands.describe(
        minutes="Timeout duration in minutes (1–10080, i.e. up to 7 days)"
    )
    @has_admin_perms()
    async def am_timeout(
        self,
        interaction: discord.Interaction,
        minutes: app_commands.Range[int, 1, 10080],
    ):
        seconds = minutes * 60
        await db.set_automod_timeout_seconds(interaction.guild_id, seconds)
        self._invalidate(interaction.guild_id)
        duration = h.fmt_duration(seconds)
        await interaction.response.send_message(
            embed=h.ok(
                f"AutoMod timeout action will now last **{duration}**.",
                "🔇 Timeout Duration Updated",
            ),
            ephemeral=True,
        )

    # ── /automod badword subgroup ──────────────────────────────────────────────
    badword_group = app_commands.Group(
        name="badword",
        description="Manage the bad-word filter list.",
        parent=automod_group,
    )

    @badword_group.command(name="add", description="Add a word to the filter.")
    @app_commands.describe(word="The word or phrase to block (case-insensitive)")
    @has_admin_perms()
    async def bw_add(self, interaction: discord.Interaction, word: str):
        word = word.lower().strip()
        if not word:
            await interaction.response.send_message(
                embed=h.err("Word cannot be empty."), ephemeral=True
            )
            return
        added = await db.add_automod_badword(interaction.guild_id, word)
        self._invalidate(interaction.guild_id)
        if added:
            await interaction.response.send_message(
                embed=h.ok(f"Added `{word}` to the bad-word filter.", "🤬 Word Added"),
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                embed=h.warn(f"`{word}` is already in the filter.", "Already Exists"),
                ephemeral=True,
            )

    @badword_group.command(name="remove", description="Remove a word from the filter.")
    @app_commands.describe(word="The word or phrase to remove")
    @has_admin_perms()
    async def bw_remove(self, interaction: discord.Interaction, word: str):
        word = word.lower().strip()
        removed = await db.remove_automod_badword(interaction.guild_id, word)
        self._invalidate(interaction.guild_id)
        if removed:
            await interaction.response.send_message(
                embed=h.ok(
                    f"Removed `{word}` from the bad-word filter.", "🤬 Word Removed"
                ),
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                embed=h.err(f"`{word}` was not in the filter."),
                ephemeral=True,
            )

    @badword_group.command(
        name="list", description="List all filtered words (shown only to you)."
    )
    @has_admin_perms()
    async def bw_list(self, interaction: discord.Interaction):
        words = await db.get_automod_badwords(interaction.guild_id)
        if not words:
            await interaction.response.send_message(
                embed=h.info("The bad-word filter is empty.", "🤬 Bad Words"),
                ephemeral=True,
            )
            return
        listed = "\n".join(f"• `{w}`" for w in sorted(words))
        e = h.embed(
            "🤬 Bad Word Filter", f"**{len(words)} word(s):**\n\n{listed}", h.BLUE
        )
        await interaction.response.send_message(embed=e, ephemeral=True)

    # ── /automod attachments ───────────────────────────────────────────────────
    @automod_group.command(
        name="attachments",
        description="Set the minimum attachment count that triggers the Word + Attachment rule.",
    )
    @app_commands.describe(
        min_attachments="Trigger if message has this many attachments or more (e.g. 4)"
    )
    @has_admin_perms()
    async def am_attachments(
        self,
        interaction: discord.Interaction,
        min_attachments: app_commands.Range[int, 1, 20] = 1,
    ):
        await db.set_automod_rule(
            interaction.guild_id, "attachment_word", min_attachments=min_attachments
        )
        self._invalidate(interaction.guild_id)
        await interaction.response.send_message(
            embed=h.ok(
                f"Word + Attachment rule triggers when a message contains a flagged word "
                f"and has **{min_attachments}+** attachment(s).",
                "📎 Attachment Threshold Updated",
            ),
            ephemeral=True,
        )

    # ── /automod attachword subgroup ───────────────────────────────────────────
    attachword_group = app_commands.Group(
        name="attachword",
        description="Manage the word list for the Word + Attachment rule.",
        parent=automod_group,
    )

    @attachword_group.command(
        name="add", description="Add a word to the attachment-word filter."
    )
    @app_commands.describe(
        word="Word or phrase to flag when sent with attachments (case-insensitive)"
    )
    @has_admin_perms()
    async def aw_add(self, interaction: discord.Interaction, word: str):
        word = word.lower().strip()
        if not word:
            await interaction.response.send_message(
                embed=h.err("Word cannot be empty."), ephemeral=True
            )
            return
        added = await db.add_automod_attachment_word(interaction.guild_id, word)
        self._invalidate(interaction.guild_id)
        if added:
            await interaction.response.send_message(
                embed=h.ok(
                    f"Added `{word}` to the attachment-word filter.", "📎 Word Added"
                ),
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                embed=h.warn(f"`{word}` is already in the filter.", "Already Exists"),
                ephemeral=True,
            )

    @attachword_group.command(
        name="remove", description="Remove a word from the attachment-word filter."
    )
    @app_commands.describe(word="Word or phrase to remove")
    @has_admin_perms()
    async def aw_remove(self, interaction: discord.Interaction, word: str):
        word = word.lower().strip()
        removed = await db.remove_automod_attachment_word(interaction.guild_id, word)
        self._invalidate(interaction.guild_id)
        if removed:
            await interaction.response.send_message(
                embed=h.ok(
                    f"Removed `{word}` from the attachment-word filter.",
                    "📎 Word Removed",
                ),
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                embed=h.err(f"`{word}` was not in the filter."),
                ephemeral=True,
            )

    @attachword_group.command(
        name="list",
        description="List all words in the attachment-word filter (shown only to you).",
    )
    @has_admin_perms()
    async def aw_list(self, interaction: discord.Interaction):
        words = await db.get_automod_attachment_words(interaction.guild_id)
        if not words:
            await interaction.response.send_message(
                embed=h.info(
                    "The attachment-word filter is empty.\nAdd words with `/automod attachword add`.",
                    "📎 Attachment Words",
                ),
                ephemeral=True,
            )
            return
        listed = "\n".join(f"• `{w}`" for w in sorted(words))
        e = h.embed(
            "📎 Attachment Word Filter",
            f"**{len(words)} word(s):**\n\n{listed}",
            h.BLUE,
        )
        await interaction.response.send_message(embed=e, ephemeral=True)

    # ── /automod regex subgroup ────────────────────────────────────────────────
    regex_group = app_commands.Group(
        name="regex",
        description="Manage the regex pattern filter.",
        parent=automod_group,
    )

    @regex_group.command(name="add", description="Add a regex pattern to the filter.")
    @app_commands.describe(
        pattern="Regex pattern to match against messages (case-insensitive)",
        label="Friendly name shown in logs and /automod status (optional but recommended)",
    )
    @has_admin_perms()
    async def rx_add(
        self,
        interaction: discord.Interaction,
        pattern: str,
        label: Optional[str] = None,
    ):
        # Validate before storing — a bad regex would silently skip in the listener
        try:
            re.compile(pattern)
        except re.error as exc:
            await interaction.response.send_message(
                embed=h.err(f"Invalid regex pattern:\n```\n{exc}\n```"),
                ephemeral=True,
            )
            return

        if _is_risky_regex(pattern):
            await interaction.response.send_message(
                embed=h.err(
                    "That pattern looks prone to catastrophic backtracking "
                    "(nested quantifiers like `(a+)+`) or is too long. Please "
                    "simplify it — such patterns can stall message processing."
                ),
                ephemeral=True,
            )
            return

        added = await db.add_automod_regex(interaction.guild_id, pattern, label)
        self._invalidate(interaction.guild_id)
        if added:
            display = f"`{pattern}`" + (f"\nLabel: **{label}**" if label else "")
            await interaction.response.send_message(
                embed=h.ok(f"Pattern added:\n{display}", "🔍 Regex Added"),
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                embed=h.warn(
                    "That pattern is already in the filter.", "Already Exists"
                ),
                ephemeral=True,
            )

    @regex_group.command(
        name="remove", description="Remove a regex pattern from the filter."
    )
    @app_commands.describe(
        pattern="Pattern to remove (use autocomplete to pick by label)"
    )
    @app_commands.autocomplete(pattern=_regex_pattern_autocomplete)
    @has_admin_perms()
    async def rx_remove(self, interaction: discord.Interaction, pattern: str):
        removed = await db.remove_automod_regex(interaction.guild_id, pattern)
        self._invalidate(interaction.guild_id)
        if removed:
            await interaction.response.send_message(
                embed=h.ok(
                    f"Removed pattern `{pattern}` from the filter.", "🔍 Regex Removed"
                ),
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                embed=h.err(f"Pattern `{pattern}` was not in the filter."),
                ephemeral=True,
            )

    @regex_group.command(
        name="list",
        description="List all regex patterns in the filter (shown only to you).",
    )
    @has_admin_perms()
    async def rx_list(self, interaction: discord.Interaction):
        patterns = await db.get_automod_regex_patterns(interaction.guild_id)
        if not patterns:
            await interaction.response.send_message(
                embed=h.info("The regex filter is empty.", "🔍 Regex Patterns"),
                ephemeral=True,
            )
            return

        lines = []
        for p in patterns:
            label_part = f" — {p['label']}" if p["label"] else ""
            lines.append(f"`#{p['id']}`  `{p['pattern']}`{label_part}")

        e = h.embed(
            "🔍 Regex Filter",
            f"**{len(patterns)} pattern(s):**\n\n" + "\n".join(lines),
            h.BLUE,
        )
        await interaction.response.send_message(embed=e, ephemeral=True)

    @regex_group.command(
        name="test",
        description="Test a string against all active regex patterns (shown only to you).",
    )
    @app_commands.describe(
        text="The text to test — paste a message here to see what matches"
    )
    @has_admin_perms()
    async def rx_test(self, interaction: discord.Interaction, text: str):
        patterns = await db.get_automod_regex_patterns(interaction.guild_id)
        if not patterns:
            await interaction.response.send_message(
                embed=h.info(
                    "No patterns configured yet. Add some with `/automod regex add`.",
                    "🔍 Regex Test",
                ),
                ephemeral=True,
            )
            return

        # Route through the same thread + wall-clock timeout the listener uses, so
        # a catastrophic-backtracking pattern can't freeze the event loop here.
        hits = await _all_matches_regex_safe(text, patterns)
        matched = [
            f"`{p['pattern']}`" + (f" ({p['label']})" if p["label"] else "")
            for p in hits
        ]

        preview = text[:200] + ("…" if len(text) > 200 else "")

        if matched:
            body = (
                f"**Input:**\n```\n{preview}\n```\n"
                f"**Matched {len(matched)} pattern(s):**\n"
                + "\n".join(f"• {m}" for m in matched)
            )
            e = h.embed("🔍 Regex Test — Match ✅", body, h.RED)
        else:
            body = f"**Input:**\n```\n{preview}\n```\nNo patterns matched."
            e = h.embed("🔍 Regex Test — No Match", body, h.GREEN)

        await interaction.response.send_message(embed=e, ephemeral=True)

    # ── /automod ignore ────────────────────────────────────────────────────────
    ignore_group = app_commands.Group(
        name="ignore",
        description="Add or remove exempt channels and roles.",
        parent=automod_group,
    )

    @ignore_group.command(name="channel", description="Toggle a channel exemption.")
    @app_commands.describe(channel="Channel to add or remove from the ignore list")
    @has_admin_perms()
    async def ig_channel(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
    ):
        toggled = await db.toggle_automod_ignore(
            interaction.guild_id, "channel", channel.id
        )
        self._invalidate(interaction.guild_id)
        if toggled == "added":
            await interaction.response.send_message(
                embed=h.ok(
                    f"{channel.mention} is now **exempt** from AutoMod.",
                    "✅ Channel Exempted",
                ),
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                embed=h.ok(
                    f"{channel.mention} is **no longer exempt** from AutoMod.",
                    "🔄 Exemption Removed",
                ),
                ephemeral=True,
            )

    @ignore_group.command(name="role", description="Toggle a role exemption.")
    @app_commands.describe(role="Role to add or remove from the ignore list")
    @has_admin_perms()
    async def ig_role(self, interaction: discord.Interaction, role: discord.Role):
        toggled = await db.toggle_automod_ignore(interaction.guild_id, "role", role.id)
        self._invalidate(interaction.guild_id)
        if toggled == "added":
            await interaction.response.send_message(
                embed=h.ok(
                    f"{role.mention} is now **exempt** from AutoMod.",
                    "✅ Role Exempted",
                ),
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                embed=h.ok(
                    f"{role.mention} is **no longer exempt** from AutoMod.",
                    "🔄 Exemption Removed",
                ),
                ephemeral=True,
            )

    # ── Message Listener ───────────────────────────────────────────────────────
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # Only process guild messages from humans
        if not message.guild or message.author.bot:
            return
        if not isinstance(message.author, discord.Member):
            return

        # Members with manage_messages are exempt (mods)
        if message.author.guild_permissions.manage_messages:
            return

        cfg = await self._get_cfg(message.guild.id)
        if not cfg or not cfg["enabled"]:
            return

        # Channel exemption
        if str(message.channel.id) in cfg.get("ignore_channels", []):
            return

        # Role exemption
        member_role_ids = {str(r.id) for r in message.author.roles}
        if member_role_ids & set(cfg.get("ignore_roles", [])):
            return

        rules = cfg["rules"]
        content = message.content or ""
        tmo = cfg.get("timeout_seconds", TIMEOUT_SECONDS)

        # ── Spam ──────────────────────────────────────────────────────────────
        r = rules.get("spam", {})
        if r.get("enabled"):
            count = r.get("count", 5)
            seconds = r.get("seconds", 5)
            if _check_spam(message.guild.id, message.author.id, count, seconds):
                _clear_spam(message.guild.id, message.author.id)
                await _execute_action(
                    message,
                    r.get("action", "warn"),
                    "spam",
                    f"{count} messages in {seconds}s",
                    tmo,
                    r.get("dm_message"),
                    self.bot,
                )
                return  # one action per message

        # Pre-compute invite check — used by both the invite and link rules to
        # avoid calling _has_invite (a regex search) twice per message.
        _invites_rule = rules.get("invites", {})
        _links_rule = rules.get("links", {})
        invite_in_msg = (
            _has_invite(content)
            if (_invites_rule.get("enabled") or _links_rule.get("enabled"))
            else False
        )

        # ── Invite links ──────────────────────────────────────────────────────
        if _invites_rule.get("enabled") and invite_in_msg:
            await _execute_action(
                message,
                _invites_rule.get("action", "delete"),
                "invites",
                "Discord invite link",
                tmo,
                _invites_rule.get("dm_message"),
                self.bot,
            )
            return

        # ── External links ────────────────────────────────────────────────────
        if _links_rule.get("enabled") and _has_link(content) and not invite_in_msg:
            await _execute_action(
                message,
                _links_rule.get("action", "delete"),
                "links",
                "External URL",
                tmo,
                _links_rule.get("dm_message"),
                self.bot,
            )
            return

        # ── Caps abuse ────────────────────────────────────────────────────────
        r = rules.get("caps", {})
        if r.get("enabled"):
            min_len = r.get("min_length", 10)
            threshold = r.get("percent", 70)
            if len(content) >= min_len and _caps_percent(content) >= threshold:
                await _execute_action(
                    message,
                    r.get("action", "warn"),
                    "caps",
                    f">{threshold}% uppercase",
                    tmo,
                    r.get("dm_message"),
                    self.bot,
                )
                return

        # ── Mass mentions ─────────────────────────────────────────────────────
        r = rules.get("mentions", {})
        if r.get("enabled"):
            limit = r.get("limit", 5)
            mention_count = _mention_count(message)
            if mention_count >= limit:
                await _execute_action(
                    message,
                    r.get("action", "warn"),
                    "mentions",
                    f"{mention_count} mentions",
                    tmo,
                    r.get("dm_message"),
                    self.bot,
                )
                return

        # ── Bad words ─────────────────────────────────────────────────────────
        r = rules.get("badwords", {})
        if r.get("enabled"):
            match = _has_badword(content, cfg.get("_badwords", []))
            if match:
                await _execute_action(
                    message,
                    r.get("action", "delete"),
                    "badwords",
                    "Filtered word",
                    tmo,
                    r.get("dm_message"),
                    self.bot,
                )
                return

        # ── Regex filter ──────────────────────────────────────────────────────
        r = rules.get("regex", {})
        if r.get("enabled"):
            match = await _matches_regex_safe(content, cfg.get("_regex_patterns", []))
            if match:
                await _execute_action(
                    message,
                    r.get("action", "delete"),
                    "regex",
                    f"Matched: {match}",
                    tmo,
                    r.get("dm_message"),
                    self.bot,
                )
                return

        # ── Word + Attachment ─────────────────────────────────────────────────
        r = rules.get("attachment_word", {})
        if r.get("enabled"):
            min_att = r.get("min_attachments", 1)
            if len(message.attachments) >= min_att:
                match = _has_badword(content, cfg.get("_attachment_words", []))
                if match:
                    await _execute_action(
                        message,
                        r.get("action", "delete"),
                        "attachment_word",
                        f"Flagged word with {len(message.attachments)} attachment(s)",
                        tmo,
                        r.get("dm_message"),
                        self.bot,
                    )
                    return


# ── Setup ──────────────────────────────────────────────────────────────────────
async def setup(bot: commands.Bot):
    await bot.add_cog(AutoMod(bot))
