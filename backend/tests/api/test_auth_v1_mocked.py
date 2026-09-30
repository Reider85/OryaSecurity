"""Unit tests for POST /api/v1/auth/login with mocked DB auth — no Postgres required."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import jwt as pyjwt
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.config import settings
from app.core.auth import AuthInfo


@pytest.fixture
def mocked_auth_success(client: TestClient):
    async def _auth(api_key: str, request=None) -> AuthInfo:
        return AuthInfo(api_key=api_key, tenant_id="mock-tenant")

    with patch("app.api.v1.auth.authenticate_api_key", side_effect=_auth):
        yield


@pytest.fixture
def mocked_auth_invalid(client: TestClient):
    async def _auth(api_key: str, request=None) -> AuthInfo:
        raise HTTPException(status_code=401, detail="Invalid API key")

    with patch("app.api.v1.auth.authenticate_api_key", side_effect=_auth):
        yield


@pytest.fixture
def mocked_auth_inactive(client: TestClient):
    async def _auth(api_key: str, request=None) -> AuthInfo:
        raise HTTPException(status_code=403, detail="API key is inactive")

    with patch("app.api.v1.auth.authenticate_api_key", side_effect=_auth):
        yield


class TestLoginEndpointMocked:
    def test_login_success_returns_valid_jwt(self, client: TestClient, mocked_auth_success) -> None:
        resp = client.post("/api/v1/auth/login", json={"api_key": "any-key"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["token_type"] == "bearer"
        assert data["tenant_id"] == "mock-tenant"
        assert data["expires_in"] == settings.jwt_ttl_seconds
        assert "token" in data

        claims = pyjwt.decode(
            data["token"],
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            issuer=settings.jwt_issuer,
        )
        assert claims["tenant_id"] == "mock-tenant"
        assert claims["sub"] == "mock-tenant"
        assert claims["iss"] == settings.jwt_issuer

    def test_login_invalid_key_returns_401(self, client: TestClient, mocked_auth_invalid) -> None:
        resp = client.post("/api/v1/auth/login", json={"api_key": "bad"})
        assert resp.status_code == 401
        assert resp.json()["detail"] == "Invalid API key"

    def test_login_inactive_key_returns_403(self, client: TestClient, mocked_auth_inactive) -> None:
        resp = client.post("/api/v1/auth/login", json={"api_key": "inactive"})
        assert resp.status_code == 403
        assert "inactive" in resp.json()["detail"].lower()

    def test_login_missing_api_key_returns_422(self, client: TestClient) -> None:
        resp = client.post("/api/v1/auth/login", json={})
        assert resp.status_code == 422

    def test_login_empty_api_key_returns_422(self, client: TestClient) -> None:
        resp = client.post("/api/v1/auth/login", json={"api_key": ""})
        assert resp.status_code == 422

    def test_login_calls_authenticate_with_body_key(self, client: TestClient) -> None:
        mock = AsyncMock(return_value=AuthInfo(api_key="k", tenant_id="t"))
        with patch("app.api.v1.auth.authenticate_api_key", mock):
            resp = client.post("/api/v1/auth/login", json={"api_key": "secret-key-xyz"})
            assert resp.status_code == 200
            mock.assert_awaited_once_with("secret-key-xyz")
