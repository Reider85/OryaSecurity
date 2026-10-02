from datetime import datetime, timedelta
from typing import List, Dict, Any
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import verify_api_key
from app.db.session import get_session_dep

router = APIRouter(prefix="/api/v1", tags=["charts"])


@router.get("/charts/requests", response_model=List[Dict[str, Any]])
async def get_requests_chart_data(
    hours: int = 24,
    session: AsyncSession = Depends(get_session_dep),
    api_key: str = Depends(verify_api_key)
) -> List[Dict[str, Any]]:
    """
    Returns requests/block rate data for the chart.
    Data is grouped by hour for the specified time range.
    """
    if hours > 168:  # Max 7 days
        hours = 168
    
    # Get time range
    now = datetime.utcnow()
    start_time = now - timedelta(hours=hours)
    
    # Query audit events grouped by hour
    query = """
    SELECT 
        DATE_TRUNC('hour', ts) as hour,
        COUNT(*) as total_requests,
        SUM(CASE WHEN verdict = 'block' THEN 1 ELSE 0 END) as blocked_requests,
        AVG(latency_ms) as avg_latency
    FROM audit_events 
    WHERE ts >= :start_time
    GROUP BY DATE_TRUNC('hour', ts)
    ORDER BY hour ASC
    """
    
    result = await session.execute(text(query), {"start_time": start_time})
    rows = result.fetchall()
    
    # Transform for chart format
    chart_data = []
    for row in rows:
        chart_data.append({
            "timestamp": row.hour.isoformat(),
            "requests": row.total_requests,
            "blocks": row.blocked_requests,
            "blockRate": round((row.blocked_requests / row.total_requests * 100) if row.total_requests > 0 else 0, 2),
            "avg_latency": round(row.avg_latency or 0, 2)
        })
    
    return chart_data


@router.get("/charts/rules", response_model=List[Dict[str, Any]])
async def get_rules_chart_data(
    days: int = 7,
    session: AsyncSession = Depends(get_session_dep),
    api_key: str = Depends(verify_api_key)
) -> List[Dict[str, Any]]:
    """
    Returns top rules matched data for the chart.
    Data is grouped by rule and day for the specified time range.
    """
    if days > 30:  # Max 30 days
        days = 30
    
    # Get time range
    now = datetime.utcnow()
    start_time = now - timedelta(days=days)
    
    # Query rules matched count
    query = """
    WITH rule_counts AS (
        SELECT 
            rules_matched->>'rule_id' as rule_id,
            COUNT(*) as match_count
        FROM audit_events 
        WHERE ts >= :start_time
        AND rules_matched IS NOT NULL
        AND rules_matched != '[]'
        GROUP BY rules_matched->>'rule_id'
    )
    SELECT 
        rc.rule_id,
        rc.match_count,
        r.name as rule_name,
        r.severity
    FROM rule_counts rc
    LEFT JOIN app.core_rules r ON rc.rule_id = r.id
    ORDER BY rc.match_count DESC
    LIMIT 10
    """
    
    # Note: This is a simplified query since we don't have a rules table in the DB
    # In a real implementation, we might need to query the rules files or cache this data
    result = await session.execute(text("""
        SELECT 
            'pii_ssn_us' as rule_id,
            COUNT(*) as match_count
        FROM audit_events 
        WHERE ts >= :start_time
        AND rules_matched::text LIKE '%pii_ssn_us%'
        UNION ALL
        SELECT 'secret_aws_key', COUNT(*) 
        FROM audit_events 
        WHERE ts >= :start_time
        AND rules_matched::text LIKE '%secret_aws_key%'
        UNION ALL
        SELECT 'pii_email', COUNT(*) 
        FROM audit_events 
        WHERE ts >= :start_time
        AND rules_matched::text LIKE '%pii_email%'
        UNION ALL
        SELECT 'secret_jwt', COUNT(*) 
        FROM audit_events 
        WHERE ts >= :start_time
        AND rules_matched::text LIKE '%secret_jwt%'
        UNION ALL
        SELECT 'pii_passport_ru', COUNT(*) 
        FROM audit_events 
        WHERE ts >= :start_time
        AND rules_matched::text LIKE '%pii_passport_ru%'
        ORDER BY match_count DESC
        LIMIT 10
    """), {"start_time": start_time})
    
    rows = result.fetchall()
    
    # Transform for chart format with severity info
    rule_severity_map = {
        "pii_ssn_us": "high",
        "secret_aws_key": "critical", 
        "pii_email": "medium",
        "secret_jwt": "high",
        "pii_passport_ru": "medium"
    }
    
    rule_names_map = {
        "pii_ssn_us": "US SSN",
        "secret_aws_key": "AWS Key",
        "pii_email": "Email", 
        "secret_jwt": "JWT Token",
        "pii_passport_ru": "RU Passport"
    }
    
    chart_data = []
    for row in rows:
        chart_data.append({
            "rule_id": row.rule_id,
            "rule_name": rule_names_map.get(row.rule_id, row.rule_id),
            "count": row.match_count,
            "severity": rule_severity_map.get(row.rule_id, "medium")
        })
    
    return chart_data


@router.get("/charts/realtime", response_model=List[Dict[str, Any]])
async def get_realtime_chart_data(
    minutes: int = 60,
    session: AsyncSession = Depends(get_session_dep),
    api_key: str = Depends(verify_api_key)
) -> List[Dict[str, Any]]:
    """
    Returns real-time request data for the chart.
    Data is grouped by minute for the specified time range.
    """
    if minutes > 1440:  # Max 24 hours
        minutes = 1440
    
    # Get time range
    now = datetime.utcnow()
    start_time = now - timedelta(minutes=minutes)
    
    # Query audit events grouped by minute
    query = """
    SELECT 
        DATE_TRUNC('minute', ts) as minute,
        COUNT(*) as requests,
        SUM(CASE WHEN verdict = 'block' THEN 1 ELSE 0 END) as blocks
    FROM audit_events 
    WHERE ts >= :start_time
    GROUP BY DATE_TRUNC('minute', ts)
    ORDER BY minute ASC
    """
    
    result = await session.execute(text(query), {"start_time": start_time})
    rows = result.fetchall()
    
    # Transform for chart format
    chart_data = []
    for row in rows:
        chart_data.append({
            "timestamp": row.minute.isoformat(),
            "requests": row.requests,
            "blocks": row.blocks,
            "blockRate": round((row.blocks / row.requests * 100) if row.requests > 0 else 0, 2)
        })
    
    return chart_data