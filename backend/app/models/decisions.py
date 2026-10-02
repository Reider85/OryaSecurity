from datetime import datetime
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field
from uuid import UUID


class RuleMatchPosition(BaseModel):
    """Position information for rule matches in text"""
    start: int
    end: int
    matched_text: str = ""


class RuleMatchDetail(BaseModel):
    """Enhanced rule match with position information for text highlighting"""
    rule_id: str
    rule_name: str
    severity: str
    action: str
    position: Optional[RuleMatchPosition] = None
    matched_value: Optional[str] = None


class LatencyBreakdown(BaseModel):
    """Detailed latency breakdown for decision analysis"""
    fast_path_ms: float = Field(description="Cache lookup + basic validation time")
    slow_path_ms: float = Field(description="Full scan + PDP evaluation time")
    pdp_ms: float = Field(description="Policy Decision Point evaluation time")
    audit_ms: float = Field(description="Audit logging time")
    total_ms: float = Field(description="Total request time")


class DecisionDetail(BaseModel):
    """Enhanced decision data with detailed information"""
    request_id: UUID
    tenant_id: Optional[str]
    prompt_hash: str
    # Optional: a row can be written without a redacted prompt (e.g. by the
    # proxy path), and core.audit._serialize passes None straight through.
    prompt_text_redacted: Optional[str] = None
    verdict: str = Field(description="allow|block")
    reason: Optional[str] = None
    rules_matched: List[RuleMatchDetail]
    policy_version: str
    cache_status: str = Field(description="HIT|MISS")
    latency_breakdown: LatencyBreakdown
    created_at: datetime
    updated_at: datetime


class DecisionFilter(BaseModel):
    """Filter parameters for decisions endpoint"""
    tenant_id: Optional[str] = None
    verdict: Optional[str] = Field(description="allow|block", default=None)
    rule_id: Optional[str] = None
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None


class DecisionListResponse(BaseModel):
    """Response for decisions list with pagination"""
    items: List[DecisionDetail]
    total: int
    page: int
    per_page: int
    has_next: bool
    has_prev: bool


class DecisionStats(BaseModel):
    """Statistics for decisions"""
    total_count: int
    allow_count: int
    block_count: int
    avg_latency_ms: float
    cache_hit_rate: float
    top_rules: List[Dict[str, Any]]