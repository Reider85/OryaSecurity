from __future__ import annotations

import time

from app.core.cache import DecisionCache


class TestDecisionCache:
    def test_set_and_get(self) -> None:
        cache = DecisionCache(ttl_seconds=60)
        cache.set("hello", "allow", "clean", [])
        entry, hit = cache.get("hello")
        assert hit is True
        assert entry is not None
        assert entry["verdict"] == "allow"

    def test_cache_miss(self) -> None:
        cache = DecisionCache()
        entry, hit = cache.get("nonexistent")
        assert hit is False
        assert entry is None

    def test_cache_expiry(self) -> None:
        cache = DecisionCache(ttl_seconds=-1)
        cache.set("hello", "allow", "clean", [])
        entry, hit = cache.get("hello")
        assert hit is False

    def test_cache_hit_miss_counting(self) -> None:
        cache = DecisionCache()
        cache.set("a", "allow", "clean", [])
        cache.get("a")
        cache.get("a")
        cache.get("b")
        assert cache.hit_count == 2
        assert cache.miss_count == 1

    def test_cache_size(self) -> None:
        cache = DecisionCache()
        assert cache.size == 0
        cache.set("a", "allow", "clean", [])
        assert cache.size == 1

    def test_cache_max_size_eviction(self) -> None:
        cache = DecisionCache(max_size=2)
        cache.set("a", "allow", "clean", [])
        time.sleep(0.001)
        cache.set("b", "allow", "clean", [])
        time.sleep(0.001)
        cache.set("c", "block", "bad", [])
        assert cache.size == 2

    def test_cache_flush(self) -> None:
        cache = DecisionCache()
        cache.set("a", "allow", "clean", [])
        cache.flush()
        assert cache.size == 0
        assert cache.hit_count == 0
        assert cache.miss_count == 0

    def test_make_key_is_sha256(self) -> None:
        key = DecisionCache._make_key("test")
        assert len(key) == 64
