from __future__ import annotations

import uuid
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class CreateApiKeyRequest(BaseModel):
    """Request to create a new API key."""
    tenant_id: Optional[str] = Field(default="default", description="Tenant identifier")
    active: bool = Field(default=True, description="Whether the key is active")


class ApiKeyResponse(BaseModel):
    """Response containing API key information (without the actual key)."""
    id: uuid.UUID = Field(description="Unique key identifier")
    key: Optional[str] = Field(
        description="The API key (only returned on creation)",
        default=None
    )
    tenant_id: str = Field(description="Tenant identifier")
    created_at: datetime = Field(description="Creation timestamp")
    active: bool = Field(description="Whether the key is active")


class ApiKeyListResponse(BaseModel):
    """Response containing list of API keys."""
    keys: List[ApiKeyResponse] = Field(description="List of API keys")
    total: int = Field(description="Total number of keys")


class ApiKeyActivateRequest(BaseModel):
    """Request to activate/deactivate API key."""
    active: bool = Field(description="Whether the key should be active")