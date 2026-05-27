"""
cogs/music.py — Voice music player.

Streams audio from YouTube (and the many other sites yt-dlp supports) into a
voice channel. Spotify track/album/playlist links are supported without an API
key: their metadata is scraped from the public embed page and each track is
matched on YouTube at play time. Designed mobile-first: a single "Now Playing"
card carries
interactive buttons (play/pause, skip, stop, loop, shuffle, replay, autoplay,
queue) so listeners can drive playback from a phone without typing commands.

Runtime requirements (see requirements.txt):
  - yt-dlp        — source extraction / search
  - PyNaCl        — Discord voice encryption
  - FFmpeg binary — must be installed on the host and on PATH

Config ([music] section, all optional — see example_config.ini):
  music_cookie_file     — path to a yt-dlp cookies.txt (age/region/rate limits)
  music_default_volume  — default volume 0-200 (default 50)
  music_idle_timeout    — seconds idle/alone before disconnect (default 180)
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
  music_autoplay_autoskip — skip autoplay filler when a user queues (true)
  music_save_videos     — keep downloads cached for replays (false)
  music_cache_max_mb / music_cache_max_age_days — cache caps (0 = off)
  music_ratelimit_cooldown / music_ratelimit_leave — 429 back-off behavior
  music_apl_prune_on_error — drop dead autoplaylist entries (true)
  music_save_history    — keep per-guild played history (true)

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
  volume / vol      — set playback volume (0-200)
  speed             — set playback speed (0.5-3.0)
  filter / fx       — apply an audio effect (bassboost, nightcore, …)
  loop / repeat     — cycle loop mode: off → track → queue
  seek / replay     — jump within / restart the current track
  lyrics            — fetch lyrics for the current track
  grab / save       — DM yourself the current track
  autoplay          — keep playing from the autoplaylist when the queue empties
  autoplaylist/apl  — manage the persistent server autoplaylist (add takes playlists)
  radio / 247       — toggle 24/7 mode: stay in voice even when empty (Manage Server)
  history / played  — show recently played tracks (clearhistory: Manage Server)
  blocksong / unblocksong / blockedsongs — song block list (Manage Server)
  blockuser / unblockuser — bar a member from music (Manage Server)
  join / summon     — connect the bot to your voice channel
"""

import asyncio
import logging
import math
import os
import random
import io
import json
import re
import shlex
import shutil
import time
from dataclasses import dataclass
from typing import Optional

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

from utils import db
from utils import helpers as h

log = logging.getLogger("NanoBot.music")

try:
    import yt_dlp

    YTDLP_AVAILABLE = True
except ImportError:  # pragma: no cover - optional dependency
    yt_dlp = None
    YTDLP_AVAILABLE = False

# ── Accent colour for the music UI (Discord fuchsia) ────────────────────────────
ACCENT = 0xEB459E

# ── Loop modes ──────────────────────────────────────────────────────────────────
LOOP_OFF = "off"
LOOP_TRACK = "track"
LOOP_QUEUE = "queue"
_LOOP_NEXT = {LOOP_OFF: LOOP_TRACK, LOOP_TRACK: LOOP_QUEUE, LOOP_QUEUE: LOOP_OFF}
_LOOP_LABEL = {LOOP_OFF: "Off", LOOP_TRACK: "🔂 Track", LOOP_QUEUE: "🔁 Queue"}

# ── Audio-effect presets (ffmpeg -af chains, comma-separated, no spaces) ────────
FILTERS: dict[str, str] = {
    "none": "",
    "bassboost": "bass=g=15",
    "treble": "treble=g=12",
    "nightcore": "asetrate=48000*1.25,aresample=48000",
    "vaporwave": "asetrate=48000*0.8,aresample=48000",
    "8d": "apulsator=hz=0.09",
    "muffle": "lowpass=f=600",
}

NP_REFRESH = 15  # seconds between live progress-bar refreshes
SEARCH_RESULTS = 5  # results shown by the search picker
PAGE_SIZE_QUEUE = 15  # tracks per page in queue display
PAGE_SIZE_APL = 25  # entries per page in autoplaylist display
NP_UP_NEXT = 5  # upcoming tracks shown on the Now Playing card

# ── Base yt-dlp options (cookies/limits merged in per call) ─────────────────────
_YTDL_BASE = {
    "format": "bestaudio[acodec!=none]/best[acodec!=none]/best",
    "noplaylist": False,
    "nocheckcertificate": True,
    "ignoreerrors": False,
    "quiet": True,
    "no_warnings": True,
    "default_search": "ytsearch",
    "source_address": "0.0.0.0",
    "skip_download": True,
}

_FFMPEG_BEFORE = "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"

# Where predownloaded tracks are cached (per-guild subdirs). Wiped on startup.
_MUSIC_CACHE_DIR = os.path.join("data", "music_cache")

# Upper bound for the Opus encode bitrate (kbps). The voice channel's own
# bitrate is the real ceiling; this just caps absurd values. Discord allows up
# to 384k on boosted servers, 512 covers any future bump.
_OPUS_MAX_KBITRATE = 512
_OPUS_MIN_KBITRATE = 48

# ── Spotify (no API key — metadata is scraped, then searched on YouTube) ────────
_SPOTIFY_RE = re.compile(
    r"open\.spotify\.com/(?:intl-\w+/)?(track|album|playlist)/(\w+)"
    r"|spotify:(track|album|playlist):(\w+)",
    re.IGNORECASE,
)
_SPOTIFY_NEXT_DATA = re.compile(
    r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.DOTALL
)
_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# ── Strip common noise from YouTube titles when looking up lyrics ───────────────
_LYRICS_NOISE = re.compile(
    r"\([^)]*\)"  # ( … )
    r"|\[[^\]]*\]"  # [ … ]
    r"|\s+(?:ft|feat)\.?\s+.*$"  # " ft. …" / " feat. …"
    r"|\b(?:official\s+)?(?:music\s+)?video\b"
    r"|\blyrics?\b"
    r"|\baudio\b"
    r"|\bhd\b|\b4k\b|\bm/?v\b",
    re.IGNORECASE,
)


def _apply_delta(arg: str, current: float) -> Optional[float]:
    """Parse a numeric command arg as absolute ("80") or relative ("+10"/"-10").

    A leading +/- adds to the current value; anything else is absolute.
    Returns None when the arg isn't a number.
    """
    arg = arg.strip().lstrip("=")
    if not arg:
        return None
    try:
        value = float(arg)
    except ValueError:
        return None
    return current + value if arg[0] in "+-" else value


def _fmt_time(seconds: float | int | None) -> str:
    """Format seconds as M:SS or H:MM:SS. Returns '🔴 LIVE' for None/0."""
    if not seconds:
        return "🔴 LIVE"
    seconds = int(seconds)
    hh, rem = divmod(seconds, 3600)
    mm, ss = divmod(rem, 60)
    if hh:
        return f"{hh}:{mm:02d}:{ss:02d}"
    return f"{mm}:{ss:02d}"


def _progress_bar(elapsed: float, total: float | None, length: int = 18) -> str:
    """Render a slider-style progress bar."""
    if not total:
        return "🔘" + "▬" * (length - 1)
    frac = max(0.0, min(1.0, elapsed / total))
    filled = int(frac * (length - 1))
    return "▬" * filled + "🔘" + "▬" * (length - 1 - filled)


def _parse_timestamp(text: str) -> Optional[int]:
    """Parse 'H:MM:SS', 'M:SS', or plain seconds into an int. None if invalid."""
    text = text.strip()
    try:
        if ":" in text:
            parts = [int(p) for p in text.split(":")]
            if len(parts) == 2:
                return parts[0] * 60 + parts[1]
            if len(parts) == 3:
                return parts[0] * 3600 + parts[1] * 60 + parts[2]
            return None
        return int(text)
    except ValueError:
        return None


# ── Track ───────────────────────────────────────────────────────────────────────
@dataclass
class Track:
    """A single queued item. The stream URL is resolved fresh at play time."""

    query: str  # webpage URL or search term used to re-resolve the stream
    title: str
    duration: Optional[int]
    webpage_url: str
    thumbnail: Optional[str]
    uploader: Optional[str]
    requester_id: int
    requester_name: str

    # Set when the track has been predownloaded to a local file (not persisted).
    local_path: Optional[str] = None
    acodec: Optional[str] = None

    def to_dict(self) -> dict:
        """Serialise the persistable fields (skips ephemeral download state)."""
        return {
            "query": self.query,
            "title": self.title,
            "duration": self.duration,
            "webpage_url": self.webpage_url,
            "thumbnail": self.thumbnail,
            "uploader": self.uploader,
            "requester_id": str(self.requester_id),
            "requester_name": self.requester_name,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Track":
        try:
            rid = int(d.get("requester_id") or 0)
        except (TypeError, ValueError):
            rid = 0
        return cls(
            query=d.get("query") or "",
            title=d.get("title") or "Unknown title",
            duration=d.get("duration"),
            webpage_url=d.get("webpage_url") or "",
            thumbnail=d.get("thumbnail"),
            uploader=d.get("uploader"),
            requester_id=rid,
            requester_name=d.get("requester_name") or "Unknown",
        )

    @classmethod
    def from_info(cls, info: dict, requester_id: int, requester_name: str) -> "Track":
        vid = info.get("id")
        webpage = (
            info.get("webpage_url")
            or info.get("url")
            or (f"https://www.youtube.com/watch?v={vid}" if vid else "")
        )
        thumb = info.get("thumbnail")
        if not thumb and info.get("thumbnails"):
            thumb = info["thumbnails"][-1].get("url")
        return cls(
            query=webpage or info.get("title", ""),
            title=info.get("title") or "Unknown title",
            duration=info.get("duration"),
            webpage_url=webpage,
            thumbnail=thumb,
            uploader=info.get("uploader") or info.get("channel"),
            requester_id=requester_id,
            requester_name=requester_name,
        )


# ── Now-Playing control panel ───────────────────────────────────────────────────
class Controls(discord.ui.View):
    """Interactive buttons attached to the Now Playing card."""

    def __init__(self, player: "GuildPlayer"):
        super().__init__(timeout=None)
        self.player = player
        self._sync()

    def _sync(self) -> None:
        """Reflect current playback state on the buttons."""
        paused = bool(self.player.voice and self.player.voice.is_paused())
        self.toggle.emoji = "▶️" if paused else "⏸️"
        self.toggle.label = "Resume" if paused else "Pause"
        self.loop_btn.label = _LOOP_LABEL[self.player.loop]
        self.loop_btn.style = (
            discord.ButtonStyle.success
            if self.player.loop != LOOP_OFF
            else discord.ButtonStyle.secondary
        )
        self.autoplay_btn.style = (
            discord.ButtonStyle.success
            if self.player.autoplay
            else discord.ButtonStyle.secondary
        )

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        voice = self.player.voice
        user_vc = getattr(interaction.user.voice, "channel", None)
        if not voice or not voice.channel:
            await interaction.response.send_message(
                "I'm not connected to a voice channel.", ephemeral=True
            )
            return False
        if user_vc != voice.channel:
            await interaction.response.send_message(
                "Join my voice channel to use these controls.", ephemeral=True
            )
            return False
        return True

    async def _update_controls(self, interaction: discord.Interaction) -> None:
        self._sync()
        try:
            await interaction.response.edit_message(
                embed=self.player.now_playing_embed(), view=self
            )
        except discord.HTTPException:
            pass

    # Row 0 — transport
    @discord.ui.button(
        emoji="⏸️", label="Pause", style=discord.ButtonStyle.primary, row=0
    )
    async def toggle(self, interaction: discord.Interaction, _: discord.ui.Button):
        voice = self.player.voice
        if voice and voice.is_paused():
            self.player.resume()
        elif voice and voice.is_playing():
            self.player.pause()
        await self._update_controls(interaction)

    @discord.ui.button(
        emoji="⏭️", label="Skip", style=discord.ButtonStyle.secondary, row=0
    )
    async def skip(self, interaction: discord.Interaction, _: discord.ui.Button):
        self.player.skip()
        await interaction.response.send_message("⏭️ Skipped.", ephemeral=True)

    @discord.ui.button(
        emoji="⏹️", label="Stop", style=discord.ButtonStyle.danger, row=0
    )
    async def stop(self, interaction: discord.Interaction, _: discord.ui.Button):
        await interaction.response.send_message(
            "⏹️ Stopped and left the channel.", ephemeral=True
        )
        await self.player.destroy(reason="stop button")

    @discord.ui.button(
        label="Off", emoji="🔁", style=discord.ButtonStyle.secondary, row=0
    )
    async def loop_btn(self, interaction: discord.Interaction, _: discord.ui.Button):
        self.player.loop = _LOOP_NEXT[self.player.loop]
        self.player._schedule_save()
        await self._update_controls(interaction)

    @discord.ui.button(
        emoji="🔀", label="Shuffle", style=discord.ButtonStyle.secondary, row=0
    )
    async def shuffle(self, interaction: discord.Interaction, _: discord.ui.Button):
        n = self.player.shuffle()
        await interaction.response.send_message(
            f"🔀 Shuffled **{n}** track(s).", ephemeral=True
        )

    # Row 1 — extras
    @discord.ui.button(
        emoji="⏮️", label="Replay", style=discord.ButtonStyle.secondary, row=1
    )
    async def replay(self, interaction: discord.Interaction, _: discord.ui.Button):
        await self.player.seek(0)
        await interaction.response.send_message("⏮️ Replaying.", ephemeral=True)

    @discord.ui.button(
        emoji="📻", label="Autoplay", style=discord.ButtonStyle.secondary, row=1
    )
    async def autoplay_btn(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ):
        self.player.autoplay = not self.player.autoplay
        self.player._added.set()
        await self._update_controls(interaction)

    @discord.ui.button(
        emoji="📜", label="Queue", style=discord.ButtonStyle.secondary, row=1
    )
    async def queue_btn(self, interaction: discord.Interaction, _: discord.ui.Button):
        needs_pages = len(self.player.queue) > PAGE_SIZE_QUEUE
        view = QueuePageView(self.player) if needs_pages else None
        await interaction.response.send_message(
            embed=self.player.queue_embed(0), view=view, ephemeral=True
        )
        if needs_pages:
            view.message = await interaction.original_response()


# ── Paginated queue view ────────────────────────────────────────────────────────
class QueuePageView(discord.ui.View):
    """Scrollable queue display; reads live from the player on each page turn."""

    def __init__(self, player: "GuildPlayer"):
        super().__init__(timeout=120)
        self.player = player
        self.page = 0
        self.message: Optional[discord.Message] = None
        self._sync_buttons()

    def _total_pages(self) -> int:
        return max(1, math.ceil(len(self.player.queue) / PAGE_SIZE_QUEUE))

    def _sync_buttons(self) -> None:
        tp = self._total_pages()
        self.prev_btn.disabled = self.page == 0
        self.next_btn.disabled = self.page >= tp - 1
        self.page_indicator.label = f"{self.page + 1}/{tp}"

    async def on_timeout(self) -> None:
        if self.message:
            try:
                for item in self.children:
                    item.disabled = True
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass

    @discord.ui.button(emoji="◀️", style=discord.ButtonStyle.secondary, disabled=True)
    async def prev_btn(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        self.page -= 1
        self._sync_buttons()
        await interaction.response.edit_message(
            embed=self.player.queue_embed(self.page), view=self
        )

    @discord.ui.button(label="1/1", style=discord.ButtonStyle.secondary, disabled=True)
    async def page_indicator(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        await interaction.response.defer()

    @discord.ui.button(emoji="▶️", style=discord.ButtonStyle.secondary)
    async def next_btn(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        self.page += 1
        self._sync_buttons()
        await interaction.response.edit_message(
            embed=self.player.queue_embed(self.page), view=self
        )


# ── Paginated autoplaylist view ─────────────────────────────────────────────────
class AplPageView(discord.ui.View):
    """Scrollable autoplaylist display; snapshot of entries at creation time."""

    def __init__(self, entries: list, invoker_id: int):
        super().__init__(timeout=120)
        self.entries = entries
        self.invoker_id = invoker_id
        self.page = 0
        self.message: Optional[discord.Message] = None
        self._sync_buttons()

    def _total_pages(self) -> int:
        return max(1, math.ceil(len(self.entries) / PAGE_SIZE_APL))

    def build_embed(self) -> discord.Embed:
        start = self.page * PAGE_SIZE_APL
        end = start + PAGE_SIZE_APL
        lines = []
        for i, e in enumerate(self.entries[start:end], start=start + 1):
            label = e["title"] or e["url"]
            if len(label) > 60:
                label = label[:57] + "…"
            lines.append(f"`{i:>2}.` [{label}]({e['url']})")
        tp = self._total_pages()
        embed = h.embed(
            f"📻 Autoplaylist · {len(self.entries)} track(s)", "\n".join(lines), ACCENT
        )
        if tp > 1:
            embed.set_footer(text=f"Page {self.page + 1}/{tp}")
        return embed

    def _sync_buttons(self) -> None:
        tp = self._total_pages()
        self.prev_btn.disabled = self.page == 0
        self.next_btn.disabled = self.page >= tp - 1
        self.page_indicator.label = f"{self.page + 1}/{tp}"

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.invoker_id:
            await interaction.response.send_message(
                "Only the person who ran this command can scroll.", ephemeral=True
            )
            return False
        return True

    async def on_timeout(self) -> None:
        if self.message:
            try:
                for item in self.children:
                    item.disabled = True
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass

    @discord.ui.button(emoji="◀️", style=discord.ButtonStyle.secondary, disabled=True)
    async def prev_btn(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        self.page -= 1
        self._sync_buttons()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="1/1", style=discord.ButtonStyle.secondary, disabled=True)
    async def page_indicator(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        await interaction.response.defer()

    @discord.ui.button(emoji="▶️", style=discord.ButtonStyle.secondary)
    async def next_btn(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        self.page += 1
        self._sync_buttons()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)


# ── Search result picker ────────────────────────────────────────────────────────
class SearchView(discord.ui.View):
    """A dropdown of search results; the invoker picks one to queue."""

    def __init__(self, cog: "Music", ctx: commands.Context, tracks: list[Track]):
        super().__init__(timeout=60)
        self.cog = cog
        self.ctx = ctx
        self.tracks = tracks
        self.message: Optional[discord.Message] = None

        options = []
        for i, t in enumerate(tracks):
            label = t.title if len(t.title) <= 90 else t.title[:87] + "…"
            options.append(
                discord.SelectOption(
                    label=label,
                    value=str(i),
                    description=_fmt_time(t.duration),
                    emoji="🎵",
                )
            )
        self.select.options = options

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message(
                "Only the person who searched can pick.", ephemeral=True
            )
            return False
        return True

    async def on_timeout(self):
        if self.message:
            try:
                await self.message.edit(
                    content="⌛ Search timed out.", view=None, embed=None
                )
            except discord.HTTPException:
                pass

    @discord.ui.select(placeholder="Pick a track to queue…")
    async def select(self, interaction: discord.Interaction, select: discord.ui.Select):
        track = self.tracks[int(select.values[0])]
        player = await self.cog._ensure_voice(self.ctx, join=True)
        if player is None:
            await interaction.response.edit_message(
                content="Couldn't join your voice channel.", view=None, embed=None
            )
            return
        was_idle = player.current is None and not player.queue
        player.add(track)
        verb = "Now playing" if was_idle else "Queued"
        await interaction.response.edit_message(
            content=None,
            embed=h.ok(
                f"**[{track.title}]({track.webpage_url})**\n`{_fmt_time(track.duration)}`",
                f"🎵 {verb}",
            ),
            view=None,
        )
        self.stop()


# ── Per-guild player ────────────────────────────────────────────────────────────
class GuildPlayer:
    """Owns the queue, voice client and playback loop for one guild."""

    def __init__(self, cog: "Music", guild: discord.Guild):
        self.cog = cog
        self.bot = cog.bot
        self.guild = guild

        self.queue: list[Track] = []
        self.current: Optional[Track] = None
        self.loop: str = LOOP_OFF
        self.volume: float = cog.default_volume()
        self.speed: float = cog.default_speed()
        self.audio_filter: str = "none"
        self.autoplay: bool = False
        self.stay_connected: bool = False  # 24/7 mode: don't leave when empty/idle
        self.skip_votes: set[int] = set()
        self.follow_target: Optional[int] = None  # user id the bot follows

        self.idle_timeout: int = cog.idle_timeout()

        self.text_channel: Optional[discord.abc.Messageable] = None
        self.now_msg: Optional[discord.Message] = None
        self.now_view: Optional[Controls] = None

        self._added = asyncio.Event()
        self._next = asyncio.Event()

        # progress tracking
        self._play_started: float = 0.0
        self._base_offset: float = 0.0
        self._paused_at: Optional[float] = None
        self._seek_to: Optional[float] = None

        # restart-in-place control (seek / effect changes)
        self._restart = False
        self._force_current = False

        # currently playing audio source (kept for live PCM volume changes)
        self._source: Optional[discord.AudioSource] = None

        # persistence + predownload bookkeeping
        self._save_handle = None  # debounce timer for save_state
        self._predl_task: Optional[asyncio.Task] = None
        self._dl_files: set[str] = set()  # predownloaded file paths to clean up

        self._destroyed = False
        # Strong refs to fire-and-forget tasks (history, presence, etc.) so they
        # aren't garbage-collected mid-flight, with their exceptions logged.
        self._bg_tasks: set[asyncio.Task] = set()
        self._loop_task = self.bot.loop.create_task(self._player_loop())
        self._refresh_task = self.bot.loop.create_task(self._refresh_loop())

    def _spawn_bg(self, coro) -> None:
        """Track a background task and log any exception it raises."""

        def _done(task: asyncio.Task) -> None:
            self._bg_tasks.discard(task)
            if not task.cancelled():
                exc = task.exception()
                if exc:
                    log.warning("music background task failed: %s", exc)

        task = self.bot.loop.create_task(coro)
        self._bg_tasks.add(task)
        task.add_done_callback(_done)

    # ── voice helpers ────────────────────────────────────────────────────────
    @property
    def voice(self) -> Optional[discord.VoiceClient]:
        return self.guild.voice_client

    def listeners(self) -> list[discord.Member]:
        if not self.voice or not self.voice.channel:
            return []
        return [m for m in self.voice.channel.members if not m.bot]

    # ── queue helpers ────────────────────────────────────────────────────────
    def add(self, track: Track) -> None:
        self.queue.append(track)
        self._added.set()
        self._schedule_save()

    def add_front(self, tracks: list[Track]) -> None:
        for t in reversed(tracks):
            self.queue.insert(0, t)
        self._added.set()
        self._schedule_save()

    def add_many(self, tracks: list[Track]) -> int:
        cap = self.cog.max_queue()
        added = 0
        for t in tracks:
            if len(self.queue) >= cap:
                break
            self.queue.append(t)
            added += 1
        if added:
            self._added.set()
            self._schedule_save()
        return added

    def shuffle(self) -> int:
        random.shuffle(self.queue)
        self._schedule_save()
        return len(self.queue)

    def clear(self) -> int:
        n = len(self.queue)
        self.queue.clear()
        self._schedule_save()
        return n

    def skip(self) -> None:
        if self.voice and (self.voice.is_playing() or self.voice.is_paused()):
            self.voice.stop()  # fires the after-callback → advances the loop

    def pause(self) -> None:
        if self.voice and self.voice.is_playing():
            self.voice.pause()
            self._paused_at = time.monotonic()

    def resume(self) -> None:
        if self.voice and self.voice.is_paused():
            self.voice.resume()
            if self._paused_at is not None:
                self._play_started += time.monotonic() - self._paused_at
                self._paused_at = None

    async def set_volume(self, value: float) -> None:
        self.volume = value
        # PCM mode has a live gain stage, so volume applies instantly. Opus
        # output bakes volume into an ffmpeg filter, so it re-streams the track.
        if not self.cog.use_opus() and isinstance(
            self._source, discord.PCMVolumeTransformer
        ):
            self._source.volume = value
            return
        await self.reapply_effects()

    def position(self) -> float:
        """Elapsed seconds of the current track (pause-aware)."""
        if not self.current or not self._play_started:
            return 0.0
        ref = self._paused_at if self._paused_at is not None else time.monotonic()
        return self._base_offset + (ref - self._play_started)

    async def seek(self, seconds: float) -> bool:
        """Restart the current track at a new offset without advancing the queue."""
        if not self.current or not self.voice:
            return False
        self._seek_to = max(0.0, seconds)
        self._restart = True
        self.voice.stop()
        return True

    async def reapply_effects(self) -> None:
        """Re-stream the current track so speed/filter changes take effect."""
        if (
            self.current
            and self.voice
            and (self.voice.is_playing() or self.voice.is_paused())
        ):
            await self.seek(self.position())

    # ── persistent queue ──────────────────────────────────────────────────────
    def _schedule_save(self) -> None:
        """Debounced save of the queue snapshot (no-op unless persistence is on)."""
        if self._destroyed or not self.cog.persist_queue():
            return
        if self._save_handle is not None:
            self._save_handle.cancel()
        self._save_handle = self.bot.loop.call_later(
            1.5, lambda: self._spawn_bg(self._save_state())
        )

    async def _save_state(self) -> None:
        if self._destroyed or not self.cog.persist_queue():
            return
        voice_id = getattr(getattr(self.voice, "channel", None), "id", None)
        text_id = getattr(self.text_channel, "id", None)
        try:
            await db.save_music_queue(
                self.guild.id,
                self.current.to_dict() if self.current else None,
                [t.to_dict() for t in self.queue],
                voice_id,
                text_id,
                self.loop,
            )
        except Exception as exc:
            log.debug("Queue save failed for %s: %s", self.guild.id, exc)

    async def _clear_saved_state(self) -> None:
        if self._save_handle is not None:
            self._save_handle.cancel()
            self._save_handle = None
        try:
            await db.clear_music_queue(self.guild.id)
        except Exception as exc:
            log.debug("Queue clear failed for %s: %s", self.guild.id, exc)

    # ── predownload (fetch the next track to disk while one plays) ──────────────
    def _start_predownload(self) -> None:
        """Kick off a background download of the next queued track."""
        if not self.cog.predownload() or self.loop == LOOP_TRACK:
            return
        if self._predl_task and not self._predl_task.done():
            return
        if not self.queue:
            return
        nxt = self.queue[0]
        if nxt.local_path:
            return
        if not nxt.duration:
            # No duration → livestream or unknown length. A live stream never
            # finishes downloading, so skip it silently and let it stream at
            # play time. The user already saw the normal "added" confirmation.
            return
        self._predl_task = self.bot.loop.create_task(self._predownload(nxt))

    async def _predownload(self, track: Track) -> None:
        try:
            result = await self.cog.download_track(track, self.guild.id)
        except Exception as exc:
            log.debug("Predownload failed for '%s': %s", track.title, exc)
            return
        if not result:
            return
        path, acodec = result
        # The track may have been skipped/removed while downloading; if so the
        # file is orphaned — track it for cleanup either way.
        self._dl_files.add(path)
        if not self._destroyed:
            track.local_path = path
            track.acodec = acodec

    def _discard_download(self, track: Optional[Track]) -> None:
        """Release a finished track's predownloaded file.

        With music_save_videos on, the file is kept in the cache for replays
        (cache-size/age limits prune it later); otherwise it's deleted now.
        """
        if not track or not track.local_path:
            return
        path = track.local_path
        track.local_path = None
        self._dl_files.discard(path)
        if self.cog.save_videos():
            return
        try:
            if os.path.isfile(path):
                os.remove(path)
        except OSError:
            pass

    def _cleanup_downloads(self) -> None:
        """Drop this guild's predownloaded files (kept when music_save_videos)."""
        if self.cog.save_videos():
            self._dl_files.clear()
            return
        for path in list(self._dl_files):
            try:
                if os.path.isfile(path):
                    os.remove(path)
            except OSError:
                pass
        self._dl_files.clear()
        guild_dir = os.path.join(_MUSIC_CACHE_DIR, str(self.guild.id))
        try:
            if os.path.isdir(guild_dir):
                shutil.rmtree(guild_dir, ignore_errors=True)
        except OSError:
            pass

    # ── source resolution ────────────────────────────────────────────────────
    def _filter_parts(self, include_volume: bool = True) -> list[str]:
        """Per-sample ffmpeg -af chain (preset + speed + volume).

        Any non-empty result forces an Opus re-encode (you can't stream-copy a
        signal you're modifying), so keep it empty whenever possible. In PCM
        mode volume is applied live by a PCMVolumeTransformer instead, so it's
        left out of the chain (include_volume=False).
        """
        parts = []
        preset = FILTERS.get(self.audio_filter, "")
        if preset:
            parts.append(preset)
        if abs(self.speed - 1.0) > 0.01:
            parts.append(f"atempo={self.speed:g}")
        if include_volume and abs(self.volume - 1.0) > 0.001:
            parts.append(f"volume={self.volume:g}")
        return parts

    def _target_kbitrate(self, info: dict) -> int:
        """Opus encode bitrate (kbps), matched to the voice channel.

        Discord relays at most the channel's own bitrate, so encoding higher is
        wasted; encoding at it (vs discord.py's fixed 128k + FEC overhead) is
        what keeps music from sounding thin. Capped at the source bitrate too —
        no point inventing bits the original stream never had.
        """
        kb = _OPUS_MAX_KBITRATE
        channel = getattr(self.voice, "channel", None)
        ch_bitrate = getattr(channel, "bitrate", None)
        if ch_bitrate:
            kb = min(kb, int(ch_bitrate) // 1000)
        abr = info.get("abr")
        if abr:
            kb = min(kb, int(abr))
        return max(_OPUS_MIN_KBITRATE, kb)

    async def _make_source(self, track: Track) -> discord.AudioSource:
        # A predownloaded local file plays directly — no stream resolution and
        # no network headers/reconnect needed.
        local = (
            track.local_path
            if track.local_path and os.path.isfile(track.local_path)
            else None
        )
        if local:
            source_url = local
            before = ""
            src_codec = (track.acodec or "").lower()
            target_kb = self._target_kbitrate({})
        else:
            info = await self.cog.resolve_stream(track.query)
            source_url = info["url"]
            before = _FFMPEG_BEFORE
            # yt-dlp provides headers (User-Agent, etc.) required for the stream
            # URL; without them YouTube returns 403 / throttles → silent failure.
            http_headers = info.get("http_headers") or {}
            if http_headers:
                header_block = "".join(f"{k}: {v}\r\n" for k, v in http_headers.items())
                before = f"{before} -headers {shlex.quote(header_block)}"
            src_codec = (info.get("acodec") or "").lower()
            target_kb = self._target_kbitrate(info)

        offset = 0.0
        if self._seek_to is not None:
            offset = self._seek_to
            before = f"{before} -ss {offset}".strip()
            self._seek_to = None
        self._base_offset = offset

        use_opus = self.cog.use_opus()

        if use_opus:
            parts = self._filter_parts(include_volume=True)
            options = "-vn -af " + ",".join(parts) if parts else "-vn"
            # An already-Opus source with no -af processing is handed straight to
            # Discord (codec="opus" → ffmpeg "-c:a copy"), skipping a lossy
            # decode→re-encode. Otherwise re-encode with libopus.
            passthrough = not parts and src_codec in ("opus", "libopus")
            self._source = discord.FFmpegOpusAudio(
                source_url,
                bitrate=target_kb,
                codec="opus" if passthrough else None,
                before_options=before or None,
                options=options,
            )
        else:
            # PCM path: discord.py encodes Opus itself; volume rides a live
            # PCMVolumeTransformer so changing it doesn't re-stream the track.
            parts = self._filter_parts(include_volume=False)
            options = "-vn -af " + ",".join(parts) if parts else "-vn"
            pcm = discord.FFmpegPCMAudio(
                source_url,
                before_options=before or None,
                options=options,
            )
            self._source = discord.PCMVolumeTransformer(pcm, volume=self.volume)

        return self._source

    # ── playback loop ────────────────────────────────────────────────────────
    async def _player_loop(self) -> None:
        await self.bot.wait_until_ready()
        try:
            while not self._destroyed:
                self._next.clear()

                if self._force_current and self.current is not None:
                    self._force_current = False
                    track = self.current
                elif self.loop == LOOP_TRACK and self.current is not None:
                    track = self.current
                else:
                    track = await self._next_track()
                    if track is None:
                        await self.destroy(reason="inactivity")
                        return

                self.current = track
                self.skip_votes.clear()

                if not self.voice or not self.voice.is_connected():
                    await self.destroy(reason="disconnected")
                    return

                try:
                    source = await self._make_source(track)
                except Exception as exc:
                    log.warning("Failed to start '%s': %s", track.title, exc)
                    self.cog._note_ratelimit(exc)
                    await self._handle_play_error(track)
                    await self._announce(
                        h.err(
                            f"Couldn't play **{track.title}** — skipping.\n`{exc}`",
                            "⚠️ Playback Error",
                        )
                    )
                    self.current = None
                    continue

                self._play_started = time.monotonic()
                self._paused_at = None
                self.voice.play(source, after=self._after)

                self._schedule_save()
                self._start_predownload()
                self._record_history(track)
                self._spawn_bg(self.cog._refresh_presence())

                await self._post_now_playing()
                await self._next.wait()

                if self._destroyed:
                    return

                if self._restart:
                    self._restart = False
                    self._force_current = True
                    continue

                if self.loop == LOOP_QUEUE and track is not None:
                    self.queue.append(track)
                    self._added.set()
                if self.loop != LOOP_TRACK:
                    self._discard_download(track)
                    self.current = None
                self._schedule_save()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover - safety net
            log.error("Player loop crashed in %s: %s", self.guild.id, exc, exc_info=exc)

    async def _next_track(self) -> Optional[Track]:
        """Pop the queue, fall back to autoplay, or wait until something arrives."""
        while not self._destroyed:
            if self.queue:
                return self.queue.pop(0)
            if self.autoplay:
                picked = await self._autoplay_pick()
                if picked is not None:
                    return picked
            self._added.clear()
            if self.stay_connected:
                # 24/7 mode: block indefinitely instead of timing out and leaving.
                await self._added.wait()
                continue
            try:
                await asyncio.wait_for(self._added.wait(), timeout=self.idle_timeout)
            except asyncio.TimeoutError:
                return None
        return None

    async def _autoplay_pick(self) -> Optional[Track]:
        entries = await db.get_autoplaylist(self.guild.id)
        if not entries:
            return None
        choice = random.choice(entries)
        try:
            tracks = await self.cog.search(
                choice["url"], requester_id=self.bot.user.id, requester_name="Autoplay"
            )
        except Exception as exc:
            log.debug("Autoplay resolve failed for %s: %s", choice["url"], exc)
            if not self.cog._note_ratelimit(exc) and self.cog.apl_prune_on_error():
                await db.remove_autoplaylist_entry(self.guild.id, choice["url"])
            return None
        if not tracks:
            if self.cog.apl_prune_on_error():
                await db.remove_autoplaylist_entry(self.guild.id, choice["url"])
            return None
        track = tracks[0]
        kept, _removed = await self.cog._filter_blocked_songs(self.guild.id, [track])
        if not kept:
            return None
        track.requester_name = "📻 Autoplay"
        return track

    def _is_autoplay_track(self, track: Optional[Track]) -> bool:
        return bool(track and self.bot.user and track.requester_id == self.bot.user.id)

    async def _handle_play_error(self, track: Track) -> None:
        """Prune a dead autoplaylist entry when its track fails to play."""
        if not self._is_autoplay_track(track) or not self.cog.apl_prune_on_error():
            return
        for url in (track.webpage_url, track.query):
            if url:
                await db.remove_autoplaylist_entry(self.guild.id, url)

    def _record_history(self, track: Track) -> None:
        if not self.cog.save_history():
            return
        self._spawn_bg(
            db.add_music_history(
                self.guild.id, track.title, track.webpage_url, track.requester_id
            )
        )

    def _after(self, error: Optional[Exception]) -> None:
        if error:
            log.warning("Voice playback error in %s: %s", self.guild.id, error)
        self.bot.loop.call_soon_threadsafe(self._next.set)

    # ── embeds ────────────────────────────────────────────────────────────────
    def now_playing_embed(self) -> discord.Embed:
        t = self.current
        if not t:
            return h.info("Nothing is playing right now.", "🎵 Idle")
        elapsed = self.position()
        bar = _progress_bar(elapsed, t.duration)
        timeline = f"`{_fmt_time(elapsed)} / {_fmt_time(t.duration)}`"
        paused = bool(self.voice and self.voice.is_paused())
        status = "⏸️ Paused" if paused else "▶️ Playing"

        e = discord.Embed(
            title=t.title,
            url=t.webpage_url or None,
            description=f"{bar}\n{timeline}",
            color=ACCENT,
        )
        e.set_author(name=f"{status} · Now Playing")
        if t.thumbnail:
            e.set_thumbnail(url=t.thumbnail)
        if t.uploader:
            e.add_field(name="Uploader", value=t.uploader, inline=True)
        e.add_field(name="Volume", value=f"{int(self.volume * 100)}%", inline=True)
        e.add_field(name="Loop", value=_LOOP_LABEL[self.loop], inline=True)
        e.add_field(name="Requested by", value=t.requester_name, inline=True)

        fx = []
        if self.audio_filter != "none":
            fx.append(self.audio_filter)
        if abs(self.speed - 1.0) > 0.01:
            fx.append(f"{self.speed:g}×")
        if fx:
            e.add_field(name="Effects", value=" · ".join(fx), inline=True)

        if self.queue:
            preview_lines = []
            for i, t in enumerate(self.queue[:NP_UP_NEXT], start=1):
                title = t.title if len(t.title) <= 45 else t.title[:42] + "…"
                preview_lines.append(f"`{i}.` {title} `{_fmt_time(t.duration)}`")
            if len(self.queue) > NP_UP_NEXT:
                preview_lines.append(f"_…and {len(self.queue) - NP_UP_NEXT} more_")
            e.add_field(
                name=f"Up Next ({len(self.queue)} in queue)",
                value="\n".join(preview_lines),
                inline=False,
            )
        e.set_footer(
            text="NanoBot Music" + (" · 📻 Autoplay on" if self.autoplay else "")
        )
        return e

    def queue_embed(self, page: int = 0) -> discord.Embed:
        if self.current is None and not self.queue:
            return h.info("The queue is empty.", "🎶 Queue")
        lines = []
        if self.current:
            lines.append(
                f"**▶️ Now:** [{self.current.title}]({self.current.webpage_url}) "
                f"`{_fmt_time(self.current.duration)}`"
            )
        if self.queue:
            lines.append("")
            start = page * PAGE_SIZE_QUEUE
            end = start + PAGE_SIZE_QUEUE
            for i, t in enumerate(self.queue[start:end], start=start + 1):
                title = t.title if len(t.title) <= 55 else t.title[:52] + "…"
                lines.append(f"`{i:>2}.` {title} `{_fmt_time(t.duration)}`")
        total = sum(t.duration or 0 for t in self.queue)
        total_pages = max(1, math.ceil(len(self.queue) / PAGE_SIZE_QUEUE))
        e = h.embed(f"🎶 Queue · {len(self.queue)} track(s)", "\n".join(lines), ACCENT)
        footer = f"Loop: {_LOOP_LABEL[self.loop]} · Total queued: {_fmt_time(total)}"
        if total_pages > 1:
            footer = f"Page {page + 1}/{total_pages} · " + footer
        e.set_footer(text=footer + " · NanoBot Music")
        return e

    async def _post_now_playing(self) -> None:
        if not self.text_channel:
            return
        self.now_view = Controls(self)
        await self._retire_now_playing(delete=True)
        try:
            self.now_msg = await self.text_channel.send(
                embed=self.now_playing_embed(), view=self.now_view
            )
        except discord.HTTPException as exc:
            log.debug("Could not post Now Playing card: %s", exc)
            self.now_msg = None

    async def _retire_now_playing(self, delete: bool = False) -> None:
        if self.now_msg is None:
            return
        try:
            if delete:
                await self.now_msg.delete()
            else:
                await self.now_msg.edit(view=None)
        except discord.HTTPException:
            pass
        finally:
            if delete:
                self.now_msg = None

    async def _announce(self, embed: discord.Embed) -> None:
        if self.text_channel:
            try:
                await self.text_channel.send(embed=embed)
            except discord.HTTPException:
                pass

    async def _refresh_loop(self) -> None:
        """Periodically refresh the Now Playing card so the progress bar moves."""
        try:
            while not self._destroyed:
                await asyncio.sleep(NP_REFRESH)
                if (
                    self.current
                    and self.now_msg
                    and self.now_view
                    and self.voice
                    and self.voice.is_playing()
                ):
                    self.now_view._sync()
                    try:
                        await self.now_msg.edit(
                            embed=self.now_playing_embed(), view=self.now_view
                        )
                    except discord.HTTPException:
                        pass
        except asyncio.CancelledError:
            raise

    # ── teardown ──────────────────────────────────────────────────────────────
    async def destroy(self, reason: str = "", *, persist_clear: bool = True) -> None:
        if self._destroyed:
            return
        self._destroyed = True
        log.info("Destroying player for guild %s (%s)", self.guild.id, reason)

        # Drop the saved queue on intentional ends (stop/idle/etc). On a bot
        # restart the process just dies — cog_unload passes persist_clear=False
        # so the queue is kept and resumed next launch.
        if self.cog.persist_queue():
            if persist_clear:
                await self._clear_saved_state()
            else:
                # Reload/restart: flush the latest snapshot before tearing down.
                if self._save_handle is not None:
                    self._save_handle.cancel()
                    self._save_handle = None
                await self._save_state()

        self.queue.clear()
        self.current = None
        self._next.set()
        self._added.set()

        if self._predl_task and not self._predl_task.done():
            self._predl_task.cancel()
        self._cleanup_downloads()

        for task in (self._loop_task, self._refresh_task):
            if task and task is not asyncio.current_task():
                task.cancel()

        for task in list(self._bg_tasks):
            if task is not asyncio.current_task():
                task.cancel()

        await self._retire_now_playing(delete=False)

        if self.voice and self.voice.is_connected():
            try:
                await self.voice.disconnect(force=True)
            except Exception:
                pass

        self.cog.players.pop(self.guild.id, None)
        await self.cog._refresh_presence()


def _apl_single_embed(entries: list) -> discord.Embed:
    lines = []
    for i, e in enumerate(entries, start=1):
        label = e["title"] or e["url"]
        if len(label) > 60:
            label = label[:57] + "…"
        lines.append(f"`{i:>2}.` [{label}]({e['url']})")
    return h.embed(
        f"📻 Autoplaylist · {len(entries)} track(s)", "\n".join(lines), ACCENT
    )


# ══════════════════════════════════════════════════════════════════════════════
class Music(commands.Cog):
    """Voice music player with an interactive Now Playing panel."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.players: dict[int, GuildPlayer] = {}
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

    # ── rate-limit back-off ────────────────────────────────────────────────────
    def _ratelimited(self) -> bool:
        return time.time() < self._ratelimit_until

    def _ratelimit_remaining(self) -> int:
        return max(0, int(self._ratelimit_until - time.time()))

    def _note_ratelimit(self, exc: Exception) -> bool:
        """If `exc` looks like a YouTube rate-limit, start a cooldown. Returns True."""
        text = str(exc).lower()
        if "429" not in text and "too many requests" not in text and "rate" not in text:
            return False
        cooldown = self.ratelimit_cooldown()
        if cooldown:
            self._ratelimit_until = time.time() + cooldown
            log.warning("yt-dlp rate-limited — backing off %ds", cooldown)
            if self.ratelimit_leave():
                for player in list(self.players.values()):
                    self._spawn_bg(player.destroy(reason="rate-limited"))
        return True

    # ── presence: "Watching <song>" while playing, else watching the server ─────
    # Discord bot presence is account-global (there's no per-guild activity API),
    # so with multiple servers playing at once this shows the first one found.
    async def _refresh_presence(self) -> None:
        track = None
        for player in self.players.values():
            if player.current:
                track = player.current
                break
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
        else:
            activity = discord.Activity(
                type=discord.ActivityType.watching, name="over the server 👁️"
            )
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

    # ── cache limits (music_save_videos) ────────────────────────────────────────
    def _in_use_files(self) -> set[str]:
        in_use: set[str] = set()
        for player in self.players.values():
            if player.current and player.current.local_path:
                in_use.add(player.current.local_path)
            in_use |= player._dl_files
        return in_use

    def _enforce_cache_limits(self) -> None:
        max_mb = self.cache_max_mb()
        max_age = self.cache_max_age_days()
        if (not max_mb and not max_age) or not os.path.isdir(_MUSIC_CACHE_DIR):
            return
        in_use = self._in_use_files()
        files = []
        for root, _dirs, names in os.walk(_MUSIC_CACHE_DIR):
            for n in names:
                fp = os.path.join(root, n)
                try:
                    st = os.stat(fp)
                except OSError:
                    continue
                files.append((fp, st.st_mtime, st.st_size))
        now = time.time()
        if max_age:
            cutoff = now - max_age * 86400
            for fp, mt, _sz in files:
                if mt < cutoff and fp not in in_use:
                    try:
                        os.remove(fp)
                    except OSError:
                        pass
            files = [(fp, mt, sz) for fp, mt, sz in files if os.path.isfile(fp)]
        if max_mb:
            cap = max_mb * 1024 * 1024
            total = sum(sz for _fp, _mt, sz in files)
            for fp, _mt, sz in sorted(files, key=lambda x: x[1]):
                if total <= cap:
                    break
                if fp in in_use:
                    continue
                try:
                    os.remove(fp)
                    total -= sz
                except OSError:
                    pass

    def _cookie_file(self) -> Optional[str]:
        val = self.bot.config.get("music_cookie_file")
        return val or None

    def _js_runtimes(self) -> dict:
        # explicit path override via config
        cfg_path = (self.bot.config.get("music_js_runtime_path") or "").strip()
        if cfg_path and os.path.isfile(cfg_path):
            name = os.path.basename(cfg_path).split(".")[0].lower()
            if name not in ("deno", "node", "bun", "quickjs"):
                name = "deno"
            return {name: {"path": cfg_path}}

        # auto-discover: deno preferred, node fallback
        _CANDIDATES = [
            (
                "deno",
                [
                    shutil.which("deno"),
                    os.path.expanduser("~/.deno/bin/deno"),
                    "/home/container/.deno/bin/deno",
                    "/usr/local/bin/deno",
                    "/usr/bin/deno",
                ],
            ),
            (
                "node",
                [
                    shutil.which("node"),
                    shutil.which("nodejs"),
                    "/opt/node22/bin/node",
                    "/opt/node21/bin/node",
                    "/opt/node20/bin/node",
                    "/usr/local/bin/node",
                    "/usr/bin/node",
                ],
            ),
        ]
        for name, paths in _CANDIDATES:
            for p in paths:
                if p and os.path.isfile(p):
                    return {name: {"path": p}}

        return {"deno": {}}  # let yt-dlp try its own discovery

    def _ytdl_opts(self, **extra) -> dict:
        opts = {**_YTDL_BASE, **extra}
        cookie = self._cookie_file()
        if cookie:
            opts["cookiefile"] = cookie
        opts["js_runtimes"] = self._js_runtimes()
        opts["source_address"] = (
            self.bot.config.get("music_source_address") or "0.0.0.0"
        )
        opts["default_search"] = self.search_service()
        proxy = (self.bot.config.get("music_proxy") or "").strip()
        if proxy:
            opts["proxy"] = proxy
        ua = (self.bot.config.get("music_user_agent") or "").strip()
        if ua:
            headers = dict(opts.get("http_headers") or {})
            headers["User-Agent"] = ua
            opts["http_headers"] = headers
        return opts

    # ── extraction (runs in a thread to avoid blocking the loop) ───────────────
    async def resolve_stream(self, query: str) -> dict:
        """Resolve a single playable stream (used at play time)."""
        opts = self._ytdl_opts(noplaylist=True)

        def _work():
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(query, download=False)
                if info and "entries" in info:
                    info = next((e for e in info["entries"] if e), info)
                return info

        return await self.bot.loop.run_in_executor(None, _work)

    async def download_track(self, track: "Track", guild_id: int) -> Optional[tuple]:
        """Download a track's audio into the per-guild cache dir.

        Returns (file_path, acodec) or None on failure. Runs in a thread.
        """
        guild_dir = os.path.join(_MUSIC_CACHE_DIR, str(guild_id))
        os.makedirs(guild_dir, exist_ok=True)
        opts = self._ytdl_opts(
            noplaylist=True,
            skip_download=False,
            paths={"home": guild_dir},
            outtmpl={"default": "%(id)s.%(ext)s"},
        )

        def _work():
            with yt_dlp.YoutubeDL(opts) as ydl:
                # Resolve metadata first: a live stream never finishes
                # downloading, so bail before pulling any bytes.
                info = ydl.extract_info(track.query, download=False)
                if info and "entries" in info:
                    info = next((e for e in info["entries"] if e), info)
                if not info or info.get("is_live") or not info.get("duration"):
                    return None
                acodec = info.get("acodec") or ""
                # Reuse an already-cached file (music_save_videos keeps them).
                candidate = ydl.prepare_filename(info)
                if os.path.isfile(candidate):
                    return candidate, acodec
                info = ydl.process_ie_result(info, download=True)
                return ydl.prepare_filename(info), (info.get("acodec") or "")

        try:
            result = await self.bot.loop.run_in_executor(None, _work)
        except Exception as exc:
            self._note_ratelimit(exc)
            raise
        if not result:
            return None
        path, acodec = result
        if not path or not os.path.isfile(path):
            return None
        if self.save_videos() and (self.cache_max_mb() or self.cache_max_age_days()):
            await self.bot.loop.run_in_executor(None, self._enforce_cache_limits)
        return path, acodec

    async def search(
        self,
        query: str,
        *,
        requester_id: int,
        requester_name: str,
        limit: int = 1,
        playlist_cap: int | None = None,
    ) -> list[Track]:
        """Resolve a query (URL, playlist, or search terms) into Track metadata."""
        if _SPOTIFY_RE.search(query):
            return await self._resolve_spotify(query, requester_id, requester_name)

        is_url = query.startswith("http://") or query.startswith("https://")
        extra = {} if playlist_cap is None else {"playlistend": playlist_cap}
        opts = self._ytdl_opts(**extra)
        if is_url:
            opts["extract_flat"] = "in_playlist"
        else:
            query = f"{self.search_service()}{limit}:{query}"

        def _work():
            with yt_dlp.YoutubeDL(opts) as ydl:
                return ydl.extract_info(query, download=False)

        info = await self.bot.loop.run_in_executor(None, _work)
        if not info:
            return []
        entries = info["entries"] if "entries" in info else [info]
        tracks = []
        for entry in entries:
            if entry:
                tracks.append(Track.from_info(entry, requester_id, requester_name))
        return tracks

    # ── Spotify (scrape metadata, search YouTube lazily at play time) ──────────
    async def _resolve_spotify(
        self, url: str, requester_id: int, requester_name: str
    ) -> list[Track]:
        """Turn a Spotify track/album/playlist link into search-backed Tracks.

        No Spotify API key is used: the public embed page carries the track
        names + artists. Each Track's ``query`` is an "artist title" string, so
        the actual audio is resolved from YouTube at play time via
        ``resolve_stream`` (default_search=ytsearch).
        """
        m = _SPOTIFY_RE.search(url)
        if not m:
            return []
        kind = m.group(1) or m.group(3)
        sid = m.group(2) or m.group(4)
        embed_url = f"https://open.spotify.com/embed/{kind}/{sid}"

        try:
            async with aiohttp.ClientSession(
                headers={"User-Agent": _BROWSER_UA}
            ) as session:
                async with session.get(
                    embed_url, timeout=aiohttp.ClientTimeout(total=15)
                ) as r:
                    html = await r.text()
            tracks = self._parse_spotify_embed(html, url, requester_id, requester_name)
            if tracks:
                return tracks
        except Exception as exc:
            log.debug("Spotify embed scrape failed for %s: %s", embed_url, exc)

        # Fallback: oEmbed gives at least a title for a single resource.
        return await self._spotify_oembed(url, requester_id, requester_name)

    def _parse_spotify_embed(
        self, html: str, url: str, requester_id: int, requester_name: str
    ) -> list[Track]:
        m = _SPOTIFY_NEXT_DATA.search(html)
        if not m:
            return []
        data = json.loads(m.group(1))
        entity = data["props"]["pageProps"]["state"]["data"]["entity"]
        items = entity.get("trackList") or [entity]
        default_cover = _spotify_cover(entity)

        tracks: list[Track] = []
        for item in items:
            title = item.get("title") or item.get("name")
            if not title:
                continue
            subtitle = item.get("subtitle")
            if not subtitle:
                artists = item.get("artists") or entity.get("artists") or []
                subtitle = ", ".join(
                    a.get("name", "") for a in artists if isinstance(a, dict)
                ).strip(", ")
            dur_ms = item.get("duration") or item.get("duration_ms")
            query = f"{subtitle} {title}".strip() if subtitle else title
            display = f"{title} — {subtitle}" if subtitle else title
            tracks.append(
                Track(
                    query=query,
                    title=display,
                    duration=int(dur_ms / 1000) if dur_ms else None,
                    webpage_url=url,
                    thumbnail=_spotify_cover(item) or default_cover,
                    uploader=subtitle or "Spotify",
                    requester_id=requester_id,
                    requester_name=requester_name,
                )
            )
        return tracks

    async def _spotify_oembed(
        self, url: str, requester_id: int, requester_name: str
    ) -> list[Track]:
        api = "https://open.spotify.com/oembed?url=" + url
        try:
            async with aiohttp.ClientSession(
                headers={"User-Agent": _BROWSER_UA}
            ) as session:
                async with session.get(
                    api, timeout=aiohttp.ClientTimeout(total=15)
                ) as r:
                    if r.status != 200:
                        return []
                    data = await r.json()
        except Exception:
            return []
        title = (data.get("title") or "").strip()
        if not title:
            return []
        return [
            Track(
                query=title,
                title=title,
                duration=None,
                webpage_url=url,
                thumbnail=data.get("thumbnail_url"),
                uploader="Spotify",
                requester_id=requester_id,
                requester_name=requester_name,
            )
        ]

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
            try:
                await author_vc.connect(self_deaf=self.self_deafen())
            except Exception as exc:
                log.warning("Connect failed in %s: %s", ctx.guild.id, exc)
                await ctx.reply(
                    embed=h.err(f"Couldn't join the channel.\n`{exc}`"), ephemeral=True
                )
                return None
        elif voice.channel != author_vc:
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
            tracks = await self.search(
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
        player = await self._ensure_voice(ctx, join=True)
        if player is None:
            return
        await ctx.defer()
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
        if not humans:
            await asyncio.sleep(20)
            if player.voice and player.voice.is_connected():
                still = [m for m in player.voice.channel.members if not m.bot]
                if not still:
                    await player._announce(
                        h.info("Left the channel — everyone disconnected.", "👋 Bye")
                    )
                    await player.destroy(reason="alone")

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

        try:
            await voice_channel.connect(self_deaf=self.self_deafen())
        except Exception as exc:
            log.warning("Resume connect failed for guild %s: %s", guild.id, exc)
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

    # ══════════════════════════════════════════════════════════════════════════
    # Commands
    # ══════════════════════════════════════════════════════════════════════════
    @commands.hybrid_command(
        name="play",
        aliases=["p"],
        description="Play a song or playlist — paste a URL or type search terms.",
        extras={
            "category": "🎵 Music",
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
            player = await self._ensure_voice(ctx, join=True)
            if player is None:
                return
            await ctx.defer()
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
            tracks = await self.search(
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
            player.skip()
            return await ctx.reply(embed=h.ok(f"Skipped **{title}**.", "⏭️ Skipped"))

        needed = max(1, math.ceil(self.skip_ratio() * len(listeners)))
        player.skip_votes.add(ctx.author.id)
        present = {m.id for m in listeners}
        votes = len(player.skip_votes & present)

        if votes >= needed:
            player.skip()
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
        player.skip()
        await ctx.reply(embed=h.ok(f"Force-skipped **{title}**.", "⏭️ Skipped"))

    @commands.hybrid_command(
        name="jump",
        aliases=["skipto"],
        description="Skip ahead to a track at a given queue position.",
        extras={
            "category": "🎵 Music",
            "short": "Jump to a queue position",
            "usage": "jump <position>",
            "desc": "Discards everything before the chosen position and plays it now.",
            "args": [("position", "Queue position to jump to")],
            "perms": "None",
            "example": "{prefix}jump 4",
        },
    )
    @app_commands.describe(position="Queue position to jump to")
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
        if not player:
            return await ctx.reply(
                embed=h.err("I'm not playing anything."), ephemeral=True
            )
        await player.destroy(reason="stop command")
        await ctx.reply(embed=h.ok("Stopped and left the channel.", "⏹️ Stopped"))

    @commands.hybrid_command(
        name="pause",
        description="Pause the current track.",
        extras={
            "category": "🎵 Music",
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

    @commands.hybrid_command(
        name="move",
        description="Move a queued track to a new position.",
        extras={
            "category": "🎵 Music",
            "short": "Reorder a queued track",
            "usage": "move <from> <to>",
            "desc": "Moves the track at one queue position to another.",
            "args": [("from_pos", "Current position"), ("to_pos", "New position")],
            "perms": "None",
            "example": "{prefix}move 5 1",
        },
    )
    @app_commands.describe(from_pos="Current position", to_pos="New position")
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

    @commands.hybrid_command(
        name="remove",
        aliases=[],
        description="Remove a track from the queue by its position.",
        extras={
            "category": "🎵 Music",
            "short": "Remove a queued track",
            "usage": "remove <position>",
            "desc": "Removes a track from the queue using the number shown in `queue`.",
            "args": [("position", "Queue position (see !queue)")],
            "perms": "None",
            "example": "{prefix}remove 3",
        },
    )
    @app_commands.describe(position="Queue position (see /queue)")
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

    @commands.hybrid_command(
        name="clear",
        description="Empty the queue (keeps the current track playing).",
        extras={
            "category": "🎵 Music",
            "short": "Clear the queue",
            "usage": "clear",
            "desc": "Removes every upcoming track. The current track keeps playing.",
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
        description="Set or adjust the playback volume (0-200).",
        extras={
            "category": "🎵 Music",
            "short": "Set playback volume",
            "usage": "volume <0-200 | +N | -N>",
            "desc": (
                "Sets playback volume as a percentage (100 is full source volume). "
                "Use a leading + or - to adjust relative to the current volume, "
                "e.g. `volume +10` or `volume -10`."
            ),
            "args": [("amount", "0-200, or +N / -N to adjust")],
            "perms": "None",
            "example": "{prefix}volume +10",
        },
    )
    @app_commands.describe(amount="0-200, or +N / -N to adjust")
    @commands.guild_only()
    async def volume(self, ctx: commands.Context, amount: str):
        player = self._active_player(ctx)
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
        description="Jump to a position in the current track (e.g. 1:30 or 90).",
        extras={
            "category": "🎵 Music",
            "short": "Seek within the current track",
            "usage": "seek <position>",
            "desc": (
                "Jumps to a position in the current track. Accepts `M:SS`, `H:MM:SS`, "
                "or a plain number of seconds."
            ),
            "args": [("position", "Timestamp like 1:30 or seconds like 90")],
            "perms": "None",
            "example": "{prefix}seek 1:30",
        },
    )
    @app_commands.describe(position="Timestamp (1:30) or seconds (90)")
    @commands.guild_only()
    async def seek(self, ctx: commands.Context, position: str):
        player = self._active_player(ctx)
        if not player or player.current is None:
            return await ctx.reply(embed=h.err("Nothing is playing."), ephemeral=True)
        secs = _parse_timestamp(position)
        if secs is None:
            return await ctx.reply(
                embed=h.err("Invalid timestamp. Use `1:30`, `0:45`, or `90`."),
                ephemeral=True,
            )
        dur = player.current.duration
        if dur and secs >= dur:
            return await ctx.reply(
                embed=h.err(f"That's past the end (`{_fmt_time(dur)}`)."),
                ephemeral=True,
            )
        if not await player.seek(secs):
            return await ctx.reply(
                embed=h.err("Couldn't seek right now."), ephemeral=True
            )
        await ctx.reply(embed=h.ok(f"Seeking to `{_fmt_time(secs)}`.", "⏩ Seek"))

    @commands.hybrid_command(
        name="replay",
        aliases=[],
        description="Restart the current track from the beginning.",
        extras={
            "category": "🎵 Music",
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
        name="lyrics",
        extras={
            "category": "🎵 Music",
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
        name="autoplay",
        extras={
            "category": "🎵 Music",
            "short": "Toggle autoplay",
            "usage": "autoplay [on|off]",
            "desc": (
                "When on, the bot keeps playing random tracks from the server "
                "autoplaylist once the queue empties. Manage the list with "
                "`autoplaylist`."
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
        entries = await db.get_autoplaylist(ctx.guild.id)
        note = ""
        if player.autoplay and not entries:
            note = "\n_The autoplaylist is empty — add tracks with `autoplaylist add`._"
        await ctx.reply(
            embed=h.ok(
                f"Autoplay is now **{'on' if player.autoplay else 'off'}**.{note}",
                "📻 Autoplay",
            )
        )

    @commands.command(
        name="radio",
        aliases=["247", "stay"],
        extras={
            "category": "🎵 Music",
            "short": "Toggle 24/7 stay-connected mode",
            "usage": "radio [on|off]",
            "desc": (
                "When on, the bot stays connected to its voice channel even when "
                "everyone leaves and the queue runs dry — overriding the normal "
                "leave-when-empty/idle behavior. Pair with `autoplay` for a "
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

    # ── /autoplaylist group ─────────────────────────────────────────────────────
    apl_group = app_commands.Group(
        name="autoplaylist",
        description="Manage the persistent server autoplaylist.",
        guild_only=True,
    )

    @apl_group.command(
        name="add", description="Add a track or whole playlist to the autoplaylist."
    )
    @app_commands.describe(url="A track, video, or playlist URL (or search terms)")
    async def apl_add(self, interaction: discord.Interaction, url: str):
        await interaction.response.defer(ephemeral=True)
        if not YTDLP_AVAILABLE:
            return await interaction.followup.send(
                embed=h.err("Music support isn't installed."), ephemeral=True
            )

        try:
            tracks = await self.search(
                url,
                requester_id=interaction.user.id,
                requester_name="apl",
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
                embed=h.warn("Everything in that link is already in the autoplaylist."),
                ephemeral=True,
            )

        # Single track vs. playlist gets a tailored confirmation.
        if len(tracks) == 1:
            msg = f"Added **{tracks[0].title}** to the autoplaylist."
        else:
            msg = f"Added **{added}** track(s) to the autoplaylist."
            if skipped:
                msg += f" ({skipped} already present, skipped.)"
        await interaction.followup.send(
            embed=h.ok(msg, "📻 Autoplaylist"), ephemeral=True
        )

    @apl_group.command(
        name="remove", description="Remove a track from the autoplaylist by position."
    )
    @app_commands.describe(position="Position shown in /autoplaylist list")
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
                f"Removed **{entry['title'] or entry['url']}**.", "📻 Autoplaylist"
            ),
            ephemeral=True,
        )

    @apl_group.command(name="list", description="Show the server autoplaylist.")
    async def apl_list(self, interaction: discord.Interaction):
        entries = await db.get_autoplaylist(interaction.guild_id)
        if not entries:
            return await interaction.response.send_message(
                embed=h.info(
                    "The autoplaylist is empty.\nAdd tracks with `/autoplaylist add`.",
                    "📻 Autoplaylist",
                ),
                ephemeral=True,
            )
        needs_pages = len(entries) > PAGE_SIZE_APL
        view = AplPageView(entries, interaction.user.id) if needs_pages else None
        embed = view.build_embed() if needs_pages else _apl_single_embed(entries)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        if needs_pages:
            view.message = await interaction.original_response()

    @apl_group.command(
        name="clear", description="Remove every track from the autoplaylist."
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def apl_clear(self, interaction: discord.Interaction):
        n = await db.clear_autoplaylist(interaction.guild_id)
        await interaction.response.send_message(
            embed=h.ok(
                f"Cleared **{n}** track(s) from the autoplaylist.", "📻 Cleared"
            ),
            ephemeral=True,
        )

    @commands.command(
        name="stream",
        extras={
            "category": "🎵 Music",
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
        player = await self._ensure_voice(ctx, join=True)
        if player is None:
            return
        await ctx.defer()
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

    # ── /blocksong group (slash interface for song block list) ─────────────────
    blocksong_group = app_commands.Group(
        name="blocksong",
        description="Manage the server's song block list.",
        guild_only=True,
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

    # ── /blockuser group (slash interface for music user block list) ───────────
    blockuser_group = app_commands.Group(
        name="blockuser",
        description="Manage the server's music user block list.",
        guild_only=True,
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
        name="autoplay", description="Toggle autoplay from the autoplaylist."
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
        await ctx.reply(
            embed=h.ok(
                f"Connected to **{ctx.author.voice.channel.name}**.", "🎤 Joined"
            )
        )


def _spotify_cover(obj: dict) -> Optional[str]:
    """Pull the highest-resolution cover-art URL from a Spotify embed object."""
    if not isinstance(obj, dict):
        return None
    art = obj.get("coverArt") or obj.get("visualIdentity") or {}
    sources = art.get("sources") if isinstance(art, dict) else None
    if sources:
        return sources[-1].get("url")
    return None


def _split_artist_title(query: str) -> tuple[str, str]:
    """Best-effort split of a query/title into (artist, title) for lyrics lookup."""
    cleaned = _LYRICS_NOISE.sub("", query)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -·|")
    if " - " in cleaned:
        artist, title = cleaned.split(" - ", 1)
        return artist.strip(), title.strip()
    return "", cleaned.strip()


# ── Setup ──────────────────────────────────────────────────────────────────────
async def setup(bot: commands.Bot):
    await bot.add_cog(Music(bot))
