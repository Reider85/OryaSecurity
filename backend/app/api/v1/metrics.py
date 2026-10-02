from datetime import datetime, timedelta
from typing import Dict, Any
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import verify_api_key
from app.core.metrics import metrics_registry
from app.db.session import get_session_dep
from app.models.metrics import MetricsSummaryResponse

router = APIRouter(prefix="/api/v1", tags=["metrics"])


@router.get("/metrics/summary", response_model=MetricsSummaryResponse)
async def get_metrics_summary(
    session: AsyncSession = Depends(get_session_dep),
    api_key: str = Depends(verify_api_key)
) -> MetricsSummaryResponse:
    """
    Returns a summary of scanner metrics for the dashboard.
    Computes rps, block_rate, avg_latency, cache_hit_rate from recent data.
    """
    # Get time range for today (UTC)
    now = datetime.utcnow()
    start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    
    # Query audit events for today
    query = """
    SELECT 
        verdict,
        COUNT(*) as count,
        AVG(latency_ms) as avg_latency
    FROM audit_events 
    WHERE ts >= :start_of_day
    GROUP BY verdict
    """
    
    # SQLAlchemy 2.x requires explicit text() wrapping for raw SQL.
    result = await session.execute(text(query), {"start_of_day": start_of_day})
    verdict_stats = {row.verdict: {"count": row.count, "avg_latency": row.avg_latency} for row in result}
    
    total_requests = sum(stats["count"] for stats in verdict_stats.values())
    
    # Calculate metrics
    rps = total_requests / ((now - start_of_day).total_seconds() or 1)  # Avoid division by zero
    
    # Block rate percentage
    block_count = verdict_stats.get("block", {}).get("count", 0)
    block_rate = (block_count / total_requests * 100) if total_requests > 0 else 0
    
    # Average latency (weighted by verdict counts)
    weighted_latency = 0
    total_latency_weight = 0
    for verdict, stats in verdict_stats.items():
        if stats["avg_latency"]:
            weighted_latency += stats["count"] * stats["avg_latency"]
            total_latency_weight += stats["count"]
    
    avg_latency = weighted_latency / total_latency_weight if total_latency_weight > 0 else 0
    
    # Cache hit rate from Prometheus counters
    cache_hits = metrics_registry.get_sample_value('scanner_cache_hits_total', {'type': 'decision'}) or 0
    cache_misses = metrics_registry.get_sample_value('scanner_cache_misses_total', {'type': 'decision'}) or 0
    total_cache_requests = cache_hits + cache_misses
    cache_hit_rate = (cache_hits / total_cache_requests * 100) if total_cache_requests > 0 else 0
    
    return MetricsSummaryResponse(
        rps=round(rps, 2),
        block_rate=round(block_rate, 2),
        avg_latency=round(avg_latency, 2),
        cache_hit_rate=round(cache_hit_rate, 2),
        timestamp=now
    )