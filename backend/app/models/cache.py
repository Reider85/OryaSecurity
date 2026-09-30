from datetime import datetime

from pydantic import BaseModel


class CacheStatsResponse(BaseModel):
    """Response model for cache statistics"""
    total_entries: int
    hit_rate_24h: float
    memory_usage: int
    ttl_average: float


class CacheEntryResponse(BaseModel):
    """Response model for a single cache entry"""
    prompt_hash: str
    verdict: str
    ts: datetime
    expires_at: datetime
