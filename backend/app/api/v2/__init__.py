"""V2 API endpoints."""

from .openai_stream import router as v2_router

__all__ = ["v2_router"]