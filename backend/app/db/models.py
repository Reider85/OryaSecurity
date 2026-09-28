from __future__ import annotations

import uuid
from datetime import datetime
from dataclasses import dataclass, field

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Index,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, REAL, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Declarative base for all SQLAlchemy models."""


class AuditEvent(Base):
    """Audit log row for a single scan decision.

    Stores the prompt hash (never the raw prompt) plus the redacted prompt
    text produced by the redaction layer.
    """

    __tablename__ = "audit_events"
    __table_args__ = (
        CheckConstraint("verdict IN ('allow', 'block')", name="ck_audit_events_verdict"),
        Index("ix_audit_events_tenant_ts", "tenant_id", text("ts DESC")),
        Index("ix_audit_events_prompt_hash", "prompt_hash"),
        Index("ix_audit_events_verdict", "verdict"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    request_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    tenant_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    prompt_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_text_redacted: Mapped[str | None] = mapped_column(Text, nullable=True)
    verdict: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    rules_matched: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    policy_version: Mapped[str | None] = mapped_column(String(16), nullable=True)
    latency_ms: Mapped[float | None] = mapped_column(REAL, nullable=True)

    def __repr__(self) -> str:
        return (
            f"AuditEvent(id={self.id!r}, request_id={self.request_id!r}, "
            f"verdict={self.verdict!r}, tenant_id={self.tenant_id!r})"
        )


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