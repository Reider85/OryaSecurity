from __future__ import annotations

import asyncio
import glob
import os
from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional

import asyncpg
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import settings

# Global connection pool
_pool: Optional[asyncpg.Pool] = None

# Global SQLAlchemy async engine (used by the ORM layer)
_engine: Optional[AsyncEngine] = None
_sessionmaker: Optional[async_sessionmaker[AsyncSession]] = None


def _normalize_asyncpg_dsn(dsn: str) -> str:
    """Normalize database URL for asyncpg (remove +asyncpg suffix if present)."""
    if dsn.startswith("postgresql+asyncpg://"):
        return dsn.replace("postgresql+asyncpg://", "postgresql://", 1)
    return dsn


async def get_pool() -> asyncpg.Pool:
    """Get the global connection pool. Initialize if not exists."""
    global _pool
    if _pool is None:
        normalized_dsn = _normalize_asyncpg_dsn(settings.database_url)
        _pool = await asyncpg.create_pool(
            normalized_dsn,
            min_size=2,
            max_size=10,
            command_timeout=60,
        )
    return _pool


def init_engine() -> AsyncEngine:
    """Get the global SQLAlchemy async engine. Initialize if not exists."""
    global _engine, _sessionmaker
    if _engine is None:
        _engine = create_async_engine(
            settings.database_url,
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=5,
        )
        _sessionmaker = async_sessionmaker(
            _engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    """Get the global async session factory. Initialize if not exists."""
    if _sessionmaker is None:
        init_engine()
    assert _sessionmaker is not None
    return _sessionmaker


@asynccontextmanager
async def get_session() -> AsyncIterator[AsyncSession]:
    """Yield an async session, committing on success and rolling back on error."""
    session = get_sessionmaker()()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


async def dispose_engine() -> None:
    """Dispose of the SQLAlchemy engine and all pooled connections."""
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None


def _migrations_dir() -> str:
    """Resolve the directory holding the raw SQL migrations."""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "migrations")


async def init_db() -> None:
    """Initialize database schema by running all SQL migrations in order."""
    pool = await get_pool()

    for path in sorted(glob.glob(os.path.join(_migrations_dir(), "*.sql"))):
        with open(path, "r") as f:
            migration_sql = f.read()

        async with pool.acquire() as conn:
            await conn.execute(migration_sql)


async def close_db() -> None:
    """Close the connection pool."""
    global _pool
    if _pool:
        await _pool.close()
        _pool = None

    await dispose_engine()


async def get_db_connection() -> asyncpg.Connection:
    """Get a database connection from the pool."""
    pool = await get_pool()
    return await pool.acquire()


async def release_db_connection(conn: asyncpg.Connection) -> None:
    """Release a database connection back to the pool."""
    pool = await get_pool()
    await pool.release(conn)