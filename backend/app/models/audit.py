from datetime import datetime
from typing import Optional
from pydantic import BaseModel


class AuditQueryRequest(BaseModel):
    """Request model for querying audit events"""
    tenant_id: Optional[str] = None
    verdict: Optional[str] = None  # "allow" | "block" | None (all)
    prompt_hash: Optional[str] = None
    start_ts: Optional[datetime] = None
    end_ts: Optional[datetime] = None
    limit: int = 50
    offset: int = 0


class RuleMatchResponse(BaseModel):
    """Response model for rule matches in audit events"""
    rule_id: str
    position: int
    value: Optional[str] = None


class AuditEventResponse(BaseModel):
    """Response model for individual audit events"""
    id: int
    ts: datetime
    request_id: str
    tenant_id: Optional[str] = None
    prompt_hash: str
    prompt_text_redacted: str
    verdict: str
    reason: Optional[str] = None
    rules_matched: list[RuleMatchResponse]
    policy_version: Optional[str] = None
    latency_ms: Optional[float] = None


class AuditEventsResponse(BaseModel):
    """Response model for audit events list"""
    items: list[AuditEventResponse]
    total: int
    page: int
    per_page: int