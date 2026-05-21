"""
cogs/music.py — Voice music player.

Streams audio from YouTube (and the many other sites yt-dlp supports) into a
voice channel. Designed mobile-first: a single "Now Playing" card carries
interactive buttons (play/pause, skip, stop, loop, shuffle, queue) so mods and
listeners can drive playback from a phone without typing commands.

Runtime requirements (see requirements.txt):
  - yt-dlp        — source extraction / search
  - PyNaCl        — Discord voice encryption
  - FFmpeg binary — must be installed on the host and on PATH

Commands (hybrid — slash + prefix), category "🎵 Music":
  play / p       — queue a song or playlist (URL or search terms)
  skip / s       — skip the current track
  stop           — stop, clear the queue, and leave the channel
  pause / resume — toggle playback
  nowplaying/np  — show the live Now Playing card
  queue / q      — show the upcoming queue
  volume / vol   — set playback volume (0-200)
  loop           — cycle loop mode: off → track → queue
  shuffle        — shuffle the queue
  remove         — remove a track from the queue by position
  clear          — empty the queue (keeps the current track)
  seek           — jump to a position in the current track
  join           — connect the bot to your voice channel
"""

import asyncio
import logging
import random
import time
from dataclasses import dataclass
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

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
_LOOP_LABEL = {
    LOOP_OFF: "Off",
    LOOP_TRACK: "🔂 Track",
    LOOP_QUEUE: "🔁 Queue",
}

# ── Limits / timeouts ───────────────────────────────────────────────────────────
IDLE_TIMEOUT = 180  # seconds alone/idle before auto-disconnect
MAX_QUEUE = 500
PLAYLIST_CAP = 50  # max tracks pulled from a single playlist
NP_REFRESH = 15  # seconds between live progress-bar refreshes

# ── yt-dlp options ──────────────────────────────────────────────────────────────
_YTDL_BASE = {
    "format": "bestaudio/best",
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
_FFMPEG_OPTS = "-vn"


def _fmt_time(seconds: float | int | None) -> str:
    """Format seconds as M:SS or H:MM:SS. Returns '🔴 LIVE' for None/0."""
    if not seconds:
        return "🔴 LIVE"
    seconds = int(seconds)
    h_, rem = divmod(seconds, 3600)
    m_, s_ = divmod(rem, 60)
    if h_:
        return f"{h_}:{m_:02d}:{s_:02d}"
    return f"{m_}:{s_:02d}"


def _progress_bar(elapsed: float, total: float | None, length: int = 18) -> str:
    """Render a slider-style progress bar."""
    if not total:
        return "🔘" + "▬" * (length - 1)
    frac = max(0.0, min(1.0, elapsed / total))
    filled = int(frac * (length - 1))
    return "▬" * filled + "🔘" + "▬" * (length - 1 - filled)


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

    @classmethod
    def from_info(cls, info: dict, requester: discord.abc.User) -> "Track":
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
            requester_id=requester.id,
            requester_name=requester.display_name,
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

    async def _refresh_message(self, interaction: discord.Interaction) -> None:
        self._sync()
        try:
            await interaction.response.edit_message(
                embed=self.player.now_playing_embed(), view=self
            )
        except discord.HTTPException:
            pass

    @discord.ui.button(emoji="⏸️", label="Pause", style=discord.ButtonStyle.primary)
    async def toggle(self, interaction: discord.Interaction, _: discord.ui.Button):
        voice = self.player.voice
        if voice and voice.is_paused():
            self.player.resume()
        elif voice and voice.is_playing():
            self.player.pause()
        await self._refresh_message(interaction)

    @discord.ui.button(emoji="⏭️", label="Skip", style=discord.ButtonStyle.secondary)
    async def skip(self, interaction: discord.Interaction, _: discord.ui.Button):
        self.player.skip()
        await interaction.response.send_message("⏭️ Skipped.", ephemeral=True)

    @discord.ui.button(emoji="⏹️", label="Stop", style=discord.ButtonStyle.danger)
    async def stop(self, interaction: discord.Interaction, _: discord.ui.Button):
        await interaction.response.send_message(
            "⏹️ Stopped and left the channel.", ephemeral=True
        )
        await self.player.destroy(reason="stop button")

    @discord.ui.button(label="Off", emoji="🔁", style=discord.ButtonStyle.secondary)
    async def loop_btn(self, interaction: discord.Interaction, _: discord.ui.Button):
        self.player.loop = _LOOP_NEXT[self.player.loop]
        await self._refresh_message(interaction)

    @discord.ui.button(emoji="🔀", label="Shuffle", style=discord.ButtonStyle.secondary)
    async def shuffle(self, interaction: discord.Interaction, _: discord.ui.Button):
        n = self.player.shuffle()
        await interaction.response.send_message(
            f"🔀 Shuffled **{n}** track(s).", ephemeral=True
        )


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
        self.volume: float = 0.5

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

        self._destroyed = False
        self._loop_task = self.bot.loop.create_task(self._player_loop())
        self._refresh_task = self.bot.loop.create_task(self._refresh_loop())

    # ── voice helpers ────────────────────────────────────────────────────────
    @property
    def voice(self) -> Optional[discord.VoiceClient]:
        return self.guild.voice_client

    # ── queue helpers ────────────────────────────────────────────────────────
    def add(self, track: Track) -> None:
        self.queue.append(track)
        self._added.set()

    def add_many(self, tracks: list[Track]) -> int:
        added = 0
        for t in tracks:
            if len(self.queue) >= MAX_QUEUE:
                break
            self.queue.append(t)
            added += 1
        if added:
            self._added.set()
        return added

    def shuffle(self) -> int:
        random.shuffle(self.queue)
        return len(self.queue)

    def clear(self) -> int:
        n = len(self.queue)
        self.queue.clear()
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

    def set_volume(self, value: float) -> None:
        self.volume = value
        src = getattr(self.voice, "source", None)
        if isinstance(src, discord.PCMVolumeTransformer):
            src.volume = value

    def position(self) -> float:
        """Elapsed seconds of the current track (pause-aware)."""
        if not self.current or not self._play_started:
            return 0.0
        ref = self._paused_at if self._paused_at is not None else time.monotonic()
        return self._base_offset + (ref - self._play_started)

    async def seek(self, seconds: float) -> bool:
        if not self.current or not self.voice:
            return False
        self._seek_to = max(0.0, seconds)
        # Restart the current track at the new offset without advancing the queue.
        if self.loop != LOOP_TRACK:
            self.queue.insert(0, self.current)
            self._added.set()
        self.voice.stop()
        return True

    # ── source resolution ────────────────────────────────────────────────────
    async def _make_source(self, track: Track) -> discord.PCMVolumeTransformer:
        info = await self.cog.resolve_stream(track.query)
        stream_url = info["url"]
        before = _FFMPEG_BEFORE
        offset = 0.0
        if self._seek_to is not None:
            offset = self._seek_to
            before = f"{before} -ss {offset}"
            self._seek_to = None
        audio = discord.FFmpegPCMAudio(
            stream_url, before_options=before, options=_FFMPEG_OPTS
        )
        self._base_offset = offset
        return discord.PCMVolumeTransformer(audio, volume=self.volume)

    # ── playback loop ────────────────────────────────────────────────────────
    async def _player_loop(self) -> None:
        await self.bot.wait_until_ready()
        try:
            while not self._destroyed:
                self._next.clear()

                if self.loop == LOOP_TRACK and self.current is not None:
                    track = self.current
                else:
                    track = await self._wait_for_track()
                    if track is None:
                        await self.destroy(reason="inactivity")
                        return

                self.current = track

                if not self.voice or not self.voice.is_connected():
                    await self.destroy(reason="disconnected")
                    return

                try:
                    source = await self._make_source(track)
                except Exception as exc:
                    log.warning("Failed to start '%s': %s", track.title, exc)
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

                await self._post_now_playing()
                await self._next.wait()

                if self._destroyed:
                    return

                if self.loop == LOOP_QUEUE and track is not None:
                    self.queue.append(track)
                    self._added.set()
                if self.loop != LOOP_TRACK:
                    self.current = None
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover - safety net
            log.error("Player loop crashed in %s: %s", self.guild.id, exc, exc_info=exc)

    async def _wait_for_track(self) -> Optional[Track]:
        while not self.queue:
            self._added.clear()
            try:
                await asyncio.wait_for(self._added.wait(), timeout=IDLE_TIMEOUT)
            except asyncio.TimeoutError:
                return None
            if self._destroyed:
                return None
        return self.queue.pop(0)

    def _after(self, error: Optional[Exception]) -> None:
        if error:
            log.warning("Voice playback error in %s: %s", self.guild.id, error)
        self.bot.loop.call_soon_threadsafe(self._next.set)

    # ── Now Playing card ──────────────────────────────────────────────────────
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
        if self.queue:
            nxt = self.queue[0]
            up_next = nxt.title if len(nxt.title) <= 50 else nxt.title[:47] + "…"
            e.add_field(
                name=f"Up Next ({len(self.queue)} in queue)",
                value=up_next,
                inline=False,
            )
        e.set_footer(text="NanoBot Music")
        return e

    async def _post_now_playing(self) -> None:
        if not self.text_channel:
            return
        self.now_view = Controls(self)
        embed = self.now_playing_embed()
        # Reuse the existing card if it's still the most recent message.
        await self._retire_now_playing(delete=True)
        try:
            self.now_msg = await self.text_channel.send(embed=embed, view=self.now_view)
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
    async def destroy(self, reason: str = "") -> None:
        if self._destroyed:
            return
        self._destroyed = True
        log.info("Destroying player for guild %s (%s)", self.guild.id, reason)

        self.queue.clear()
        self.current = None
        self._next.set()
        self._added.set()

        for task in (self._loop_task, self._refresh_task):
            if task and task is not asyncio.current_task():
                task.cancel()

        await self._retire_now_playing(delete=False)

        if self.voice and self.voice.is_connected():
            try:
                await self.voice.disconnect(force=True)
            except Exception:
                pass

        self.cog.players.pop(self.guild.id, None)


# ══════════════════════════════════════════════════════════════════════════════
class Music(commands.Cog):
    """Voice music player with an interactive Now Playing panel."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.players: dict[int, GuildPlayer] = {}

    async def cog_unload(self) -> None:
        for player in list(self.players.values()):
            await player.destroy(reason="cog unload")

    # ── extraction (runs in a thread to avoid blocking the loop) ───────────────
    async def resolve_stream(self, query: str) -> dict:
        """Resolve a single playable stream (used at play time)."""
        opts = {**_YTDL_BASE, "noplaylist": True}

        def _work():
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(query, download=False)
                if info and "entries" in info:
                    info = next((e for e in info["entries"] if e), info)
                return info

        return await self.bot.loop.run_in_executor(None, _work)

    async def search(self, query: str, requester: discord.abc.User) -> list[Track]:
        """Resolve a query (URL, playlist, or search terms) into Track metadata."""
        is_url = query.startswith("http://") or query.startswith("https://")
        opts = {**_YTDL_BASE, "playlistend": PLAYLIST_CAP}
        if is_url:
            # extract_flat keeps big playlists fast; single videos still resolve fully.
            opts["extract_flat"] = "in_playlist"
        else:
            query = f"ytsearch1:{query}"

        def _work():
            with yt_dlp.YoutubeDL(opts) as ydl:
                return ydl.extract_info(query, download=False)

        info = await self.bot.loop.run_in_executor(None, _work)
        if not info:
            return []

        entries = info["entries"] if "entries" in info else [info]
        tracks: list[Track] = []
        for entry in entries:
            if not entry:
                continue
            tracks.append(Track.from_info(entry, requester))
        return tracks

    # ── shared helpers ─────────────────────────────────────────────────────────
    def get_player(self, guild: discord.Guild) -> GuildPlayer:
        player = self.players.get(guild.id)
        if player is None or player._destroyed:
            player = GuildPlayer(self, guild)
            self.players[guild.id] = player
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

        player = self.get_player(ctx.guild)
        player.text_channel = ctx.channel

        voice = ctx.guild.voice_client
        if voice is None:
            if not join:
                await ctx.reply(
                    embed=h.err("I'm not in a voice channel."), ephemeral=True
                )
                return None
            try:
                await author_vc.connect()
            except Exception as exc:
                log.warning("Connect failed in %s: %s", ctx.guild.id, exc)
                await ctx.reply(
                    embed=h.err(f"Couldn't join the channel.\n`{exc}`"), ephemeral=True
                )
                return None
        elif voice.channel != author_vc:
            await voice.move_to(author_vc)

        return player

    def _active_player(self, ctx: commands.Context) -> Optional[GuildPlayer]:
        player = self.players.get(ctx.guild.id)
        if player is None or player._destroyed:
            return None
        return player

    # ── auto-disconnect when left alone ─────────────────────────────────────────
    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if member.bot:
            return
        player = self.players.get(member.guild.id)
        if not player or not player.voice or not player.voice.is_connected():
            return
        channel = player.voice.channel
        humans = [m for m in channel.members if not m.bot]
        if not humans:
            await asyncio.sleep(20)
            if player.voice and player.voice.is_connected():
                still = [m for m in player.voice.channel.members if not m.bot]
                if not still:
                    await player._announce(
                        h.info("Left the channel — everyone disconnected.", "👋 Bye")
                    )
                    await player.destroy(reason="alone")

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
                "(or other site) URL, a playlist URL, or plain search terms. If "
                "something is already playing it's added to the queue."
            ),
            "args": [("query", "A URL or search terms")],
            "perms": "None",
            "example": "!play never gonna give you up\n!play https://youtu.be/dQw4w9WgXcQ",
        },
    )
    @app_commands.describe(query="A URL, playlist URL, or search terms")
    @commands.guild_only()
    async def play(self, ctx: commands.Context, *, query: str):
        player = await self._ensure_voice(ctx, join=True)
        if player is None:
            return

        await ctx.defer()
        try:
            tracks = await self.search(query, ctx.author)
        except Exception as exc:
            log.warning("Search failed for %r: %s", query, exc)
            return await ctx.reply(
                embed=h.err(f"Couldn't find anything for that.\n`{exc}`"),
                ephemeral=True,
            )

        if not tracks:
            return await ctx.reply(embed=h.err("No results found."), ephemeral=True)

        was_idle = player.current is None and not player.queue

        if len(tracks) == 1:
            player.add(tracks[0])
            t = tracks[0]
            if not was_idle:
                pos = len(player.queue)
                e = h.embed(
                    "➕ Added to Queue",
                    f"**[{t.title}]({t.webpage_url})**\n"
                    f"`{_fmt_time(t.duration)}` · position **#{pos}** · "
                    f"requested by {t.requester_name}",
                    ACCENT,
                )
                if t.thumbnail:
                    e.set_thumbnail(url=t.thumbnail)
                await ctx.reply(embed=e)
            else:
                await ctx.reply(
                    embed=h.ok(
                        f"Now playing **[{t.title}]({t.webpage_url})**.",
                        "🎵 Starting Playback",
                    )
                )
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

    @commands.hybrid_command(
        name="skip",
        aliases=["s"],
        description="Skip the current track.",
        extras={
            "category": "🎵 Music",
            "short": "Skip the current track",
            "usage": "skip",
            "desc": "Stops the current track and advances to the next one in the queue.",
            "args": [],
            "perms": "None",
            "example": "!skip",
        },
    )
    @commands.guild_only()
    async def skip(self, ctx: commands.Context):
        player = self._active_player(ctx)
        if not player or player.current is None:
            return await ctx.reply(embed=h.err("Nothing is playing."), ephemeral=True)
        title = player.current.title
        player.skip()
        await ctx.reply(embed=h.ok(f"Skipped **{title}**.", "⏭️ Skipped"))

    @commands.hybrid_command(
        name="stop",
        aliases=["leave", "disconnect", "dc"],
        description="Stop playback, clear the queue, and leave the channel.",
        extras={
            "category": "🎵 Music",
            "short": "Stop and leave the voice channel",
            "usage": "stop",
            "desc": "Stops playback, empties the queue, and disconnects the bot.",
            "args": [],
            "perms": "None",
            "example": "!stop",
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
            "example": "!pause",
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
            "example": "!resume",
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
            "example": "!np",
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
            "example": "!queue",
        },
    )
    @commands.guild_only()
    async def queue(self, ctx: commands.Context):
        player = self._active_player(ctx)
        if not player or (player.current is None and not player.queue):
            return await ctx.reply(
                embed=h.info("The queue is empty.", "🎶 Queue"), ephemeral=True
            )

        lines = []
        if player.current:
            lines.append(
                f"**▶️ Now:** [{player.current.title}]({player.current.webpage_url}) "
                f"`{_fmt_time(player.current.duration)}`"
            )
        if player.queue:
            lines.append("")
            for i, t in enumerate(player.queue[:15], start=1):
                title = t.title if len(t.title) <= 55 else t.title[:52] + "…"
                lines.append(f"`{i:>2}.` {title} `{_fmt_time(t.duration)}`")
            if len(player.queue) > 15:
                lines.append(f"_…and {len(player.queue) - 15} more._")

        total = sum(t.duration or 0 for t in player.queue)
        e = h.embed(
            f"🎶 Queue · {len(player.queue)} track(s)",
            "\n".join(lines),
            ACCENT,
        )
        e.set_footer(
            text=f"Loop: {_LOOP_LABEL[player.loop]} · "
            f"Total queued: {_fmt_time(total)} · NanoBot Music"
        )
        await ctx.reply(embed=e)

    @commands.hybrid_command(
        name="volume",
        aliases=["vol"],
        description="Set the playback volume (0-200).",
        extras={
            "category": "🎵 Music",
            "short": "Set playback volume",
            "usage": "volume <0-200>",
            "desc": "Sets playback volume as a percentage. 100 is the default.",
            "args": [("level", "Volume percentage, 0-200")],
            "perms": "None",
            "example": "!volume 80",
        },
    )
    @app_commands.describe(level="Volume percentage (0-200)")
    @commands.guild_only()
    async def volume(self, ctx: commands.Context, level: int):
        player = self._active_player(ctx)
        if not player:
            return await ctx.reply(embed=h.err("Nothing is playing."), ephemeral=True)
        if not 0 <= level <= 200:
            return await ctx.reply(
                embed=h.err("Volume must be between **0** and **200**."),
                ephemeral=True,
            )
        player.set_volume(level / 100)
        await ctx.reply(embed=h.ok(f"Volume set to **{level}%**.", "🔊 Volume"))

    @commands.hybrid_command(
        name="loop",
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
            "example": "!loop\n!loop track",
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
        await ctx.reply(
            embed=h.ok(f"Loop mode: **{_LOOP_LABEL[player.loop]}**.", "🔁 Loop")
        )

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
            "example": "!shuffle",
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
        name="remove",
        aliases=["rm"],
        description="Remove a track from the queue by its position.",
        extras={
            "category": "🎵 Music",
            "short": "Remove a queued track",
            "usage": "remove <position>",
            "desc": "Removes a track from the queue using the number shown in `queue`.",
            "args": [("position", "Queue position (see !queue)")],
            "perms": "None",
            "example": "!remove 3",
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
            "example": "!clear",
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
            "example": "!seek 1:30",
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
        ok_ = await player.seek(secs)
        if not ok_:
            return await ctx.reply(
                embed=h.err("Couldn't seek right now."), ephemeral=True
            )
        await ctx.reply(embed=h.ok(f"Seeking to `{_fmt_time(secs)}`.", "⏩ Seek"))

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
            "example": "!join",
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


def _parse_timestamp(text: str) -> Optional[int]:
    """Parse 'H:MM:SS', 'M:SS', or plain seconds into an int. None if invalid."""
    text = text.strip()
    try:
        if ":" in text:
            parts = [int(p) for p in text.split(":")]
            if len(parts) == 2:
                m, s = parts
                return m * 60 + s
            if len(parts) == 3:
                hh, mm, ss = parts
                return hh * 3600 + mm * 60 + ss
            return None
        return int(text)
    except ValueError:
        return None


# ── Setup ──────────────────────────────────────────────────────────────────────
async def setup(bot: commands.Bot):
    await bot.add_cog(Music(bot))
