"""Rate limiting for uploads: a sliding window per signed-in user,
checked before a single byte of the upload is read.

An upload is the most expensive thing a user can ask this application to
do -- parsing, validation, cleaning, imputation and indexing of a
multi-year panel -- and the one place a single account could occupy the
server by accident (a stuck browser retrying) or on purpose. The limit is
per user rather than per address because every user is signed in, and
it is enforced by the Ingest page before `getvalue()` for the same reason
the size cap is: the point is to decline the work, not to do it and then
complain.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable

from .config import get_settings


class RateLimited(Exception):
    """The caller has used its allowance; `wait_seconds` says when the
    next request will be accepted."""

    def __init__(self, key: str, wait_seconds: float, limit: int) -> None:
        self.key, self.wait_seconds, self.limit = key, wait_seconds, limit
        super().__init__(
            f"{key} has started {limit} uploads in the last minute; the next will be "
            f"accepted in {wait_seconds:.0f} seconds")


class SlidingWindowLimiter:
    """At most `limit` events per `window_seconds` per key."""

    def __init__(self, limit: int, window_seconds: float = 60.0,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.limit, self.window, self._clock = limit, window_seconds, clock
        self._events: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def _prune(self, key: str, now: float) -> deque[float]:
        events = self._events.setdefault(key, deque())
        while events and now - events[0] >= self.window:
            events.popleft()
        return events

    def wait_time(self, key: str) -> float:
        """Seconds until `key` may proceed; 0.0 if it may proceed now."""
        with self._lock:
            now = self._clock()
            events = self._prune(key, now)
            if len(events) < self.limit:
                return 0.0
            return max(0.0, self.window - (now - events[0]))

    def acquire(self, key: str) -> None:
        """Record one event for `key`, or raise `RateLimited` without
        recording anything."""
        with self._lock:
            now = self._clock()
            events = self._prune(key, now)
            if len(events) >= self.limit:
                raise RateLimited(key, self.window - (now - events[0]), self.limit)
            events.append(now)

    def reset(self, key: str | None = None) -> None:
        with self._lock:
            if key is None:
                self._events.clear()
            else:
                self._events.pop(key, None)


_upload_limiter: SlidingWindowLimiter | None = None
_guard = threading.Lock()


def upload_limiter() -> SlidingWindowLimiter:
    """The process-wide upload limiter, sized from settings on first use."""
    global _upload_limiter
    with _guard:
        if _upload_limiter is None:
            _upload_limiter = SlidingWindowLimiter(get_settings().upload_rate_limit_per_minute)
        return _upload_limiter


def reset_upload_limiter() -> None:
    """Test-only."""
    global _upload_limiter
    with _guard:
        _upload_limiter = None
