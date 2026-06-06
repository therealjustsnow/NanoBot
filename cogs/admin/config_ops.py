"""DM-only config inspect/edit operations for the admin `config` command.

Lives as a mixin so the cog class stays focused on the command surface.
Secrets are masked on every echo via utils.config.mask_value.
"""

import logging
from typing import Optional

from discord.ext import commands

from utils import config as cfg_mod
from utils import helpers as h

log = logging.getLogger("NanoBot.admin")


class ConfigMixin:
    """Implements `!config show|get|set` against config.ini."""

    @staticmethod
    def _resolve_key(raw: str) -> Optional[tuple[str, str]]:
        """Accept either 'section.key' or bare 'key'. Returns (section, key) or None."""
        raw = raw.strip().lower()
        if "." in raw:
            section, _, bare = raw.partition(".")
            if (
                section in cfg_mod.SECTION_ORDER
                and cfg_mod.SECTION_MAP.get(bare) == section
            ):
                return section, bare
            return None
        if raw in cfg_mod.SECTION_MAP:
            return cfg_mod.SECTION_MAP[raw], raw
        return None

    @staticmethod
    def _display(key: str, val) -> str:
        masked = cfg_mod.mask_value(key, val)
        return "_(unset)_" if masked == "(unset)" else f"`{masked}`"

    async def _config_show(self, ctx: commands.Context):
        cfg = cfg_mod.load()
        lines: list[str] = []
        for section in cfg_mod.SECTION_ORDER:
            keys = [k for k, sec in cfg_mod.SECTION_MAP.items() if sec == section]
            if not keys:
                continue
            lines.append(f"**[{section}]**")
            for k in keys:
                val = cfg.get(k, cfg_mod.DEFAULTS.get(k))
                lines.append(f"  `{k}` = {self._display(k, val)}")
            lines.append("")
        e = h.embed(
            title="⚙️ Config (config.ini)",
            description="\n".join(lines).rstrip(),
            color=h.BLUE,
        )
        e.set_footer(
            text="Secrets are masked · `!config set <key> <value>` to change · NanoBot"
        )
        await ctx.reply(embed=e)

    async def _config_get(self, ctx: commands.Context, section: str, key: str):
        cfg = cfg_mod.load()
        val = cfg.get(key, cfg_mod.DEFAULTS.get(key))
        desc = f"**[{section}]** `{key}` = {self._display(key, val)}"
        if key in cfg_mod.SENSITIVE_KEYS:
            desc += "\n_(masked — secret)_"
        await ctx.reply(
            embed=h.embed(
                title="⚙️ Config Value",
                description=desc,
                color=h.BLUE,
            )
        )

    async def _config_set(
        self, ctx: commands.Context, section: str, key: str, raw_value: str
    ):
        # Coerce the string through the same pipeline used by config.load()
        coerced = cfg_mod._coerce(key, raw_value)

        # Block obviously bad values before touching disk.
        cfg = cfg_mod.load()
        cfg[key] = coerced
        issues = [i for i in cfg_mod.validate(cfg) if i.field == key and i.fatal]
        if issues:
            return await ctx.reply(
                embed=h.err(
                    f"Rejected — `{key}` failed validation: {issues[0].message}"
                )
            )

        try:
            cfg_mod.set_value(key, coerced)
        except Exception as exc:
            log.error(f"config set {key} failed: {exc}", exc_info=exc)
            return await ctx.reply(embed=h.err(f"Could not write config.ini: {exc}"))

        if hasattr(self.bot, "reload_config"):
            self.bot.reload_config()

        log.info(f"config set: [{section}] {key} changed by {h.user_log(ctx.author)}")
        display = self._display(key, coerced)
        await ctx.reply(
            embed=h.ok(
                f"**[{section}]** `{key}` = {display}\n"
                "Saved to `config.ini` and live now.",
                "⚙️ Config Updated",
            )
        )
