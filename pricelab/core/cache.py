"""Deterministic, bounded result cache.

Replaces the previous `@st.cache_resource` keyed on `(file_bytes, cfg_json,
label)`: that design held every uploaded file's full analysis, including
matplotlib Figure objects, in process memory for the process lifetime with
no eviction, which is a real memory-exhaustion risk under concurrent users
on a shared server.

This cache holds only the analysis data (DataFrames, dicts, and the
narrative's `Finding` objects, which carry a chart *name* rather than a
Figure), never a rendered chart, and evicts the least-recently-used entry
once a configured entry count is exceeded. A Figure is cheap to rebuild from
already-computed series and is rebuilt fresh on every access rather than
retained, so nothing here ever holds one past the call that made it.

`BoundedCache` also supports an optional per-entry time-to-live, added for
`data.connectors`: an external agency's response should expire on its own
schedule (an hourly series polled once a day is stale a day early; a daily
series polled every minute is hammering the agency for nothing new), which
is a different axis from the LRU-by-count eviction the analysis cache uses
and does not need. A cache with no TTL configured behaves exactly as
before -- entries never expire by time, only by the LRU bound -- so the
analysis cache's behaviour is unchanged by this.
"""

from __future__ import annotations

import hashlib
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Generic, TypeVar

from .config import get_settings

T = TypeVar("T")


@dataclass
class _Entry(Generic[T]):
    value: T
    expires_at: float | None  # a `_clock()` reading; None means "never"


def content_key(*parts: bytes | str) -> str:
    """A deterministic key over any mix of byte and string inputs. Used to
    key the analysis cache on exactly what determines its output: the
    uploaded file's bytes and the run configuration's JSON, so a changed
    input always produces a different key rather than reusing a stale
    entry."""
    digest = hashlib.sha256()
    for part in parts:
        digest.update(part if isinstance(part, bytes) else part.encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


class BoundedCache(Generic[T]):
    """A minimal, explicit LRU cache.

    Deliberately not `functools.lru_cache`: that decorator keys on its
    function's positional arguments (which would mean keying on raw file
    bytes directly, fine in principle) but offers no way to invalidate one
    specific entry when a caller already knows an upstream input changed,
    and no way to inspect current occupancy, which the eviction test this
    module ships with needs.
    """

    def __init__(
        self,
        max_entries: int | None = None,
        default_ttl_seconds: float | None = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._max_entries = max_entries if max_entries is not None else get_settings().cache_max_entries
        if self._max_entries < 1:
            raise ValueError("a cache must hold at least one entry")
        self._default_ttl = default_ttl_seconds
        self._clock = clock
        self._store: OrderedDict[str, _Entry[T]] = OrderedDict()

    def get(self, key: str) -> T | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        if entry.expires_at is not None and self._clock() >= entry.expires_at:
            del self._store[key]
            return None
        self._store.move_to_end(key)
        return entry.value

    def set(self, key: str, value: T, ttl_seconds: float | None = None) -> None:
        ttl = ttl_seconds if ttl_seconds is not None else self._default_ttl
        expires_at = self._clock() + ttl if ttl is not None else None
        if key in self._store:
            self._store.move_to_end(key)
        self._store[key] = _Entry(value=value, expires_at=expires_at)
        while len(self._store) > self._max_entries:
            self._store.popitem(last=False)

    def invalidate(self, key: str) -> None:
        self._store.pop(key, None)

    def clear(self) -> None:
        self._store.clear()

    def __len__(self) -> int:
        return len(self._store)

    def __contains__(self, key: str) -> bool:
        return self.get(key) is not None


_analysis_cache: BoundedCache[dict[str, object]] | None = None


def get_analysis_cache() -> BoundedCache[dict[str, object]]:
    """The process-wide analysis result cache. Lazily constructed so it
    honours whatever `Settings.cache_max_entries` is in effect the first
    time it's actually needed, rather than whatever was in effect at import
    time."""
    global _analysis_cache
    if _analysis_cache is None:
        _analysis_cache = BoundedCache(max_entries=get_settings().cache_max_entries)
    return _analysis_cache


def reset_analysis_cache() -> None:
    """Test-only: drop the cached singleton so a newly configured
    `cache_max_entries` takes effect."""
    global _analysis_cache
    _analysis_cache = None
