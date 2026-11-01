"""V2 Classification endpoint with rate limiting."""

import hashlib
import time
import uuid
from typing import Optional, Dict, Any

import structlog
from fastapi import APIRouter, Depends, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.config import settings
from app.core.auth import verify_api_key, AuthInfo
from app.core.detectors.ml_classifier import ClassificationResult, get_classifier
from app.core.metrics import (
    scanner_classification_requests_total,
    scanner_classification_duration_seconds,
)
from app.core.rate_limit import RateLimiter

router = APIRouter(prefix="/v2", tags=["v2"])
logger = structlog.get_logger()

# Global rate limiter instance
rate_limiter = RateLimiter(max_rps=settings.rate_limit_rps)


# Request/response models
class ClassificationRequest(BaseModel):
    """Request model for classification endpoint."""
    
    text: str
    model: Optional[str] = None


class ClassificationResponse(BaseModel):
    """Response model for classification endpoint."""
    
    id: str
    model: str
    result: Dict[str, Any]
    should_flag: bool


@router.post("/classify")
async def classify_endpoint(
    payload: ClassificationRequest,
    response: Response,
    auth_info: AuthInfo = Depends(verify_api_key),
):
    """Classify text prompt for security threats with rate limiting."""
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
    
    # Get classifier
    classifier = await get_classifier()
    if not classifier or not classifier.available:
        logger.warning("classifier_unavailable", request_id=request_id)
        return JSONResponse(
            status_code=503,
            content={"error": "Classification service unavailable"},
        )
    
    try:
        # Classify the text
        result = await classifier.classify(payload.text)
        if result is None:
            logger.error("classification_failed", request_id=request_id)
            return JSONResponse(
                status_code=500,
                content={"error": "Classification failed"},
            )
        
        # Calculate duration
        duration = (time.perf_counter() - start_time) * 1000
        scanner_classification_duration_seconds.observe(duration / 1000)
        scanner_classification_requests_total.inc()
        
        logger.info(
            "classification_completed",
            request_id=request_id,
            duration_ms=duration,
            top_class=result.top_class,
            confidence=result.confidence,
        )
        
        # Return classification result
        return ClassificationResponse(
            id=request_id,
            model=settings.classifier_model_name,
            result=result.to_dict(),
            should_flag=classifier.should_flag(result),
        )
        
    except Exception as e:
        logger.error("classification_endpoint_error", request_id=request_id, error=str(e))
        return JSONResponse(
            status_code=500,
            content={"error": "Internal server error"},
        )