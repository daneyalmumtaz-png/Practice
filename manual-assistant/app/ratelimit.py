"""A per-user sliding window limiter, in memory.

One process, one window. Good enough for a prototype behind one reverse proxy;
a second app replica would need shared state.
"""

from __future__ import annotations

import threading
import time
from collections import deque


class RateLimiter:
    def __init__(self, limit: int, window_seconds: float = 60.0) -> None:
        self.limit = limit
        self.window = window_seconds
        self._seen: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def check(self, key: str, *, now: float | None = None) -> tuple[bool, float]:
        """(allowed, seconds until a slot frees up)."""
        if self.limit <= 0:
            return True, 0.0
        moment = time.monotonic() if now is None else now
        with self._lock:
            window = self._seen.setdefault(key, deque())
            while window and moment - window[0] >= self.window:
                window.popleft()
            if len(window) >= self.limit:
                return False, max(0.0, self.window - (moment - window[0]))
            window.append(moment)
            return True, 0.0

    def reset(self) -> None:
        with self._lock:
            self._seen.clear()
