"""JWT encode/decode helpers for UI authentication.

MVP design: simple API-key -> JWT exchange. The JWT carries the tenant_id
claim so downstream request.state.tenant_id can be populated without a DB
lookup. OPA-based authz appears in ALPHA.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

import jwt

from app.config import settings


def create_jwt(tenant_id: str, ttl_seconds: int | None = None) -> str:
    """Create a signed JWT for a tenant.

    Claims:
        sub / tenant_id: tenant identifier
        jti: unique token id
        iat / exp: issued-at / expiry (unix seconds)
        iss: token issuer
    """
    ttl = ttl_seconds if ttl_seconds is not None else settings.jwt_ttl_seconds
    now = int(time.time())
    payload: dict[str, Any] = {
        "sub": tenant_id,
        "tenant_id": tenant_id,
        "jti": str(uuid.uuid4()),
        "iat": now,
        "exp": now + ttl,
        "iss": settings.jwt_issuer,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_jwt(token: str) -> dict[str, Any]:
    """Decode and validate a JWT.

    Raises:
        jwt.InvalidTokenError: if the token is malformed, expired,
            issued by another party, or signed with a different secret.
    """
    return jwt.decode(
        token,
        settings.jwt_secret,
        algorithms=[settings.jwt_algorithm],
        issuer=settings.jwt_issuer,
        options={"require": ["exp", "iat", "sub"]},
    )


def is_jwt_candidate(token: str) -> bool:
    """Cheap heuristic: JWTs are three base64url segments starting with eyJ."""
    return token.startswith("eyJ") and token.count(".") == 2
