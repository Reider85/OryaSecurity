from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Depends, Query, HTTPException

from app.core.auth import verify_api_key
from app.core.audit import query_events, count_events
from app.models.audit import AuditEventsResponse, AuditQueryRequest

router = APIRouter(prefix="/api/v1", tags=["audit"])


@router.get("/audit", response_model=AuditEventsResponse)
async def get_audit_events(
    limit: int = Query(10, ge=1, le=100, description="Maximum number of events to return"),
    page: int = Query(1, ge=1, description="Page number"),
    verdict: Optional[str] = Query(None, pattern="^(allow|block)$", description="Filter by verdict"),
    tenant_id: Optional[str] = Query(None, description="Filter by tenant ID"),
    prompt_hash: Optional[str] = Query(None, description="Filter by prompt hash"),
    start_ts: Optional[datetime] = Query(None, description="Filter by start timestamp"),
    end_ts: Optional[datetime] = Query(None, description="Filter by end timestamp"),
    api_key: str = Depends(verify_api_key)
) -> AuditEventsResponse:
    """
    Retrieve audit events with optional filtering and pagination.
    Returns recent events sorted by timestamp (newest first).
    """
    # Calculate offset
    offset = (page - 1) * limit
    
    # Query events
    events = await query_events(
        tenant_id=tenant_id,
        verdict=verdict,
        prompt_hash=prompt_hash,
        start_ts=start_ts,
        end_ts=end_ts,
        limit=limit,
        offset=offset
    )
    
    # Get total count
    total = await count_events(
        tenant_id=tenant_id,
        verdict=verdict,
        prompt_hash=prompt_hash,
        start_ts=start_ts,
        end_ts=end_ts
    )
    
    # Convert to response format
    response_events = []
    for event in events:
        rules_matched = []
        for rule_match in event.get("rules_matched") or []:
            rules_matched.append({
                "rule_id": rule_match.get("rule_id"),
                "position": rule_match.get("position"),
                "value": rule_match.get("value")
            })
        
        response_events.append({
            "id": event.get("id"),
            "ts": event.get("ts"),
            "request_id": event.get("request_id"),
            "tenant_id": event.get("tenant_id"),
            "prompt_hash": event.get("prompt_hash"),
            "prompt_text_redacted": event.get("prompt_text_redacted"),
            "verdict": event.get("verdict"),
            "reason": event.get("reason"),
            "rules_matched": rules_matched,
            "policy_version": event.get("policy_version"),
            "latency_ms": event.get("latency_ms")
        })
    
    return AuditEventsResponse(
        items=response_events,
        total=total,
        page=page,
        per_page=limit
    )