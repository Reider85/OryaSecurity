from __future__ import annotations

import uuid
from datetime import datetime
from dataclasses import dataclass, field


@dataclass
class ApiKeyRecord:
    """Database record for API keys."""
    id: uuid.UUID
    key_hash: str
    tenant_id: str
    created_at: datetime
    active: bool = True
    
    @classmethod
    def create(
        cls,
        key_hash: str,
        tenant_id: str = "default",
        active: bool = True,
    ) -> "ApiKeyRecord":
        """Create a new API key record."""
        return cls(
            id=uuid.uuid4(),
            key_hash=key_hash,
            tenant_id=tenant_id,
            created_at=datetime.utcnow(),
            active=active,
        )