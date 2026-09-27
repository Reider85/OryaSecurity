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
    import bcrypt
    
    # Generate and hash test key
    api_key = secrets.token_urlsafe(32)
    key_hash = bcrypt.hashpw(api_key.encode(), bcrypt.gensalt()).decode()
    
    # Insert into database
    async with get_db_connection() as conn:
        await conn.execute(
            """
            INSERT INTO api_keys (key_hash, tenant_id, created_at, active)
            VALUES ($1, $2, NOW(), TRUE)
            """,
            key_hash,
            "test-tenant",
        )
    
    yield api_key
    
    # Cleanup
    async with get_db_connection() as conn:
        await conn.execute(
            "DELETE FROM api_keys WHERE key_hash = $1",
            key_hash,
        )


@pytest.fixture
async def test_auth_headers(test_api_key: str):
    """Auth headers for test API key."""
    return {"Authorization": f"Bearer {test_api_key}"}
