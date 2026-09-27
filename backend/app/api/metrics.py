from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["metrics"])


@router.get("/metrics", summary="Prometheus metrics endpoint")
async def metrics() -> dict:
    return {
        "status": "metrics not yet implemented",
        "hint": "Will expose prometheus_client metrics in EP-06",
    }
