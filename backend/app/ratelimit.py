"""Small in-process sliding-window rate limiter for expensive endpoints.

Suitable for a single API instance. Behind several replicas, swap it for a shared store
(e.g. Redis) — the interface is one ``check`` call.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

from app.errors import ApiError


class RateLimiter:
    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str, limit: int, window_seconds: float = 60.0) -> None:
        now = time.monotonic()
        with self._lock:
            hits = self._hits[key]
            while hits and now - hits[0] > window_seconds:
                hits.popleft()
            if len(hits) >= limit:
                retry = max(1, int(window_seconds - (now - hits[0])) + 1)
                raise ApiError(429, "rate_limited", f"Too many requests; retry in {retry}s.",
                               headers={"Retry-After": str(retry)})
            hits.append(now)

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


limiter = RateLimiter()
