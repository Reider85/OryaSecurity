from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    """Request model for the UI login exchange (API key -> JWT)."""
    api_key: str = Field(..., min_length=1, description="Scanner API key")


class LoginResponse(BaseModel):
    """Response model returned after a successful login."""
    token: str
    token_type: str = "bearer"
    expires_in: int
    tenant_id: str
