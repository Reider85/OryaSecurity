#!/usr/bin/env python3
"""
Demo script for Prompt 6 - API-key auth implementation.
This script demonstrates the new PostgreSQL-backed authentication system.
"""

import asyncio
import secrets
import bcrypt
from app.db.session import get_db_connection, init_db
from app.core.auth import _hash_api_key, _verify_api_key_hash


async def demo_api_key_auth():
    """Demonstrate API key creation and verification."""
    print("=== API Key Auth Demo ===")
    
    # 1. Create a test API key
    api_key = secrets.token_urlsafe(32)
    print(f"Generated API key: {api_key}")
    
    # 2. Hash the key using bcrypt
    key_hash = _hash_api_key(api_key)
    print(f"Key hash: {key_hash[:20]}...")
    
    # 3. Store it in database
    async with get_db_connection() as conn:
        await conn.execute(
            """
            INSERT INTO api_keys (key_hash, tenant_id, created_at, active)
            VALUES ($1, $2, NOW(), TRUE)
            """,
            key_hash,
            "demo-tenant",
        )
        print("API key stored in database")
    
    # 4. Verify the key works
    is_valid = _verify_api_key_hash(api_key, key_hash)
    print(f"Key verification result: {is_valid}")
    
    # 5. Test with wrong key
    wrong_key = "wrong-key-12345"
    is_wrong_valid = _verify_api_key_hash(wrong_key, key_hash)
    print(f"Wrong key verification result: {is_wrong_valid}")
    
    # 6. Cleanup
    async with get_db_connection() as conn:
        await conn.execute("DELETE FROM api_keys WHERE key_hash = $1", key_hash)
        print("API key cleaned up")
    
    print("=== Demo Complete ===")


if __name__ == "__main__":
    asyncio.run(demo_api_key_auth())