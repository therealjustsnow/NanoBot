"""
cogs/eli5.py
ELI5 — Explain It Like I'm 5.

Sends a topic to Google Gemini Flash and returns a plain-English
explanation short enough to read comfortably on mobile.

──────────────────────────────────────────────────────
Commands
──────────────────────────────────────────────────────
  !eli5 <topic>       → explain topic (prefix)
  /eli5 <topic>       → explain topic (slash)

Config
──────────────────────────────────────────────────────
  Requires GEMINI_API_KEY env var  OR  "gemini_api_key" in config.json.
  Get a free key at: https://aistudio.google.com/apikey
  Free tier: 1,500 requests/day, 15 RPM — plenty for a bot command.

Rate limiting
──────────────────────────────────────────────────────
  Per-user cooldown: 1 use per 15 seconds.
"""

import logging
import os

import discord
import google.generativeai as genai
from google.api_core.exceptions import GoogleAPIError, ResourceExhausted, Unauthenticated
from discord import app_commands
from discord.ext import commands

from utils import helpers as h

log = logging.getLogger("NanoBot.eli5")

_MODEL = "gemini-2.0-flash"
_MAX_TOKENS = 300  # ~200 words; tight enough for mobile, enough for a clear explanation

_SYSTEM_PROMPT = (
    "You explain things like the person asking is 5 years old. "
    "Use short sentences. Avoid jargon. Use a simple analogy if it helps. "
    "Never exceed 200 words. Do not use headers or bullet points — just talk naturally."
)


def _get_api_key(bot: commands.Bot) -> str | None:
    """Env var takes priority; fall back to config.json value stored on the bot."""
    return os.getenv("GEMINI_API_KEY") or getattr(bot, "gemini_api_key", None)


# ══════════════════════════════════════════════════════════════════════════════
class ELI5(commands.Cog):
    """Explain anything in plain language using Google Gemini Flash."""

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
                embed=h.err("Give me something to explain!\nExample: `!eli5 black holes`"),
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
                    "No Gemini API key configured.\n"
                    "Add `GEMINI_API_KEY` to your environment or `config.json`.\n"
                    "Get a free key at: https://aistudio.google.com/apikey",
                    "⚙️ Not Configured",
                ),
                ephemeral=True,
            )

        # Defer early — API calls can take 2–5 s; Discord times out at 3 s
        await ctx.defer()

        try:
            genai.configure(api_key=api_key)
            model = genai.GenerativeModel(
                model_name=_MODEL,
                system_instruction=_SYSTEM_PROMPT,
                generation_config=genai.GenerationConfig(max_output_tokens=_MAX_TOKENS),
            )
            response = await model.generate_content_async(f"Explain: {topic}")
            explanation = response.text.strip()
        except Unauthenticated:
            log.error("ELI5: Invalid Gemini API key")
            return await ctx.reply(
                embed=h.err(
                    "The Gemini API key is invalid. Check your config.",
                    "🔑 Auth Error",
                )
            )
        except ResourceExhausted:
            log.warning("ELI5: Gemini rate limit hit")
            return await ctx.reply(
                embed=h.warn(
                    "The free tier rate limit was hit. Try again in a minute.",
                    "⏱️ Rate Limited",
                )
            )
        except GoogleAPIError as exc:
            log.error(f"ELI5: Gemini API error: {exc}")
            return await ctx.reply(
                embed=h.err(
                    "Something went wrong talking to Gemini. Try again shortly.",
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
