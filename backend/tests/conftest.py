from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.config import settings
from app.db.session import get_pool, close_db, init_db, get_db_connection


@pytest.fixture
def client() -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def api_key() -> str:
    return settings.api_keys[0]


@pytest.fixture
def auth_headers(api_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}"}


@pytest.fixture(scope="session")
async def db_pool():
    """Create a test database pool."""
    # Use the existing database URL for tests
    # The migration will create the table if it doesn't exist
    
    # Create pool and run migration
    pool = await get_pool()
    await init_db()
    
    yield pool
    
    # Cleanup
    await close_db()


@pytest.fixture
async def test_client(db_pool):
    """Test client with database pool."""
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
async def test_api_key(db_pool):
    """Create a test API key in the database."""
    import secrets

    from app.core.auth import _fingerprint_api_key, _hash_api_key

    # Generate and hash test key
    api_key = secrets.token_urlsafe(32)
    key_hash = _hash_api_key(api_key)
    # authenticate_api_key() looks rows up by fingerprint (sha256 of the raw
    # key), so the column must be populated or every request 401s.
    fingerprint = _fingerprint_api_key(api_key)

    # Insert into database
    async with get_db_connection() as conn:
        await conn.execute(
            "DELETE FROM api_keys WHERE key_fingerprint = $1",
            fingerprint,
        )
        await conn.execute(
            """
            INSERT INTO api_keys (key_hash, key_fingerprint, tenant_id, created_at, active)
            VALUES ($1, $2, $3, NOW(), TRUE)
            """,
            key_hash,
            fingerprint,
            "test-tenant",
        )

    yield api_key

    # Cleanup
    async with get_db_connection() as conn:
        await conn.execute(
            "DELETE FROM api_keys WHERE key_fingerprint = $1",
            fingerprint,
        )

@pytest.fixture
async def test_auth_headers(test_api_key: str):
    """Auth headers for test API key."""
    return {"Authorization": f"Bearer {test_api_key}"}


@pytest.fixture
async def fake_redis():
    """Fake Redis client using fakeredis for unit tests."""
    import fakeredis.aioredis
    
    # Create fake Redis server and client
    server = fakeredis.FakeServer()
    client = fakeredis.aioredis.FakeRedis(server=server, decode_responses=True)
    
    yield client
    
    # Cleanup
    await client.aclose()
