"""
cogs/fun/cog.py
Fun commands -- social interactions, solo reactions, ship, 8-ball, fml,
thigh, would-you-rather.

GIFs sourced from nekos.best (no API key required).
Thigh images sourced from Nekosia API (no API key required).
WYR questions from three sources (scraped/generated daily):
  1. truthordarebot.xyz API (PG + PG13 ratings, separate pools)
  2. Kaggle dataset one-time seed (~2700 questions on first run)
  3. Groq LLM generation (~20 fresh questions per day, if API key set)
FML stories cached from fmylife.com (scraped daily).
All image/GIF URLs cached in cache_db and served from cache.
Falls back to live API if cache is empty for a given endpoint.

Slash (1 top-level slot, 8 subcommands):
  /fun social <action> [user]   -- autocomplete picker, 26 social actions
  /fun react <action>           -- autocomplete picker, 33 solo reactions
  /fun ship <user1> <user2>
  /fun 8ball <question>
  /fun fml
  /fun thigh
  /fun wyr [duration]
  /fun rps [user]

Prefix (flat):
  !hug, !slap, !cry, !dance, !ship, !8ball, !fml, !thigh, !wyr, !rps, etc.
"""

import asyncio
import logging
import random
import time
from typing import Optional

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks

from utils import cache_db
from utils import helpers as h

from .constants import (
    _THIGH_TAGS,
    _PINK,
    _FML_BLUE,
    _MAX_CONSEC_FAILS,
)
from .actions import (
    _SOCIAL_ACTIONS,
    _REACT_ACTIONS,
    _8BALL_POSITIVE,
    _8BALL_NEUTRAL,
    _8BALL_NEGATIVE,
    _ALL_NEKOS_ENDPOINTS,
)
from .helpers import (
    _scrape_cfg,
    _ship_score,
    _ship_name,
    _progress_bar,
    _ship_verdict,
    _split_wyr,
    _parse_duration,
)
from .sources import (
    _fetch_nekos_batch,
    _fetch_nekosia_single,
    _scrape_fml_bulk,
    _scrape_wyr_bulk,
    _seed_kaggle_wyr,
    _generate_wyr_groq,
    _get_gif,
    _get_nekosia,
)
from .views import WyrView, RpsView

log = logging.getLogger("NanoBot.fun")


class Fun(commands.Cog):
    """Fun social interaction and reaction commands."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._session: aiohttp.ClientSession | None = None

    async def cog_load(self):
        self._session = aiohttp.ClientSession()
        self._dynamic_cmds: list[commands.Command] = []
        self._scrape_lock = asyncio.Lock()
        self._register_prefix_commands()
        self._scrape_loop.start()
        self._revalidate_loop.start()

    async def cog_unload(self):
        self._scrape_loop.cancel()
        self._revalidate_loop.cancel()
        for cmd in self._dynamic_cmds:
            self.bot.remove_command(cmd.name)
        if self._session and not self._session.closed:
            await self._session.close()

    # ══════════════════════════════════════════════════════════════════════════
    #  Daily content scraper -- fills cache_db
    # ══════════════════════════════════════════════════════════════════════════

    @tasks.loop(hours=24)
    async def _scrape_loop(self):
        await self._run_scrape()

    async def _run_scrape(self) -> bool:
        """Run full content scrape. Returns False if already running."""
        if self._scrape_lock.locked():
            log.warning("Scrape already in progress, skipping.")
            return False
        if not self._session or self._session.closed:
            return False

        async with self._scrape_lock:
            start = time.monotonic()

            # ── FML ───────────────────────────────────────────────────────────
            try:
                fml_stories = await _scrape_fml_bulk(
                    self._session, _scrape_cfg(self.bot, "fml_pages_per_scrape")
                )
                if fml_stories:
                    added = await cache_db.add_fml_stories(fml_stories)
                    total = await cache_db.count_fml()
                    log.info(
                        f"FML scrape: {len(fml_stories)} scraped, "
                        f"{added} new, {total} total"
                    )
                else:
                    log.warning("FML scrape: 0 stories (site may be down)")
            except Exception as exc:
                log.error(f"FML scrape error: {exc}")

            # ── WYR (truthordarebot API -- PG + PG13) ─────────────────────────
            try:
                wyr_questions = await _scrape_wyr_bulk(
                    self._session, _scrape_cfg(self.bot, "wyr_requests_per_scrape")
                )
                if wyr_questions:
                    added = await cache_db.add_wyr_questions(wyr_questions)
                    total = await cache_db.count_wyr()
                    log.info(
                        f"WYR scrape: {len(wyr_questions)} fetched, "
                        f"{added} new, {total} total"
                    )
                else:
                    log.warning("WYR scrape: 0 questions (API may be down)")
            except Exception as exc:
                log.error(f"WYR scrape error: {exc}")

            # ── WYR Kaggle seed (one-time) ────────────────────────────────────
            kaggle_done = await cache_db.get_meta("kaggle_wyr_seeded")
            if not kaggle_done:
                try:
                    kaggle_qs = await _seed_kaggle_wyr(self._session)
                    if kaggle_qs:
                        added = await cache_db.add_wyr_questions(kaggle_qs)
                        await cache_db.set_meta("kaggle_wyr_seeded", "1")
                        total = await cache_db.count_wyr()
                        log.info(
                            f"WYR Kaggle seed: {len(kaggle_qs)} parsed, "
                            f"{added} new, {total} total"
                        )
                    else:
                        log.warning("WYR Kaggle seed: 0 questions (download failed)")
                except Exception as exc:
                    log.error(f"WYR Kaggle seed error: {exc}")

            # ── WYR Groq generation ───────────────────────────────────────────
            groq_key = getattr(self.bot, "groq_api_key", None)
            if groq_key:
                try:
                    groq_qs = await _generate_wyr_groq(
                        self._session,
                        groq_key,
                        _scrape_cfg(self.bot, "groq_wyr_system"),
                    )
                    if groq_qs:
                        added = await cache_db.add_wyr_questions(groq_qs)
                        total = await cache_db.count_wyr()
                        log.info(
                            f"WYR Groq: {len(groq_qs)} generated, "
                            f"{added} new, {total} total"
                        )
                except Exception as exc:
                    log.error(f"WYR Groq generation error: {exc}")
            else:
                log.debug("WYR Groq: no API key, skipping generation")

            # ── nekos.best (GIFs + static images) ─────────────────────────────
            nekos_per_endpoint = _scrape_cfg(self.bot, "nekos_per_endpoint")
            nekos_total_added = 0
            for ep in _ALL_NEKOS_ENDPOINTS:
                try:
                    results = await _fetch_nekos_batch(
                        self._session, ep, nekos_per_endpoint
                    )
                    if results:
                        img_dicts = [
                            {
                                "url": r["url"],
                                "source_url": r.get("source_url"),
                                "artist": r.get("artist_name"),
                            }
                            for r in results
                        ]
                        added = await cache_db.add_images("nekos", ep, img_dicts)
                        nekos_total_added += added
                except Exception as exc:
                    log.debug(f"nekos.best scrape error for '{ep}': {exc}")
                await asyncio.sleep(0.3)

            nekos_total = await cache_db.count_images("nekos")
            log.info(
                f"nekos.best scrape: {len(_ALL_NEKOS_ENDPOINTS)} endpoints, "
                f"{nekos_total_added} new, {nekos_total} total"
            )

            # ── Nekosia (thigh tags) ──────────────────────────────────────────
            nekosia_per_tag = _scrape_cfg(self.bot, "nekosia_per_tag")
            nekosia_total_added = 0
            for tag in _THIGH_TAGS:
                consecutive_fail = 0
                for _ in range(nekosia_per_tag):
                    try:
                        img, src = await _fetch_nekosia_single(self._session, tag)
                        if img:
                            consecutive_fail = 0
                            added = await cache_db.add_images(
                                "nekosia",
                                tag,
                                [{"url": img, "source_url": src}],
                            )
                            nekosia_total_added += added
                        else:
                            consecutive_fail += 1
                    except Exception as exc:
                        consecutive_fail += 1
                        log.debug(f"Nekosia scrape error for '{tag}': {exc}")
                    if consecutive_fail >= _MAX_CONSEC_FAILS:
                        log.warning(
                            f"Nekosia scrape ('{tag}'): {consecutive_fail} failures "
                            f"in a row — skipping rest of this tag"
                        )
                        break
                    await asyncio.sleep(0.5)

            nekosia_total = await cache_db.count_images("nekosia")
            log.info(
                f"Nekosia scrape: {len(_THIGH_TAGS)} tags, "
                f"{nekosia_total_added} new, {nekosia_total} total"
            )

            elapsed = time.monotonic() - start
            await cache_db.set_meta("last_scrape", str(time.time()))
            log.info(f"Daily scrape complete in {elapsed:.0f}s")
            return True

    @_scrape_loop.before_loop
    async def _before_scrape(self):
        """Wait for bot ready, log cache state."""
        await self.bot.wait_until_ready()
        fml_count = await cache_db.count_fml()
        wyr_count = await cache_db.count_wyr()
        img_count = await cache_db.count_images()
        if fml_count == 0 or wyr_count == 0 or img_count == 0:
            log.info(
                f"Cache sparse (FML={fml_count}, WYR={wyr_count}, "
                f"images={img_count}), initial scrape starting..."
            )
        else:
            log.info(
                f"Cache loaded: {fml_count} FML, {wyr_count} WYR, "
                f"{img_count} images"
            )

    # ══════════════════════════════════════════════════════════════════════════
    #  URL revalidation -- prune dead image URLs every 6 hours
    # ══════════════════════════════════════════════════════════════════════════

    @tasks.loop(hours=6)
    async def _revalidate_loop(self):
        """HEAD-check stale image URLs and remove dead ones."""
        if not self._session or self._session.closed:
            return

        stale = await cache_db.get_stale_images(
            max_age_seconds=_scrape_cfg(self.bot, "revalidate_age"),
            limit=_scrape_cfg(self.bot, "revalidate_batch"),
        )
        if not stale:
            return

        removed = 0
        verified = 0
        for entry in stale:
            try:
                async with self._session.head(
                    entry["url"],
                    timeout=aiohttp.ClientTimeout(total=5),
                    allow_redirects=True,
                ) as resp:
                    if resp.status in (200, 301, 302, 304):
                        await cache_db.mark_verified(entry["hash"])
                        verified += 1
                    else:
                        await cache_db.remove_image(entry["hash"])
                        removed += 1
            except Exception:
                # Network error -- don't remove, just skip this round
                pass
            await asyncio.sleep(0.2)

        if removed or verified:
            log.info(
                f"Revalidation: {verified} verified, {removed} removed "
                f"(of {len(stale)} checked)"
            )

    @_revalidate_loop.before_loop
    async def _before_revalidate(self):
        await self.bot.wait_until_ready()
        # Stagger so it doesn't overlap with the scrape loop start
        await asyncio.sleep(300)

    # ── Shared embed builders ─────────────────────────────────────────────────

    async def _action_embed(self, guild_me, author, target, data, *, color=None):
        """Build an embed for a social action using a _SOCIAL_ACTIONS entry."""
        c = color or data.get("color", _PINK)
        if target is None or target == author:
            desc = data["self_msg"]
        elif target == guild_me:
            desc = data["bot_msg"]
        else:
            desc = (
                data["action_msg"]
                .replace("{author}", f"**{author.display_name}**")
                .replace("{target}", target.mention)
            )
        e = discord.Embed(description=desc, color=c)
        gif = await _get_gif(self._session, data["endpoint"])
        if gif:
            e.set_image(url=gif)
        e.set_footer(text="NanoBot Fun")
        return e

    async def _react_embed(self, author, data, *, color=None):
        """Build an embed for a solo reaction using a _REACT_ACTIONS entry."""
        c = color or data.get("color", _PINK)
        e = discord.Embed(
            description=data["msg"].replace("{author}", f"**{author.display_name}**"),
            color=c,
        )
        gif = await _get_gif(self._session, data["endpoint"])
        if gif:
            e.set_image(url=gif)
        e.set_footer(text="NanoBot Fun")
        return e

    # ══════════════════════════════════════════════════════════════════════════
    #  SLASH: /fun group  (8 subcommands, 1 top-level slot)
    # ══════════════════════════════════════════════════════════════════════════

    fun_group = app_commands.Group(
        name="fun",
        description="Fun commands -- social interactions, reactions, ship, 8-ball, fml!",
        guild_only=True,
    )

    # ── /fun social ───────────────────────────────────────────────────────────

    @fun_group.command(
        name="social",
        description="Social interactions -- hug, kiss, slap, and more!",
    )
    @app_commands.describe(action="What to do", user="Who to target")
    async def s_social(
        self,
        i: discord.Interaction,
        action: str,
        user: Optional[discord.Member] = None,
    ):
        data = _SOCIAL_ACTIONS.get(action.lower())
        if not data:
            return await i.response.send_message(
                "Unknown action. Pick one from the list!", ephemeral=True
            )
        e = await self._action_embed(i.guild.me, i.user, user, data)
        await i.response.send_message(embed=e)

    @s_social.autocomplete("action")
    async def _social_ac(self, i: discord.Interaction, current: str):
        q = current.lower()
        return [
            app_commands.Choice(name=v["label"], value=k)
            for k, v in _SOCIAL_ACTIONS.items()
            if q in k or q in v["label"]
        ][:25]

    # ── /fun react ────────────────────────────────────────────────────────────

    @fun_group.command(
        name="react",
        description="Express yourself -- cry, dance, laugh, and more!",
    )
    @app_commands.describe(action="How to react")
    async def s_react(self, i: discord.Interaction, action: str):
        data = _REACT_ACTIONS.get(action.lower())
        if not data:
            return await i.response.send_message(
                "Unknown reaction. Pick one from the list!", ephemeral=True
            )
        e = await self._react_embed(i.user, data)
        await i.response.send_message(embed=e)

    @s_react.autocomplete("action")
    async def _react_ac(self, i: discord.Interaction, current: str):
        q = current.lower()
        return [
            app_commands.Choice(name=v["label"], value=k)
            for k, v in _REACT_ACTIONS.items()
            if q in k or q in v["label"]
        ][:25]

    # ── /fun ship ─────────────────────────────────────────────────────────────

    @fun_group.command(name="ship", description="Ship two users! \U0001f495")
    @app_commands.describe(user1="First user", user2="Second user")
    async def s_ship(
        self, i: discord.Interaction, user1: discord.Member, user2: discord.Member
    ):
        if user1 == user2:
            e = discord.Embed(
                title="\U0001f495 Ship",
                description=(
                    f"**{user1.display_name}** + **{user2.display_name}**\n\n"
                    "Loving yourself is valid, but this is next level. \U0001f4af"
                ),
                color=_PINK,
            )
            e.set_footer(text="NanoBot Fun \u00b7 Results are totally scientific")
            return await i.response.send_message(embed=e)
        if i.guild.me in (user1, user2):
            e = discord.Embed(
                title="\U0001f495 Ship",
                description="I'm flattered, but I'm in a committed relationship with my codebase. \U0001f4be",
                color=_PINK,
            )
            e.set_footer(text="NanoBot Fun \u00b7 Results are totally scientific")
            return await i.response.send_message(embed=e)
        score = _ship_score(user1.id, user2.id)
        name = _ship_name(user1.display_name, user2.display_name)
        e = discord.Embed(title=f"\U0001f495 {name}", color=_PINK)
        e.add_field(
            name=f"{user1.display_name} \u00d7 {user2.display_name}",
            value=f"{_progress_bar(score)} **{score}%**\n{_ship_verdict(score)}",
            inline=False,
        )
        e.set_footer(text="NanoBot Fun \u00b7 Results are totally scientific")
        await i.response.send_message(embed=e)

    # ── /fun 8ball ────────────────────────────────────────────────────────────

    @fun_group.command(name="8ball", description="Ask the magic 8-ball. \U0001f3b1")
    @app_commands.describe(question="Your yes/no question")
    async def s_8ball(self, i: discord.Interaction, question: str):
        pool = random.choice([_8BALL_POSITIVE, _8BALL_NEUTRAL, _8BALL_NEGATIVE])
        answer = random.choice(pool)
        color = (
            h.GREEN
            if pool is _8BALL_POSITIVE
            else (h.YELLOW if pool is _8BALL_NEUTRAL else h.RED)
        )
        e = discord.Embed(title="\U0001f3b1 Magic 8-Ball", color=color)
        e.add_field(name="Question", value=question[:256], inline=False)
        e.add_field(name="Answer", value=f"**{answer}**", inline=False)
        e.set_footer(text="NanoBot Fun")
        await i.response.send_message(embed=e)

    # ── /fun fml ──────────────────────────────────────────────────────────

    @fun_group.command(
        name="fml", description="Get a random FML story from fmylife.com"
    )
    async def s_fml(self, i: discord.Interaction):
        story = await cache_db.get_random_fml()
        if not story:
            return await i.response.send_message(
                "No FML stories cached yet -- try again in a few minutes!",
                ephemeral=True,
            )
        e = discord.Embed(description=story, color=_FML_BLUE)
        e.set_footer(text="NanoBot Fun \u00b7 fmylife.com")
        await i.response.send_message(embed=e)

    # ── /fun thigh ─────────────────────────────────────────────────────────

    @fun_group.command(name="thigh", description="Random anime thigh pic (SFW)")
    async def s_thigh(self, i: discord.Interaction):
        tag = random.choice(_THIGH_TAGS)
        img, src = await _get_nekosia(self._session, tag)
        if not img:
            return await i.response.send_message(
                "No thigh images cached yet -- try again in a few minutes!",
                ephemeral=True,
            )
        e = discord.Embed(color=_PINK)
        e.set_image(url=img)
        if src:
            e.description = f"[\U0001f517 Source]({src})"
        e.set_footer(text="NanoBot Fun \u00b7 nekosia.cat")
        await i.response.send_message(embed=e)

    # ── /fun wyr ───────────────────────────────────────────────────────────

    @fun_group.command(name="wyr", description="Would You Rather -- vote with buttons!")
    @app_commands.describe(
        duration="How long voting lasts (e.g. 30m, 2h, 1h30m). Default: 1h"
    )
    async def s_wyr(self, i: discord.Interaction, duration: str | None = None):
        secs = _parse_duration(duration)
        question = await cache_db.get_random_wyr()
        if not question:
            return await i.response.send_message(
                "No WYR questions cached yet -- try again in a few minutes!",
                ephemeral=True,
            )
        opt_a, opt_b = _split_wyr(question)
        view = WyrView(opt_a, opt_b, duration=secs)
        await i.response.send_message(embed=view._voting_embed(), view=view)
        view.message = await i.original_response()

    # ── /fun rps ───────────────────────────────────────────────────────────

    @fun_group.command(
        name="rps", description="Rock Paper Scissors -- challenge someone or the bot!"
    )
    @app_commands.describe(user="Who to challenge (leave empty to play vs the bot)")
    async def s_rps(
        self, i: discord.Interaction, user: Optional[discord.Member] = None
    ):
        if user and user == i.user:
            e = discord.Embed(
                title="\u270a\u270b\u2702\ufe0f Rock Paper Scissors",
                description="You can't challenge yourself! Try challenging someone else or the bot.",
                color=h.YELLOW,
            )
            e.set_footer(text="NanoBot Fun")
            return await i.response.send_message(embed=e, ephemeral=True)

        is_bot = user is None or user == i.guild.me
        opponent = None if is_bot else user
        view = RpsView(i.user, opponent, is_bot=is_bot)
        await i.response.send_message(embed=view._waiting_embed(), view=view)
        view.message = await i.original_response()

    # ══════════════════════════════════════════════════════════════════════════
    #  PREFIX: flat commands  (!hug, !cry, !ship, !8ball, etc.)
    # ══════════════════════════════════════════════════════════════════════════

    def _register_prefix_commands(self):
        """Build and register all factory prefix commands on the bot."""
        cog = self

        for action, data in _SOCIAL_ACTIONS.items():
            name = "funkick" if action == "kick" else action
            aliases = ["fk"] if action == "kick" else []
            extras = {
                "category": "\U0001f389 Fun",
                "short": data["short"],
                "usage": f"{name} [user]",
                "desc": data["short"] + " with a random anime GIF.",
                "args": [("user", "Who to target (optional)")],
                "perms": "None",
                "example": f"!{name} @Snow",
            }

            def _make_social(name, aliases, extras, data):
                @commands.command(name=name, aliases=aliases, extras=extras)
                @commands.cooldown(1, 3, commands.BucketType.user)
                async def social_cmd(ctx, user: Optional[discord.Member] = None):
                    e = await cog._action_embed(ctx.guild.me, ctx.author, user, data)
                    await ctx.reply(embed=e)

                return social_cmd

            social_cmd = _make_social(name, aliases, extras, data)
            self.bot.add_command(social_cmd)
            self._dynamic_cmds.append(social_cmd)

        for action, data in _REACT_ACTIONS.items():
            extras = {
                "category": "\U0001f604 React",
                "short": data["short"],
                "usage": action,
                "desc": data["short"] + " with a random anime GIF.",
                "args": [],
                "perms": "None",
                "example": f"!{action}",
            }

            def _make_react(action, extras, data):
                @commands.command(name=action, extras=extras)
                @commands.cooldown(1, 3, commands.BucketType.user)
                async def react_cmd(ctx):
                    e = await cog._react_embed(ctx.author, data)
                    await ctx.reply(embed=e)

                return react_cmd

            react_cmd = _make_react(action, extras, data)
            self.bot.add_command(react_cmd)
            self._dynamic_cmds.append(react_cmd)

    # ── ship & 8ball prefix ───────────────────────────────────────────────────

    @commands.command(
        name="ship",
        extras={
            "category": "\U0001f389 Fun",
            "short": "Ship two users",
            "usage": "ship <user1> <user2>",
            "desc": "Smashes two users' names together and gives a compatibility score.",
            "args": [("user1", "First user"), ("user2", "Second user")],
            "perms": "None",
            "example": "{prefix}ship @Snow @Nano",
        },
    )
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def pfx_ship(self, ctx, user1: discord.Member, user2: discord.Member):
        if user1 == user2:
            e = discord.Embed(
                title="\U0001f495 Ship",
                description=(
                    f"**{user1.display_name}** + **{user2.display_name}**\n\n"
                    "Loving yourself is valid, but this is next level. \U0001f4af"
                ),
                color=_PINK,
            )
            e.set_footer(text="NanoBot Fun \u00b7 Results are totally scientific")
            return await ctx.reply(embed=e)
        if ctx.guild.me in (user1, user2):
            e = discord.Embed(
                title="\U0001f495 Ship",
                description="I'm flattered, but I'm in a committed relationship with my codebase. \U0001f4be",
                color=_PINK,
            )
            e.set_footer(text="NanoBot Fun \u00b7 Results are totally scientific")
            return await ctx.reply(embed=e)
        score = _ship_score(user1.id, user2.id)
        name = _ship_name(user1.display_name, user2.display_name)
        e = discord.Embed(title=f"\U0001f495 {name}", color=_PINK)
        e.add_field(
            name=f"{user1.display_name} \u00d7 {user2.display_name}",
            value=f"{_progress_bar(score)} **{score}%**\n{_ship_verdict(score)}",
            inline=False,
        )
        e.set_footer(text="NanoBot Fun \u00b7 Results are totally scientific")
        await ctx.reply(embed=e)

    @commands.command(
        name="8ball",
        aliases=["eightball", "magic8ball"],
        extras={
            "category": "\U0001f389 Fun",
            "short": "Ask the magic 8-ball",
            "usage": "8ball <question>",
            "desc": "Ask a yes/no question and the magic 8-ball will answer.",
            "args": [("question", "Your question")],
            "perms": "None",
            "example": "!8ball Will I pass my exam?",
        },
    )
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def pfx_8ball(self, ctx, *, question: str):
        pool = random.choice([_8BALL_POSITIVE, _8BALL_NEUTRAL, _8BALL_NEGATIVE])
        answer = random.choice(pool)
        color = (
            h.GREEN
            if pool is _8BALL_POSITIVE
            else (h.YELLOW if pool is _8BALL_NEUTRAL else h.RED)
        )
        e = discord.Embed(title="\U0001f3b1 Magic 8-Ball", color=color)
        e.add_field(name="Question", value=question[:256], inline=False)
        e.add_field(name="Answer", value=f"**{answer}**", inline=False)
        e.set_footer(text="NanoBot Fun")
        await ctx.reply(embed=e)

    @commands.command(
        name="fml",
        extras={
            "category": "\U0001f389 Fun",
            "short": "Random FML story",
            "usage": "fml",
            "desc": "Get a random FML story from fmylife.com.",
            "args": [],
            "perms": "None",
            "example": "{prefix}fml",
        },
    )
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def pfx_fml(self, ctx):
        story = await cache_db.get_random_fml()
        if not story:
            return await ctx.reply(
                "No FML stories cached yet -- try again in a few minutes!"
            )
        e = discord.Embed(description=story, color=_FML_BLUE)
        e.set_footer(text="NanoBot Fun \u00b7 fmylife.com")
        await ctx.reply(embed=e)

    @commands.command(
        name="thigh",
        aliases=["thighs", "legs", "leg"],
        extras={
            "category": "\U0001f389 Fun",
            "short": "Random anime thigh pic",
            "usage": "thigh",
            "desc": "Get a random anime thigh pic (SFW).",
            "args": [],
            "perms": "None",
            "example": "{prefix}thigh",
        },
    )
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def pfx_thigh(self, ctx):
        tag = random.choice(_THIGH_TAGS)
        img, src = await _get_nekosia(self._session, tag)
        if not img:
            return await ctx.reply(
                "No thigh images cached yet -- try again in a few minutes!"
            )
        e = discord.Embed(color=_PINK)
        e.set_image(url=img)
        if src:
            e.description = f"[\U0001f517 Source]({src})"
        e.set_footer(text="NanoBot Fun \u00b7 nekosia.cat")
        await ctx.reply(embed=e)

    @commands.command(
        name="wyr",
        aliases=["wouldyourather"],
        extras={
            "category": "\U0001f389 Fun",
            "short": "Would You Rather",
            "usage": "wyr [duration]",
            "desc": "Start a Would You Rather poll with buttons. Duration examples: 30m, 2h, 1h30m. Default: 1h. Max: 24h.",
            "args": [("duration", "How long voting lasts (optional, default 1h)")],
            "perms": "None",
            "example": "{prefix}wyr 30m",
        },
    )
    @commands.cooldown(1, 10, commands.BucketType.channel)
    async def pfx_wyr(self, ctx, *, duration: str | None = None):
        secs = _parse_duration(duration)
        question = await cache_db.get_random_wyr()
        if not question:
            return await ctx.reply(
                "No WYR questions cached yet -- try again in a few minutes!"
            )
        opt_a, opt_b = _split_wyr(question)
        view = WyrView(opt_a, opt_b, duration=secs)
        msg = await ctx.reply(embed=view._voting_embed(), view=view)
        view.message = msg

    @commands.command(
        name="rps",
        aliases=["rockpaperscissors"],
        extras={
            "category": "\U0001f389 Fun",
            "short": "Rock Paper Scissors",
            "usage": "rps [user]",
            "desc": "Challenge someone to Rock Paper Scissors! Leave user empty to play vs the bot.",
            "args": [("user", "Who to challenge (optional)")],
            "perms": "None",
            "example": "{prefix}rps @Snow",
        },
    )
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def pfx_rps(self, ctx, user: Optional[discord.Member] = None):
        if user and user == ctx.author:
            e = discord.Embed(
                title="\u270a\u270b\u2702\ufe0f Rock Paper Scissors",
                description="You can't challenge yourself! Try challenging someone else or the bot.",
                color=h.YELLOW,
            )
            e.set_footer(text="NanoBot Fun")
            return await ctx.reply(embed=e)

        is_bot = user is None or user == ctx.guild.me
        opponent = None if is_bot else user
        view = RpsView(ctx.author, opponent, is_bot=is_bot)
        msg = await ctx.reply(embed=view._waiting_embed(), view=view)
        view.message = msg
