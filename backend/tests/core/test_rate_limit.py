from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.core.rate_limit import RateLimiter


class TestRateLimiter:
    """Test suite for RateLimiter class."""
    
    @pytest.fixture
    def rate_limiter(self) -> RateLimiter:
        """Create a rate limiter with 5 RPS for testing."""
        return RateLimiter(max_rps=5)
    
    @pytest.mark.asyncio
    async def test_within_limit(self, rate_limiter: RateLimiter) -> None:
        """Test that requests within limit are allowed."""
        key = "test_key"
        
        # Should allow 5 requests
        for _ in range(5):
            await rate_limiter.check(key)
        
        # 6th request should be allowed (sliding window)
        remaining = await rate_limiter.check(key)
        assert remaining == 0
    
    @pytest.mark.asyncio
    async def test_rate_limit_exceeded(self, rate_limiter: RateLimiter) -> None:
        """Test that requests exceeding limit raise 429."""
        key = "test_key"
        
        # Use up all 5 requests
        for _ in range(5):
            await rate_limiter.check(key)
        
        # 6th request should raise 429
        with pytest.raises(HTTPException) as exc_info:
            await rate_limiter.check(key)
        
        assert exc_info.value.status_code == 429
        assert exc_info.value.detail == "Rate limit exceeded"
        assert exc_info.value.headers["X-RateLimit-Limit"] == "5"
        assert exc_info.value.headers["X-RateLimit-Remaining"] == "0"
        assert exc_info.value.headers["Retry-After"] == "1"
    
    @pytest.mark.asyncio
    async def test_sliding_window(self, rate_limiter: RateLimiter) -> None:
        """Test that requests slide out of window after 1 second."""
        key = "test_key"
        
        # Use up all 5 requests
        for _ in range(5):
            await rate_limiter.check(key)
        
        # 6th request should fail
        with pytest.raises(HTTPException):
            await rate_limiter.check(key)
        
        # Wait for window to slide (1 second + buffer)
        await asyncio.sleep(1.1)
        
        # Now should be allowed again
        remaining = await rate_limiter.check(key)
        assert remaining == 4  # 1 request used, 4 remaining
    
    @pytest.mark.asyncio
    async def test_different_keys(self, rate_limiter: RateLimiter) -> None:
        """Test that different keys have independent limits."""
        key1 = "key1"
        key2 = "key2"
        
        # Use up limit for key1
        for _ in range(5):
            await rate_limiter.check(key1)
        
        # key1 should be blocked
        with pytest.raises(HTTPException):
            await rate_limiter.check(key1)
        
        # key2 should still work
        remaining = await rate_limiter.check(key2)
        assert remaining == 4
    
    @pytest.mark.asyncio
    async def test_get_stats(self, rate_limiter: RateLimiter) -> None:
        """Test getting rate limit stats."""
        key = "test_key"
        
        # Initial stats
        stats = await rate_limiter.get_stats(key)
        assert stats == {"count": 0, "remaining": 5, "limit": 5}
        
        # Make some requests
        await rate_limiter.check(key)
        await rate_limiter.check(key)
        
        # Check updated stats
        stats = await rate_limiter.get_stats(key)
        assert stats == {"count": 2, "remaining": 3, "limit": 5}
    
    @pytest.mark.asyncio
    async def test_clear_all(self, rate_limiter: RateLimiter) -> None:
        """Test clearing all rate limit data."""
        key1 = "key1"
        key2 = "key2"
        
        # Add some data
        await rate_limiter.check(key1)
        await rate_limiter.check(key2)
        
        # Clear all
        rate_limiter.clear()
        
        # Should work again
        await rate_limiter.check(key1)
        await rate_limiter.check(key2)
    
    @pytest.mark.asyncio
    async def test_clear_single_key(self, rate_limiter: RateLimiter) -> None:
        """Test clearing rate limit for a single key."""
        key1 = "key1"
        key2 = "key2"
        
        # Use up limit for key1
        for _ in range(5):
            await rate_limiter.check(key1)
        
        # key1 should be blocked
        with pytest.raises(HTTPException):
            await rate_limiter.check(key1)
        
        # Clear only key1
        rate_limiter.clear(key1)
        
        # key1 should work again
        await rate_limiter.check(key1)
        
        # key2 should still be blocked if it was at limit
        # (this test assumes key2 was also at limit, but we'll test separately)
        await rate_limiter.check(key2)
    
    @pytest.mark.asyncio
    async def test_concurrent_requests(self, rate_limiter: RateLimiter) -> None:
        """Test that rate limiter handles concurrent requests safely."""
        key = "concurrent_key"
        
        async def make_request() -> None:
            await rate_limiter.check(key)
        
        # Make 3 concurrent requests
        tasks = [make_request() for _ in range(3)]
        await asyncio.gather(*tasks)
        
        # Should have 3 requests recorded
        stats = await rate_limiter.get_stats(key)
        assert stats["count"] == 3
        assert stats["remaining"] == 2
    
    @pytest.mark.asyncio
    async def test_empty_key(self, rate_limiter: RateLimiter) -> None:
        """Test behavior with empty string as key."""
        # Should work with empty key
        await rate_limiter.check("")
        
        stats = await rate_limiter.get_stats("")
        assert stats["count"] == 1
        assert stats["remaining"] == 4