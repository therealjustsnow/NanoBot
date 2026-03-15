"""
cogs/eli5.py
ELI5 — Explain It Like I'm 5.

Sends a topic to the Anthropic API and returns a plain-English
explanation short enough to read comfortably on mobile.

──────────────────────────────────────────────────────
Commands
──────────────────────────────────────────────────────
  !eli5 <topic>       → explain topic (prefix)
  /eli5 <topic>       → explain topic (slash)

Config
──────────────────────────────────────────────────────
  Requires ANTHROPIC_API_KEY env var  OR  "anthropic_api_key" in config.json.
  If the key is absent the command fails gracefully with a clear error.

Rate limiting
──────────────────────────────────────────────────────
  Per-user cooldown: 1 use per 15 seconds.
  Per-guild bucket:  shared across all users, 5 uses per 10 seconds.
"""

import logging
import os

import anthropic
import discord
from discord import app_commands
from discord.ext import commands

from utils import helpers as h

log = logging.getLogger("NanoBot.eli5")

_MODEL = "claude-haiku-4-5-20251001"  # fast + cheap — ideal for one-shot explanations
_MAX_TOKENS = 300  # ~200 words; plenty for an ELI5, keeps mobile output tight

_SYSTEM_PROMPT = (
    "You explain things like the person asking is 5 years old. "
    "Use short sentences. Avoid jargon. Use a simple analogy if it helps. "
    "Never exceed 200 words. Do not add headers or bullet points — just talk naturally."
)


def _get_api_key(bot: commands.Bot) -> str | None:
    """Env var takes priority; fall back to config.json value stored on the bot."""
    return os.getenv("ANTHROPIC_API_KEY") or getattr(bot, "anthropic_api_key", None)


# ══════════════════════════════════════════════════════════════════════════════
class ELI5(commands.Cog):
    """Explain anything in plain language using Claude."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ── /eli5 ─────────────────────────────────────────────────────────────────
    @commands.hybrid_command(
        name="eli5",
        description="Explain a topic like I'm 5 years old.",
    )
    @app_commands.describe(topic="The thing you want explained simply.")
    @commands.cooldown(rate=1, per=15, type=commands.BucketType.user)
    async def eli5(self, ctx: commands.Context, *, topic: str):
        """
        Explain a topic in plain, simple language.

        Usage:
          !eli5 black holes
          /eli5 topic:why the sky is blue
        """
        topic = topic.strip()
        if not topic:
            return await ctx.reply(
                embed=h.err(
                    "Give me something to explain!\nExample: `!eli5 black holes`"
                ),
                ephemeral=True,
            )
        if len(topic) > 300:
            return await ctx.reply(
                embed=h.err("Topic is too long — keep it under 300 characters."),
                ephemeral=True,
            )

        api_key = _get_api_key(self.bot)
        if not api_key:
            return await ctx.reply(
                embed=h.err(
                    "No Anthropic API key configured.\n"
                    "Add `ANTHROPIC_API_KEY` to your environment or `config.json`.",
                    "⚙️ Not Configured",
                ),
                ephemeral=True,
            )

        # Defer early — API calls can take 2–5 s; Discord times out at 3 s
        await ctx.defer()

        try:
            client = anthropic.AsyncAnthropic(api_key=api_key)
            message = await client.messages.create(
                model=_MODEL,
                max_tokens=_MAX_TOKENS,
                system=_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": f"Explain: {topic}"}],
            )
            explanation = message.content[0].text.strip()
        except anthropic.AuthenticationError:
            log.error("ELI5: Invalid Anthropic API key")
            return await ctx.reply(
                embed=h.err(
                    "The Anthropic API key is invalid. Check your config.",
                    "🔑 Auth Error",
                )
            )
        except anthropic.RateLimitError:
            log.warning("ELI5: Anthropic rate limit hit")
            return await ctx.reply(
                embed=h.warn(
                    "Claude is a bit overwhelmed right now. Try again in a moment.",
                    "⏱️ Rate Limited",
                )
            )
        except anthropic.APIError as exc:
            log.error(f"ELI5: Anthropic API error: {exc}")
            return await ctx.reply(
                embed=h.err(
                    "Something went wrong talking to Claude. Try again shortly.",
                    "💥 API Error",
                )
            )

        e = discord.Embed(
            title=f"🧒 ELI5 — {topic[:80]}{'…' if len(topic) > 80 else ''}",
            description=explanation,
            color=h.BLUE,
        )
        e.set_footer(text=f"Asked by {ctx.author.display_name}  ·  NanoBot")
        await ctx.reply(embed=e)
        log.info(f"ELI5: '{topic}' for {ctx.author} in {ctx.guild}")


# ── Registration ───────────────────────────────────────────────────────────────
async def setup(bot: commands.Bot):
    await bot.add_cog(ELI5(bot))
