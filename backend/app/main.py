from __future__ import annotations

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.api.scan import router as scan_router
from app.api.health import router as health_router
from app.api.metrics import router as metrics_router
from app.api.openai_compat import router as openai_compat_router
from app.api.admin_apikey import router as admin_apikey_router
from app.api.v1 import metrics_router as v1_metrics_router, audit_router as v1_audit_router
from app.core.headers import ScannerHeadersMiddleware
from app.db.session import init_db, close_db
from app.db.redis_client import init_redis, close_redis
from app.core.bootstrap import ensure_bootstrap_api_keys
from app.core.metrics import scanner_uptime_seconds, python_info
import time
import sys
import platform

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.processors.JSONRenderer(),
    ],
)

app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Added after CORS so that it sits *inside* the CORS layer: Starlette runs the
# last-registered middleware first, and CORS needs to be able to decorate error
# responses produced further down the stack.
app.add_middleware(ScannerHeadersMiddleware)


@app.on_event("startup")
async def startup_event():
    """Initialize database, Redis, and ensure bootstrap API keys on startup."""
    await init_db()
    await init_redis()
    await ensure_bootstrap_api_keys()
    
    # Initialize uptime and Python info metrics
    scanner_uptime_seconds.set(time.time())
    python_info.labels(
        version=sys.version.split()[0],
        implementation=platform.python_implementation(),
        platform=platform.platform(),
    ).set(1)


@app.on_event("shutdown")
async def shutdown_event():
    """Close Redis and database connections on shutdown."""
    await close_redis()
    await close_db()


app.include_router(health_router)
app.include_router(metrics_router)
app.include_router(scan_router)
app.include_router(openai_compat_router)
app.include_router(admin_apikey_router)
app.include_router(v1_metrics_router)
app.include_router(v1_audit_router)