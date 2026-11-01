"""V2 Embeddings endpoint with rate limiting."""

import hashlib
import time
import uuid
from typing import Optional, Dict, Any, List

import numpy as np
import structlog
from fastapi import APIRouter, Depends, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.config import settings
from app.core.auth import verify_api_key, AuthInfo
from app.core.cache_embedding import embedding_cache
from app.core.detectors import get_shared_embedding_client
from app.core.metrics import (
    scanner_embedding_requests_total,
    scanner_embedding_duration_seconds,
    scanner_embedding_cache_hits_total,
    scanner_embedding_cache_misses_total,
)
from app.core.rate_limit import RateLimiter

router = APIRouter(prefix="/v2", tags=["v2"])
logger = structlog.get_logger()

# Global rate limiter instance
rate_limiter = RateLimiter(max_rps=settings.rate_limit_rps)


# Request/response models
class EmbeddingRequest(BaseModel):
    """Request model for embeddings endpoint."""
    
    input: str
    model: Optional[str] = None
    encoding_format: Optional[str] = "float"


class EmbeddingResponse(BaseModel):
    """Response model for embeddings endpoint."""
    
    id: str
    object: str
    data: List[Dict[str, Any]]
    model: str
    usage: Dict[str, int]


@router.post("/embeddings")
async def embeddings_endpoint(
    payload: EmbeddingRequest,
    response: Response,
    auth_info: AuthInfo = Depends(verify_api_key),
):
    """Generate embeddings for input text with rate limiting."""
    start_time = time.perf_counter()
    request_id = str(uuid.uuid4())
    
    # Rate limit check
    api_key_hash = hashlib.sha256(auth_info.api_key.encode()).hexdigest()
    try:
        remaining = await rate_limiter.check(api_key_hash)
    except Exception as e:
        logger.error("rate_limit_check_failed", request_id=request_id, error=str(e))
        return JSONResponse(
            status_code=429,
            content={"error": "Rate limit check failed"},
            headers={
                "Retry-After": "1",
                "X-RateLimit-Limit": str(settings.rate_limit_rps),
                "X-RateLimit-Remaining": "0",
            }
        )
    
    # Add rate limit headers
    response.headers.update({
        "X-RateLimit-Limit": str(settings.rate_limit_rps),
        "X-RateLimit-Remaining": str(remaining),
    })
    
    # Get embedding client
    embedding_client = get_shared_embedding_client()
    if not embedding_client or not embedding_client.available:
        logger.warning("embedding_unavailable", request_id=request_id)
        return JSONResponse(
            status_code=503,
            content={"error": "Embedding service unavailable"},
        )
    
    try:
        # Check cache first
        cached_embedding = await embedding_cache.get(payload.input)
        if cached_embedding is not None:
            scanner_embedding_cache_hits_total.inc()
            logger.info("embedding_cache_hit", request_id=request_id)
        else:
            scanner_embedding_cache_misses_total.inc()
            logger.info("embedding_cache_miss", request_id=request_id)
            
            # Generate embedding
            embedding = await embedding_client.embed(payload.input)
            if embedding is None:
                logger.error("embedding_generation_failed", request_id=request_id)
                return JSONResponse(
                    status_code=500,
                    content={"error": "Failed to generate embedding"},
                )
            
            # Cache the embedding
            await embedding_cache.set(payload.input, embedding)
        
        # Calculate duration
        duration = (time.perf_counter() - start_time) * 1000
        scanner_embedding_duration_seconds.observe(duration / 1000)
        scanner_embedding_requests_total.inc()
        
        logger.info(
            "embedding_generated",
            request_id=request_id,
            duration_ms=duration,
            cached=cached_embedding is not None,
        )
        
        # Return OpenAI-compatible response
        return EmbeddingResponse(
            id=request_id,
            object="embedding",
            data=[{
                "object": "embedding",
                "embedding": cached_embedding.tolist() if cached_embedding is not None else embedding.tolist(),
                "index": 0,
            }],
            model=settings.embedding_model_name,
            usage={
                "prompt_tokens": len(payload.input.split()),
                "total_tokens": len(payload.input.split()),
            },
        )
        
    except Exception as e:
        logger.error("embedding_endpoint_error", request_id=request_id, error=str(e))
        return JSONResponse(
            status_code=500,
            content={"error": "Internal server error"},
        )