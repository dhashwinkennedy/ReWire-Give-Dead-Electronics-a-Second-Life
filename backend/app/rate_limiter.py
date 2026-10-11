"""
Sliding-window in-memory rate limiter for compute-heavy endpoints.
Protects AI vision inference and Ollama reranking from denial-of-service or burst traffic.
"""
from __future__ import annotations

import collections
import threading
import time
from typing import Any

from app.exceptions import RateLimitExceededError


class SlidingWindowRateLimiter:
    """Thread-safe rate limiter tracking client requests in a sliding time window."""

    def __init__(self, max_requests: int = 40, window_seconds: int = 60) -> None:
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._history: dict[str, collections.deque[float]] = collections.defaultdict(collections.deque)
        self._lock = threading.Lock()

    def check(self, client_ip: str, endpoint: str = "") -> None:
        """Verify client request quota. Raises RateLimitExceededError if exceeded."""
        if not client_ip or client_ip in {"127.0.0.1", "localhost", "testclient"}:
            # Bypass rate limit for local tests or internal caller
            return

        now = time.time()
        window_start = now - self.window_seconds

        with self._lock:
            timestamps = self._history[client_ip]

            # Prune timestamps older than window
            while timestamps and timestamps[0] < window_start:
                timestamps.popleft()

            if len(timestamps) >= self.max_requests:
                oldest = timestamps[0]
                retry_after = max(1, int(oldest + self.window_seconds - now))
                raise RateLimitExceededError(retry_after=retry_after, endpoint=endpoint)

            timestamps.append(now)

    def get_stats(self) -> dict[str, Any]:
        """Return rate limiter operational metrics."""
        with self._lock:
            active_ips = len(self._history)
            total_tracked = sum(len(q) for q in self._history.values())
        return {
            "max_requests": self.max_requests,
            "window_seconds": self.window_seconds,
            "tracked_clients": active_ips,
            "active_window_requests": total_tracked,
        }


# Global limiters for expensive operations
vision_rate_limiter = SlidingWindowRateLimiter(max_requests=40, window_seconds=60)
ai_search_rate_limiter = SlidingWindowRateLimiter(max_requests=30, window_seconds=60)
