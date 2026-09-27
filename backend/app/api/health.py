from __future__ import annotations

import time

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from app.config import settings
from app.core.deps import check_all_dependencies

router = APIRouter(tags=["health"])

_start_time = time.time()


@router.get("/health", summary="Health check endpoint")
async def health_check() -> JSONResponse:
    # Calculate uptime
    uptime = time.time() - _start_time
    
    # Check all dependencies
    dependency_status = await check_all_dependencies()
    
    # Determine overall status
    postgres_ok = dependency_status["postgres"]
    redis_ok = dependency_status["redis"]
    
    if postgres_ok and redis_ok:
        status = "ok"
        http_status = 200
    elif postgres_ok or redis_ok:
        status = "degraded"
        http_status = 200
    else:
        status = "down"
        http_status = 503
    
    # Prepare response
    response_data = {
        "status": status,
        "version": settings.app_version,
        "uptime_seconds": round(uptime, 1),
        "dependencies": {
            "postgres": "ok" if postgres_ok else "fail",
            "redis": "ok" if redis_ok else "fail",
        },
    }
    
    return JSONResponse(
        content=response_data,
        status_code=http_status,
        headers={"X-Scanner-Status": status}
    )
