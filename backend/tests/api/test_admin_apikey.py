from __future__ import annotations

import pytest
import secrets
import bcrypt
from fastapi.testclient import TestClient


class TestAdminApiKey:
    def test_create_api_key(self, test_client: TestClient, test_auth_headers: dict) -> None:
        """Test creating a new API key."""
        resp = test_client.post(
            "/api/v1/admin/keys",
            json={"tenant_id": "new-tenant", "active": True},
            headers=test_auth_headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        
        # Verify response structure
        assert "id" in data
        assert "key" in data  # Key should be returned on creation
        assert data["tenant_id"] == "new-tenant"
        assert data["active"] is True
        assert "created_at" in data
        
        # Store the key for cleanup
        created_key = data["key"]
        created_id = data["id"]
        
        # Verify key exists in database
        async def verify_key_exists():
            from app.db.session import get_db_connection
            async with get_db_connection() as conn:
                record = await conn.fetchrow(
                    "SELECT id, key_hash, tenant_id, active FROM api_keys WHERE id = $1",
                    created_id,
                )
                assert record is not None
                assert record["tenant_id"] == "new-tenant"
                assert record["active"] is True
        
        # Run async verification
        import asyncio
        asyncio.run(verify_key_exists())
        
        # Cleanup
        async def cleanup():
            from app.db.session import get_db_connection
            async with get_db_connection() as conn:
                await conn.execute("DELETE FROM api_keys WHERE id = $1", created_id)
        
        asyncio.run(cleanup())

    def test_list_api_keys(self, test_client: TestClient, test_auth_headers: dict) -> None:
        """Test listing API keys."""
        # First create a key
        resp = test_client.post(
            "/api/v1/admin/keys",
            json={"tenant_id": "list-test-tenant"},
            headers=test_auth_headers,
        )
        assert resp.status_code == 201
        created_id = resp.json()["id"]
        
        # Then list all keys
        resp = test_client.get("/api/v1/admin/keys", headers=test_auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        
        assert "keys" in data
        assert "total" in data
        assert data["total"] >= 1
        
        # Verify no actual key values are returned
        for key in data["keys"]:
            assert "key" not in key or key["key"] is None
        
        # Cleanup
        async def cleanup():
            from app.db.session import get_db_connection
            async with get_db_connection() as conn:
                await conn.execute("DELETE FROM api_keys WHERE id = $1", created_id)
        
        import asyncio
        asyncio.run(cleanup())

    def test_activate_deactivate_key(self, test_client: TestClient, test_auth_headers: dict) -> None:
        """Test activating and deactivating an API key."""
        # First create a key
        resp = test_client.post(
            "/api/v1/admin/keys",
            json={"tenant_id": "activate-test-tenant", "active": True},
            headers=test_auth_headers,
        )
        assert resp.status_code == 201
        created_id = resp.json()["id"]
        
        # Deactivate the key
        resp = test_client.post(
            f"/api/v1/admin/keys/{created_id}/activate",
            json={"active": False},
            headers=test_auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["active"] is False
        
        # Try to use the deactivated key (should fail)
        async def test_deactivated_key():
            from app.core.auth import verify_api_key
            from fastapi import Request
            
            # Create a mock request with state
            class MockRequest:
                def __init__(self):
                    self.state = {}
            
            mock_request = MockRequest()
            
            # This should raise 403
            from fastapi import HTTPException
            try:
                await verify_api_key(
                    type("MockCredentials", (), {"credentials": resp.json()["key"]})(),
                    mock_request,
                )
                assert False, "Should have raised 403"
            except HTTPException as e:
                assert e.status_code == 403
        
        import asyncio
        asyncio.run(test_deactivated_key())
        
        # Reactivate the key
        resp = test_client.post(
            f"/api/v1/admin/keys/{created_id}/activate",
            json={"active": True},
            headers=test_auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["active"] is True
        
        # Cleanup
        async def cleanup():
            from app.db.session import get_db_connection
            async with get_db_connection() as conn:
                await conn.execute("DELETE FROM api_keys WHERE id = $1", created_id)
        
        asyncio.run(cleanup())

    def test_delete_api_key(self, test_client: TestClient, test_auth_headers: dict) -> None:
        """Test deleting an API key."""
        # First create a key
        resp = test_client.post(
            "/api/v1/admin/keys",
            json={"tenant_id": "delete-test-tenant"},
            headers=test_auth_headers,
        )
        assert resp.status_code == 201
        created_id = resp.json()["id"]
        
        # Delete the key
        resp = test_client.delete(f"/api/v1/admin/keys/{created_id}", headers=test_auth_headers)
        assert resp.status_code == 204
        
        # Verify key is deleted
        async def verify_key_deleted():
            from app.db.session import get_db_connection
            async with get_db_connection() as conn:
                record = await conn.fetchrow(
                    "SELECT id FROM api_keys WHERE id = $1",
                    created_id,
                )
                assert record is None
        
        import asyncio
        asyncio.run(verify_key_deleted())

    def test_create_key_without_auth(self, test_client: TestClient) -> None:
        """Test creating API key without authentication."""
        resp = test_client.post(
            "/api/v1/admin/keys",
            json={"tenant_id": "unauth-test"},
        )
        assert resp.status_code == 401

    def test_activate_nonexistent_key(self, test_client: TestClient, test_auth_headers: dict) -> None:
        """Test activating a non-existent key."""
        import uuid
        fake_id = uuid.uuid4()
        
        resp = test_client.post(
            f"/api/v1/admin/keys/{fake_id}/activate",
            json={"active": True},
            headers=test_auth_headers,
        )
        assert resp.status_code == 404

    def test_delete_nonexistent_key(self, test_client: TestClient, test_auth_headers: dict) -> None:
        """Test deleting a non-existent key."""
        import uuid
        fake_id = uuid.uuid4()
        
        resp = test_client.delete(
            f"/api/v1/admin/keys/{fake_id}",
            headers=test_auth_headers,
        )
        assert resp.status_code == 404