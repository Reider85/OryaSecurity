from __future__ import annotations

import bcrypt
import hashlib
import uuid
from typing import Any, Dict, Optional

import jwt
from fastapi import Request, HTTPException, Security
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from app.config import settings
from app.core.jwt import decode_jwt, is_jwt_candidate
from app.db.session import get_db_connection

_bearer_scheme = HTTPBearer(auto_error=False)


def _hash_api_key(api_key: str) -> str:
    """Hash an API key using bcrypt."""
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(api_key.encode(), salt).decode()


def _fingerprint_api_key(api_key: str) -> str:
    """Generate a SHA256 fingerprint for API key lookup (constant time)."""
    return hashlib.sha256(api_key.encode()).hexdigest()


def _verify_api_key_hash(api_key: str, key_hash: str) -> bool:
    """Verify an API key against its bcrypt hash."""
    return bcrypt.checkpw(api_key.encode(), key_hash.encode())


class AuthInfo:
    """Information extracted from API key / JWT authentication."""
    def __init__(self, api_key: str, tenant_id: str):
        self.api_key = api_key
        self.tenant_id = tenant_id


async def authenticate_api_key(api_key: str, request: Request | None = None) -> AuthInfo:
    """Verify a raw API key against the database and return AuthInfo.

    Raises HTTPException:
        401 if the key is unknown or bcrypt mismatch
        403 if the key exists but is inactive
    """
    api_key_fingerprint = _fingerprint_api_key(api_key)

    # Check database for the API key fingerprint
    async with get_db_connection() as conn:
        record = await conn.fetchrow(
            """
            SELECT id, key_hash, tenant_id, active
            FROM api_keys
            WHERE key_fingerprint = $1
            """,
            api_key_fingerprint,
        )

        if not record:
            raise HTTPException(status_code=401, detail="Invalid API key")

        if not record["active"]:
            raise HTTPException(status_code=403, detail="API key is inactive")

        # Verify the actual key using bcrypt (to prevent hash collisions)
        if not _verify_api_key_hash(api_key, record["key_hash"]):
            raise HTTPException(status_code=401, detail="Invalid API key")

        # Store tenant_id in request state for downstream use
        if request:
            request.state.tenant_id = record["tenant_id"]

        return AuthInfo(api_key=api_key, tenant_id=record["tenant_id"])


async def verify_api_key(
    credentials: HTTPAuthorizationCredentials | None = Security(_bearer_scheme),
    request: Request = None,
) -> AuthInfo:
    """Verify the Authorization Bearer credential.

    Accepts either:
      - a JWT issued by POST /api/v1/auth/login (UI auth flow), or
      - a raw API key (SDK / API clients / dogfooding).

    JWT tokens are trusted for their full TTL (MVP: no server-side revocation).
    """
    if credentials is None:
        raise HTTPException(status_code=401, detail="Missing Authorization header")

    token = credentials.credentials

    # 1) JWT path (UI login flow)
    if is_jwt_candidate(token):
        try:
            claims = decode_jwt(token)
        except jwt.InvalidTokenError:
            raise HTTPException(status_code=401, detail="Invalid or expired token")
        tenant_id = claims.get("tenant_id") or claims.get("sub")
        if not tenant_id:
            raise HTTPException(status_code=401, detail="Token missing tenant claim")
        if request:
            request.state.tenant_id = tenant_id
        return AuthInfo(api_key=token, tenant_id=tenant_id)

    # 2) Raw API key path (existing behaviour)
    return await authenticate_api_key(token, request=request)
