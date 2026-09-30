from __future__ import annotations

from fastapi import APIRouter

from app.config import settings
from app.core.auth import authenticate_api_key
from app.core.jwt import create_jwt
from app.models.auth import LoginRequest, LoginResponse

router = APIRouter(prefix="/api/v1", tags=["auth"])


@router.post("/auth/login", response_model=LoginResponse)
async def login(body: LoginRequest) -> LoginResponse:
    """Exchange a scanner API key for a short-lived JWT (UI auth flow).

    Public endpoint (no Authorization header required). The raw API key is
    verified against the database; on success a JWT carrying the tenant_id
    claim is returned with a TTL from settings.jwt_ttl_seconds (default 24h).
    """
    auth_info = await authenticate_api_key(body.api_key)
    token = create_jwt(tenant_id=auth_info.tenant_id)
    return LoginResponse(
        token=token,
        token_type="bearer",
        expires_in=settings.jwt_ttl_seconds,
        tenant_id=auth_info.tenant_id,
    )
