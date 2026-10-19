from __future__ import annotations

import time
import uuid

import structlog
from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import StreamingResponse

from app.config import settings
from app.core.audit import write_event
from app.core.auth import verify_api_key, AuthInfo
from app.core.headers import set_scanner_context
from app.core.metrics import (
    scanner_requests_total,
    scanner_request_duration_seconds,
)
from app.core.pdp import scan_text
from app.core.proxy import LLMProxyError, _build_forward_payload, _extract_prompt_text
from app.core.rate_limit import RateLimiter
from app.core.detectors.toxicity import ToxicityDetector
from app.core.streaming.inspector import StreamingInspector
from app.core.streaming.sse import format_sse, sse_response_headers, scanner_blocked_event
from app.models.openai import ChatCompletionRequest

router = APIRouter(prefix="/v2", tags=["v2"])
logger = structlog.get_logger()

# Global rate limiter instance
rate_limiter = RateLimiter(max_rps=settings.rate_limit_rps)


async def chat_completions_stream(
    payload: ChatCompletionRequest,
    request: Request,
    response: Response,
    auth_info: AuthInfo = Depends(verify_api_key),
):
    """OpenAI-compatible Chat Completions streaming endpoint with per-chunk inspection."""
    start = time.perf_counter()
    request_id = str(uuid.uuid4())
    
    # Rate limit check
    api_key_hash = auth_info.api_key
    remaining = await rate_limiter.check(api_key_hash)
    
    # Add headers to response
    response.headers.update({
        "X-RateLimit-Limit": str(settings.rate_limit_rps),
        "X-RateLimit-Remaining": str(remaining),
        "X-Request-Id": request_id,
    })
    
    # Prompt-side inspection (same as v1)
    prompt_text = _extract_prompt_text(payload.messages)
    prompt_matches, prompt_hash = scan_text(prompt_text)
    
    # Check cache
    from app.core.cache import decision_cache
    cached, cache_hit = await decision_cache.get(prompt_text)
    
    if cached:
        verdict_action = cached["verdict"]
        verdict_reason = cached["reason"]
        verdict_matches = cached["rules_matched"]
        logger.info(
            "proxy_cache_hit",
            request_id=request_id,
            prompt_hash=prompt_hash,
            verdict=verdict_action,
        )
    else:
        from app.core.pdp import decide
        verdict = decide(prompt_matches)
        verdict_action = verdict.action
        verdict_reason = verdict.reason
        verdict_matches = [m.to_dict() for m in verdict.rules_matched]
        await decision_cache.set(prompt_text, verdict_action, verdict_reason, verdict_matches)
    
    latency_ms = (time.perf_counter() - start) * 1000
    
    logger.info(
        "proxy_prompt_inspection",
        request_id=request_id,
        prompt_hash=prompt_hash,
        verdict=verdict_action,
        rules_matched_count=len(verdict_matches),
        latency_ms=round(latency_ms, 2),
        cache_hit=cache_hit,
    )
    
    # If prompt is blocked, return 403 before starting stream
    if verdict_action == "block":
        await write_event(
            request_id=request_id,
            prompt_hash=prompt_hash,
            verdict="block",
            reason=verdict_reason,
            rules_matched=verdict_matches,
            tenant_id=auth_info.tenant_id or settings.default_tenant_id,
            latency_ms=latency_ms,
            metadata={"direction": "prompt"},
        )
        
        set_scanner_context(
            request,
            verdict="block",
            reason=verdict_reason,
            latency_ms=latency_ms,
            request_id=request_id,
        )
        
        # Record request metrics
        scanner_request_duration_seconds.observe(latency_ms / 1000)
        scanner_requests_total.labels(
            verdict="block",
            tenant_id=auth_info.tenant_id or settings.default_tenant_id or "unknown",
        ).inc()
        
        return StreamingResponse(
            content=generate_error_stream(
                {"error": {"message": verdict_reason, "type": "scanner_error"}},
                request_id
            ),
            status_code=403,
            headers=sse_response_headers(request_id),
        )
    
    # Prompt is allowed, start streaming
    await write_event(
        request_id=request_id,
        prompt_hash=prompt_hash,
        verdict="allow",
        reason=verdict_reason,
        rules_matched=verdict_matches,
        tenant_id=auth_info.tenant_id or settings.default_tenant_id,
        latency_ms=latency_ms,
        metadata={"direction": "prompt"},
    )
    
    set_scanner_context(
        request,
        verdict="allow",
        reason=verdict_reason,
        latency_ms=latency_ms,
        request_id=request_id,
    )
    
    # Record request metrics
    scanner_request_duration_seconds.observe(latency_ms / 1000)
    scanner_requests_total.labels(
        verdict="allow",
        tenant_id=auth_info.tenant_id or settings.default_tenant_id or "unknown",
    ).inc()
    
    # Create streaming inspector
    inspector = StreamingInspector(
        chunk_size=settings.streaming_chunk_size,
        toxicity_detector=ToxicityDetector(threshold=settings.toxicity_threshold),
    )
    
    # Start streaming response
    return StreamingResponse(
        content=generate_stream(
            payload=payload,
            request_id=request_id,
            inspector=inspector,
            tenant_id=auth_info.tenant_id or settings.default_tenant_id
        ),
        headers=sse_response_headers(request_id),
    )


async def generate_stream(
    payload: ChatCompletionRequest,
    request_id: str,
    inspector: StreamingInspector,
    tenant_id: str,
) -> AsyncGenerator[str, None]:
    """Generate SSE stream with per-chunk inspection."""
    try:
        # Build forward payload
        forward_payload = _build_forward_payload(payload)
        
        # Create HTTP client and stream from LLM provider
        import httpx
        
        async with httpx.AsyncClient() as client:
            async with client.stream(
                "POST",
                f"{settings.llm_provider_url.rstrip('/')}{settings.llm_api_path}",
                json=forward_payload,
                timeout=settings.llm_timeout_seconds,
                headers={
                    "X-Request-Id": request_id,
                    **(
                        {"Authorization": f"Bearer {settings.llm_api_key}"}
                        if settings.llm_api_key
                        else {}
                    ),
                },
            ) as upstream:
                # Process SSE chunks
                async for line in upstream.aiter_lines():
                    line = line.strip()
                    if not line:
                        continue
                    
                    # Parse SSE line
                    if line.startswith("data: "):
                        data_str = line[6:]
                        if data_str == "[DONE]":
                            # Stream completed
                            yield format_sse({"type": "stream_complete"})
                            break
                        
                        # Parse JSON chunk
                        try:
                            chunk_data = eval(data_str)  # Simple JSON parse for demo; use json.loads for production
                        except:
                            # Skip malformed chunks
                            continue
                        
                        # Extract content from chunk
                        content = ""
                        reasoning_content = ""
                        
                        if "choices" in chunk_data and len(chunk_data["choices"]) > 0:
                            choice = chunk_data["choices"][0]
                            if "delta" in choice:
                                delta = choice["delta"]
                                content = delta.get("content", "")
                                reasoning_content = delta.get("reasoning_content", "")
                        
                        # Inspect the chunk
                        if content:
                            result = await inspector.inspect_chunk(content)
                            
                            if result.blocked:
                                # Block detected - terminate stream
                                yield format_sse(scanner_blocked_event(
                                    reason=result.decision.reason,
                                    rule_ids=result.decision.rule_ids
                                ))
                                yield format_sse({"type": "stream_complete"})
                                break
                            
                            # Forward the chunk to client
                            yield format_sse(chunk_data)
                        
                        # Handle reasoning content (if any)
                        if reasoning_content:
                            # Inspect reasoning content
                            result = await inspector.inspect_chunk(reasoning_content)
                            
                            if result.blocked:
                                # Block detected - terminate stream
                                yield format_sse(scanner_blocked_event(
                                    reason=result.decision.reason,
                                    rule_ids=result.decision.rule_ids
                                ))
                                yield format_sse({"type": "stream_complete"})
                                break
                            
                            # Create reasoning chunk
                            reasoning_chunk = {
                                "id": chunk_data["id"],
                                "object": "chat.completion.chunk",
                                "created": chunk_data["created"],
                                "model": chunk_data["model"],
                                "choices": [
                                    {
                                        "index": 0,
                                        "delta": {"role": "assistant", "reasoning_content": reasoning_content},
                                        "finish_reason": None,
                                    }
                                ],
                            }
                            yield format_sse(reasoning_chunk)
                
                # Stream completed - run final inspection
                final_result = await inspector.finalize()
                
                # Log final inspection if needed
                if final_result.matches:
                    logger.info(
                        "stream_final_inspection",
                        request_id=request_id,
                        matches_count=len(final_result.matches),
                    )
                
                # Send final DONE
                yield format_sse({"type": "stream_complete"})
                
    except httpx.HTTPError as exc:
        # Upstream error during streaming
        logger.error(
            "stream_upstream_error",
            request_id=request_id,
            error=str(exc),
        )
        yield format_sse({
            "type": "scanner_error",
            "error": {"message": "Upstream service error", "type": "upstream_error"}
        })
        yield format_sse({"type": "stream_complete"})
    except Exception as exc:
        # Other errors
        logger.error(
            "stream_internal_error",
            request_id=request_id,
            error=str(exc),
        )
        yield format_sse({
            "type": "scanner_error",
            "error": {"message": "Internal scanner error", "type": "scanner_error"}
        })
        yield format_sse({"type": "stream_complete"})


async def generate_error_stream(error_data: dict, request_id: str) -> AsyncGenerator[str, None]:
    """Generate error stream."""
    yield format_sse(error_data)
    yield format_sse({"type": "stream_complete"})


@router.post(
    "/chat/completions",
    summary="OpenAI-compatible Chat Completions streaming endpoint",
    description="Streaming endpoint with per-chunk inspection. Blocks mid-stream on violations.",
)
async def chat_completions(
    payload: ChatCompletionRequest,
    request: Request,
    response: Response,
    auth_info: AuthInfo = Depends(verify_api_key),
):
    """Handle chat completions with streaming."""
    return await chat_completions_stream(
        payload,
        request,
        response,
        auth_info,
    )