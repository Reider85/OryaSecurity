from __future__ import annotations

import hashlib
from typing import Any

from app.config import settings
from app.db.session import get_db_connection
from app.core.auth import _hash_api_key, _fingerprint_api_key


async def ensure_bootstrap_api_keys() -> int:
    """Ensure API keys from settings.api_keys exist in the database.
    
    This function inserts any keys from settings.api_keys that are absent 
    from the database. It uses the key fingerprint for conflict detection,
    so it's idempotent and safe to run multiple times.
    
    Returns:
        Number of new keys that were inserted.
    """
    inserted_count = 0
    
    if not settings.api_keys:
        return inserted_count
    
    async with get_db_connection() as conn:
        for api_key in settings.api_keys:
            if not api_key:
                continue
                
            key_hash = _hash_api_key(api_key)
            key_fingerprint = _fingerprint_api_key(api_key)
            tenant_id = settings.default_tenant_id
            
            # Check if this key already exists (by fingerprint)
            existing = await conn.fetchrow(
                """
                SELECT id FROM api_keys 
                WHERE key_fingerprint = $1
                """,
                key_fingerprint,
            )
            
            if not existing:
                # Insert the new key
                await conn.execute(
                    """
                    INSERT INTO api_keys (key_hash, key_fingerprint, tenant_id, created_at, active)
                    VALUES ($1, $2, $3, NOW(), TRUE)
                    """,
                    key_hash,
                    key_fingerprint,
                    tenant_id,
                )
                inserted_count += 1
    
    return inserted_count