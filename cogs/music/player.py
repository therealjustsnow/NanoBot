"""GuildPlayer: per-guild playback engine (queue, player loop, predownload,
source building, Now Playing card)."""

import asyncio
import logging
import math
import os
import random
import shlex
import shutil
import time
from collections import deque
from typing import Optional, TYPE_CHECKING

import discord

from utils import db
from utils import helpers as h

from .constants import (
    ACCENT,
    LOOP_OFF,
    LOOP_TRACK,
    LOOP_QUEUE,
    _LOOP_LABEL,
    FILTERS,
    NP_REFRESH,
    PAGE_SIZE_QUEUE,
    NP_UP_NEXT,
    _FFMPEG_BEFORE,
    _MUSIC_CACHE_DIR,
    _OPUS_MAX_KBITRATE,
    _OPUS_MIN_KBITRATE,
    _GUILDPLAY_REQUESTER,
    _AUTOPLAY_REQUESTER,
)
from .helpers import _extract_ytid, _fmt_time, _ellipsize, _progress_bar
from .track import Track
from .views import Controls

log = logging.getLogger("NanoBot.music")

if TYPE_CHECKING:
    from .cog import Music


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
        self.autoplay: bool = False  # smart: queue YouTube-related tracks
        self.guildplay: bool = False  # play random tracks from the guild playlist
        # Smart autoplay seed pools. User seeds anchor the mix to your taste and
        # are never evicted by autoplay. Pick seeds carry the radio forward so
        # the queue is endless; kept separate so they can't crowd out the user
        # seeds (which is what made autoplay collapse onto one artist).
        self._smart_seeds: deque[str] = deque(maxlen=8)  # user-queued YouTube URLs
        self._smart_pick_seeds: deque[str] = deque(maxlen=5)  # recent autoplay picks
        self._smart_recent: deque[str] = deque(maxlen=50)  # recent URLs (de-dupe)
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
        """Kick off a background download of the next queued track.

        Runs when predownload is on (gapless playback) or when the cache is being
        kept (music_save_videos) — otherwise enabling the cache alone would never
        populate it, since downloading only happens here.
        """
        if (
            not (
                self.cog.predownload()
                or self.cog.save_videos()
                or self.cog.sponsorblock()
            )
            or self.loop == LOOP_TRACK
        ):
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
            result = await self.cog.source.download_track(track, self.guild.id)
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
        # SponsorBlock cuts only apply to a downloaded file (the postprocessor
        # rewrites it). If this track wasn't predownloaded yet, fetch it now so
        # its non-music segments are removed before playback — trading a little
        # start latency for the skip. Live streams (no duration) can't download,
        # so they fall through and stream uncut.
        if not local and self.cog.sponsorblock() and track.duration:
            try:
                result = await self.cog.source.download_track(track, self.guild.id)
            except Exception as exc:
                log.debug(
                    "SponsorBlock predownload failed for '%s': %s", track.title, exc
                )
                result = None
            if result:
                path, acodec = result
                self._dl_files.add(path)
                track.local_path = path
                track.acodec = acodec
                local = path
        if local:
            source_url = local
            before = ""
            src_codec = (track.acodec or "").lower()
            target_kb = self._target_kbitrate({})
        else:
            info = await self.cog.source.resolve_stream(track.query)
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
                        await self.destroy(reason="inactivity", persist_clear=False)
                        return

                self.current = track
                self.skip_votes.clear()

                # Seed smart autoplay from what the *user* queued, not from
                # autoplay's own picks. Feeding picks back in collapses the mix:
                # the maxlen=5 deque evicts the real songs, every seed becomes a
                # recent pick, and an artist's radio keeps surfacing that artist
                # → autoplay locks onto one artist. User tracks keep it varied.
                if track.webpage_url and not self._is_autoplay_track(track):
                    ytid = _extract_ytid(track.webpage_url)
                    if ytid:
                        self._smart_seeds.append(track.webpage_url)

                if not self.voice or not self.voice.is_connected():
                    await self.destroy(reason="disconnected", persist_clear=False)
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
        """Pop the queue, fall back to autoplay/guildplay, or wait until something arrives."""
        while not self._destroyed:
            if self.queue:
                return self.queue.pop(0)
            if self.autoplay:
                picked = await self._smart_autoplay_pick()
                if picked is not None:
                    return picked
                # Pick failed (rate-limit, network blip, etc.).  If we have
                # seeds, retry after a brief pause rather than falling through
                # to the idle wait — that would stall autoplay until the user
                # manually queues something.
                if self._smart_seeds:
                    await asyncio.sleep(5)
                    continue
            if self.guildplay:
                picked = await self._guildplay_pick()
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

    async def _guildplay_pick(self) -> Optional[Track]:
        entries = await db.get_autoplaylist(self.guild.id)
        if not entries:
            return None
        choice = random.choice(entries)
        try:
            tracks = await self.cog.source.search(
                choice["url"],
                requester_id=self.bot.user.id,
                requester_name=_GUILDPLAY_REQUESTER,
            )
        except Exception as exc:
            log.debug("Guild play resolve failed for %s: %s", choice["url"], exc)
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
        track.requester_name = _GUILDPLAY_REQUESTER
        return track

    async def _smart_autoplay_pick(self) -> Optional[Track]:
        """Pick a related track using YouTube's RD radio mix (endless queue).

        Builds an endless YouTube-Music-style radio that stays varied:

        - Seed pool = your queued tracks (anchor) + recent autoplay picks
          (forward momentum). Picks evolve the mix so it never runs dry, but
          they live in a separate deque so they can't evict the user anchors.
        - The seed whose mix we pull from is chosen at random, not newest-first,
          so it doesn't keep re-using the just-played track's radio.
        - The track within that mix is chosen at random, not first-eligible.

        Those three together stop autoplay collapsing onto a single artist while
        keeping the queue effectively infinite.
        """
        pool = list(self._smart_seeds) + list(self._smart_pick_seeds)
        seed_ids = [_extract_ytid(u) for u in pool]
        seed_ids = list(dict.fromkeys(s for s in seed_ids if s))  # dedup, keep YT only
        if not seed_ids:
            return None

        seed_id_set = set(seed_ids)
        order = seed_ids[:]
        random.shuffle(order)

        for ytid in order:
            mix_url = f"https://www.youtube.com/watch?v={ytid}&list=RD{ytid}"
            try:
                tracks = await self.cog.source.search(
                    mix_url,
                    requester_id=self.bot.user.id,
                    requester_name=_AUTOPLAY_REQUESTER,
                    playlist_cap=20,
                )
            except Exception as exc:
                log.debug("Smart autoplay mix fetch failed for seed %s: %s", ytid, exc)
                self.cog._note_ratelimit(exc)
                continue  # try next seed

            eligible = []
            for track in tracks:
                url = track.webpage_url or track.query or ""
                if not url:
                    continue
                tid = _extract_ytid(url)
                if tid and tid in seed_id_set:
                    continue  # don't replay a seed track itself
                if url in self._smart_recent:
                    continue  # already played recently
                eligible.append(track)
            if not eligible:
                continue  # this seed's mix is exhausted — try the next seed

            kept, _ = await self.cog._filter_blocked_songs(self.guild.id, eligible)
            if not kept:
                continue

            chosen = random.choice(kept)
            chosen.requester_name = _AUTOPLAY_REQUESTER
            url = chosen.webpage_url or chosen.query or ""
            self._smart_recent.append(url)
            # Carry the radio forward: this pick seeds the next round so the
            # queue never runs dry (but only in the pick pool, never the anchor).
            if chosen.webpage_url and _extract_ytid(chosen.webpage_url):
                self._smart_pick_seeds.append(chosen.webpage_url)
            return chosen

        return None

    def _is_autoplay_track(self, track: Optional[Track]) -> bool:
        return bool(track and self.bot.user and track.requester_id == self.bot.user.id)

    async def _handle_play_error(self, track: Track) -> None:
        """Prune a dead guild playlist entry when its track fails to play."""
        if not self._is_autoplay_track(track) or not self.cog.apl_prune_on_error():
            return
        if track.requester_name != _GUILDPLAY_REQUESTER:
            return  # only prune entries that came from the guild playlist
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
        if t.artist:
            e.add_field(name="Artist", value=t.artist, inline=True)
        elif t.uploader:
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
                title = _ellipsize(t.title, 45)
                preview_lines.append(f"`{i}.` {title} `{_fmt_time(t.duration)}`")
            if len(self.queue) > NP_UP_NEXT:
                preview_lines.append(f"_…and {len(self.queue) - NP_UP_NEXT} more_")
            e.add_field(
                name=f"Up Next ({len(self.queue)} in queue)",
                value="\n".join(preview_lines),
                inline=False,
            )
        e.set_footer(
            text="NanoBot Music"
            + (" · ✨ Autoplay" if self.autoplay else "")
            + (" · 📻 Guild Play" if self.guildplay else "")
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
                title = _ellipsize(t.title, 55)
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
        # Best-effort artist enrichment before the card renders. Never blocks
        # playback (audio already started) and fails silently.
        await self.cog._enrich_metadata(self.current)
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
