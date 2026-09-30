from __future__ import annotations

import asyncio
import pytest
import time
from unittest.mock import AsyncMock, patch

from app.core.cache import DecisionCache


@pytest.fixture(autouse=True)
def use_fake_redis(fake_redis, monkeypatch: pytest.MonkeyPatch):
    """Route DecisionCache through fakeredis so tests do not need a live Redis."""
    async def _get_redis():
        return fake_redis

    monkeypatch.setattr("app.core.cache.get_redis_connection", _get_redis)
    return fake_redis


class TestDecisionCache:
    @pytest.mark.asyncio
    async def test_set_and_get(self) -> None:
        cache = DecisionCache(ttl_seconds=60)
        await cache.set("hello", "allow", "clean", [])
        entry, hit = await cache.get("hello")
        assert hit is True
        assert entry is not None
        assert entry["verdict"] == "allow"

    @pytest.mark.asyncio
    async def test_cache_miss(self) -> None:
        cache = DecisionCache()
        entry, hit = await cache.get("nonexistent")
        assert hit is False
        assert entry is None

    @pytest.mark.asyncio
    async def test_cache_expiry(self) -> None:
        cache = DecisionCache(ttl_seconds=-1)
        await cache.set("hello", "allow", "clean", [])
        entry, hit = await cache.get("hello")
        assert hit is False

    @pytest.mark.asyncio
    async def test_cache_hit_miss_counting(self) -> None:
        cache = DecisionCache()
        await cache.set("a", "allow", "clean", [])
        await cache.get("a")
        await cache.get("a")
        await cache.get("b")
        assert cache.hit_count == 2
        assert cache.miss_count == 1

    @pytest.mark.asyncio
    async def test_cache_size(self) -> None:
        cache = DecisionCache()
        assert await cache.count() == 0
        await cache.set("a", "allow", "clean", [])
        assert await cache.count() == 1

    @pytest.mark.asyncio
    async def test_cache_max_size_eviction(self) -> None:
        # Note: Redis handles TTL automatically, max_size is not enforced at Redis level
        # This test mainly checks that we don't crash when setting many items
        cache = DecisionCache(max_size=2)
        await cache.set("a", "allow", "clean", [])
        await cache.set("b", "allow", "clean", [])
        await cache.set("c", "block", "bad", [])
        # All items should still be accessible (Redis doesn't enforce max_size)
        entry_a, hit_a = await cache.get("a")
        entry_b, hit_b = await cache.get("b")
        entry_c, hit_c = await cache.get("c")
        assert hit_a is True
        assert hit_b is True
        assert hit_c is True

    @pytest.mark.asyncio
    async def test_cache_flush(self) -> None:
        cache = DecisionCache()
        await cache.set("a", "allow", "clean", [])
        await cache.flush()
        assert cache.hit_count == 0
        assert cache.miss_count == 0
        entry, hit = await cache.get("a")
        assert hit is False

    @pytest.mark.asyncio
    async def test_make_key_is_sha256(self) -> None:
        # This is a static method, doesn't need Redis
        key = DecisionCache._make_key("test")
        assert len(key) == 64
        # Verify it's a valid hex string
        assert all(c in "0123456789abcdef" for c in key)

    @pytest.mark.asyncio
    async def test_cache_fallback_on_redis_error(self) -> None:
        """Test that cache gracefully falls back when Redis is down."""
        cache = DecisionCache()

        # Mock Redis to raise an exception
        with patch("app.core.cache.get_redis_connection") as mock_get_redis:
            mock_client = AsyncMock()
            mock_client.get.side_effect = Exception("Redis connection failed")
            mock_client.set.side_effect = Exception("Redis connection failed")
            mock_client.delete.side_effect = Exception("Redis connection failed")
            mock_client.scan.side_effect = Exception("Redis connection failed")
            mock_get_redis.return_value = mock_client

            # Test get fallback
            entry, hit = await cache.get("test")
            assert hit is False
            assert entry is None
            assert cache.miss_count == 1

            # Test set fallback (should not raise exception)
            await cache.set("test", "allow", "clean", [])

            # Test flush fallback
            await cache.flush()

            # New helpers degrade gracefully too
            assert await cache.count() == 0
            assert await cache.list_entries() == []
            stats = await cache.stats()
            assert stats["total_entries"] == 0
            assert stats["hit_rate_24h"] == 0.0
            assert await cache.delete_entry("a" * 64) is False

    @pytest.mark.asyncio
    async def test_cache_ttl_handling(self) -> None:
        """Test that TTL is properly handled in Redis."""
        cache = DecisionCache(ttl_seconds=1)

        # Set a value with 1 second TTL
        await cache.set("ttl_test", "allow", "clean", [])

        # Should be available immediately
        entry, hit = await cache.get("ttl_test")
        assert hit is True

        # Wait for TTL to expire
        await asyncio.sleep(1.1)

        # Should now be expired
        entry, hit = await cache.get("ttl_test")
        assert hit is False

    @pytest.mark.asyncio
    async def test_cache_json_serialization(self) -> None:
        """Test that complex data is properly serialized/deserialized."""
        cache = DecisionCache()

        rules_matched = [
            {"rule_id": "test_rule", "position": [0, 5], "severity": "high"},
            {"rule_id": "another_rule", "position": [10, 20], "severity": "medium"},
        ]

        await cache.set("complex_test", "block", "PII detected", rules_matched)

        entry, hit = await cache.get("complex_test")
        assert hit is True
        assert entry["rules_matched"] == rules_matched
        assert entry["verdict"] == "block"
        assert entry["reason"] == "PII detected"
