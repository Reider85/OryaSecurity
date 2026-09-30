"""Tests for UI auth: POST /api/v1/auth/login and JWT acceptance on protected endpoints.

Async tests using httpx.ASGITransport so the app, pool, and tests share one
event loop (the global asyncpg pool is loop-bound).
"""

from __future__ import annotations

import os
import secrets
import time

import httpx
import jwt as pyjwt
import pytest

from app.config import settings
from app.core.auth import _fingerprint_api_key, _hash_api_key
from app.main import app


def _postgres_available() -> bool:
    """Check if a real PostgreSQL is reachable, without failing the suite.

    Uses a throwaway connection — never the global pool — so the
    session-scoped db_pool fixture can create a healthy pool later.
    """
    if os.getenv("SCANNER_SKIP_DB_TESTS") == "1":
        return False

    async def check() -> bool:
        try:
            import asyncpg

            from app.config import settings as cfg

            dsn = cfg.database_url.replace("postgresql+asyncpg://", "postgresql://")
            con = await asyncpg.connect(dsn, timeout=2)
            try:
                await con.fetchval("SELECT 1")
            finally:
                await con.close()
        except Exception:
            return False
        return True

    import asyncio

    try:
        return asyncio.run(check())
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _postgres_available(), reason="PostgreSQL is not available"
)


async def _insert_api_key(api_key: str, tenant_id: str, active: bool = True) -> None:
    """Insert an API key WITH fingerprint (required for verify lookup)."""
    from app.db.session import get_db_connection

    key_hash = _hash_api_key(api_key)
    fingerprint = _fingerprint_api_key(api_key)
    async with get_db_connection() as conn:
        await conn.execute(
            """
            INSERT INTO api_keys (key_hash, key_fingerprint, tenant_id, created_at, active)
            VALUES ($1, $2, $3, NOW(), $4)
            """,
            key_hash,
            fingerprint,
            tenant_id,
            active,
        )


async def _delete_api_key(api_key: str) -> None:
    from app.db.session import get_db_connection

    fingerprint = _fingerprint_api_key(api_key)
    async with get_db_connection() as conn:
        await conn.execute(
            "DELETE FROM api_keys WHERE key_fingerprint = $1",
            fingerprint,
        )


@pytest.fixture
async def db_ready():
    """Fresh DB pool + SQLAlchemy engine bound to the current test event loop.

    Globals are loop-bound; reset them so each test gets healthy clients.
    """
    from app.db import session as db_session
    from app.db.session import get_pool, init_db

    if db_session._pool is not None:
        try:
            await db_session._pool.close()
        except Exception:
            pass
        db_session._pool = None
    if db_session._engine is not None:
        try:
            await db_session._engine.dispose()
        except Exception:
            pass
        db_session._engine = None
        db_session._sessionmaker = None

    await get_pool()
    await init_db()
    yield


@pytest.fixture
async def async_client(db_ready):
    """httpx AsyncClient wired to the FastAPI app via ASGITransport."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


class TestLoginEndpoint:
    async def test_login_success_returns_jwt(self, async_client: httpx.AsyncClient) -> None:
        api_key = secrets.token_urlsafe(32)
        await _insert_api_key(api_key, tenant_id="login-tenant")
        try:
            resp = await async_client.post("/api/v1/auth/login", json={"api_key": api_key})
            assert resp.status_code == 200
            data = resp.json()
            assert "token" in data
            assert data["token_type"] == "bearer"
            assert data["tenant_id"] == "login-tenant"
            assert data["expires_in"] == settings.jwt_ttl_seconds

            claims = pyjwt.decode(
                data["token"],
                settings.jwt_secret,
                algorithms=[settings.jwt_algorithm],
                issuer=settings.jwt_issuer,
            )
            assert claims["tenant_id"] == "login-tenant"
            assert claims["sub"] == "login-tenant"
        finally:
            await _delete_api_key(api_key)

    async def test_login_invalid_key_returns_401(self, async_client: httpx.AsyncClient) -> None:
        resp = await async_client.post(
            "/api/v1/auth/login",
            json={"api_key": "definitely-not-a-valid-key"},
        )
        assert resp.status_code == 401
        assert "Invalid API key" in resp.json()["detail"]

    async def test_login_inactive_key_returns_403(self, async_client: httpx.AsyncClient) -> None:
        api_key = secrets.token_urlsafe(32)
        await _insert_api_key(api_key, tenant_id="inactive-tenant", active=False)
        try:
            resp = await async_client.post("/api/v1/auth/login", json={"api_key": api_key})
            assert resp.status_code == 403
            assert "inactive" in resp.json()["detail"].lower()
        finally:
            await _delete_api_key(api_key)

    async def test_login_missing_api_key_returns_422(self, async_client: httpx.AsyncClient) -> None:
        resp = await async_client.post("/api/v1/auth/login", json={})
        assert resp.status_code == 422

    async def test_login_empty_api_key_returns_422(self, async_client: httpx.AsyncClient) -> None:
        resp = await async_client.post("/api/v1/auth/login", json={"api_key": ""})
        assert resp.status_code == 422


class TestJwtOnProtectedEndpoints:
    async def test_jwt_accepted_on_protected_endpoint(self, async_client: httpx.AsyncClient) -> None:
        api_key = secrets.token_urlsafe(32)
        await _insert_api_key(api_key, tenant_id="jwt-accept-tenant")
        try:
            login = await async_client.post("/api/v1/auth/login", json={"api_key": api_key})
            assert login.status_code == 200
            token = login.json()["token"]

            # /api/v1/rules is auth-protected and does not require Redis
            resp = await async_client.get(
                "/api/v1/rules",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 200
            assert "items" in resp.json()
        finally:
            await _delete_api_key(api_key)

    async def test_jwt_accepted_on_audit_endpoint(self, async_client: httpx.AsyncClient) -> None:
        api_key = secrets.token_urlsafe(32)
        await _insert_api_key(api_key, tenant_id="jwt-audit-tenant")
        try:
            login = await async_client.post("/api/v1/auth/login", json={"api_key": api_key})
            token = login.json()["token"]
            resp = await async_client.get(
                "/api/v1/audit?limit=1",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 200
        finally:
            await _delete_api_key(api_key)

    async def test_expired_jwt_returns_401(self, async_client: httpx.AsyncClient) -> None:
        now = int(time.time())
        expired = pyjwt.encode(
            {
                "sub": "expired-tenant",
                "tenant_id": "expired-tenant",
                "iat": now - 120,
                "exp": now - 60,
                "iss": settings.jwt_issuer,
            },
            settings.jwt_secret,
            algorithm=settings.jwt_algorithm,
        )
        resp = await async_client.get(
            "/api/v1/rules",
            headers={"Authorization": f"Bearer {expired}"},
        )
        assert resp.status_code == 401
        assert "token" in resp.json()["detail"].lower()

    async def test_garbage_jwt_like_token_returns_401(self, async_client: httpx.AsyncClient) -> None:
        resp = await async_client.get(
            "/api/v1/rules",
            headers={"Authorization": "Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.bad-signature"},
        )
        assert resp.status_code == 401

    async def test_wrong_secret_jwt_returns_401(self, async_client: httpx.AsyncClient) -> None:
        now = int(time.time())
        forged = pyjwt.encode(
            {
                "sub": "forged",
                "tenant_id": "forged",
                "iat": now,
                "exp": now + 3600,
                "iss": settings.jwt_issuer,
            },
            "not-the-real-secret-0123456789abcdef",
            algorithm=settings.jwt_algorithm,
        )
        resp = await async_client.get(
            "/api/v1/rules",
            headers={"Authorization": f"Bearer {forged}"},
        )
        assert resp.status_code == 401

    async def test_raw_api_key_still_works(self, async_client: httpx.AsyncClient) -> None:
        """Regression: SDK / API clients keep using raw API keys."""
        api_key = secrets.token_urlsafe(32)
        await _insert_api_key(api_key, tenant_id="raw-key-tenant")
        try:
            resp = await async_client.get(
                "/api/v1/rules",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            assert resp.status_code == 200
        finally:
            await _delete_api_key(api_key)

    async def test_missing_bearer_returns_401(self, async_client: httpx.AsyncClient) -> None:
        resp = await async_client.get("/api/v1/rules")
        assert resp.status_code == 401
