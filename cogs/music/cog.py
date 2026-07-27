"""
cogs/music/cog.py — Voice music player.

Streams audio from YouTube (and the many other sites yt-dlp supports) into a
voice channel. Spotify track/album/playlist links are supported without an API
key: their metadata is scraped from the public embed page and each track is
matched on YouTube at play time. Designed mobile-first: a single "Now Playing"
card carries interactive buttons (play/pause, skip, stop, loop, shuffle, replay,
autoplay, guild play, queue) so listeners can drive playback from a phone without typing commands.

Runtime requirements (see requirements.txt):
  - yt-dlp        — source extraction / search
  - PyNaCl        — Discord voice encryption
  - FFmpeg binary — must be installed on the host and on PATH

Config ([music] section, all optional — see example_config.ini):
  music_cookie_file     — path to a yt-dlp cookies.txt (age/region/rate limits)
  music_default_volume  — default volume 0-200 (default 50)
  music_idle_timeout    — seconds before disconnect when channel empties (pauses
                          immediately; auto-resumes if someone returns within window)
                          or queue goes idle; default 180
  music_skip_ratio      — percent of listeners needed to vote-skip (default 50)
  music_max_queue       — max tracks per queue (default 500)
  music_js_runtime_path — explicit path to deno/node/bun binary for yt-dlp JS
                          (auto-detected from PATH + common locations if omitted)
  music_use_opus        — send Opus (true, default) or PCM (false). PCM gives
                          live volume changes but costs more CPU
  music_persist_queue   — save the queue to disk so it survives a restart
                          (true by default)
  music_predownload     — download the next queued track to disk while one
                          plays for gapless playback; live streams skipped
                          (true by default)
  music_self_deafen     — self-deafen on join (true by default)
  music_default_speed   — default playback speed 0.5-3.0 (default 1.0)
  music_search_service  — ytsearch / ytmsearch / scsearch (default ytsearch)
  music_status_message  — presence text while playing; {title} = song. YouTube/
                          Twitch tracks show a Streaming "Watch" button; idle =
                          watching the server (blank = bare song title)
  music_proxy           — HTTP/HTTPS proxy for yt-dlp (blank)
  music_user_agent      — static yt-dlp User-Agent (blank)
  music_source_address  — local bind IP for yt-dlp (default 0.0.0.0)
  music_request_throttle — seconds between yt-dlp HTTP requests; paces the bot
                          so a proxy IP flags slower (0 = off, try 1; default 0)
  music_player_client   — force a yt-dlp YouTube player client (web_safari/mweb/
                          android_vr); blank = auto. Set if HLS playback 403s
  music_pot_provider_url — base URL of a bgutil PO-token provider (fixes GVS
                          "format not available"/403); blank = auto 127.0.0.1:4416
  music_autoplay_autoskip — skip autoplay/guild-play filler when a user queues (true)
  music_save_videos     — keep downloads cached for replays (false)
  music_cache_max_mb / music_cache_max_age_days — cache caps (0 = off)
  music_ratelimit_cooldown / music_ratelimit_leave — 429 back-off behavior
  music_apl_prune_on_error — drop dead guild playlist entries on playback error (true)
  music_save_history    — keep per-guild played history (true)
  music_metadata_lookup — fill missing artist via free iTunes Search API (true)
  music_sponsorblock    — skip non-music/sponsor segments via SponsorBlock;
                          downloads + cuts the track before play (false)
  music_sponsorblock_categories — segments to remove (default music_offtopic)

Commands (hybrid — slash + prefix), category "🎵 Music":
  play / p          — queue a song or playlist (URL, Spotify link, or search)
  playnext / pn     — queue a track to play next
  playnow           — skip the current track and play this immediately
  stream            — queue a livestream / direct media URL
  shuffleplay / sp  — queue a playlist with its tracks shuffled
  search            — search and pick a result from a menu
  follow            — make the bot follow you between voice channels
  pldump            — export the queue's URLs to a text file
  skip / s          — vote-skip (requester / Manage Server force-skips)
  forceskip / fs    — force-skip (Manage Server)
  jump              — skip ahead to a queue position
  stop              — stop, clear the queue, and leave the channel
  pause / resume    — toggle playback
  nowplaying / np   — show the live Now Playing card
  queue / q         — show the upcoming queue
  move              — reorder a track in the queue
  remove / clear    — remove one / all queued tracks
  shuffle           — shuffle the queue
  volume / vol      — show or set playback volume (0-200)
  speed             — set playback speed (0.5-3.0)
  filter / fx       — apply an audio effect (bassboost, nightcore, …)
  loop              — cycle loop mode: off → track → queue
  seek / replay     — jump within / restart the current track
  lyrics            — fetch lyrics for the current track
  grab / save       — DM yourself the current track
  autoplay          — smart autoplay: queue YouTube-related tracks when the queue empties
  guildplay         — keep playing from the guild playlist when the queue empties
                      (the playlist itself is curated over slash only —
                       /music guildplaylist add|remove|list|clear)
  radio / 247       — toggle 24/7 mode: stay in voice even when empty (Manage Server)
  history / played  — show recently played tracks (clearhistory: Manage Server)
  blocksong / unblocksong / blockedsongs — song block list (Manage Server)
  blockuser / unblockuser — bar a member from music (Manage Server)
  join / summon     — connect the bot to your voice channel
  musichealth / mhealth — probe yt-dlp/proxy/cookies, diagnose 403/429 (owner)

Slash layout: the playback controls stay flat (/play, /skip, /pause, /queue,
/volume, … — typed constantly, so they earn their top-level slots). Everything
occasional lives under /music, including the three library/moderation groups
and the four queue-editing commands that used to hold top-level slots of their
own:
  /music queue · clear · remove · move · jump   (queue editing)
  /music guildplaylist add|remove|list|clear
  /music blocksong add|remove|list
  /music blockuser add|remove
The queue four were the worst names on the tree: /clear, /remove, /move and
/jump say nothing about music, and /clear sat one letter from moderation's
/clean (delete bot messages) while doing something destructive.
The prefix names are unchanged: `blocksong`, `unblocksong`, `blockedsongs`,
`blockuser` and `unblockuser` are all still flat. Guild-playlist *curation* has
always been slash-only — it moved from /guildplaylist to /music guildplaylist.
"""

import asyncio
import io
import logging
import math
import os
import random
import shutil
import time
from typing import Optional

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

from utils import db
from utils import helpers as h

from .constants import (
    ACCENT,
    LOOP_OFF,
    LOOP_TRACK,
    LOOP_QUEUE,
    _LOOP_NEXT,
    _LOOP_LABEL,
    FILTERS,
    SEARCH_RESULTS,
    PAGE_SIZE_QUEUE,
    PAGE_SIZE_APL,
    _MUSIC_CACHE_DIR,
    _SPONSORBLOCK_CATEGORIES,
    _SPONSORBLOCK_DEFAULT,
)
from .helpers import (
    _apply_delta,
    _fmt_time,
    _mask_proxy,
    _parse_seek,
    _metadata_query,
    _pick_itunes_match,
    _split_artist_title,
)
from .track import Track
from .player import GuildPlayer
from .views import QueuePageView, AplPageView, SearchView, _apl_single_embed
from .source import MusicSource, YTDLP_AVAILABLE

log = logging.getLogger("NanoBot.music")

# Shown when a control fires but the voice connection has gone away underneath
# us (e.g. Discord re-established the voice session and discord.py lost the
# client reference). The track may still be audible from a stale connection, so
# tell the user how to recover instead of falsely reporting success.
_LOST_VOICE_MSG = (
    "I've lost track of the voice connection — try `stop` then `play` again "
    "to reconnect."
)


class Music(commands.Cog):
    """Voice music player with an interactive Now Playing panel."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.players: dict[int, GuildPlayer] = {}
        self.source = MusicSource(self)
        self._ratelimit_until: float = 0.0  # yt-dlp 429 back-off (monotonic-ish)
        self._bg_tasks: set[asyncio.Task] = set()
        # Wipe leftover predownloads from a previous run — unless the cache is
        # being kept on purpose (music_save_videos).
        if not self.save_videos():
            try:
                if os.path.isdir(_MUSIC_CACHE_DIR):
                    shutil.rmtree(_MUSIC_CACHE_DIR, ignore_errors=True)
            except OSError:
                pass

    def _spawn_bg(self, coro) -> None:
        """Track a fire-and-forget task and log any exception it raises."""

        def _done(task: asyncio.Task) -> None:
            self._bg_tasks.discard(task)
            if not task.cancelled():
                exc = task.exception()
                if exc:
                    log.warning("music cog background task failed: %s", exc)

        task = self.bot.loop.create_task(coro)
        self._bg_tasks.add(task)
        task.add_done_callback(_done)

    async def cog_unload(self) -> None:
        # persist_clear=False so a persisted queue survives a reload/restart.
        for player in list(self.players.values()):
            await player.destroy(reason="cog unload", persist_clear=False)

    # ── config helpers (read live from bot.config so reloadconfig applies) ─────
    def _cfg_int(self, key: str, default: int) -> int:
        val = self.bot.config.get(key)
        try:
            return int(val) if val not in (None, "") else default
        except (TypeError, ValueError):
            return default

    def default_volume(self) -> float:
        return max(0, min(200, self._cfg_int("music_default_volume", 50))) / 100

    def idle_timeout(self) -> int:
        return max(30, self._cfg_int("music_idle_timeout", 180))

    def skip_ratio(self) -> float:
        return max(0, min(100, self._cfg_int("music_skip_ratio", 50))) / 100

    def max_queue(self) -> int:
        return max(1, self._cfg_int("music_max_queue", 500))

    def _cfg_bool(self, key: str, default: bool) -> bool:
        val = self.bot.config.get(key)
        if val is None or val == "":
            return default
        if isinstance(val, bool):
            return val
        return str(val).strip().lower() in ("true", "1", "yes", "on")

    def use_opus(self) -> bool:
        return self._cfg_bool("music_use_opus", True)

    def persist_queue(self) -> bool:
        return self._cfg_bool("music_persist_queue", True)

    def predownload(self) -> bool:
        return self._cfg_bool("music_predownload", True)

    def self_deafen(self) -> bool:
        return self._cfg_bool("music_self_deafen", True)

    def default_speed(self) -> float:
        val = self.bot.config.get("music_default_speed")
        try:
            speed = float(val) if val not in (None, "") else 1.0
        except (TypeError, ValueError):
            speed = 1.0
        return max(0.5, min(3.0, speed))

    def search_service(self) -> str:
        val = (self.bot.config.get("music_search_service") or "").strip()
        return val if val in ("ytsearch", "ytmsearch", "scsearch") else "ytsearch"

    def status_template(self) -> str:
        return (self.bot.config.get("music_status_message") or "").strip()

    def autoplay_autoskip(self) -> bool:
        return self._cfg_bool("music_autoplay_autoskip", True)

    def save_videos(self) -> bool:
        return self._cfg_bool("music_save_videos", False)

    def cache_max_mb(self) -> int:
        return max(0, self._cfg_int("music_cache_max_mb", 0))

    def cache_max_age_days(self) -> int:
        return max(0, self._cfg_int("music_cache_max_age_days", 0))

    def ratelimit_cooldown(self) -> int:
        return max(0, self._cfg_int("music_ratelimit_cooldown", 600))

    def ratelimit_leave(self) -> bool:
        return self._cfg_bool("music_ratelimit_leave", False)

    def apl_prune_on_error(self) -> bool:
        return self._cfg_bool("music_apl_prune_on_error", True)

    def save_history(self) -> bool:
        return self._cfg_bool("music_save_history", True)

    def metadata_lookup(self) -> bool:
        return self._cfg_bool("music_metadata_lookup", True)

    def sponsorblock(self) -> bool:
        return self._cfg_bool("music_sponsorblock", False)

    def sponsorblock_categories(self) -> list[str]:
        """SponsorBlock segment categories to remove (validated, deduped).

        Defaults to ["music_offtopic"] — the non-music part of a music video.
        Accepts a comma/space-separated list in config; unknown names dropped.
        """
        raw = (self.bot.config.get("music_sponsorblock_categories") or "").strip()
        if not raw:
            return list(_SPONSORBLOCK_DEFAULT)
        cats = [c.strip() for c in raw.replace(",", " ").split() if c.strip()]
        cats = list(dict.fromkeys(c for c in cats if c in _SPONSORBLOCK_CATEGORIES))
        return cats or list(_SPONSORBLOCK_DEFAULT)

    # ── metadata enrichment (free, keyless iTunes Search API) ───────────────────
    async def _enrich_metadata(self, track: Optional[Track]) -> None:
        """Fill in a missing artist via Apple's iTunes Search API.

        Free and keyless. Runs once per track, only when the artist is unknown,
        and fails silently — purely cosmetic for the Now Playing card.
        """
        if not track or track.meta_checked:
            return
        track.meta_checked = True
        if track.artist or not self.metadata_lookup():
            return
        query = _metadata_query(track.title)
        if not query:
            return
        try:
            params = {"term": query, "entity": "song", "limit": 5}
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    "https://itunes.apple.com/search",
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=6),
                ) as r:
                    if r.status != 200:
                        return
                    # iTunes serves JSON as text/javascript — skip the type check.
                    data = await r.json(content_type=None)
        except Exception as exc:
            log.debug("metadata lookup failed for '%s': %s", track.title, exc)
            return
        match = _pick_itunes_match(
            track.title, data.get("results") or [], track.uploader
        )
        if match:
            track.artist = match[0]
            log.debug("metadata: '%s' → artist '%s'", track.title, match[0])

    # ── rate-limit back-off ────────────────────────────────────────────────────
    def _ratelimited(self) -> bool:
        return time.time() < self._ratelimit_until

    def _ratelimit_remaining(self) -> int:
        return max(0, int(self._ratelimit_until - time.time()))

    def _note_ratelimit(self, exc: Exception) -> bool:
        """If `exc` looks like a YouTube rate-limit, start a cooldown. Returns True."""
        text = str(exc).lower()
        signals = ("429", "403", "too many requests", "forbidden", "rate")
        if not any(s in text for s in signals):
            return False
        cooldown = self.ratelimit_cooldown()
        if cooldown:
            self._ratelimit_until = time.time() + cooldown
            log.warning("yt-dlp rate-limited — backing off %ds", cooldown)
            if self.ratelimit_leave():
                mins = max(1, cooldown // 60)
                for player in list(self.players.values()):
                    # Notify the last control-panel channel before disconnecting
                    # so the server knows why the music stopped. Capture the
                    # channel now — destroy() may clear it concurrently.
                    self._spawn_bg(
                        self._notify_ratelimit_leave(player.text_channel, mins)
                    )
                    self._spawn_bg(
                        player.destroy(reason="rate-limited", persist_clear=False)
                    )
        return True

    async def _notify_ratelimit_leave(self, channel, mins: int) -> None:
        """Tell a guild's last control-panel channel we left due to a rate-limit."""
        if not channel:
            return
        try:
            await channel.send(
                embed=h.warn(
                    "YouTube rate-limited me (HTTP 429/403), so I left the voice "
                    f"channel to cool down. Try again in about **{mins} min**.",
                    "⏳ Music Paused — Rate-Limited",
                )
            )
        except Exception as exc:
            log.debug("Could not send rate-limit notice: %s", exc)

    # ── presence: "Watching <song>" while playing, else the bot's idle status ───
    # Discord bot presence is account-global (there's no per-guild activity API),
    # so with multiple servers playing at once this shows the first one found.
    # The activity is handed to the bot's presence manager, which falls back to
    # the rotating idle status when nothing is playing.
    async def _refresh_presence(self) -> None:
        track = None
        for player in self.players.values():
            if player.current:
                track = player.current
                break
        activity = None
        if track:
            tmpl = self.status_template()
            text = tmpl.replace("{title}", track.title) if tmpl else track.title
            # Streaming activity renders a clickable "Watch" button when the URL
            # is a youtube.com/twitch.tv link (as MusicBot does). Fall back to a
            # plain "Watching" activity when there's no usable URL.
            url = track.webpage_url or ""
            if "youtube.com" in url or "youtu.be" in url or "twitch.tv" in url:
                activity = discord.Streaming(name=text[:128], url=url)
            else:
                activity = discord.Activity(
                    type=discord.ActivityType.watching, name=text[:128]
                )
        setter = getattr(self.bot, "set_music_activity", None)
        if setter:
            setter(activity)
        else:  # pragma: no cover — fallback if the bot lacks the helper
            try:
                await self.bot.change_presence(activity=activity)
            except Exception:
                pass

    # ── song blocklist ──────────────────────────────────────────────────────────
    async def _filter_blocked_songs(
        self, guild_id: int, tracks: list[Track]
    ) -> tuple[list[Track], int]:
        """Drop tracks matching any blocked pattern. Returns (kept, removed)."""
        patterns = await db.get_music_song_blocks(guild_id)
        if not patterns:
            return tracks, 0
        kept = []
        removed = 0
        for t in tracks:
            haystack = f"{t.title} {t.query} {t.webpage_url}".lower()
            if any(p in haystack for p in patterns):
                removed += 1
            else:
                kept.append(t)
        return kept, removed

    # ── shared helpers ─────────────────────────────────────────────────────────
    def get_player(self, guild: discord.Guild) -> GuildPlayer:
        player = self.players.get(guild.id)
        if player is None or player._destroyed:
            player = GuildPlayer(self, guild)
            self.players[guild.id] = player
        return player

    def _active_player(self, ctx: commands.Context) -> Optional[GuildPlayer]:
        player = self.players.get(ctx.guild.id)
        if player is None or player._destroyed:
            return None
        return player

    async def _defer(self, ctx: commands.Context) -> None:
        """Defer a slash interaction before a slow operation (idempotent).

        No-op for prefix invocations and when the interaction was already
        responded to/deferred, so it's safe to call from shared helpers.
        """
        itx = ctx.interaction
        if itx is None or itx.response.is_done():
            return
        try:
            await ctx.defer()
        except discord.HTTPException as exc:
            log.debug("Defer failed in %s: %s", getattr(ctx.guild, "id", "?"), exc)

    async def _clear_ghost_voice(self, guild: discord.Guild) -> bool:
        """Force-leave a voice channel the gateway still thinks we're in.

        A failed/timed-out voice handshake can leave Discord believing the bot
        is connected (it still shows in the channel) while discord.py holds no
        VoiceClient — guild.voice_client is None. In that state destroy() can't
        disconnect (nothing to call) and a fresh connect() often times out
        because the server considers us already present. Sending a gateway
        voice-state update with channel=None clears that ghost so we can recover.

        Returns True when a ghost was found and a leave was issued.
        """
        me_voice = getattr(guild.me, "voice", None)
        ghost = (
            guild.voice_client is None
            and me_voice is not None
            and me_voice.channel is not None
        )
        if not ghost:
            return False
        try:
            await guild.change_voice_state(channel=None)
            # Give the gateway a moment to process the leave before any reconnect.
            await asyncio.sleep(0.5)
        except Exception as exc:
            log.debug("Ghost voice cleanup failed in %s: %s", guild.id, exc)
            return False
        log.info("Cleared ghost voice connection in guild %s", guild.id)
        return True

    async def _ensure_voice(
        self, ctx: commands.Context, *, join: bool = True
    ) -> Optional[GuildPlayer]:
        """Validate the caller is in voice and (optionally) connect/move the bot."""
        if not YTDLP_AVAILABLE:
            await ctx.reply(
                embed=h.err(
                    "Music support isn't installed.\n"
                    "Run `pip install -r requirements.txt` and make sure **FFmpeg** "
                    "is on the host's PATH."
                ),
                ephemeral=True,
            )
            return None

        if await db.is_music_user_blocked(ctx.guild.id, ctx.author.id):
            await ctx.reply(
                embed=h.err("You're blocked from using music commands on this server."),
                ephemeral=True,
            )
            return None

        author_vc = getattr(ctx.author.voice, "channel", None)
        if author_vc is None:
            await ctx.reply(embed=h.err("Join a voice channel first."), ephemeral=True)
            return None

        perms = author_vc.permissions_for(ctx.guild.me)
        if not perms.connect or not perms.speak:
            await ctx.reply(
                embed=h.err(
                    f"I need **Connect** and **Speak** permissions in {author_vc.mention}."
                ),
                ephemeral=True,
            )
            return None

        # Establish the voice connection BEFORE creating the player. get_player
        # spins up background loop/refresh tasks; if we created it first and the
        # connect failed, that player would linger with live tasks and no voice
        # client until the idle timeout reaped it.
        voice = ctx.guild.voice_client
        if voice is None:
            if not join:
                await ctx.reply(
                    embed=h.err("I'm not in a voice channel."), ephemeral=True
                )
                return None
            # Connecting waits on the voice handshake (up to ~30s on a slow or
            # failing region), well past the 3s slash-interaction window. Defer
            # first so the reply below — success or error — doesn't 404 with
            # 'Unknown interaction'.
            await self._defer(ctx)
            # Clear any ghost session first: a previous failed handshake can
            # leave the gateway thinking we're connected, which makes connect()
            # time out. Best-effort, then connect.
            await self._clear_ghost_voice(ctx.guild)
            try:
                await author_vc.connect(self_deaf=self.self_deafen())
            except Exception as exc:
                log.warning("Connect failed in %s: %s", ctx.guild.id, exc)
                # Leave no ghost behind on a failed/aborted handshake.
                await self._clear_ghost_voice(ctx.guild)
                await ctx.reply(
                    embed=h.err(f"Couldn't join the channel.\n`{exc}`"), ephemeral=True
                )
                return None
        elif voice.channel != author_vc:
            await self._defer(ctx)
            try:
                await voice.move_to(author_vc)
            except Exception as exc:
                log.warning("Move failed in %s: %s", ctx.guild.id, exc)
                await ctx.reply(
                    embed=h.err(f"Couldn't move to your channel.\n`{exc}`"),
                    ephemeral=True,
                )
                return None

        player = self.get_player(ctx.guild)
        player.text_channel = ctx.channel
        player.stay_connected = await db.get_music_stay(ctx.guild.id)
        return player

    async def _resolve_for(
        self, ctx: commands.Context, query: str
    ) -> Optional[list[Track]]:
        if self._ratelimited():
            await ctx.reply(
                embed=h.warn(
                    f"YouTube is rate-limiting me. Try again in "
                    f"~{self._ratelimit_remaining() // 60 + 1} min.",
                    "⏳ Rate-limited",
                ),
                ephemeral=True,
            )
            return None
        try:
            tracks = await self.source.search(
                query,
                requester_id=ctx.author.id,
                requester_name=ctx.author.display_name,
            )
        except Exception as exc:
            log.warning("Search failed for %r: %s", query, exc)
            self._note_ratelimit(exc)
            await ctx.reply(
                embed=h.err(f"Couldn't find anything for that.\n`{exc}`"),
                ephemeral=True,
            )
            return None
        if not tracks:
            await ctx.reply(embed=h.err("No results found."), ephemeral=True)
            return None

        tracks, removed = await self._filter_blocked_songs(ctx.guild.id, tracks)
        if not tracks:
            await ctx.reply(
                embed=h.err("That track is on this server's music block list."),
                ephemeral=True,
            )
            return None
        if removed:
            await ctx.reply(
                embed=h.warn(
                    f"Skipped **{removed}** blocked track(s) from that request.",
                    "🚫 Blocked",
                ),
                ephemeral=True,
            )
        return tracks

    async def _queue_query(
        self, ctx: commands.Context, query: str, *, front: bool, then_skip: bool
    ) -> None:
        # Defer up front: both joining (voice handshake) and resolving (yt-dlp)
        # can each blow past the 3s slash window. _defer is idempotent, so the
        # second connect-path defer inside _ensure_voice is a no-op.
        await self._defer(ctx)
        player = await self._ensure_voice(ctx, join=True)
        if player is None:
            return
        tracks = await self._resolve_for(ctx, query)
        if tracks is None:
            return

        was_idle = player.current is None and not player.queue

        if len(tracks) == 1:
            t = tracks[0]
            if front:
                player.add_front([t])
            else:
                player.add(t)
            if was_idle:
                await ctx.reply(
                    embed=h.ok(
                        f"Now playing **[{t.title}]({t.webpage_url})**.",
                        "🎵 Starting Playback",
                    )
                )
            else:
                pos = 1 if front else len(player.queue)
                verb = "Playing Next" if front else "Added to Queue"
                e = h.embed(
                    f"➕ {verb}",
                    f"**[{t.title}]({t.webpage_url})**\n"
                    f"`{_fmt_time(t.duration)}` · position **#{pos}** · "
                    f"requested by {t.requester_name}",
                    ACCENT,
                )
                if t.thumbnail:
                    e.set_thumbnail(url=t.thumbnail)
                await ctx.reply(embed=e)
        else:
            if front:
                player.add_front(tracks)
                added = len(tracks)
            else:
                added = player.add_many(tracks)
            total = sum(t.duration or 0 for t in tracks[:added])
            await ctx.reply(
                embed=h.embed(
                    "➕ Playlist Queued",
                    f"Added **{added}** track(s) · total `{_fmt_time(total)}`.",
                    ACCENT,
                )
            )

        if then_skip and not was_idle and player.current is not None:
            player.skip()
        elif (
            not was_idle
            and self.autoplay_autoskip()
            and player._is_autoplay_track(player.current)
        ):
            # A real request interrupts autoplay filler immediately.
            player.skip()

    # ── follow + auto-disconnect when left alone ────────────────────────────────
    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        # The bot's own voice state: if it was disconnected (kicked, moved out,
        # or a gateway drop) the player can be left alive with a track still
        # "current", which pins the global Streaming presence on forever. Tear it
        # down so the presence resets. (destroy() is guarded against re-entry, so
        # this is a no-op when we disconnected ourselves via stop/idle.)
        if member.id == self.bot.user.id:
            if before.channel is not None and after.channel is None:
                player = self.players.get(member.guild.id)
                if player and not player._destroyed:
                    await player.destroy(
                        reason="voice disconnected", persist_clear=False
                    )
                else:
                    # No live player to tear down — make sure a stale streaming
                    # presence from a previous session is still cleared.
                    await self._refresh_presence()
            return

        if member.bot:
            return
        player = self.players.get(member.guild.id)
        if not player or not player.voice or not player.voice.is_connected():
            return

        # Follow the target member between voice channels.
        if (
            player.follow_target == member.id
            and after.channel is not None
            and after.channel != player.voice.channel
        ):
            try:
                await player.voice.move_to(after.channel)
            except Exception as exc:
                log.debug("Follow move failed in %s: %s", member.guild.id, exc)
            return

        if player.stay_connected:
            return  # 24/7 mode — stay put even with an empty channel

        humans = [m for m in player.voice.channel.members if not m.bot]

        if humans:
            # Someone is here — cancel any pending disconnect and resume if
            # playback was auto-paused because the channel went empty.
            if player._alone_task and not player._alone_task.done():
                player._alone_task.cancel()
                player._alone_task = None
            if player._auto_paused and player.voice and player.voice.is_paused():
                player.resume()
                player._auto_paused = False
        else:
            # Channel is now empty — pause immediately, then disconnect after
            # idle_timeout seconds if nobody comes back.
            if player.voice and player.voice.is_playing():
                player.pause()
                player._auto_paused = True
            if player._alone_task and not player._alone_task.done():
                player._alone_task.cancel()

            async def _alone_disconnect(p=player):
                await asyncio.sleep(p.cog.idle_timeout())
                if p.voice and p.voice.is_connected():
                    still = [m for m in p.voice.channel.members if not m.bot]
                    if not still:
                        p._auto_paused = False
                        await p._announce(
                            h.info(
                                "Left the channel — everyone disconnected.", "👋 Bye"
                            )
                        )
                        await p.destroy(reason="alone", persist_clear=False)

            player._alone_task = asyncio.ensure_future(_alone_disconnect())

    # ── resume persisted queues on startup ─────────────────────────────────────
    @commands.Cog.listener()
    async def on_restore_schedules(self):
        if not self.persist_queue() or not YTDLP_AVAILABLE:
            return
        try:
            saved = await db.get_all_persisted_queues()
        except Exception as exc:
            log.warning("Could not load persisted music queues: %s", exc)
            return
        resumed = 0
        for entry in saved:
            try:
                if await self._resume_guild(entry):
                    resumed += 1
            except Exception as exc:
                log.warning(
                    "Queue resume failed for guild %s: %s",
                    entry.get("guild_id"),
                    exc,
                )
        if resumed:
            log.info("Music: resumed %d saved queue(s)", resumed)

    async def _resume_guild(self, entry: dict) -> bool:
        guild = self.bot.get_guild(int(entry["guild_id"]))
        if not guild or guild.id in self.players:
            return False

        vc_id = entry.get("voice_channel_id")
        voice_channel = guild.get_channel(int(vc_id)) if vc_id else None
        tracks: list[Track] = []
        if entry.get("current"):
            tracks.append(Track.from_dict(entry["current"]))
        tracks.extend(Track.from_dict(d) for d in entry.get("queue", []))

        if (
            not tracks
            or not isinstance(
                voice_channel, (discord.VoiceChannel, discord.StageChannel)
            )
            or not voice_channel.permissions_for(guild.me).connect
            or not voice_channel.permissions_for(guild.me).speak
        ):
            await db.clear_music_queue(guild.id)
            return False

        # Skip resume if the channel is empty — keep the queue in DB so the
        # next /join or /play picks it up once someone is there.
        humans = [m for m in voice_channel.members if not m.bot]
        if not humans:
            log.info(
                "Music: skipping resume for guild %s — voice channel is empty",
                guild.id,
            )
            return False

        # A handshake left over from before the restart can make Discord think
        # we're still connected; clear it so connect() doesn't time out.
        await self._clear_ghost_voice(guild)
        try:
            await voice_channel.connect(self_deaf=self.self_deafen())
        except Exception as exc:
            log.warning("Resume connect failed for guild %s: %s", guild.id, exc)
            # Don't leave a half-open handshake behind for the next command.
            await self._clear_ghost_voice(guild)
            return False

        player = self.get_player(guild)
        text_id = entry.get("text_channel_id")
        text_channel = guild.get_channel(int(text_id)) if text_id else None
        if isinstance(text_channel, discord.abc.Messageable):
            player.text_channel = text_channel
        player.stay_connected = await db.get_music_stay(guild.id)
        loop_mode = entry.get("loop_mode")
        if loop_mode in (LOOP_OFF, LOOP_TRACK, LOOP_QUEUE):
            player.loop = loop_mode
        player.queue.extend(tracks)
        player._added.set()
        return True

    async def _try_resume_saved_queue(self, player: GuildPlayer) -> bool:
        """Populate an already-connected player from its saved queue (idle-resume)."""
        if not self.persist_queue():
            return False
        try:
            saved = await db.get_all_persisted_queues()
        except Exception as exc:
            log.warning(
                "Could not load saved queue for guild %s: %s", player.guild.id, exc
            )
            return False
        entry = next((e for e in saved if int(e["guild_id"]) == player.guild.id), None)
        if not entry:
            return False
        tracks: list[Track] = []
        if entry.get("current"):
            tracks.append(Track.from_dict(entry["current"]))
        tracks.extend(Track.from_dict(d) for d in entry.get("queue", []))
        if not tracks:
            return False
        loop_mode = entry.get("loop_mode")
        if loop_mode in (LOOP_OFF, LOOP_TRACK, LOOP_QUEUE):
            player.loop = loop_mode
        player.queue.extend(tracks)
        player._added.set()
        return True

    # ══════════════════════════════════════════════════════════════════════════
    # Commands
    # ══════════════════════════════════════════════════════════════════════════
    @commands.hybrid_command(
        name="play",
        aliases=["p"],
        description="Play a song or playlist — paste a URL or type search terms.",
        extras={
            "category": "🎵 Music",
            "sub": "▶️ Play & Queue Up",
            "short": "Queue a song or playlist",
            "usage": "play <url | search terms>",
            "desc": (
                "Joins your voice channel and queues the track. Accepts a YouTube "
                "(or other site) URL, a playlist URL, or plain search terms.\n"
                "Slash only: `mode` — normal (end of queue), next (play after "
                "current), now (skip current), shuffle (playlist in random order)."
            ),
            "args": [
                ("query", "A URL or search terms"),
                ("mode", "(slash) normal / next / now / shuffle"),
            ],
            "perms": "None",
            "example": "{prefix}play never gonna give you up\n{prefix}play https://youtu.be/dQw4w9WgXcQ",
        },
    )
    @app_commands.describe(
        query="A URL, playlist URL, or search terms",
        mode="How to queue the track (default: normal)",
    )
    @app_commands.choices(
        mode=[
            app_commands.Choice(name="Normal — add to end of queue", value="normal"),
            app_commands.Choice(name="Next — play right after current", value="next"),
            app_commands.Choice(
                name="Now — skip current and play immediately", value="now"
            ),
            app_commands.Choice(
                name="Shuffle — queue playlist in random order", value="shuffle"
            ),
        ]
    )
    @commands.guild_only()
    async def play(self, ctx: commands.Context, *, query: str, mode: str = "normal"):
        if mode == "shuffle":
            await self._defer(ctx)
            player = await self._ensure_voice(ctx, join=True)
            if player is None:
                return
            tracks = await self._resolve_for(ctx, query)
            if tracks is None:
                return
            random.shuffle(tracks)
            added = player.add_many(tracks)
            total = sum(t.duration or 0 for t in tracks[:added])
            await ctx.reply(
                embed=h.embed(
                    "🔀 Playlist Queued (shuffled)",
                    f"Added **{added}** track(s) · total `{_fmt_time(total)}`.",
                    ACCENT,
                )
            )
        else:
            front = mode in ("next", "now")
            then_skip = mode == "now"
            await self._queue_query(ctx, query, front=front, then_skip=then_skip)

    @commands.command(
        name="playnext",
        aliases=["pn", "playtop"],
        extras={
            "category": "🎵 Music",
            "sub": "▶️ Play & Queue Up",
            "short": "Queue a track to play next",
            "usage": "playnext <url | search terms>",
            "desc": "Adds the track to the front of the queue so it plays next. Slash: use /play with mode: Next.",
            "args": [("query", "A URL or search terms")],
            "perms": "None",
            "example": "{prefix}playnext lofi beats",
        },
    )
    @commands.guild_only()
    async def playnext(self, ctx: commands.Context, *, query: str):
        await self._queue_query(ctx, query, front=True, then_skip=False)

    @commands.command(
        name="playnow",
        aliases=["playskip", "ps"],
        extras={
            "category": "🎵 Music",
            "sub": "▶️ Play & Queue Up",
            "short": "Play a track immediately",
            "usage": "playnow <url | search terms>",
            "desc": "Adds the track to the front of the queue and skips the current one. Slash: use /play with mode: Now.",
            "args": [("query", "A URL or search terms")],
            "perms": "None",
            "example": "{prefix}playnow https://youtu.be/dQw4w9WgXcQ",
        },
    )
    @commands.guild_only()
    async def playnow(self, ctx: commands.Context, *, query: str):
        await self._queue_query(ctx, query, front=True, then_skip=True)

    @commands.hybrid_command(
        name="search",
        description="Search for a track and pick a result from a menu.",
        extras={
            "category": "🎵 Music",
            "sub": "▶️ Play & Queue Up",
            "short": "Search and pick a result",
            "usage": "search <terms>",
            "desc": "Shows the top results in a dropdown so you can choose which to queue.",
            "args": [("terms", "What to search for")],
            "perms": "None",
            "example": "{prefix}search daft punk one more time",
        },
    )
    @app_commands.describe(terms="What to search for")
    @commands.guild_only()
    async def search_cmd(self, ctx: commands.Context, *, terms: str):
        if not YTDLP_AVAILABLE:
            return await ctx.reply(
                embed=h.err("Music support isn't installed."), ephemeral=True
            )
        await ctx.defer()
        try:
            tracks = await self.source.search(
                terms,
                requester_id=ctx.author.id,
                requester_name=ctx.author.display_name,
                limit=SEARCH_RESULTS,
            )
        except Exception as exc:
            return await ctx.reply(
                embed=h.err(f"Search failed.\n`{exc}`"), ephemeral=True
            )
        if not tracks:
            return await ctx.reply(embed=h.err("No results found."), ephemeral=True)

        view = SearchView(self, ctx, tracks)
        e = h.embed(
            f"🔎 Results for “{terms}”",
            "\n".join(
                f"`{i + 1}.` {t.title} `{_fmt_time(t.duration)}`"
                for i, t in enumerate(tracks)
            ),
            ACCENT,
        )
        msg = await ctx.reply(embed=e, view=view)
        view.message = msg

    @commands.hybrid_command(
        name="skip",
        aliases=["s", "voteskip"],
        description="Vote to skip the current track.",
        extras={
            "category": "🎵 Music",
            "sub": "⏯️ Playback Controls",
            "short": "Vote-skip the current track",
            "usage": "skip",
            "desc": (
                "Votes to skip. The track requester and members with Manage Server "
                "always skip instantly; otherwise a majority of listeners is needed."
            ),
            "args": [],
            "perms": "None",
            "example": "{prefix}skip",
        },
    )
    @commands.guild_only()
    async def skip(self, ctx: commands.Context):
        player = self._active_player(ctx)
        if not player or player.current is None:
            return await ctx.reply(embed=h.err("Nothing is playing."), ephemeral=True)

        title = player.current.title
        listeners = player.listeners()
        is_requester = player.current.requester_id == ctx.author.id
        perms = ctx.author.guild_permissions
        is_mod = perms.manage_guild or perms.manage_messages
        alone = len(listeners) <= 1

        if is_requester or is_mod or alone:
            if not player.skip():
                return await ctx.reply(embed=h.err(_LOST_VOICE_MSG), ephemeral=True)
            return await ctx.reply(embed=h.ok(f"Skipped **{title}**.", "⏭️ Skipped"))

        needed = max(1, math.ceil(self.skip_ratio() * len(listeners)))
        player.skip_votes.add(ctx.author.id)
        present = {m.id for m in listeners}
        votes = len(player.skip_votes & present)

        if votes >= needed:
            if not player.skip():
                return await ctx.reply(embed=h.err(_LOST_VOICE_MSG), ephemeral=True)
            return await ctx.reply(
                embed=h.ok(f"Vote passed — skipped **{title}**.", "⏭️ Skipped")
            )
        await ctx.reply(
            embed=h.info(
                f"Skip vote: **{votes}/{needed}**.\n"
                "Need more listeners to vote, or ask a mod to `forceskip`.",
                "🗳️ Vote to Skip",
            )
        )

    @commands.hybrid_command(
        name="forceskip",
        aliases=["fs"],
        description="Force-skip the current track (Manage Server).",
        extras={
            "category": "🎵 Music",
            "sub": "⏯️ Playback Controls",
            "short": "Force-skip a track",
            "usage": "forceskip",
            "desc": "Immediately skips the current track, bypassing the skip vote.",
            "args": [],
            "perms": "Manage Server",
            "example": "{prefix}forceskip",
        },
    )
    @commands.has_permissions(manage_guild=True)
    @commands.guild_only()
    async def forceskip(self, ctx: commands.Context):
        player = self._active_player(ctx)
        if not player or player.current is None:
            return await ctx.reply(embed=h.err("Nothing is playing."), ephemeral=True)
        title = player.current.title
        if not player.skip():
            return await ctx.reply(embed=h.err(_LOST_VOICE_MSG), ephemeral=True)
        await ctx.reply(embed=h.ok(f"Force-skipped **{title}**.", "⏭️ Skipped"))

    # ── Queue editing: prefix-only, slash lives under /music ─────────────────
    # `/jump`, `/move`, `/remove` and `/clear` each held a top-level slash slot
    # under a name that said nothing about music — worst of all `/clear`, which
    # sat one letter from the moderation cog's `/clean` (delete bot messages)
    # and is destructive. Under /music the same words are unambiguous, and the
    # four of them turn up together when you open the group. Prefix names are
    # unchanged.
    @commands.command(
        name="jump",
        aliases=["skipto"],
        description="Skip ahead to a track at a given queue position.",
        extras={
            "category": "🎵 Music",
            "sub": "📜 The Queue",
            "short": "Jump to a queue position",
            "usage": "jump <position>",
            "desc": "Discards everything before the chosen position and plays it now. On slash this is `/music jump`.",
            "args": [("position", "Queue position to jump to")],
            "perms": "None",
            "example": "{prefix}jump 4",
        },
    )
    @commands.guild_only()
    async def jump(self, ctx: commands.Context, position: int):
        player = self._active_player(ctx)
        if not player or not player.queue:
            return await ctx.reply(embed=h.err("The queue is empty."), ephemeral=True)
        if not 1 <= position <= len(player.queue):
            return await ctx.reply(
                embed=h.err(
                    f"Pick a position between **1** and **{len(player.queue)}**."
                ),
                ephemeral=True,
            )
        target = player.queue[position - 1]
        del player.queue[: position - 1]
        player._schedule_save()
        player.skip()
        await ctx.reply(embed=h.ok(f"Jumping to **{target.title}**.", "⏩ Jump"))

    @commands.hybrid_command(
        name="stop",
        aliases=["disconnect", "dc"],
        description="Stop playback, clear the queue, and leave the channel.",
        extras={
            "category": "🎵 Music",
            "sub": "⏯️ Playback Controls",
            "short": "Stop and leave the voice channel",
            "usage": "stop",
            "desc": "Stops playback, empties the queue, and disconnects the bot.",
            "args": [],
            "perms": "None",
            "example": "{prefix}stop",
        },
    )
    @commands.guild_only()
    async def stop(self, ctx: commands.Context):
        player = self._active_player(ctx)
        if player:
            await player.destroy(reason="stop command")
            return await ctx.reply(
                embed=h.ok("Stopped and left the channel.", "⏹️ Stopped")
            )
        # No live player. There may still be a real voice client (loop crashed
        # and left it behind) or a ghost session the gateway thinks we're in.
        # Tear down whatever we can so the user isn't stuck with a bot that
        # looks connected but won't respond.
        vc = ctx.guild.voice_client
        if vc is not None:
            try:
                await vc.disconnect(force=True)
            except Exception as exc:
                log.debug("Stop disconnect failed in %s: %s", ctx.guild.id, exc)
            return await ctx.reply(
                embed=h.ok("Stopped and left the channel.", "⏹️ Stopped")
            )
        if await self._clear_ghost_voice(ctx.guild):
            return await ctx.reply(
                embed=h.ok("Cleared a stuck voice connection.", "⏹️ Stopped")
            )
        await ctx.reply(embed=h.err("I'm not playing anything."), ephemeral=True)

    @commands.hybrid_command(
        name="pause",
        description="Pause the current track.",
        extras={
            "category": "🎵 Music",
            "sub": "⏯️ Playback Controls",
            "short": "Pause playback",
            "usage": "pause",
            "desc": "Pauses the current track. Resume with `resume`.",
            "args": [],
            "perms": "None",
            "example": "{prefix}pause",
        },
    )
    @commands.guild_only()
    async def pause(self, ctx: commands.Context):
        player = self._active_player(ctx)
        if not player or not player.voice or not player.voice.is_playing():
            return await ctx.reply(embed=h.err("Nothing is playing."), ephemeral=True)
        player.pause()
        await ctx.reply(embed=h.ok("Paused.", "⏸️ Paused"))

    @commands.hybrid_command(
        name="resume",
        aliases=["unpause"],
        description="Resume a paused track.",
        extras={
            "category": "🎵 Music",
            "sub": "⏯️ Playback Controls",
            "short": "Resume playback",
            "usage": "resume",
            "desc": "Resumes a track that was paused.",
            "args": [],
            "perms": "None",
            "example": "{prefix}resume",
        },
    )
    @commands.guild_only()
    async def resume(self, ctx: commands.Context):
        player = self._active_player(ctx)
        if not player or not player.voice or not player.voice.is_paused():
            return await ctx.reply(embed=h.err("Nothing is paused."), ephemeral=True)
        player.resume()
        await ctx.reply(embed=h.ok("Resumed.", "▶️ Resumed"))

    @commands.hybrid_command(
        name="nowplaying",
        aliases=["np"],
        description="Show the live Now Playing card.",
        extras={
            "category": "🎵 Music",
            "sub": "⏯️ Playback Controls",
            "short": "Show what's playing",
            "usage": "nowplaying",
            "desc": "Re-posts the interactive Now Playing card with live progress.",
            "args": [],
            "perms": "None",
            "example": "{prefix}np",
        },
    )
    @commands.guild_only()
    async def nowplaying(self, ctx: commands.Context):
        player = self._active_player(ctx)
        if not player or player.current is None:
            return await ctx.reply(
                embed=h.info("Nothing is playing.", "🎵 Idle"), ephemeral=True
            )
        player.text_channel = ctx.channel
        await player._post_now_playing()
        if ctx.interaction:
            await ctx.reply(
                embed=h.ok("Posted the Now Playing card.", "🎵"), ephemeral=True
            )

    @commands.hybrid_command(
        name="queue",
        aliases=["q"],
        description="Show the upcoming queue.",
        extras={
            "category": "🎵 Music",
            "sub": "📜 The Queue",
            "short": "List queued tracks",
            "usage": "queue",
            "desc": "Shows the currently playing track and up to 15 upcoming tracks.",
            "args": [],
            "perms": "None",
            "example": "{prefix}queue",
        },
    )
    @commands.guild_only()
    async def queue(self, ctx: commands.Context):
        player = self._active_player(ctx)
        if not player or (player.current is None and not player.queue):
            return await ctx.reply(
                embed=h.info("The queue is empty.", "🎶 Queue"), ephemeral=True
            )
        needs_pages = len(player.queue) > PAGE_SIZE_QUEUE
        view = QueuePageView(player) if needs_pages else None
        msg = await ctx.reply(embed=player.queue_embed(0), view=view)
        if needs_pages:
            view.message = msg

    @commands.command(
        name="move",
        description="Move a queued track to a new position.",
        extras={
            "category": "🎵 Music",
            "sub": "📜 The Queue",
            "short": "Reorder a queued track",
            "usage": "move <from> <to>",
            "desc": "Moves the track at one queue position to another. On slash this is `/music move`.",
            "args": [("from_pos", "Current position"), ("to_pos", "New position")],
            "perms": "None",
            "example": "{prefix}move 5 1",
        },
    )
    @commands.guild_only()
    async def move(self, ctx: commands.Context, from_pos: int, to_pos: int):
        player = self._active_player(ctx)
        if not player or not player.queue:
            return await ctx.reply(embed=h.err("The queue is empty."), ephemeral=True)
        n = len(player.queue)
        if not (1 <= from_pos <= n and 1 <= to_pos <= n):
            return await ctx.reply(
                embed=h.err(f"Positions must be between **1** and **{n}**."),
                ephemeral=True,
            )
        track = player.queue.pop(from_pos - 1)
        player.queue.insert(to_pos - 1, track)
        player._schedule_save()
        await ctx.reply(
            embed=h.ok(
                f"Moved **{track.title}** to position **#{to_pos}**.", "↕️ Moved"
            )
        )

    @commands.command(
        name="remove",
        aliases=[],
        description="Remove a track from the queue by its position.",
        extras={
            "category": "🎵 Music",
            "sub": "📜 The Queue",
            "short": "Remove a queued track",
            "usage": "remove <position>",
            "desc": "Removes a track from the queue using the number shown in `queue`. On slash this is `/music remove`.",
            "args": [("position", "Queue position (see !queue)")],
            "perms": "None",
            "example": "{prefix}remove 3",
        },
    )
    @commands.guild_only()
    async def remove(self, ctx: commands.Context, position: int):
        player = self._active_player(ctx)
        if not player or not player.queue:
            return await ctx.reply(embed=h.err("The queue is empty."), ephemeral=True)
        if not 1 <= position <= len(player.queue):
            return await ctx.reply(
                embed=h.err(
                    f"Position must be between **1** and **{len(player.queue)}**."
                ),
                ephemeral=True,
            )
        removed = player.queue.pop(position - 1)
        player._schedule_save()
        await ctx.reply(
            embed=h.ok(f"Removed **{removed.title}** from the queue.", "🗑️ Removed")
        )

    @commands.command(
        name="clear",
        description="Empty the queue (keeps the current track playing).",
        extras={
            "category": "🎵 Music",
            "sub": "📜 The Queue",
            "short": "Clear the queue",
            "usage": "clear",
            "desc": "Removes every upcoming track. The current track keeps playing. On slash this is `/music clear`.",
            "args": [],
            "perms": "None",
            "example": "{prefix}clear",
        },
    )
    @commands.guild_only()
    async def clear(self, ctx: commands.Context):
        player = self._active_player(ctx)
        if not player or not player.queue:
            return await ctx.reply(
                embed=h.err("The queue is already empty."), ephemeral=True
            )
        n = player.clear()
        await ctx.reply(embed=h.ok(f"Cleared **{n}** track(s).", "🧹 Queue Cleared"))

    @commands.hybrid_command(
        name="shuffle",
        description="Shuffle the queue.",
        extras={
            "category": "🎵 Music",
            "sub": "📜 The Queue",
            "short": "Shuffle the queue",
            "usage": "shuffle",
            "desc": "Randomly reorders the upcoming tracks in the queue.",
            "args": [],
            "perms": "None",
            "example": "{prefix}shuffle",
        },
    )
    @commands.guild_only()
    async def shuffle(self, ctx: commands.Context):
        player = self._active_player(ctx)
        if not player or not player.queue:
            return await ctx.reply(embed=h.err("The queue is empty."), ephemeral=True)
        n = player.shuffle()
        await ctx.reply(embed=h.ok(f"Shuffled **{n}** track(s).", "🔀 Shuffled"))

    @commands.hybrid_command(
        name="volume",
        aliases=["vol"],
        description="Show, set, or adjust the playback volume (0-200).",
        extras={
            "category": "🎵 Music",
            "sub": "🔊 Sound",
            "short": "Show or set playback volume",
            "usage": "volume [0-200 | +N | -N]",
            "desc": (
                "With no argument, shows the current volume. "
                "Give a number (0-200) to set it as a percentage (100 is full source "
                "volume), or a leading + or - to adjust relative to the current volume, "
                "e.g. `volume 80`, `volume +10`, or `volume -10`."
            ),
            "args": [("amount", "0-200, or +N / -N to adjust (omit to check current)")],
            "perms": "None",
            "example": "{prefix}volume +10",
        },
    )
    @app_commands.describe(
        amount="0-200, or +N / -N to adjust (leave empty to check current)"
    )
    @commands.guild_only()
    async def volume(self, ctx: commands.Context, amount: Optional[str] = None):
        player = self._active_player(ctx)
        # Checking the volume should work even when nothing is playing — fall
        # back to the configured default so /volume always answers.
        if amount is None:
            if player:
                current = int(round(player.volume * 100))
                return await ctx.reply(
                    embed=h.info(f"Volume is **{current}%**.", "🔊 Volume")
                )
            current = int(round(self.default_volume() * 100))
            return await ctx.reply(
                embed=h.info(
                    f"Default volume is **{current}%** (nothing playing).",
                    "🔊 Volume",
                )
            )
        if not player:
            return await ctx.reply(embed=h.err("Nothing is playing."), ephemeral=True)
        target = _apply_delta(amount, player.volume * 100)
        if target is None:
            return await ctx.reply(
                embed=h.err("Give a number like **80**, **+10**, or **-10**."),
                ephemeral=True,
            )
        level = max(0, min(200, round(target)))
        await player.set_volume(level / 100)
        await ctx.reply(embed=h.ok(f"Volume set to **{level}%**.", "🔊 Volume"))

    @commands.hybrid_command(
        name="speed",
        description="Set or adjust playback speed (0.5-3.0).",
        extras={
            "category": "🎵 Music",
            "sub": "🔊 Sound",
            "short": "Set playback speed",
            "usage": "speed <0.5-3.0 | +N | -N>",
            "desc": (
                "Changes how fast the track plays (1.0 is normal). Use a leading "
                "+ or - to adjust relative to the current speed, e.g. `speed +0.25`."
            ),
            "args": [("rate", "0.5-3.0, or +N / -N to adjust")],
            "perms": "None",
            "example": "{prefix}speed +0.25",
        },
    )
    @app_commands.describe(rate="0.5-3.0, or +N / -N to adjust")
    @commands.guild_only()
    async def speed(self, ctx: commands.Context, rate: str):
        player = self._active_player(ctx)
        if not player or player.current is None:
            return await ctx.reply(embed=h.err("Nothing is playing."), ephemeral=True)
        target = _apply_delta(rate, player.speed)
        if target is None:
            return await ctx.reply(
                embed=h.err("Give a number like **1.25**, **+0.25**, or **-0.25**."),
                ephemeral=True,
            )
        if not 0.5 <= target <= 3.0:
            return await ctx.reply(
                embed=h.err("Speed must be between **0.5** and **3.0**."),
                ephemeral=True,
            )
        player.speed = round(target, 2)
        await ctx.reply(
            embed=h.ok(f"Speed set to **{player.speed:g}×** — applying…", "⏩ Speed")
        )
        await player.reapply_effects()

    @commands.hybrid_command(
        name="filter",
        aliases=["fx", "effect"],
        description="Apply an audio effect (bassboost, nightcore, vaporwave…).",
        extras={
            "category": "🎵 Music",
            "sub": "🔊 Sound",
            "short": "Apply an audio effect",
            "usage": "filter <none|bassboost|nightcore|vaporwave|treble|8d|muffle>",
            "desc": "Applies a DSP effect to playback. Use `none` to clear it.",
            "args": [("name", "Effect name")],
            "perms": "None",
            "example": "{prefix}filter bassboost",
        },
    )
    @app_commands.describe(name="Effect to apply")
    @app_commands.choices(name=[app_commands.Choice(name=k, value=k) for k in FILTERS])
    @commands.guild_only()
    async def filter_cmd(self, ctx: commands.Context, name: str):
        player = self._active_player(ctx)
        if not player or player.current is None:
            return await ctx.reply(embed=h.err("Nothing is playing."), ephemeral=True)
        if name not in FILTERS:
            return await ctx.reply(
                embed=h.err(
                    "Unknown effect. Options: " + ", ".join(f"`{k}`" for k in FILTERS)
                ),
                ephemeral=True,
            )
        player.audio_filter = name
        label = "cleared" if name == "none" else f"set to **{name}**"
        await ctx.reply(embed=h.ok(f"Effect {label} — applying…", "🎚️ Filter"))
        await player.reapply_effects()

    @commands.hybrid_command(
        name="loop",
        aliases=[],
        description="Cycle loop mode: off → track → queue.",
        extras={
            "category": "🎵 Music",
            "sub": "📜 The Queue",
            "short": "Change loop mode",
            "usage": "loop [off|track|queue]",
            "desc": (
                "Sets the loop mode. With no argument it cycles to the next mode. "
                "`track` repeats the current song; `queue` repeats the whole queue."
            ),
            "args": [("mode", "off, track, or queue (optional)")],
            "perms": "None",
            "example": "{prefix}loop\n{prefix}loop track",
        },
    )
    @app_commands.describe(mode="off, track, or queue")
    @app_commands.choices(
        mode=[
            app_commands.Choice(name="Off", value=LOOP_OFF),
            app_commands.Choice(name="Track (repeat current)", value=LOOP_TRACK),
            app_commands.Choice(name="Queue (repeat all)", value=LOOP_QUEUE),
        ]
    )
    @commands.guild_only()
    async def loop(self, ctx: commands.Context, mode: Optional[str] = None):
        player = self._active_player(ctx)
        if not player:
            return await ctx.reply(embed=h.err("Nothing is playing."), ephemeral=True)
        if mode is None:
            player.loop = _LOOP_NEXT[player.loop]
        elif mode in (LOOP_OFF, LOOP_TRACK, LOOP_QUEUE):
            player.loop = mode
        else:
            return await ctx.reply(
                embed=h.err("Mode must be `off`, `track`, or `queue`."),
                ephemeral=True,
            )
        player._schedule_save()
        await ctx.reply(
            embed=h.ok(f"Loop mode: **{_LOOP_LABEL[player.loop]}**.", "🔁 Loop")
        )

    @commands.hybrid_command(
        name="seek",
        description="Jump to a position in the current track (e.g. 1:30, 90, or +30/-30).",
        extras={
            "category": "🎵 Music",
            "sub": "⏯️ Playback Controls",
            "short": "Seek within the current track",
            "usage": "seek <position>",
            "desc": (
                "Jumps to a position in the current track. Accepts `M:SS`, `H:MM:SS`, "
                "or a plain number of seconds. Prefix with `+` or `-` to jump "
                "forward/back from the current spot (e.g. `+30` fast-forwards 30s, "
                "`-15` rewinds 15s)."
            ),
            "args": [
                (
                    "position",
                    "Timestamp like 1:30, seconds like 90, or a relative "
                    "+30 / -15 offset",
                )
            ],
            "perms": "None",
            "example": "{prefix}seek +30",
        },
    )
    @app_commands.describe(
        position="Timestamp (1:30), seconds (90), or offset (+30/-15)"
    )
    @commands.guild_only()
    async def seek(self, ctx: commands.Context, position: str):
        player = self._active_player(ctx)
        if not player or player.current is None:
            return await ctx.reply(embed=h.err("Nothing is playing."), ephemeral=True)
        parsed = _parse_seek(position)
        if parsed is None:
            return await ctx.reply(
                embed=h.err(
                    "Invalid timestamp. Use `1:30`, `0:45`, `90`, or a relative "
                    "`+30` / `-15`."
                ),
                ephemeral=True,
            )
        offset, relative = parsed
        dur = player.current.duration
        if relative:
            target = max(0, int(player.position()) + offset)
            # A forward jump past the end just lands near the end instead of erroring.
            if dur and target >= dur:
                target = max(0, int(dur) - 1)
        else:
            target = offset
            if dur and target >= dur:
                return await ctx.reply(
                    embed=h.err(f"That's past the end (`{_fmt_time(dur)}`)."),
                    ephemeral=True,
                )
        if not await player.seek(target):
            return await ctx.reply(
                embed=h.err("Couldn't seek right now."), ephemeral=True
            )
        await ctx.reply(embed=h.ok(f"Seeking to `{_fmt_time(target)}`.", "⏩ Seek"))

    @commands.hybrid_command(
        name="replay",
        aliases=[],
        description="Restart the current track from the beginning.",
        extras={
            "category": "🎵 Music",
            "sub": "⏯️ Playback Controls",
            "short": "Restart the current track",
            "usage": "replay",
            "desc": "Seeks the current track back to 0:00.",
            "args": [],
            "perms": "None",
            "example": "{prefix}replay",
        },
    )
    @commands.guild_only()
    async def replay(self, ctx: commands.Context):
        player = self._active_player(ctx)
        if not player or player.current is None:
            return await ctx.reply(embed=h.err("Nothing is playing."), ephemeral=True)
        await player.seek(0)
        await ctx.reply(embed=h.ok("Restarting the track.", "⏮️ Replay"))

    # ══════════════════════════════════════════════════════════════════════════
    #  /music group — low-frequency music library commands (1 top-level slot).
    #  Prefix stays flat (!grab, !247, !played, …) for mobile muscle memory; the
    #  slash twins below build a Context from the interaction and reuse the same
    #  prefix callbacks via Command.__call__.
    # ══════════════════════════════════════════════════════════════════════════
    music_slash = app_commands.Group(
        name="music",
        description="Music library: save, lyrics, history, autoplay, 24/7, and more.",
        guild_only=True,
    )

    @commands.command(
        name="grab",
        aliases=["save"],
        extras={
            "category": "🎵 Music",
            "sub": "📚 History & Extras",
            "short": "Save the current track to DMs",
            "usage": "grab",
            "desc": "Sends you a DM with a link to the track that's playing now.",
            "args": [],
            "perms": "None",
            "example": "{prefix}grab",
        },
    )
    @commands.guild_only()
    async def pfx_grab(self, ctx: commands.Context):
        player = self._active_player(ctx)
        if not player or player.current is None:
            return await ctx.reply(embed=h.err("Nothing is playing."), ephemeral=True)
        t = player.current
        e = h.embed(
            "🔖 Saved Track",
            f"**[{t.title}]({t.webpage_url})**\n"
            f"`{_fmt_time(t.duration)}`"
            + (f" · {t.uploader}" if t.uploader else "")
            + f"\nFrom **{ctx.guild.name}**",
            ACCENT,
        )
        if t.thumbnail:
            e.set_thumbnail(url=t.thumbnail)
        try:
            await ctx.author.send(embed=e)
        except discord.Forbidden:
            return await ctx.reply(
                embed=h.err("I couldn't DM you — check your privacy settings."),
                ephemeral=True,
            )
        await ctx.reply(
            embed=h.ok("Sent it to your DMs.", "🔖 Grabbed"), ephemeral=True
        )

    @commands.command(
        name="musichealth",
        aliases=["mhealth", "ythealth"],
        extras={
            "category": "🎵 Music",
            "sub": "🚫 Blocks & Admin",
            "short": "Probe yt-dlp + proxy/cookies health (owner)",
            "usage": "musichealth",
            "desc": (
                "Owner-only. Resolves a known YouTube video through the bot's live "
                "yt-dlp config (proxy, cookies, source address) and reports the "
                "result — diagnose 403/429 blocks from Discord without SSH."
            ),
            "args": [],
            "perms": "Bot owner",
            "example": "{prefix}musichealth",
        },
    )
    @commands.is_owner()
    async def pfx_musichealth(self, ctx: commands.Context):
        await ctx.defer()

        # ── Config snapshot (proxy creds masked) ───────────────────────────────
        cfg = self.bot.config
        proxy_raw = (cfg.get("music_proxy") or "").strip()
        cookie = (cfg.get("music_cookie_file") or "").strip()
        cookie_disp = (
            f"`{cookie}` ({'✅ exists' if os.path.isfile(cookie) else '❌ missing'})"
            if cookie
            else "_(none)_"
        )
        src_addr = (cfg.get("music_source_address") or "0.0.0.0").strip()
        pot_status = await self._potoken_status()

        lines = [
            f"**yt-dlp:** {'✅ available' if YTDLP_AVAILABLE else '❌ not installed'}",
            f"**Proxy:** {_mask_proxy(proxy_raw) if proxy_raw else '_(none)_'}",
            f"**Cookies:** {cookie_disp}",
            f"**Source address:** `{src_addr}`",
            f"**Search service:** `{self.search_service()}`",
            f"**PO-token provider:** {pot_status}",
        ]

        if not YTDLP_AVAILABLE:
            return await ctx.reply(embed=h.err("\n".join(lines), "🩺 Music Health"))

        # ── Live probe through the real playback path ──────────────────────────
        # Uses resolve_stream so the test exercises the exact opts playback does
        # (proxy + cookies + source_address + authcheck skip), not a fresh config.
        test_url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        t0 = time.monotonic()
        try:
            info = await self.source.resolve_stream(test_url)
        except Exception as exc:
            dur_ms = int((time.monotonic() - t0) * 1000)
            text = str(exc).lower()
            if any(s in text for s in ("429", "too many requests", "rate")):
                verdict = "⛔ **HTTP 429 — rate-limited.** Back off, or set a proxy."
            elif any(s in text for s in ("403", "forbidden")):
                verdict = (
                    "⛔ **HTTP 403 — IP blocked.** Set/rotate `music_proxy` "
                    "(residential, not datacenter)."
                )
            else:
                verdict = "❌ probe failed"
            err = str(exc)
            if len(err) > 400:
                err = err[:400] + "…"
            lines.append(f"\n**Probe:** {verdict} ({dur_ms} ms)\n```\n{err}\n```")
            return await ctx.reply(embed=h.err("\n".join(lines), "🩺 Music Health"))

        dur_ms = int((time.monotonic() - t0) * 1000)
        title = (info or {}).get("title")
        if info and (info.get("url") or info.get("formats")):
            lines.append(
                f"\n**Probe:** ✅ resolved **{title or 'video'}** in {dur_ms} ms"
            )
            return await ctx.reply(embed=h.ok("\n".join(lines), "🩺 Music Health"))
        lines.append(f"\n**Probe:** ⚠️ no playable stream returned ({dur_ms} ms)")
        await ctx.reply(embed=h.warn("\n".join(lines), "🩺 Music Health"))

    async def _potoken_status(self) -> str:
        """Ping the bgutil PO-token provider; report up/down for musichealth.

        Uses music_pot_provider_url when set, else the plugin's default
        127.0.0.1:4416. YouTube gates audio formats behind a GVS PO token, so a
        down provider is the usual cause of "format not available" / 403.
        """
        base = (self.bot.config.get("music_pot_provider_url") or "").strip()
        base = base.rstrip("/") or "http://127.0.0.1:4416"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{base}/ping", timeout=aiohttp.ClientTimeout(total=4)
                ) as resp:
                    if resp.status != 200:
                        return f"⚠️ HTTP {resp.status} at `{base}`"
                    try:
                        data = await resp.json()
                    except Exception:
                        data = {}
        except Exception:
            return f"❌ not reachable at `{base}` — start the provider"
        parts = []
        if data.get("version"):
            parts.append(f"v{data['version']}")
        if isinstance(data.get("server_uptime"), (int, float)):
            parts.append(f"up {int(data['server_uptime'])}s")
        suffix = f" ({', '.join(parts)})" if parts else ""
        return f"✅ running at `{base}`{suffix}"

    @commands.command(
        name="lyrics",
        extras={
            "category": "🎵 Music",
            "sub": "📚 History & Extras",
            "short": "Look up song lyrics",
            "usage": "lyrics [artist - title]",
            "desc": (
                "Fetches lyrics from lyrics.ovh. With no argument it uses the current "
                "track's title; otherwise pass `Artist - Title` for best results."
            ),
            "args": [("query", "Artist - Title (optional)")],
            "perms": "None",
            "example": "{prefix}lyrics\n{prefix}lyrics daft punk - one more time",
        },
    )
    @commands.guild_only()
    async def pfx_lyrics(self, ctx: commands.Context, *, query: Optional[str] = None):
        player = self._active_player(ctx)
        if not query:
            if not player or player.current is None:
                return await ctx.reply(
                    embed=h.err("Nothing is playing — pass `Artist - Title`."),
                    ephemeral=True,
                )
            query = player.current.title

        artist, title = _split_artist_title(query)
        if not title:
            return await ctx.reply(
                embed=h.err("Couldn't parse a song title from that."), ephemeral=True
            )

        await ctx.defer()
        url = f"https://api.lyrics.ovh/v1/{artist}/{title}"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url, timeout=aiohttp.ClientTimeout(total=15)
                ) as r:
                    if r.status != 200:
                        raise ValueError(f"status {r.status}")
                    data = await r.json()
        except Exception:
            return await ctx.reply(
                embed=h.err(
                    f"No lyrics found for **{title}**"
                    + (f" by **{artist}**" if artist else "")
                    + ".\nTry `lyrics Artist - Title`."
                ),
                ephemeral=True,
            )

        text = (data.get("lyrics") or "").strip()
        if not text:
            return await ctx.reply(
                embed=h.err(f"No lyrics found for **{title}**."), ephemeral=True
            )
        if len(text) > 4000:
            text = text[:3990] + "\n…(truncated)"
        e = h.embed(f"🎤 {title}" + (f" — {artist}" if artist else ""), text, ACCENT)
        e.set_footer(text="Lyrics via lyrics.ovh · NanoBot Music")
        await ctx.reply(embed=e)

    @commands.command(
        name="guildplay",
        extras={
            "category": "🎵 Music",
            "sub": "🤖 Modes & Automation",
            "short": "Toggle guild playlist playback",
            "usage": "guildplay [on|off]",
            "desc": (
                "When on, the bot keeps playing random tracks from the server "
                "guild playlist once the queue empties. Manage the list with "
                "`guildplaylist`."
            ),
            "args": [("state", "on or off (optional — toggles if omitted)")],
            "perms": "None",
            "example": "{prefix}guildplay on",
        },
    )
    @commands.guild_only()
    async def pfx_guildplay(self, ctx: commands.Context, state: Optional[str] = None):
        player = self._active_player(ctx) or self.get_player(ctx.guild)
        player.text_channel = ctx.channel
        if state == "on":
            player.guildplay = True
        elif state == "off":
            player.guildplay = False
        else:
            player.guildplay = not player.guildplay
        player._added.set()  # wake the loop if it's idle
        entries = await db.get_autoplaylist(ctx.guild.id)
        note = ""
        if player.guildplay and not entries:
            note = (
                "\n_The guild playlist is empty — add tracks with `guildplaylist add`._"
            )
        await ctx.reply(
            embed=h.ok(
                f"Guild play is now **{'on' if player.guildplay else 'off'}**.{note}",
                "📻 Guild Play",
            )
        )

    @commands.command(
        name="autoplay",
        extras={
            "category": "🎵 Music",
            "sub": "🤖 Modes & Automation",
            "short": "Toggle smart autoplay",
            "usage": "autoplay [on|off]",
            "desc": (
                "When on, the bot automatically queues YouTube-related tracks "
                "when the queue empties, based on what was last playing. "
                "A YouTube track must have played first to seed the mix."
            ),
            "args": [("state", "on or off (optional — toggles if omitted)")],
            "perms": "None",
            "example": "{prefix}autoplay on",
        },
    )
    @commands.guild_only()
    async def pfx_autoplay(self, ctx: commands.Context, state: Optional[str] = None):
        player = self._active_player(ctx) or self.get_player(ctx.guild)
        player.text_channel = ctx.channel
        if state == "on":
            player.autoplay = True
        elif state == "off":
            player.autoplay = False
        else:
            player.autoplay = not player.autoplay
        player._added.set()  # wake the loop if it's idle
        note = ""
        if player.autoplay and not player._smart_seeds:
            note = "\n_Play a YouTube track first to seed the mix._"
        await ctx.reply(
            embed=h.ok(
                f"Smart autoplay is now **{'on' if player.autoplay else 'off'}**.{note}",
                "✨ Autoplay",
            )
        )

    @commands.command(
        name="radio",
        aliases=["247", "stay"],
        extras={
            "category": "🎵 Music",
            "sub": "🤖 Modes & Automation",
            "short": "Toggle 24/7 stay-connected mode",
            "usage": "radio [on|off]",
            "desc": (
                "When on, the bot stays connected to its voice channel even when "
                "everyone leaves and the queue runs dry — overriding the normal "
                "leave-when-empty/idle behavior. Pair with `guildplay` for a "
                "non-stop radio. Off by default; the setting is saved per server."
            ),
            "args": [("state", "on or off (optional — toggles if omitted)")],
            "perms": "Manage Server",
            "example": "{prefix}radio on",
        },
    )
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    async def pfx_radio(self, ctx: commands.Context, state: Optional[str] = None):
        current = await db.get_music_stay(ctx.guild.id)
        if state == "on":
            new_value = True
        elif state == "off":
            new_value = False
        else:
            new_value = not current

        await db.set_music_stay(ctx.guild.id, new_value)
        player = self._active_player(ctx) or self.players.get(ctx.guild.id)
        if player:
            player.stay_connected = new_value
            player._added.set()  # wake the loop so the new idle behavior applies

        if new_value:
            msg = (
                "**24/7 mode is on.** I'll stay in the voice channel even when it's "
                "empty and the queue is done. Turn on `autoplay` for non-stop music."
            )
        else:
            msg = (
                "**24/7 mode is off.** I'll leave when the channel empties or after "
                "being idle."
            )
        await ctx.reply(embed=h.ok(msg, "📻 24/7 Mode"))

    # ── /music guildplaylist group ───────────────────────────────────────────────
    # Nested under /music rather than sitting at the top level: the playback
    # controls (/play, /skip, /queue…) earn their flat slots by being typed
    # constantly, but curating the server playlist is a once-in-a-while job that
    # only clutters the picker for everyone else. `guild_only` is inherited from
    # the parent — Discord only honours it on the top-level command anyway.
    gpl_group = app_commands.Group(
        name="guildplaylist",
        description="Manage the persistent server guild playlist.",
        parent=music_slash,
    )

    @gpl_group.command(
        name="add", description="Add a track or whole playlist to the guild playlist."
    )
    @app_commands.describe(url="A track, video, or playlist URL (or search terms)")
    async def apl_add(self, interaction: discord.Interaction, url: str):
        await interaction.response.defer(ephemeral=True)
        if not YTDLP_AVAILABLE:
            return await interaction.followup.send(
                embed=h.err("Music support isn't installed."), ephemeral=True
            )

        try:
            tracks = await self.source.search(
                url,
                requester_id=interaction.user.id,
                requester_name="gpl",
                playlist_cap=None,
            )
        except Exception as exc:
            return await interaction.followup.send(
                embed=h.err(f"Couldn't resolve that.\n`{exc}`"), ephemeral=True
            )
        if not tracks:
            return await interaction.followup.send(
                embed=h.err("Nothing found for that link or search."), ephemeral=True
            )

        added = 0
        skipped = 0
        for t in tracks:
            entry_url = t.webpage_url or t.query
            if not entry_url:
                continue
            if await db.add_autoplaylist_entry(
                interaction.guild_id, entry_url, t.title, interaction.user.id
            ):
                added += 1
            else:
                skipped += 1

        if added == 0:
            return await interaction.followup.send(
                embed=h.warn(
                    "Everything in that link is already in the guild playlist."
                ),
                ephemeral=True,
            )

        # Single track vs. playlist gets a tailored confirmation.
        if len(tracks) == 1:
            msg = f"Added **{tracks[0].title}** to the guild playlist."
        else:
            msg = f"Added **{added}** track(s) to the guild playlist."
            if skipped:
                msg += f" ({skipped} already present, skipped.)"
        await interaction.followup.send(
            embed=h.ok(msg, "📻 Guild Playlist"), ephemeral=True
        )

    @gpl_group.command(
        name="remove", description="Remove a track from the guild playlist by position."
    )
    @app_commands.describe(position="Position shown in /music guildplaylist list")
    async def apl_remove(self, interaction: discord.Interaction, position: int):
        entries = await db.get_autoplaylist(interaction.guild_id)
        if not 1 <= position <= len(entries):
            return await interaction.response.send_message(
                embed=h.err(f"Pick a position between 1 and {len(entries)}."),
                ephemeral=True,
            )
        entry = entries[position - 1]
        await db.remove_autoplaylist_entry(interaction.guild_id, entry["url"])
        await interaction.response.send_message(
            embed=h.ok(
                f"Removed **{entry['title'] or entry['url']}**.", "📻 Guild Playlist"
            ),
            ephemeral=True,
        )

    @gpl_group.command(name="list", description="Show the server guild playlist.")
    async def apl_list(self, interaction: discord.Interaction):
        entries = await db.get_autoplaylist(interaction.guild_id)
        if not entries:
            return await interaction.response.send_message(
                embed=h.info(
                    "The guild playlist is empty.\nAdd tracks with `/music guildplaylist add`.",
                    "📻 Guild Playlist",
                ),
                ephemeral=True,
            )
        needs_pages = len(entries) > PAGE_SIZE_APL
        view = AplPageView(entries, interaction.user.id) if needs_pages else None
        embed = view.build_embed() if needs_pages else _apl_single_embed(entries)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        if needs_pages:
            view.message = await interaction.original_response()

    @gpl_group.command(
        name="clear", description="Remove every track from the guild playlist."
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def apl_clear(self, interaction: discord.Interaction):
        n = await db.clear_autoplaylist(interaction.guild_id)
        await interaction.response.send_message(
            embed=h.ok(
                f"Cleared **{n}** track(s) from the guild playlist.", "📻 Cleared"
            ),
            ephemeral=True,
        )

    @commands.command(
        name="stream",
        extras={
            "category": "🎵 Music",
            "sub": "▶️ Play & Queue Up",
            "short": "Queue a live stream URL",
            "usage": "stream <url>",
            "desc": (
                "Queues a livestream or direct audio URL. Works like `play` but is "
                "intended for continuous live sources."
            ),
            "args": [("url", "A livestream or media URL")],
            "perms": "None",
            "example": "{prefix}stream https://example.com/radio.mp3",
        },
    )
    @commands.guild_only()
    async def pfx_stream(self, ctx: commands.Context, *, url: str):
        await self._queue_query(ctx, url, front=False, then_skip=False)

    @commands.command(
        name="shuffleplay",
        aliases=["sp"],
        extras={
            "category": "🎵 Music",
            "sub": "▶️ Play & Queue Up",
            "short": "Queue a playlist, shuffled",
            "usage": "shuffleplay <playlist url | search>",
            "desc": "Resolves a playlist, shuffles the tracks, then adds them all. Slash: use /play with mode: Shuffle.",
            "args": [("query", "A playlist URL or search terms")],
            "perms": "None",
            "example": "{prefix}shuffleplay https://open.spotify.com/playlist/...",
        },
    )
    @commands.guild_only()
    async def shuffleplay(self, ctx: commands.Context, *, query: str):
        await self._defer(ctx)
        player = await self._ensure_voice(ctx, join=True)
        if player is None:
            return
        tracks = await self._resolve_for(ctx, query)
        if tracks is None:
            return
        random.shuffle(tracks)
        added = player.add_many(tracks)
        total = sum(t.duration or 0 for t in tracks[:added])
        await ctx.reply(
            embed=h.embed(
                "🔀 Playlist Queued (shuffled)",
                f"Added **{added}** track(s) · total `{_fmt_time(total)}`.",
                ACCENT,
            )
        )

    @commands.command(
        name="follow",
        extras={
            "category": "🎵 Music",
            "sub": "🤖 Modes & Automation",
            "short": "Follow you between channels",
            "usage": "follow",
            "desc": (
                "The bot moves with you whenever you switch voice channels. "
                "Run it again to stop following."
            ),
            "args": [],
            "perms": "None",
            "example": "{prefix}follow",
        },
    )
    @commands.guild_only()
    async def pfx_follow(self, ctx: commands.Context):
        player = await self._ensure_voice(ctx, join=True)
        if player is None:
            return
        if player.follow_target == ctx.author.id:
            player.follow_target = None
            return await ctx.reply(
                embed=h.ok("Stopped following you.", "🚶 Follow Off")
            )
        player.follow_target = ctx.author.id
        await ctx.reply(
            embed=h.ok(
                f"Now following **{ctx.author.display_name}** between channels.",
                "🐾 Following",
            )
        )

    @commands.command(
        name="pldump",
        extras={
            "category": "🎵 Music",
            "sub": "📜 The Queue",
            "short": "Export the queue to a file",
            "usage": "pldump",
            "desc": "Sends a .txt file listing the URLs of the current and queued tracks.",
            "args": [],
            "perms": "None",
            "example": "{prefix}pldump",
        },
    )
    @commands.guild_only()
    async def pfx_pldump(self, ctx: commands.Context):
        player = self._active_player(ctx)
        if not player or (player.current is None and not player.queue):
            return await ctx.reply(embed=h.err("The queue is empty."), ephemeral=True)
        lines = []
        if player.current and player.current.webpage_url:
            lines.append(player.current.webpage_url)
        lines.extend(t.webpage_url for t in player.queue if t.webpage_url)
        if not lines:
            return await ctx.reply(
                embed=h.err("No exportable URLs in the queue."), ephemeral=True
            )
        buf = io.BytesIO("\n".join(lines).encode("utf-8"))
        file = discord.File(buf, filename=f"queue-{ctx.guild.id}.txt")
        await ctx.reply(
            embed=h.ok(f"Exported **{len(lines)}** track URL(s).", "📄 Queue Dump"),
            file=file,
        )

    @commands.command(
        name="blocksong",
        extras={
            "category": "🎵 Music",
            "sub": "🚫 Blocks & Admin",
            "short": "Block a song from the queue",
            "usage": "blocksong <url | word | phrase>",
            "desc": (
                "Adds a URL, word, or phrase to this server's music block list. "
                "Any track whose title, search query, or URL contains it can't be "
                "queued."
            ),
            "args": [("pattern", "A URL, word, or phrase to block")],
            "perms": "Manage Server",
            "example": "{prefix}blocksong rickroll",
        },
    )
    @commands.has_permissions(manage_guild=True)
    @commands.guild_only()
    async def blocksong(self, ctx: commands.Context, *, pattern: str):
        pattern = pattern.strip()
        if len(pattern) < 2:
            return await ctx.reply(
                embed=h.err("Give at least 2 characters to block."), ephemeral=True
            )
        added = await db.add_music_song_block(ctx.guild.id, pattern, ctx.author.id)
        if not added:
            return await ctx.reply(
                embed=h.warn(f"**{pattern}** is already blocked."), ephemeral=True
            )
        await ctx.reply(
            embed=h.ok(f"Blocked **{pattern}** from the queue.", "🚫 Song Blocked")
        )

    @commands.command(
        name="unblocksong",
        extras={
            "category": "🎵 Music",
            "sub": "🚫 Blocks & Admin",
            "short": "Unblock a song pattern",
            "usage": "unblocksong <pattern>",
            "desc": "Removes a previously blocked URL, word, or phrase.",
            "args": [("pattern", "The exact blocked pattern to remove")],
            "perms": "Manage Server",
            "example": "{prefix}unblocksong rickroll",
        },
    )
    @commands.has_permissions(manage_guild=True)
    @commands.guild_only()
    async def unblocksong(self, ctx: commands.Context, *, pattern: str):
        removed = await db.remove_music_song_block(ctx.guild.id, pattern.strip())
        if not removed:
            return await ctx.reply(
                embed=h.err(f"**{pattern}** isn't on the block list."), ephemeral=True
            )
        await ctx.reply(embed=h.ok(f"Unblocked **{pattern}**.", "✅ Unblocked"))

    @commands.command(
        name="blockedsongs",
        extras={
            "category": "🎵 Music",
            "sub": "🚫 Blocks & Admin",
            "short": "List blocked song patterns",
            "usage": "blockedsongs",
            "desc": "Lists every URL, word, or phrase blocked from the queue.",
            "args": [],
            "perms": "Manage Server",
            "example": "{prefix}blockedsongs",
        },
    )
    @commands.has_permissions(manage_guild=True)
    @commands.guild_only()
    async def blockedsongs(self, ctx: commands.Context):
        patterns = await db.get_music_song_blocks(ctx.guild.id)
        if not patterns:
            return await ctx.reply(
                embed=h.info("Nothing is blocked.", "🚫 Song Block List"),
                ephemeral=True,
            )
        body = "\n".join(f"`{i}.` {p}" for i, p in enumerate(patterns, 1))
        await ctx.reply(
            embed=h.embed(f"🚫 Song Block List · {len(patterns)}", body[:4000], ACCENT),
            ephemeral=True,
        )

    @commands.command(
        name="blockuser",
        extras={
            "category": "🎵 Music",
            "sub": "🚫 Blocks & Admin",
            "short": "Block a member from music",
            "usage": "blockuser <member>",
            "desc": "Stops a member from using any music command on this server.",
            "args": [("member", "The member to block")],
            "perms": "Manage Server",
            "example": "{prefix}blockuser @spammer",
        },
    )
    @commands.has_permissions(manage_guild=True)
    @commands.guild_only()
    async def blockuser(self, ctx: commands.Context, member: discord.Member):
        added = await db.add_music_user_block(ctx.guild.id, member.id, ctx.author.id)
        if not added:
            return await ctx.reply(
                embed=h.warn(f"**{member.display_name}** is already blocked."),
                ephemeral=True,
            )
        await ctx.reply(
            embed=h.ok(
                f"**{member.display_name}** can no longer use music commands.",
                "🚫 User Blocked",
            )
        )

    @commands.command(
        name="unblockuser",
        extras={
            "category": "🎵 Music",
            "sub": "🚫 Blocks & Admin",
            "short": "Unblock a member",
            "usage": "unblockuser <member>",
            "desc": "Removes a member from the music block list.",
            "args": [("member", "The member to unblock")],
            "perms": "Manage Server",
            "example": "{prefix}unblockuser @user",
        },
    )
    @commands.has_permissions(manage_guild=True)
    @commands.guild_only()
    async def unblockuser(self, ctx: commands.Context, member: discord.Member):
        removed = await db.remove_music_user_block(ctx.guild.id, member.id)
        if not removed:
            return await ctx.reply(
                embed=h.err(f"**{member.display_name}** isn't blocked."),
                ephemeral=True,
            )
        await ctx.reply(
            embed=h.ok(
                f"**{member.display_name}** can use music commands again.",
                "✅ Unblocked",
            )
        )

    # ── /music blocksong group (slash interface for song block list) ───────────
    # Manage-Server music moderation — nested for the same reason as
    # guildplaylist above: rarely used, and never by the members it was showing up for.
    blocksong_group = app_commands.Group(
        name="blocksong",
        description="Manage the server's song block list.",
        parent=music_slash,
    )

    @blocksong_group.command(
        name="add", description="Block a song URL, word, or phrase from being queued."
    )
    @app_commands.describe(pattern="A URL, word, or phrase to block")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def blocksong_slash_add(self, interaction: discord.Interaction, pattern: str):
        pattern = pattern.strip()
        if len(pattern) < 2:
            return await interaction.response.send_message(
                embed=h.err("Give at least 2 characters to block."), ephemeral=True
            )
        added = await db.add_music_song_block(
            interaction.guild_id, pattern, interaction.user.id
        )
        if not added:
            return await interaction.response.send_message(
                embed=h.warn(f"**{pattern}** is already blocked."), ephemeral=True
            )
        await interaction.response.send_message(
            embed=h.ok(f"Blocked **{pattern}** from the queue.", "🚫 Song Blocked")
        )

    @blocksong_group.command(
        name="remove", description="Remove a pattern from the music block list."
    )
    @app_commands.describe(pattern="The exact blocked pattern to remove")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def blocksong_slash_remove(
        self, interaction: discord.Interaction, pattern: str
    ):
        removed = await db.remove_music_song_block(
            interaction.guild_id, pattern.strip()
        )
        if not removed:
            return await interaction.response.send_message(
                embed=h.err(f"**{pattern}** isn't on the block list."), ephemeral=True
            )
        await interaction.response.send_message(
            embed=h.ok(f"Unblocked **{pattern}**.", "✅ Unblocked")
        )

    @blocksong_group.command(
        name="list", description="Show the server's music block list."
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def blocksong_slash_list(self, interaction: discord.Interaction):
        patterns = await db.get_music_song_blocks(interaction.guild_id)
        if not patterns:
            return await interaction.response.send_message(
                embed=h.info("Nothing is blocked.", "🚫 Song Block List"),
                ephemeral=True,
            )
        body = "\n".join(f"`{i}.` {p}" for i, p in enumerate(patterns, 1))
        await interaction.response.send_message(
            embed=h.embed(f"🚫 Song Block List · {len(patterns)}", body[:4000], ACCENT),
            ephemeral=True,
        )

    # ── /music blockuser group (slash interface for music user block list) ─────
    blockuser_group = app_commands.Group(
        name="blockuser",
        description="Manage the server's music user block list.",
        parent=music_slash,
    )

    @blockuser_group.command(
        name="add", description="Bar a member from using music commands here."
    )
    @app_commands.describe(member="The member to block from music")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def blockuser_slash_add(
        self, interaction: discord.Interaction, member: discord.Member
    ):
        added = await db.add_music_user_block(
            interaction.guild_id, member.id, interaction.user.id
        )
        if not added:
            return await interaction.response.send_message(
                embed=h.warn(f"**{member.display_name}** is already blocked."),
                ephemeral=True,
            )
        await interaction.response.send_message(
            embed=h.ok(
                f"**{member.display_name}** can no longer use music commands.",
                "🚫 User Blocked",
            )
        )

    @blockuser_group.command(
        name="remove", description="Let a blocked member use music commands again."
    )
    @app_commands.describe(member="The member to unblock from music")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def blockuser_slash_remove(
        self, interaction: discord.Interaction, member: discord.Member
    ):
        removed = await db.remove_music_user_block(interaction.guild_id, member.id)
        if not removed:
            return await interaction.response.send_message(
                embed=h.err(f"**{member.display_name}** isn't blocked."),
                ephemeral=True,
            )
        await interaction.response.send_message(
            embed=h.ok(
                f"**{member.display_name}** can use music commands again.",
                "✅ Unblocked",
            )
        )

    @commands.command(
        name="history",
        aliases=["played"],
        extras={
            "category": "🎵 Music",
            "sub": "📚 History & Extras",
            "short": "Show recently played tracks",
            "usage": "history",
            "desc": "Lists the most recently played tracks (newest first).",
            "args": [],
            "perms": "None",
            "example": "{prefix}history",
        },
    )
    @commands.guild_only()
    async def pfx_history(self, ctx: commands.Context):
        if not self.save_history():
            return await ctx.reply(
                embed=h.info(
                    "History tracking is disabled (`music_save_history`).",
                    "🕘 History",
                ),
                ephemeral=True,
            )
        rows = await db.get_music_history(ctx.guild.id, limit=20)
        if not rows:
            return await ctx.reply(
                embed=h.info("Nothing has played yet.", "🕘 History"), ephemeral=True
            )
        lines = []
        for i, r in enumerate(rows, 1):
            title = r["title"] or "Unknown"
            if len(title) > 55:
                title = title[:52] + "…"
            stamp = f"<t:{int(r['played_at'])}:R>"
            if r["url"]:
                lines.append(f"`{i:>2}.` [{title}]({r['url']}) · {stamp}")
            else:
                lines.append(f"`{i:>2}.` {title} · {stamp}")
        await ctx.reply(embed=h.embed("🕘 Recently Played", "\n".join(lines), ACCENT))

    @commands.command(
        name="clearhistory",
        extras={
            "category": "🎵 Music",
            "sub": "📚 History & Extras",
            "short": "Clear played history",
            "usage": "clearhistory",
            "desc": "Deletes every entry from this server's played-track history.",
            "args": [],
            "perms": "Manage Server",
            "example": "{prefix}clearhistory",
        },
    )
    @commands.has_permissions(manage_guild=True)
    @commands.guild_only()
    async def pfx_clearhistory(self, ctx: commands.Context):
        n = await db.clear_music_history(ctx.guild.id)
        await ctx.reply(
            embed=h.ok(f"Cleared **{n}** history entry/entries.", "🧹 History Cleared")
        )

    # ── /music slash twins ───────────────────────────────────────────────────
    # Each builds a Context from the interaction and reuses the prefix callback
    # via Command.__call__ (which skips the prefix command's own checks; the
    # group/subcommand decorators below carry the slash-side gating instead).
    # Queue editing — the four that used to hold generic top-level slots.
    @music_slash.command(name="queue", description="Show the upcoming queue.")
    async def slash_music_queue(self, interaction: discord.Interaction):
        """A duplicate of the flat /queue, deliberately. Someone who opens
        /music to edit the queue should be able to look at it from the same
        place — the flat /queue keeps its slot because it is typed constantly."""
        ctx = await commands.Context.from_interaction(interaction)
        await self.queue(ctx)

    @music_slash.command(
        name="clear", description="Empty the queue (keeps the current track playing)."
    )
    async def slash_music_clear(self, interaction: discord.Interaction):
        ctx = await commands.Context.from_interaction(interaction)
        await self.clear(ctx)

    @music_slash.command(
        name="remove", description="Remove a track from the queue by its position."
    )
    @app_commands.describe(position="Queue position (see /queue)")
    async def slash_music_remove(self, interaction: discord.Interaction, position: int):
        ctx = await commands.Context.from_interaction(interaction)
        await self.remove(ctx, position)

    @music_slash.command(
        name="move", description="Move a queued track to a new position."
    )
    @app_commands.describe(from_pos="Current position", to_pos="New position")
    async def slash_music_move(
        self, interaction: discord.Interaction, from_pos: int, to_pos: int
    ):
        ctx = await commands.Context.from_interaction(interaction)
        await self.move(ctx, from_pos, to_pos)

    @music_slash.command(
        name="jump", description="Skip ahead to a track at a given queue position."
    )
    @app_commands.describe(position="Queue position to jump to")
    async def slash_music_jump(self, interaction: discord.Interaction, position: int):
        ctx = await commands.Context.from_interaction(interaction)
        await self.jump(ctx, position)

    @music_slash.command(
        name="grab", description="Save the currently playing track to your DMs."
    )
    async def slash_music_grab(self, interaction: discord.Interaction):
        ctx = await commands.Context.from_interaction(interaction)
        await self.pfx_grab(ctx)

    @music_slash.command(name="lyrics", description="Look up lyrics for a song.")
    @app_commands.describe(
        query="Artist - Title (optional; uses current track if omitted)"
    )
    async def slash_music_lyrics(
        self, interaction: discord.Interaction, query: Optional[str] = None
    ):
        ctx = await commands.Context.from_interaction(interaction)
        await self.pfx_lyrics(ctx, query=query)

    @music_slash.command(
        name="autoplay",
        description="Toggle smart autoplay (queues YouTube-related tracks).",
    )
    @app_commands.describe(state="on or off")
    @app_commands.choices(
        state=[
            app_commands.Choice(name="On", value="on"),
            app_commands.Choice(name="Off", value="off"),
        ]
    )
    async def slash_music_autoplay(
        self, interaction: discord.Interaction, state: Optional[str] = None
    ):
        ctx = await commands.Context.from_interaction(interaction)
        await self.pfx_autoplay(ctx, state)

    @music_slash.command(
        name="guildplay", description="Toggle playback from the server guild playlist."
    )
    @app_commands.describe(state="on or off")
    @app_commands.choices(
        state=[
            app_commands.Choice(name="On", value="on"),
            app_commands.Choice(name="Off", value="off"),
        ]
    )
    async def slash_music_guildplay(
        self, interaction: discord.Interaction, state: Optional[str] = None
    ):
        ctx = await commands.Context.from_interaction(interaction)
        await self.pfx_guildplay(ctx, state)

    @music_slash.command(name="radio", description="Toggle 24/7 stay-connected mode.")
    @app_commands.describe(state="on or off")
    @app_commands.choices(
        state=[
            app_commands.Choice(name="On", value="on"),
            app_commands.Choice(name="Off", value="off"),
        ]
    )
    @app_commands.default_permissions(manage_guild=True)
    async def slash_music_radio(
        self, interaction: discord.Interaction, state: Optional[str] = None
    ):
        ctx = await commands.Context.from_interaction(interaction)
        await self.pfx_radio(ctx, state)

    @music_slash.command(
        name="stream", description="Queue a live stream or direct media URL."
    )
    @app_commands.describe(url="A livestream or media URL")
    async def slash_music_stream(self, interaction: discord.Interaction, url: str):
        ctx = await commands.Context.from_interaction(interaction)
        await self.pfx_stream(ctx, url=url)

    @music_slash.command(
        name="follow", description="Toggle the bot following you between channels."
    )
    async def slash_music_follow(self, interaction: discord.Interaction):
        ctx = await commands.Context.from_interaction(interaction)
        await self.pfx_follow(ctx)

    @music_slash.command(
        name="pldump", description="Export the queue's URLs to a text file."
    )
    async def slash_music_pldump(self, interaction: discord.Interaction):
        ctx = await commands.Context.from_interaction(interaction)
        await self.pfx_pldump(ctx)

    @music_slash.command(name="history", description="Show recently played tracks.")
    async def slash_music_history(self, interaction: discord.Interaction):
        ctx = await commands.Context.from_interaction(interaction)
        await self.pfx_history(ctx)

    @music_slash.command(
        name="clearhistory", description="Clear this server's played-track history."
    )
    @app_commands.default_permissions(manage_guild=True)
    async def slash_music_clearhistory(self, interaction: discord.Interaction):
        ctx = await commands.Context.from_interaction(interaction)
        await self.pfx_clearhistory(ctx)

    @commands.hybrid_command(
        name="join",
        aliases=["connect", "summon"],
        description="Connect the bot to your voice channel.",
        extras={
            "category": "🎵 Music",
            "sub": "🤖 Modes & Automation",
            "short": "Join your voice channel",
            "usage": "join",
            "desc": "Connects (or moves) the bot to the voice channel you're in.",
            "args": [],
            "perms": "None",
            "example": "{prefix}join",
        },
    )
    @commands.guild_only()
    async def join(self, ctx: commands.Context):
        player = await self._ensure_voice(ctx, join=True)
        if player is None:
            return
        resumed = (
            not player.queue
            and not player.current
            and await self._try_resume_saved_queue(player)
        )
        if resumed:
            await ctx.reply(
                embed=h.ok(
                    f"Connected to **{ctx.author.voice.channel.name}** and resuming your saved queue.",
                    "🎤 Resumed",
                )
            )
        else:
            await ctx.reply(
                embed=h.ok(
                    f"Connected to **{ctx.author.voice.channel.name}**.", "🎤 Joined"
                )
            )
