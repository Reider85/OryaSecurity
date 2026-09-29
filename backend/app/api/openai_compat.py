from __future__ import annotations

import time
import uuid

import structlog
from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse

from app.config import settings
from app.core.auth import verify_api_key, AuthInfo
from app.core.headers import set_scanner_context
from app.core.proxy import LLMProxyError, proxy_chat
from app.core.rate_limit import RateLimiter
from app.models.openai import ChatCompletionRequest

router = APIRouter(prefix="/v1", tags=["openai-compat"])
logger = structlog.get_logger()

# Global rate limiter instance
rate_limiter = RateLimiter(max_rps=settings.rate_limit_rps)


@router.post(
    "/chat/completions",
    response_model=dict,
    summary="OpenAI-compatible Chat Completions proxy",
    description="Drop-in replacement for OpenAI Chat Completions API. "
    "Inspects prompt and generation for PII/secrets before forwarding.",
)
async def chat_completions(
    payload: ChatCompletionRequest,
    request: Request,
    response: Response,
    auth_info: AuthInfo = Depends(verify_api_key),
) -> dict:
    start = time.perf_counter()
    request_id = str(uuid.uuid4())

    # Rate limit check
    api_key_hash = auth_info.api_key  # verify_api_key returns the raw API key
    remaining = await rate_limiter.check(api_key_hash)

    # Add rate limit headers
    response.headers["X-RateLimit-Limit"] = str(settings.rate_limit_rps)
    response.headers["X-RateLimit-Remaining"] = str(remaining)

    response.headers["X-Request-Id"] = request_id

    try:
        result = await proxy_chat(payload)
    except LLMProxyError as exc:
        # proxy_chat raises for both policy blocks (403) and upstream failures
        # (502). Only a 403 is a scanner decision; a 502 means the request was
        # allowed but the LLM could not be reached, so reporting it as a block
        # would be misleading. Both still carry a reason for the client.
        verdict = "block" if exc.status_code == 403 else "allow"
        latency_ms = (time.perf_counter() - start) * 1000

        set_scanner_context(
            request,
            verdict=verdict,
            reason=exc.detail,
            latency_ms=latency_ms,
            request_id=request_id,
        )

        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"message": exc.detail, "type": "scanner_error"}},
            headers={
                "X-RateLimit-Limit": str(settings.rate_limit_rps),
                "X-RateLimit-Remaining": str(remaining),
                "X-Request-Id": request_id,
            },
        )

    latency_ms = (time.perf_counter() - start) * 1000

    # proxy_chat masks blocked generations but still returns a 200 response, so
    # the prompt verdict is "allow" here.
    set_scanner_context(
        request,
        verdict="allow",
        reason="Prompt passed inspection",
        latency_ms=latency_ms,
        request_id=request_id,
    )

    return result.model_dump()
