from __future__ import annotations

import hashlib

from fastapi import Request, HTTPException, Security
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from app.config import settings

_bearer_scheme = HTTPBearer(auto_error=False)


def _hash_key(api_key: str) -> str:
    return hashlib.sha256(api_key.encode()).hexdigest()


_VALID_KEY_HASHES: set[str] = set()


def _load_keys() -> None:
    global _VALID_KEY_HASHES
    _VALID_KEY_HASHES = {_hash_key(k) for k in settings.api_keys}


_load_keys()


def verify_api_key(
    credentials: HTTPAuthorizationCredentials | None = Security(_bearer_scheme),
) -> str:
    if credentials is None:
        raise HTTPException(status_code=401, detail="Missing Authorization header")

    token = credentials.credentials
    token_hash = _hash_key(token)

    if token_hash not in _VALID_KEY_HASHES:
        raise HTTPException(status_code=401, detail="Invalid API key")

    return token
