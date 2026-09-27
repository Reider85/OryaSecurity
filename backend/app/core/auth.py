from __future__ import annotations

import bcrypt
import uuid
from typing import Any, Dict, Optional

from fastapi import Request, HTTPException, Security
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from app.config import settings
from app.db.session import get_db_connection

_bearer_scheme = HTTPBearer(auto_error=False)


def _hash_api_key(api_key: str) -> str:
    """Hash an API key using bcrypt."""
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(api_key.encode(), salt).decode()


def _verify_api_key_hash(api_key: str, key_hash: str) -> bool:
    """Verify an API key against its bcrypt hash."""
    return bcrypt.checkpw(api_key.encode(), key_hash.encode())


class AuthInfo:
    """Information extracted from API key authentication."""
    def __init__(self, api_key: str, tenant_id: str):
        self.api_key = api_key
        self.tenant_id = tenant_id


async def verify_api_key(
    credentials: HTTPAuthorizationCredentials | None = Security(_bearer_scheme),
    request: Request = None,
) -> AuthInfo:
    """Verify API key from database and return tenant_id."""
    if credentials is None:
        raise HTTPException(status_code=401, detail="Missing Authorization header")

    api_key = credentials.credentials
    
    # Check database for the API key
    async with get_db_connection() as conn:
        record = await conn.fetchrow(
            """
            SELECT key_hash, tenant_id, active
            FROM api_keys
            WHERE key_hash = $1
            """,
            _hash_api_key(api_key),
        )
        
        if not record:
            raise HTTPException(status_code=401, detail="Invalid API key")
        
        if not record["active"]:
            raise HTTPException(status_code=403, detail="API key is inactive")
        
        # Store tenant_id in request state for downstream use
        if request:
            request.state.tenant_id = record["tenant_id"]
        
        return AuthInfo(api_key=api_key, tenant_id=record["tenant_id"])
