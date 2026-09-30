"""Unit tests for verify_api_key JWT path — no database required."""

from __future__ import annotations

import time
from types import SimpleNamespace

import jwt as pyjwt
import pytest
from fastapi import HTTPException

from app.config import settings
from app.core.auth import verify_api_key
from app.core.jwt import create_jwt


def _credentials(token: str) -> SimpleNamespace:
    return SimpleNamespace(credentials=token)


def _request() -> SimpleNamespace:
    return SimpleNamespace(state=SimpleNamespace())


class TestVerifyApiKeyJwtPath:
    def test_valid_jwt_sets_tenant_and_state(self) -> None:
        token = create_jwt(tenant_id="tenant-jwt")
        request = _request()
        info = asyncio_run(verify_api_key(_credentials(token), request))
        assert info.tenant_id == "tenant-jwt"
        assert info.api_key == token
        assert request.state.tenant_id == "tenant-jwt"

    def test_expired_jwt_raises_401(self) -> None:
        now = int(time.time())
        expired = pyjwt.encode(
            {
                "sub": "t",
                "tenant_id": "t",
                "iat": now - 120,
                "exp": now - 60,
                "iss": settings.jwt_issuer,
            },
            settings.jwt_secret,
            algorithm=settings.jwt_algorithm,
        )
        with pytest.raises(HTTPException) as exc:
            asyncio_run(verify_api_key(_credentials(expired), _request()))
        assert exc.value.status_code == 401

    def test_forged_jwt_raises_401(self) -> None:
        now = int(time.time())
        forged = pyjwt.encode(
            {
                "sub": "t",
                "tenant_id": "t",
                "iat": now,
                "exp": now + 3600,
                "iss": settings.jwt_issuer,
            },
            "wrong-secret-0123456789abcdef0123456789",
            algorithm=settings.jwt_algorithm,
        )
        with pytest.raises(HTTPException) as exc:
            asyncio_run(verify_api_key(_credentials(forged), _request()))
        assert exc.value.status_code == 401

    def test_missing_header_raises_401(self) -> None:
        with pytest.raises(HTTPException) as exc:
            asyncio_run(verify_api_key(None, _request()))
        assert exc.value.status_code == 401


def asyncio_run(coro):
    import asyncio

    return asyncio.run(coro)
