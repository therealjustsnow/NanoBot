"""Audio source layer: yt-dlp extraction, downloading, the on-disk cache, and
Spotify metadata scraping. Kept separate from the cog so the command surface in
cog.py stays focused on Discord wiring."""

import json
import logging
import os
import shutil
import time
from typing import Optional, TYPE_CHECKING

import aiohttp

from .constants import (
    _YTDL_BASE,
    _MUSIC_CACHE_DIR,
    _SPOTIFY_RE,
    _SPOTIFY_NEXT_DATA,
    _BROWSER_UA,
)
from .helpers import _spotify_cover
from .track import Track

log = logging.getLogger("NanoBot.music")

try:
    import yt_dlp

    YTDLP_AVAILABLE = True
except ImportError:  # pragma: no cover - optional dependency
    yt_dlp = None
    YTDLP_AVAILABLE = False

if TYPE_CHECKING:
    from .cog import Music


class MusicSource:
    """Owns yt-dlp extraction, downloading, and the per-guild disk cache."""

    def __init__(self, cog: "Music"):
        self.cog = cog

    def _in_use_files(self) -> set[str]:
        in_use: set[str] = set()
        for player in self.cog.players.values():
            if player.current and player.current.local_path:
                in_use.add(player.current.local_path)
            in_use |= player._dl_files
        return in_use

    def _enforce_cache_limits(self) -> None:
        max_mb = self.cog.cache_max_mb()
        max_age = self.cog.cache_max_age_days()
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
        val = self.cog.bot.config.get("music_cookie_file")
        return val or None

    def _js_runtimes(self) -> dict:
        # explicit path override via config
        cfg_path = (self.cog.bot.config.get("music_js_runtime_path") or "").strip()
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
            self.cog.bot.config.get("music_source_address") or "0.0.0.0"
        )
        opts["default_search"] = self.cog.search_service()
        proxy = (self.cog.bot.config.get("music_proxy") or "").strip()
        if proxy:
            opts["proxy"] = proxy
        ua = (self.cog.bot.config.get("music_user_agent") or "").strip()
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

        return await self.cog.bot.loop.run_in_executor(None, _work)

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
            result = await self.cog.bot.loop.run_in_executor(None, _work)
        except Exception as exc:
            self.cog._note_ratelimit(exc)
            raise
        if not result:
            return None
        path, acodec = result
        if not path or not os.path.isfile(path):
            return None
        if self.cog.save_videos() and (
            self.cog.cache_max_mb() or self.cog.cache_max_age_days()
        ):
            await self.cog.bot.loop.run_in_executor(None, self._enforce_cache_limits)
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
            query = f"{self.cog.search_service()}{limit}:{query}"

        def _work():
            with yt_dlp.YoutubeDL(opts) as ydl:
                return ydl.extract_info(query, download=False)

        info = await self.cog.bot.loop.run_in_executor(None, _work)
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
                    artist=subtitle or None,
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
