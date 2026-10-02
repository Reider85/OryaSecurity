from __future__ import annotations

import asyncio
import pytest
import time
from unittest.mock import AsyncMock, patch

from app.core.cache import DecisionCache


class TestRedisCacheIntegration:
    """Integration tests for Redis-backed decision cache using fakeredis fixtures."""

    @pytest.mark.asyncio
    async def test_cache_hit_returns_cached_verdict(self, redis_client) -> None:
        """Test that cache hit returns cached verdict."""
        cache = DecisionCache(ttl_seconds=60)
        
        # Set a value
        await cache.set("test_prompt", "allow", "clean prompt", [])
        
        # Get it back
        entry, hit = await cache.get("test_prompt")
        
        assert hit is True
        assert entry is not None
        assert entry["verdict"] == "allow"
        assert entry["reason"] == "clean prompt"
        assert entry["rules_matched"] == []

    @pytest.mark.asyncio
    async def test_cache_miss_returns_none(self, redis_client) -> None:
        """Test that cache miss returns None."""
        cache = DecisionCache()
        
        entry, hit = await cache.get("nonexistent_prompt")
        
        assert hit is False
        assert entry is None

    @pytest.mark.asyncio
    async def test_cache_ttl_expiry(self, redis_client) -> None:
        """Test that cache entries expire after TTL."""
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
    async def test_cache_overwrite(self, redis_client) -> None:
        """Test that setting the same prompt overwrites the previous value."""
        cache = DecisionCache(ttl_seconds=60)
        
        # Set initial value
        await cache.set("overwrite_test", "allow", "clean", [])
        
        # Overwrite with different value
        await cache.set("overwrite_test", "block", "contains PII", [{"rule_id": "test"}])
        
        # Should get the new value
        entry, hit = await cache.get("overwrite_test")
        assert hit is True
        assert entry["verdict"] == "block"
        assert entry["reason"] == "contains PII"
        assert entry["rules_matched"] == [{"rule_id": "test"}]

    @pytest.mark.asyncio
    async def test_cache_flush(self, redis_client) -> None:
        """Test that flush clears all entries."""
        cache = DecisionCache(ttl_seconds=60)
        
        # Set some values
        await cache.set("flush_test_1", "allow", "clean1", [])
        await cache.set("flush_test_2", "block", "blocked", [])
        
        # Verify they exist
        entry1, hit1 = await cache.get("flush_test_1")
        entry2, hit2 = await cache.get("flush_test_2")
        assert hit1 is True
        assert hit2 is True
        
        # Flush cache
        await cache.flush()
        
        # Should be gone
        entry1, hit1 = await cache.get("flush_test_1")
        entry2, hit2 = await cache.get("flush_test_2")
        assert hit1 is False
        assert hit2 is False

    @pytest.mark.asyncio
    async def test_cache_fallback_on_redis_down(self, redis_client) -> None:
        """Test that cache gracefully degrades when Redis is unavailable."""
        cache = DecisionCache()
        
        # Mock Redis to be unavailable
        with patch('app.db.redis_client.get_redis_connection') as mock_get_redis:
            mock_client = AsyncMock()
            mock_client.get.side_effect = Exception("Connection refused")
            mock_client.set.side_effect = Exception("Connection refused")
            mock_client.delete.side_effect = Exception("Connection refused")
            mock_get_redis.return_value = mock_client
            
            # Test get fallback
            entry, hit = await cache.get("test_fallback")
            assert hit is False
            assert entry is None
            assert cache.miss_count == 1
            
            # Test set fallback (should not raise exception)
            await cache.set("test_fallback", "allow", "clean", [])
            
            # Test flush fallback
            await cache.flush()

    @pytest.mark.asyncio
    async def test_cache_key_consistency(self, redis_client) -> None:
        """Test that the same prompt always generates the same key."""
        cache = DecisionCache()
        
        # Test that the same prompt generates the same key
        key1 = DecisionCache._make_key("test prompt")
        key2 = DecisionCache._make_key("test prompt")
        assert key1 == key2
        
        # Test that different prompts generate different keys
        key3 = DecisionCache._make_key("different prompt")
        assert key1 != key3
        
        # Verify it's a SHA256 hex string (64 chars, hex digits)
        assert len(key1) == 64
        assert all(c in "0123456789abcdef" for c in key1)

    @pytest.mark.asyncio
    async def test_cache_json_serialization_complex_data(self, redis_client) -> None:
        """Test that complex rule data is properly serialized/deserialized."""
        cache = DecisionCache(ttl_seconds=60)
        
        complex_rules = [
            {
                "rule_id": "pii_ssn_us",
                "position": [10, 20],
                "severity": "high",
                "matched_text": "123-45-6789"
            },
            {
                "rule_id": "secret_aws_key",
                "position": [30, 50],
                "severity": "critical",
                "matched_text": "AKIAIOSFODNN7EXAMPLE"
            }
        ]
        
        await cache.set("complex_rules_test", "block", "Multiple matches", complex_rules)
        
        entry, hit = await cache.get("complex_rules_test")
        assert hit is True
        assert entry["rules_matched"] == complex_rules
        assert entry["verdict"] == "block"
        assert entry["reason"] == "Multiple matches"

    @pytest.mark.asyncio
    async def test_cache_concurrent_access(self, redis_client) -> None:
        """Test that cache handles concurrent access correctly."""
        cache = DecisionCache(ttl_seconds=60)
        
        # Set initial value
        await cache.set("concurrent_test", "allow", "initial", [])
        
        async def get_value():
            return await cache.get("concurrent_test")
        
        async def set_value(value):
            await cache.set("concurrent_test", value, f"reason_{value}", [])
        
        # Test concurrent gets
        get_tasks = [get_value() for _ in range(5)]
        get_results = await asyncio.gather(*get_tasks)
        
        # All gets should return the same value
        for entry, hit in get_results:
            assert hit is True
            assert entry["verdict"] == "allow"
        
        # Test concurrent sets and gets
        set_tasks = [set_value(f"val_{i}") for i in range(3)]
        await asyncio.gather(*set_tasks)
        
        # Get the final value (should be one of the set values)
        final_entry, final_hit = await get_value()
        assert final_hit is True
        assert final_entry["verdict"] in ["val_0", "val_1", "val_2"]

    @pytest.mark.asyncio
    async def test_cache_large_prompt_handling(self, redis_client) -> None:
        """Test that cache handles large prompts correctly."""
        cache = DecisionCache(ttl_seconds=60)
        
        # Create a large prompt (1KB)
        large_prompt = "This is a large prompt. " * 100
        
        # Set and get large prompt
        await cache.set(large_prompt, "allow", "large prompt accepted", [])
        entry, hit = await cache.get(large_prompt)
        
        assert hit is True
        assert entry is not None
        assert entry["verdict"] == "allow"
        
        # Verify the key is still SHA256 (64 chars)
        key = DecisionCache._make_key(large_prompt)
        assert len(key) == 64