"""V2 API endpoints."""

from fastapi import APIRouter

from .openai_stream import router as openai_stream_router
from .notifications import router as notifications_router

v2_router = APIRouter()
v2_router.include_router(openai_stream_router)
v2_router.include_router(notifications_router)

__all__ = ["v2_router"]