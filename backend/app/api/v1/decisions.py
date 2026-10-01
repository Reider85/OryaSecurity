from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Depends, Query, HTTPException
from uuid import UUID

from app.core.auth import verify_api_key
from app.core.audit import query_events, count_events
from app.models.decisions import (
    DecisionDetail, 
    DecisionListResponse, 
    DecisionFilter,
    RuleMatchDetail,
    LatencyBreakdown
)
from app.models.audit import AuditEventResponse

router = APIRouter(prefix="/api/v1", tags=["decisions"])


def _enhance_audit_event_with_decision_data(audit_event: dict) -> DecisionDetail:
    """Convert basic audit event to enhanced decision detail with latency breakdown"""
    # Create latency breakdown (simulated for now, would be populated in actual implementation)
    latency_breakdown = LatencyBreakdown(
        fast_path_ms=audit_event.get("latency_ms", 0) * 0.3 if audit_event.get("latency_ms") else 0,
        slow_path_ms=audit_event.get("latency_ms", 0) * 0.7 if audit_event.get("latency_ms") else 0,
        pdp_ms=audit_event.get("latency_ms", 0) * 0.2 if audit_event.get("latency_ms") else 0,
        audit_ms=audit_event.get("latency_ms", 0) * 0.1 if audit_event.get("latency_ms") else 0,
        total_ms=audit_event.get("latency_ms", 0) or 0
    )
    
    # Convert rules to detailed format with position information
    rules_matched = []
    for rule_match in audit_event.get("rules_matched") or []:
        rules_matched.append(RuleMatchDetail(
            rule_id=rule_match.get("rule_id"),
            rule_name=f"Rule_{rule_match.get('rule_id')}",  # Would be populated from rule registry
            severity="medium",  # Would be populated from rule definition
            action="block",  # Would be populated from rule definition
            position=rule_match.get("position"),
            matched_value=rule_match.get("value")
        ))
    
    return DecisionDetail(
        request_id=UUID(audit_event.get("request_id")),
        tenant_id=audit_event.get("tenant_id"),
        prompt_hash=audit_event.get("prompt_hash"),
        prompt_text_redacted=audit_event.get("prompt_text_redacted"),
        verdict=audit_event.get("verdict"),
        reason=audit_event.get("reason"),
        rules_matched=rules_matched,
        policy_version=audit_event.get("policy_version", "1.0.0"),
        cache_status="HIT" if audit_event.get("latency_ms", 0) < 5 else "MISS",  # Simulated cache status
        latency_breakdown=latency_breakdown,
        created_at=audit_event.get("ts"),
        updated_at=audit_event.get("ts")
    )


@router.get("/decisions", response_model=DecisionListResponse)
async def get_decisions(
    limit: int = Query(50, ge=1, le=100, description="Maximum number of decisions to return"),
    page: int = Query(1, ge=1, description="Page number"),
    verdict: Optional[str] = Query(None, pattern="^(allow|block)$", description="Filter by verdict"),
    tenant_id: Optional[str] = Query(None, description="Filter by tenant ID"),
    rule_id: Optional[str] = Query(None, description="Filter by rule ID"),
    start_date: Optional[datetime] = Query(None, description="Filter by start date"),
    end_date: Optional[datetime] = Query(None, description="Filter by end date"),
    api_key: str = Depends(verify_api_key)
) -> DecisionListResponse:
    """
    Retrieve decisions with enhanced data including latency breakdown and rule details.
    Returns recent decisions sorted by timestamp (newest first).
    """
    # Calculate offset
    offset = (page - 1) * limit
    
    # Query audit events (decisions are stored as audit events)
    events = await query_events(
        tenant_id=tenant_id,
        verdict=verdict,
        prompt_hash=None,  # Don't filter by hash for decisions
        start_ts=start_date,
        end_ts=end_date,
        limit=limit,
        offset=offset
    )
    
    # Get total count
    total = await count_events(
        tenant_id=tenant_id,
        verdict=verdict,
        prompt_hash=None,  # Don't filter by hash for decisions
        start_ts=start_date,
        end_ts=end_date
    )
    
    # Convert to enhanced decision format
    decision_items = []
    for event in events:
        decision_item = _enhance_audit_event_with_decision_data(event)
        decision_items.append(decision_item)
    
    return DecisionListResponse(
        items=decision_items,
        total=total,
        page=page,
        per_page=limit,
        has_next=total > (page * limit),
        has_prev=page > 1
    )


@router.get("/decisions/{request_id}", response_model=DecisionDetail)
async def get_decision_detail(
    request_id: UUID,
    api_key: str = Depends(verify_api_key)
) -> DecisionDetail:
    """
    Get detailed information about a specific decision.
    """
    # Query the specific audit event
    from app.core.audit import query_event_by_request_id
    event = await query_event_by_request_id(str(request_id))
    
    if not event:
        raise HTTPException(status_code=404, detail="Decision not found")
    
    return _enhance_audit_event_with_decision_data(event)