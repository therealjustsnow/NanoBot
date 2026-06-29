"""Role-panel helpers: per-guild autogen concurrency locks, the short id
generator, and the persistent button custom_id encode/decode.
"""

import asyncio
import random
import string

# ── Per-guild autogen concurrency locks ────────────────────────────────────────
_autogen_locks: dict[int, asyncio.Lock] = {}


def _get_autogen_lock(guild_id: int) -> asyncio.Lock:
    if guild_id not in _autogen_locks:
        _autogen_locks[guild_id] = asyncio.Lock()
    return _autogen_locks[guild_id]


# ── ID generator ───────────────────────────────────────────────────────────────
def _new_id(length: int = 6) -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=length))


# ── Button custom_id encoding ──────────────────────────────────────────────────
# Format: "rp:{panel_id}:{role_id}"  — survives restarts, no state in view object.


def _encode_cid(panel_id: str, role_id: int) -> str:
    return f"rp:{panel_id}:{role_id}"


def _decode_cid(custom_id: str) -> tuple[str, int] | None:
    parts = custom_id.split(":")
    if len(parts) != 3 or parts[0] != "rp":
        return None
    try:
        return parts[1], int(parts[2])
    except ValueError:
        return None
