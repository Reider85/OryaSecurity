from __future__ import annotations

import uuid

import structlog
from fastapi import APIRouter, Depends, Request, Response

from app.core.auth import verify_api_key, AuthInfo
from app.core.proxy import LLMProxyError, proxy_chat
from app.core.rate_limit import RateLimiter
from app.models.openai import ChatCompletionRequest

router = APIRouter(prefix="/v1", tags=["openai-compat"])
logger = structlog.get_logger()

# Global rate limiter instance
rate_limiter = RateLimiter(max_rps=100)


@router.post(
    "/chat/completions",
    response_model=dict,
    summary="OpenAI-compatible Chat Completions proxy",
    description="Drop-in replacement for OpenAI Chat Completions API. "
    "Inspects prompt and generation for PII/secrets before forwarding.",
)
async def chat_completions(
    request: ChatCompletionRequest,
    response: Response,
    auth_info: AuthInfo = Depends(verify_api_key),
) -> dict:
    # Rate limit check
    api_key_hash = auth_info.api_key  # verify_api_key returns the raw API key
    remaining = await rate_limiter.check(api_key_hash)
    
    # Add rate limit headers
    response.headers["X-RateLimit-Limit"] = "100"
    response.headers["X-RateLimit-Remaining"] = str(remaining)
    
    request_id = str(uuid.uuid4())
    response.headers["X-Request-Id"] = request_id

    try:
        result = await proxy_chat(request)
    except LLMProxyError as exc:
        response.headers["X-Scanner-Verdict"] = "block"
        response.headers["X-Scanner-Reason"] = exc.detail
        response.headers["X-Scanner-Request-Id"] = request_id
        from fastapi.responses import JSONResponse

        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"message": exc.detail, "type": "scanner_error"}},
            headers=dict(response.headers),
        )

    response.headers["X-Scanner-Verdict"] = "allow"
    response.headers["X-Scanner-Request-Id"] = request_id

    return result.model_dump()
