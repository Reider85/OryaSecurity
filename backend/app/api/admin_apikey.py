from __future__ import annotations

import uuid
import secrets
import bcrypt
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPBearer

from app.core.auth import verify_api_key, _fingerprint_api_key
from app.db.session import get_db_connection
from app.db.models import ApiKeyRecord
from app.models.apikey import (
    CreateApiKeyRequest,
    ApiKeyResponse,
    ApiKeyListResponse,
    ApiKeyActivateRequest,
)

router = APIRouter(prefix="/api/v1/admin", tags=["admin-apikey"])
bearer_scheme = HTTPBearer()


def _generate_api_key() -> str:
    """Generate a random API key."""
    return secrets.token_urlsafe(32)


def _hash_api_key(api_key: str) -> str:
    """Hash an API key using bcrypt."""
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(api_key.encode(), salt).decode()


def _verify_api_key(api_key: str, key_hash: str) -> bool:
    """Verify an API key against its hash."""
    return bcrypt.checkpw(api_key.encode(), key_hash.encode())


@router.post("/keys", response_model=ApiKeyResponse, status_code=status.HTTP_201_CREATED)
async def create_api_key(
    request: CreateApiKeyRequest,
    _auth_info = Depends(verify_api_key),
) -> ApiKeyResponse:
    """Create a new API key."""
    # Generate and hash the key
    api_key = _generate_api_key()
    key_hash = _hash_api_key(api_key)
    key_fingerprint = _fingerprint_api_key(api_key)
    
    # Create record
    record = ApiKeyRecord.create(
        key_hash=key_hash,
        key_fingerprint=key_fingerprint,
        tenant_id=request.tenant_id,
        active=request.active,
    )
    
    # Save to database
    async with get_db_connection() as conn:
        await conn.execute(
            """
            INSERT INTO api_keys (id, key_hash, key_fingerprint, tenant_id, created_at, active)
            VALUES ($1, $2, $3, $4, $5, $6)
            """,
            record.id,
            record.key_hash,
            record.key_fingerprint,
            record.tenant_id,
            record.created_at,
            record.active,
        )
    
    return ApiKeyResponse(
        id=record.id,
        key=api_key,  # Only return the key once!
        tenant_id=record.tenant_id,
        created_at=record.created_at,
        active=record.active,
    )


@router.get("/keys", response_model=ApiKeyListResponse)
async def list_api_keys(
    _auth_info = Depends(verify_api_key),
) -> ApiKeyListResponse:
    """List all API keys (without their actual values)."""
    async with get_db_connection() as conn:
        records = await conn.fetch(
            """
            SELECT id, key_hash, tenant_id, created_at, active
            FROM api_keys
            ORDER BY created_at DESC
            """
        )
    
    keys = [
        ApiKeyResponse(
            id=record["id"],
            key=None,  # Don't return actual keys
            tenant_id=record["tenant_id"],
            created_at=record["created_at"],
            active=record["active"],
        )
        for record in records
    ]
    
    return ApiKeyListResponse(keys=keys, total=len(keys))


@router.post("/keys/{key_id}/activate", response_model=ApiKeyResponse)
async def activate_api_key(
    key_id: uuid.UUID,
    request: ApiKeyActivateRequest,
    _auth_info = Depends(verify_api_key),
) -> ApiKeyResponse:
    """Activate or deactivate an API key."""
    async with get_db_connection() as conn:
        # Check if key exists
        record = await conn.fetchrow(
            "SELECT id, tenant_id, created_at, active FROM api_keys WHERE id = $1",
            key_id,
        )
        
        if not record:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="API key not found",
            )
        
        # Update active status
        await conn.execute(
            "UPDATE api_keys SET active = $1 WHERE id = $2",
            request.active,
            key_id,
        )
        
        return ApiKeyResponse(
            id=key_id,
            key=None,  # Don't return actual key
            tenant_id=record["tenant_id"],
            created_at=record["created_at"],
            active=request.active,
        )


@router.delete("/keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_api_key(
    key_id: uuid.UUID,
    _auth_info = Depends(verify_api_key),
) -> None:
    """Delete an API key."""
    async with get_db_connection() as conn:
        # Check if key exists and delete it
        result = await conn.execute(
            "DELETE FROM api_keys WHERE id = $1",
            key_id,
        )
        
        if "DELETE 0" in result:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="API key not found",
            )