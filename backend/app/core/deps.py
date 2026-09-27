from __future__ import annotations

import asyncio
from typing import Any

import asyncpg
import redis.asyncio as redis

from app.config import settings


async def check_postgres() -> bool:
    """Check if PostgreSQL database is available and responsive."""
    try:
        # Create connection pool to test connectivity
        pool = await asyncpg.create_pool(
            settings.database_url,
            min_size=1,
            max_size=1,
            command_timeout=5,
        )
        
        # Test the connection with a simple query
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        
        # Close the pool
        await pool.close()
        return True
        
    except Exception:
        # Any exception means the database is not available
        return False


async def check_redis() -> bool:
    """Check if Redis server is available and responsive."""
    try:
        # Create Redis client
        client = redis.from_url(settings.redis_url, decode_responses=True)
        
        # Test the connection with PING
        await client.ping()
        
        # Close the connection
        await client.close()
        return True
        
    except Exception:
        # Any exception means Redis is not available
        return False


async def check_all_dependencies() -> dict[str, bool]:
    """Check all dependencies concurrently and return their status."""
    # Run both checks concurrently for better performance
    results = await asyncio.gather(
        check_postgres(),
        check_redis(),
        return_exceptions=True
    )
    
    # Process results, converting exceptions to False
    dependency_status = {}
    
    # Postgres check (first result)
    if isinstance(results[0], Exception):
        dependency_status["postgres"] = False
    else:
        dependency_status["postgres"] = results[0]
    
    # Redis check (second result)
    if isinstance(results[1], Exception):
        dependency_status["redis"] = False
    else:
        dependency_status["redis"] = results[1]
    
    return dependency_status