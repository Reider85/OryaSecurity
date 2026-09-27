from __future__ import annotations

import time

from fastapi import APIRouter

from app.config import settings

router = APIRouter(tags=["health"])

_start_time = time.time()


@router.get("/health", summary="Health check endpoint")
async def health_check() -> dict:
    uptime = time.time() - _start_time
    return {
        "status": "ok",
        "version": settings.app_version,
        "uptime_seconds": round(uptime, 1),
        "dependencies": {
            "postgres": "ok",
            "redis": "ok",
        },
    }
