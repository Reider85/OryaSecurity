"""Unit tests for app.core.jwt — no database required."""

from __future__ import annotations

import time

import jwt as pyjwt
import pytest

from app.config import settings
from app.core.jwt import create_jwt, decode_jwt, is_jwt_candidate


class TestCreateDecode:
    def test_roundtrip_returns_claims(self) -> None:
        token = create_jwt(tenant_id="acme")
        claims = decode_jwt(token)
        assert claims["tenant_id"] == "acme"
        assert claims["sub"] == "acme"
        assert claims["iss"] == settings.jwt_issuer
        assert "jti" in claims
        assert claims["exp"] > claims["iat"]

    def test_custom_ttl(self) -> None:
        token = create_jwt(tenant_id="t1", ttl_seconds=60)
        claims = decode_jwt(token)
        assert claims["exp"] - claims["iat"] == 60

    def test_default_ttl_from_settings(self) -> None:
        token = create_jwt(tenant_id="t2")
        claims = decode_jwt(token)
        assert claims["exp"] - claims["iat"] == settings.jwt_ttl_seconds

    def test_expired_token_raises(self) -> None:
        token = create_jwt(tenant_id="t3", ttl_seconds=-10)
        with pytest.raises(pyjwt.ExpiredSignatureError):
            decode_jwt(token)

    def test_wrong_secret_raises(self) -> None:
        token = pyjwt.encode(
            {
                "sub": "x",
                "tenant_id": "x",
                "iat": int(time.time()),
                "exp": int(time.time()) + 60,
                "iss": settings.jwt_issuer,
            },
            "another-secret",
            algorithm=settings.jwt_algorithm,
        )
        with pytest.raises(pyjwt.InvalidTokenError):
            decode_jwt(token)

    def test_garbage_token_raises(self) -> None:
        with pytest.raises(pyjwt.InvalidTokenError):
            decode_jwt("not-a-jwt")

    def test_missing_sub_raises(self) -> None:
        now = int(time.time())
        token = pyjwt.encode(
            {"iat": now, "exp": now + 60, "iss": settings.jwt_issuer},
            settings.jwt_secret,
            algorithm=settings.jwt_algorithm,
        )
        with pytest.raises(pyjwt.InvalidTokenError):
            decode_jwt(token)

    def test_wrong_issuer_raises(self) -> None:
        now = int(time.time())
        token = pyjwt.encode(
            {
                "sub": "x",
                "tenant_id": "x",
                "iat": now,
                "exp": now + 60,
                "iss": "someone-else",
            },
            settings.jwt_secret,
            algorithm=settings.jwt_algorithm,
        )
        with pytest.raises(pyjwt.InvalidTokenError):
            decode_jwt(token)


class TestIsJwtCandidate:
    @pytest.mark.parametrize(
        "token,expected",
        [
            ("eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.sig", True),
            ("dev-key-12345", False),
            ("", False),
            ("eyJ.only-two-segments", False),
            ("a.b.c", False),
        ],
    )
    def test_heuristic(self, token: str, expected: bool) -> None:
        assert is_jwt_candidate(token) is expected
