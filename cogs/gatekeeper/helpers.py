"""Pure Gatekeeper helpers: the learnavatar SSRF guard and the perceptual
(difference) hash + Hamming distance used for stock-avatar matching.
"""

import io
import ipaddress
import logging
import socket
from typing import Optional
from urllib.parse import urlparse

log = logging.getLogger("NanoBot.gatekeeper")

try:
    from PIL import Image

    _PILLOW_OK = True
    # Decompression-bomb guard: a tiny crafted file can expand to a huge bitmap
    # and exhaust memory. Avatars are small, so cap well below Pillow's ~178M
    # default — anything bigger raises DecompressionBombError (caught at decode).
    Image.MAX_IMAGE_PIXELS = 24_000_000
except ImportError:  # pragma: no cover
    Image = None
    _PILLOW_OK = False
    log.warning("Pillow not installed — stock-avatar detection disabled.")


def _is_safe_public_url(url: str) -> bool:
    """True only for an http(s) URL whose host resolves entirely to public IPs.

    Blocks SSRF via /gatekeeper learnavatar: a Manage-Server user could otherwise
    point the fetch at loopback, link-local (cloud metadata at 169.254.169.254),
    or private-range addresses to probe the host's internal network. Every
    resolved address must be global, so a hostname that maps to a private IP is
    rejected too.
    """
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return False
    try:
        infos = socket.getaddrinfo(parsed.hostname, parsed.port or 0)
    except (socket.gaierror, UnicodeError, ValueError):
        return False
    if not infos:
        return False
    for info in infos:
        addr = info[4][0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            return False
        if not ip.is_global or ip.is_reserved:
            return False
    return True


# ══════════════════════════════════════════════════════════════════════════════
#  Perceptual hashing (pure Pillow — no extra dependency)
# ══════════════════════════════════════════════════════════════════════════════


def _dhash(image_bytes: bytes) -> Optional[int]:
    """Return a 64-bit difference hash of an image, or None on failure."""
    if not _PILLOW_OK:
        return None
    try:
        im = (
            Image.open(io.BytesIO(image_bytes))
            .convert("L")
            .resize((9, 8), Image.LANCZOS)
        )
    except Exception as exc:  # pragma: no cover - defensive
        log.debug(f"dhash decode failed: {exc}")
        return None
    px = im.tobytes()  # 9 wide * 8 tall, row-major, one byte per pixel ("L")
    bits = 0
    for row in range(8):
        base = row * 9
        for col in range(8):
            bits = (bits << 1) | (1 if px[base + col] > px[base + col + 1] else 0)
    return bits


def _hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")
