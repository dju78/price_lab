"""Tests for the bounded analysis cache that replaced the unbounded
st.cache_resource keyed on file bytes plus config JSON."""

import gc
import weakref

import pytest

from pricelab.core.cache import BoundedCache, content_key


def test_content_key_is_deterministic_and_input_sensitive():
    a = content_key(b"file-bytes", '{"formula": "jevons"}', "label")
    b = content_key(b"file-bytes", '{"formula": "jevons"}', "label")
    c = content_key(b"file-bytes", '{"formula": "dutot"}', "label")
    assert a == b
    assert a != c


def test_get_and_set_round_trip():
    cache: BoundedCache[str] = BoundedCache(max_entries=4)
    cache.set("k1", "v1")
    assert cache.get("k1") == "v1"
    assert cache.get("missing") is None


def test_cache_evicts_the_least_recently_used_entry_under_a_configured_bound():
    cache: BoundedCache[str] = BoundedCache(max_entries=2)
    cache.set("k1", "v1")
    cache.set("k2", "v2")
    assert len(cache) == 2

    cache.set("k3", "v3")  # exceeds the bound of 2

    assert len(cache) == 2
    assert "k1" not in cache  # the oldest entry was evicted
    assert cache.get("k2") == "v2"
    assert cache.get("k3") == "v3"


def test_accessing_an_entry_protects_it_from_eviction():
    cache: BoundedCache[str] = BoundedCache(max_entries=2)
    cache.set("k1", "v1")
    cache.set("k2", "v2")
    cache.get("k1")  # k1 is now the most recently used
    cache.set("k3", "v3")  # k2, not k1, should be evicted

    assert "k1" in cache
    assert "k2" not in cache
    assert "k3" in cache


def test_evicted_entries_are_actually_released_from_memory():
    """The eviction policy is only meaningful if it actually frees memory:
    prove an evicted value has no remaining reference from the cache."""

    class Payload:
        pass

    cache: BoundedCache[Payload] = BoundedCache(max_entries=1)
    first = Payload()
    ref = weakref.ref(first)
    cache.set("k1", first)
    del first
    cache.set("k2", Payload())  # evicts k1

    gc.collect()
    assert ref() is None  # nothing keeps the evicted payload alive


def test_invalidate_removes_a_specific_entry():
    cache: BoundedCache[str] = BoundedCache(max_entries=4)
    cache.set("k1", "v1")
    cache.invalidate("k1")
    assert "k1" not in cache


def test_zero_or_negative_bound_is_rejected():
    with pytest.raises(ValueError):
        BoundedCache(max_entries=0)


# ---------------------------------------------------------------------
# Time-to-live, added for data.connectors: response caching on a schedule
# independent of the LRU-by-count bound the analysis cache uses.
# ---------------------------------------------------------------------
class _FakeClock:
    """A controllable clock so TTL expiry can be tested without a real
    sleep: advance it explicitly instead of waiting."""

    def __init__(self, start: float = 0.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_entry_with_no_ttl_never_expires_by_time():
    clock = _FakeClock()
    cache: BoundedCache[str] = BoundedCache(max_entries=4, clock=clock)
    cache.set("k1", "v1")
    clock.advance(10_000)
    assert cache.get("k1") == "v1"


def test_entry_expires_after_its_ttl_elapses():
    clock = _FakeClock()
    cache: BoundedCache[str] = BoundedCache(max_entries=4, clock=clock)
    cache.set("k1", "v1", ttl_seconds=60)
    clock.advance(59)
    assert cache.get("k1") == "v1"
    clock.advance(2)
    assert cache.get("k1") is None


def test_expired_entry_is_evicted_from_the_store_not_just_hidden():
    clock = _FakeClock()
    cache: BoundedCache[str] = BoundedCache(max_entries=4, clock=clock)
    cache.set("k1", "v1", ttl_seconds=60)
    clock.advance(61)
    assert "k1" not in cache
    assert len(cache) == 0  # actually removed, not merely unreachable via get()


def test_default_ttl_applies_when_set_does_not_override_it():
    clock = _FakeClock()
    cache: BoundedCache[str] = BoundedCache(max_entries=4, default_ttl_seconds=30, clock=clock)
    cache.set("k1", "v1")
    clock.advance(31)
    assert cache.get("k1") is None


def test_per_call_ttl_overrides_the_cache_wide_default():
    clock = _FakeClock()
    cache: BoundedCache[str] = BoundedCache(max_entries=4, default_ttl_seconds=30, clock=clock)
    cache.set("k1", "v1", ttl_seconds=300)
    clock.advance(31)
    assert cache.get("k1") == "v1"  # outlives the default because of its own longer ttl
