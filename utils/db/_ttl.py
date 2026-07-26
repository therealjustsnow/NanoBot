"""utils/db/_ttl.py — a tiny bounded TTL cache for repeated read-only queries.

Separate from `_cache.py` on purpose: that one holds per-guild *config* rows and
is invalidated by their setters, so it is never stale. This one is for results
that are expensive to compute and merely need to be *recent* — a server-scoped
leaderboard, where the alternative is re-running ceil(members/900) queries and
re-materialising every row because someone pressed the next-page button.

Entries expire by time, not by invalidation. Nothing here may back a decision
(a purchase, a debit, a claim) — only things a member reads.
"""

import time
from collections import OrderedDict
from typing import Any


class TTLCache:
    """Bounded, time-expiring memo. Oldest entry evicted when full."""

    def __init__(self, maxsize: int, ttl: float):
        self.maxsize = maxsize
        self.ttl = ttl
        self._data: OrderedDict[Any, tuple[float, Any]] = OrderedDict()
        self.hits = 0
        self.misses = 0

    def get(self, key, now: float | None = None):
        """Cached value, or None when absent or expired."""
        now = time.time() if now is None else now
        entry = self._data.get(key)
        if entry is None:
            self.misses += 1
            return None
        stored_at, value = entry
        if now - stored_at > self.ttl:
            del self._data[key]
            self.misses += 1
            return None
        self._data.move_to_end(key)
        self.hits += 1
        return value

    def put(self, key, value, now: float | None = None):
        """Store `value` and hand it back, so callers can `return put(…)`."""
        now = time.time() if now is None else now
        self._data[key] = (now, value)
        self._data.move_to_end(key)
        while len(self._data) > self.maxsize:
            self._data.popitem(last=False)
        return value

    def clear(self) -> None:
        self._data.clear()
        self.hits = self.misses = 0

    def __len__(self) -> int:
        return len(self._data)
