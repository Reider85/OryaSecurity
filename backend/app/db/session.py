from __future__ import annotations

import asyncio
import asyncpg
from typing import Optional

from app.config import settings

# Global connection pool
_pool: Optional[asyncpg.Pool] = None


async def get_pool() -> asyncpg.Pool:
    """Get the global connection pool. Initialize if not exists."""
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            settings.database_url,
            min_size=2,
            max_size=10,
            command_timeout=60,
        )
    return _pool


async def init_db() -> None:
    """Initialize database schema by running migrations."""
    pool = await get_pool()
    
    # Read and execute migration
    migration_path = "app/db/migrations/001_api_keys.sql"
    try:
        with open(migration_path, 'r') as f:
            migration_sql = f.read()
        
        async with pool.acquire() as conn:
            await conn.execute(migration_sql)
    except FileNotFoundError:
        # Migration file not found, skip
        pass


async def close_db() -> None:
    """Close the connection pool."""
    global _pool
    if _pool:
        await _pool.close()
        _pool = None


async def get_db_connection() -> asyncpg.Connection:
    """Get a database connection from the pool."""
    pool = await get_pool()
    return await pool.acquire()


async def release_db_connection(conn: asyncpg.Connection) -> None:
    """Release a database connection back to the pool."""
    pool = await get_pool()
    await pool.release(conn)