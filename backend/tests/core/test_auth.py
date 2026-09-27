from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


class TestAuth:
    def test_missing_auth_returns_401(self, test_client: TestClient) -> None:
        resp = test_client.post("/scan", json={"prompt": "hello"})
        assert resp.status_code == 401

    def test_invalid_key_returns_401(self, test_client: TestClient) -> None:
        resp = test_client.post(
            "/scan",
            json={"prompt": "hello"},
            headers={"Authorization": "Bearer invalid-key-12345"},
        )
        assert resp.status_code == 401

    def test_inactive_key_returns_403(self, test_client: TestClient) -> None:
        # Create inactive key
        import secrets
        import bcrypt
        
        api_key = secrets.token_urlsafe(32)
        key_hash = bcrypt.hashpw(api_key.encode(), bcrypt.gensalt()).decode()
        
        async def create_inactive_key():
            from app.db.session import get_db_connection
            async with get_db_connection() as conn:
                await conn.execute(
                    """
                    INSERT INTO api_keys (key_hash, tenant_id, created_at, active)
                    VALUES ($1, $2, NOW(), FALSE)
                    """,
                    key_hash,
                    "test-tenant",
                )
        
        # Run async function
        import asyncio
        asyncio.run(create_inactive_key())
        
        try:
            resp = test_client.post(
                "/scan",
                json={"prompt": "hello"},
                headers={"Authorization": f"Bearer {api_key}"},
            )
            assert resp.status_code == 403
        finally:
            # Cleanup
            async def cleanup():
                from app.db.session import get_db_connection
                async with get_db_connection() as conn:
                    await conn.execute("DELETE FROM api_keys WHERE key_hash = $1", key_hash)
            
            asyncio.run(cleanup())

    def test_valid_key_allows(self, test_client: TestClient, test_auth_headers: dict) -> None:
        resp = test_client.post("/scan", json={"prompt": "hello"}, headers=test_auth_headers)
        assert resp.status_code == 200

    def test_health_no_auth_needed(self, test_client: TestClient) -> None:
        resp = test_client.get("/health")
        assert resp.status_code == 200

    def test_metrics_no_auth_needed(self, test_client: TestClient) -> None:
        resp = test_client.get("/metrics")
        assert resp.status_code == 200

    def test_tenant_id_in_request_state(self, test_client: TestClient, test_auth_headers: dict) -> None:
        # This test verifies that tenant_id is properly stored in request.state
        # Note: We can't directly access request.state in TestClient, so we test that the scan works
        resp = test_client.post(
            "/scan",
            json={"prompt": "hello", "tenant_id": "explicit-tenant"},
            headers=test_auth_headers,
        )
        assert resp.status_code == 200
        # The actual tenant_id validation happens in the scan endpoint logic
