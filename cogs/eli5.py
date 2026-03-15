"""
cogs/eli5.py
ELI5 — Explain It Like I'm 5.

Sends a topic to Groq (Llama 3.1 8B) and returns a plain-English
explanation short enough to read comfortably on mobile.

──────────────────────────────────────────────────────
Commands
──────────────────────────────────────────────────────
  !eli5 <topic>       → explain topic (prefix)
  /eli5 <topic>       → explain topic (slash)

Config
──────────────────────────────────────────────────────
  Requires GROQ_API_KEY env var  OR  "groq_api_key" in config.json.
  Get a free key at: https://console.groq.com
  Free tier: 14,400 requests/day, 30 RPM — very generous.

Rate limiting
──────────────────────────────────────────────────────
  Per-user cooldown: 1 use per 15 seconds.
"""

import logging
import os

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

from utils import helpers as h

log = logging.getLogger("NanoBot.eli5")

_GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
_MODEL = "llama-3.1-8b-instant"
_MAX_TOKENS = 300  # ~200 words; tight enough for mobile, enough for a clear explanation

_SYSTEM_PROMPT = (
    "You explain things like the person asking is 5 years old. "
    "Use short sentences. Avoid jargon. Use a simple analogy if it helps. "
    "Never exceed 200 words. Do not use headers or bullet points — just talk naturally."
)


def _get_api_key(bot: commands.Bot) -> str | None:
    """Env var takes priority; fall back to config.json value stored on the bot."""
    return os.getenv("GROQ_API_KEY") or getattr(bot, "groq_api_key", None)


# ══════════════════════════════════════════════════════════════════════════════
class ELI5(commands.Cog):
    """Explain anything in plain language using Groq / Llama 3."""

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
                    "No Groq API key configured.\n"
                    "Add `GROQ_API_KEY` to your environment or `config.json`.\n"
                    "Get a free key at: https://console.groq.com",
                    "⚙️ Not Configured",
                ),
                ephemeral=True,
            )

        # Defer early — API calls can take 1–3 s; Discord times out at 3 s
        await ctx.defer()

        payload = {
            "model": _MODEL,
            "max_tokens": _MAX_TOKENS,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": f"Explain: {topic}"},
            ],
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    _GROQ_URL, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=15)
                ) as resp:
                    data = await resp.json()

                    if resp.status == 401:
                        log.error("ELI5: Invalid Groq API key")
                        return await ctx.reply(
                            embed=h.err("The Groq API key is invalid. Check your config.", "🔑 Auth Error")
                        )
                    if resp.status == 429:
                        log.warning("ELI5: Groq rate limit hit")
                        return await ctx.reply(
                            embed=h.warn("Rate limit hit. Try again in a moment.", "⏱️ Rate Limited")
                        )
                    if resp.status != 200:
                        log.error(f"ELI5: Groq returned {resp.status}: {data}")
                        return await ctx.reply(
                            embed=h.err("Something went wrong. Try again shortly.", "💥 API Error")
                        )

                    explanation = data["choices"][0]["message"]["content"].strip()

        except aiohttp.ClientError as exc:
            log.error(f"ELI5: Network error: {exc}")
            return await ctx.reply(
                embed=h.err("Couldn't reach Groq. Check your connection and try again.", "🌐 Network Error")
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
