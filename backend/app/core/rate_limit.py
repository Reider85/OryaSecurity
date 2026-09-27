from __future__ import annotations

import asyncio
import time
from collections import deque
from typing import Any

from fastapi import HTTPException


class RateLimiter:
    """Sliding window rate limiter per key.
    
    Tracks requests per key in a sliding window (1 second).
    Returns 429 Too Many Requests when limit exceeded.
    """
    
    def __init__(self, max_rps: int):
        self._max_rps = max_rps
        self._requests: dict[str, deque[float]] = {}
        self._lock = asyncio.Lock()
    
    async def check(self, key: str) -> None:
        """Check if request is allowed for this key.
        
        Args:
            key: Rate limit key (e.g., API key hash)
            
        Raises:
            HTTPException: 429 if rate limit exceeded
        """
        async with self._lock:
            now = time.monotonic()
            window_start = now - 1.0  # 1 second sliding window
            
            # Get or create deque for this key
            timestamps = self._requests.setdefault(key, deque())
            
            # Remove timestamps older than window
            while timestamps and timestamps[0] < window_start:
                timestamps.popleft()
            
            # Check if limit exceeded
            if len(timestamps) >= self._max_rps:
                raise HTTPException(
                    status_code=429,
                    detail="Rate limit exceeded",
                    headers={
                        "X-RateLimit-Limit": str(self._max_rps),
                        "X-RateLimit-Remaining": "0",
                        "Retry-After": "1",
                    }
                )
            
            # Add current request
            timestamps.append(now)
            
            # Calculate remaining for headers
            remaining = self._max_rps - len(timestamps)
            
            return remaining
    
    async def get_stats(self, key: str) -> dict[str, Any]:
        """Get current rate limit stats for a key.
        
        Args:
            key: Rate limit key
            
        Returns:
            Dict with current count and remaining
        """
        async with self._lock:
            timestamps = self._requests.get(key, deque())
            now = time.monotonic()
            window_start = now - 1.0
            
            # Clean old timestamps
            while timestamps and timestamps[0] < window_start:
                timestamps.popleft()
            
            return {
                "count": len(timestamps),
                "remaining": max(0, self._max_rps - len(timestamps)),
                "limit": self._max_rps,
            }
    
    def clear(self, key: str | None = None) -> None:
        """Clear rate limit data.
        
        Args:
            key: Specific key to clear, or None to clear all
        """
        if key is None:
            self._requests.clear()
        else:
            self._requests.pop(key, None)