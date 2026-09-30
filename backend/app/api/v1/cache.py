from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.auth import verify_api_key
from app.core.cache import decision_cache
from app.models.cache import CacheEntryResponse, CacheStatsResponse

router = APIRouter(prefix="/api/v1", tags=["cache"])


def _entry_to_response(item: dict) -> CacheEntryResponse:
    return CacheEntryResponse(
        prompt_hash=item["prompt_hash"],
        verdict=item["verdict"],
        ts=datetime.fromtimestamp(item["ts"], tz=timezone.utc),
        expires_at=datetime.fromtimestamp(item["expires_at"], tz=timezone.utc),
    )


@router.get("/cache/stats", response_model=CacheStatsResponse)
async def get_cache_stats(_api_key: str = Depends(verify_api_key)) -> CacheStatsResponse:
    """Retrieve cache statistics (size, hit rate, memory, TTL average)."""
    stats = await decision_cache.stats()
    return CacheStatsResponse(**stats)


@router.get("/cache", response_model=list[CacheEntryResponse])
async def list_cache_entries(
    limit: int = Query(50, ge=1, le=200, description="Maximum number of entries to return"),
    _api_key: str = Depends(verify_api_key),
) -> list[CacheEntryResponse]:
    """List recent cache entries, newest first."""
    entries = await decision_cache.list_entries(limit=limit)
    return [_entry_to_response(item) for item in entries]


@router.delete("/cache/{prompt_hash}", status_code=204)
async def delete_cache_entry(
    prompt_hash: str,
    _api_key: str = Depends(verify_api_key),
) -> None:
    """Delete a single cache entry by prompt hash."""
    deleted = await decision_cache.delete_entry(prompt_hash)
    if not deleted:
        raise HTTPException(status_code=404, detail="Cache entry not found")


@router.delete("/cache", status_code=204)
async def flush_cache(_api_key: str = Depends(verify_api_key)) -> None:
    """Flush all cache entries."""
    await decision_cache.flush()
