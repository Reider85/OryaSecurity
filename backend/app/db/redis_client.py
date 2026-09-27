from __future__ import annotations

import asyncio
import redis.asyncio as redis

from app.config import settings

# Global Redis client instance
_client: redis.Redis | None = None


async def get_redis() -> redis.Redis:
    """Get the global Redis client. Initialize if not exists."""
    global _client
    if _client is None:
        _client = redis.from_url(
            settings.redis_url,
            decode_responses=True,
            health_check_interval=30,
        )
    return _client


async def init_redis() -> None:
    """Initialize Redis connection and test connectivity."""
    client = await get_redis()
    # Test the connection with PING
    await client.ping()


async def close_redis() -> None:
    """Close the Redis connection."""
    global _client
    if _client:
        await _client.aclose()
        _client = None


async def get_redis_connection() -> redis.Redis:
    """Get a Redis connection (alias for get_redis for consistency)."""
    return await get_redis()