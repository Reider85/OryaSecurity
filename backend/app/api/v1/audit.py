from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import verify_api_key
from app.core.audit import query_events
from app.db.session import get_session
from app.models.audit import AuditEventsResponse, AuditQueryRequest

router = APIRouter(prefix="/api/v1", tags=["audit"])


@router.get("/audit", response_model=AuditEventsResponse)
async def get_audit_events(
    limit: int = Query(10, ge=1, le=100, description="Maximum number of events to return"),
    page: int = Query(1, ge=1, description="Page number"),
    verdict: Optional[str] = Query(None, regex="^(allow|block)$", description="Filter by verdict"),
    tenant_id: Optional[str] = Query(None, description="Filter by tenant ID"),
    prompt_hash: Optional[str] = Query(None, description="Filter by prompt hash"),
    start_ts: Optional[datetime] = Query(None, description="Filter by start timestamp"),
    end_ts: Optional[datetime] = Query(None, description="Filter by end timestamp"),
    session: AsyncSession = Depends(get_session),
    api_key: str = Depends(verify_api_key)
) -> AuditEventsResponse:
    """
    Retrieve audit events with optional filtering and pagination.
    Returns recent events sorted by timestamp (newest first).
    """
    # Calculate offset
    offset = (page - 1) * limit
    
    # Build query parameters
    query_params = AuditQueryRequest(
        tenant_id=tenant_id,
        verdict=verdict,
        prompt_hash=prompt_hash,
        start_ts=start_ts,
        end_ts=end_ts,
        limit=limit,
        offset=offset
    )
    
    # Query events
    events = await query_events(session, query_params)
    total = len(events)
    
    # Convert to response format
    response_events = []
    for event in events:
        rules_matched = []
        for rule_match in event.rules_matched or []:
            rules_matched.append({
                "rule_id": rule_match.rule_id,
                "position": rule_match.position,
                "value": rule_match.value
            })
        
        response_events.append({
            "id": event.id,
            "ts": event.ts,
            "request_id": str(event.request_id),
            "tenant_id": event.tenant_id,
            "prompt_hash": event.prompt_hash,
            "prompt_text_redacted": event.prompt_text_redacted,
            "verdict": event.verdict,
            "reason": event.reason,
            "rules_matched": rules_matched,
            "policy_version": event.policy_version,
            "latency_ms": event.latency_ms
        })
    
    return AuditEventsResponse(
        items=response_events,
        total=total,
        page=page,
        per_page=limit
    )