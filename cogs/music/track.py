"""The Track dataclass: one queued item."""

from dataclasses import dataclass
from typing import Optional

from .helpers import _clean_artist


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
    # Cleaned artist label (de-duplicated vs the title). May be None.
    artist: Optional[str] = None

    # Set when the track has been predownloaded to a local file (not persisted).
    local_path: Optional[str] = None
    acodec: Optional[str] = None
    # True once metadata enrichment has run for this track (not persisted).
    meta_checked: bool = False

    def to_dict(self) -> dict:
        """Serialise the persistable fields (skips ephemeral download state)."""
        return {
            "query": self.query,
            "title": self.title,
            "duration": self.duration,
            "webpage_url": self.webpage_url,
            "thumbnail": self.thumbnail,
            "uploader": self.uploader,
            "artist": self.artist,
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
            artist=d.get("artist"),
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
        title = info.get("title") or "Unknown title"
        uploader = info.get("uploader") or info.get("channel")
        return cls(
            query=webpage or info.get("title", ""),
            title=title,
            duration=info.get("duration"),
            webpage_url=webpage,
            thumbnail=thumb,
            uploader=uploader,
            artist=_clean_artist(
                title, uploader, info.get("artist") or info.get("creator")
            ),
            requester_id=requester_id,
            requester_name=requester_name,
        )
