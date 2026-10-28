from __future__ import annotations

import json
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
from app.core.redaction.detokenizer import StreamingDetokenizer
from app.core.redaction.redactor import VAULT_UNAVAILABLE_REASON, redact_messages_for_llm
from app.core.rate_limit import RateLimiter
from app.core.detectors import get_shared_toxicity_detector
from app.core.detectors.toxicity import ToxicityDetector
from app.core.streaming.inspector import StreamingInspector
from app.core.streaming.post_factum import get_post_factum_analyzer
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
    from app.core.pdp import decide_with_opa, get_active_policy_version

    policy_version = await get_active_policy_version()
    cached, cache_hit = await decision_cache.get(prompt_text, policy_version)
    
    if cached:
        verdict_action = cached["verdict"]
        verdict_reason = cached["reason"]
        verdict_matches = cached["rules_matched"]
        policy_version = cached.get("policy_version") or policy_version
        logger.info(
            "proxy_cache_hit",
            request_id=request_id,
            prompt_hash=prompt_hash,
            verdict=verdict_action,
        )
    else:
        pdp_result = await decide_with_opa(
            prompt_matches,
            tenant_id=auth_info.tenant_id or settings.default_tenant_id,
            request_id=request_id,
        )
        verdict = pdp_result.verdict
        policy_version = pdp_result.policy_version
        verdict_action = verdict.action
        verdict_reason = verdict.reason
        verdict_matches = [m.to_dict() for m in verdict.rules_matched]
        await decision_cache.set(
            prompt_text,
            verdict_action,
            verdict_reason,
            verdict_matches,
            policy_version=policy_version,
        )
    
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
            policy_version=policy_version,
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

    # Tokenize PII/secrets before the prompt reaches the LLM (EP-13/Prompt 32).
    # Fail-closed per ADR-0008: unable to mint a token => do not start the stream.
    redaction = await redact_messages_for_llm(
        payload.messages,
        auth_info.tenant_id or settings.default_tenant_id,
    )
    if not redaction.ok:
        await write_event(
            request_id=request_id,
            prompt_hash=prompt_hash,
            verdict="block",
            reason=VAULT_UNAVAILABLE_REASON,
            rules_matched=verdict_matches,
            policy_version=policy_version,
            tenant_id=auth_info.tenant_id or settings.default_tenant_id,
            latency_ms=latency_ms,
            metadata={"direction": "prompt"},
        )

        set_scanner_context(
            request,
            verdict="block",
            reason=VAULT_UNAVAILABLE_REASON,
            latency_ms=latency_ms,
            request_id=request_id,
        )

        scanner_request_duration_seconds.observe(latency_ms / 1000)
        scanner_requests_total.labels(
            verdict="block",
            tenant_id=auth_info.tenant_id or settings.default_tenant_id or "unknown",
        ).inc()

        return StreamingResponse(
            content=generate_error_stream(
                {"error": {"message": VAULT_UNAVAILABLE_REASON, "type": "scanner_error"}},
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
        policy_version=policy_version,
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
    
    # Create streaming inspector (use shared detector instance)
    shared_detector = get_shared_toxicity_detector()
    if shared_detector is None:
        # Fallback: create detector per-request (should not happen in normal operation)
        logger.warning("toxicity_detector_missing_per_request")
        shared_detector = ToxicityDetector(threshold=settings.toxicity_threshold)

    # Async post-factum deep analysis hook (Prompt 6): runs in the background
    # after the stream completes; never blocks or retracts shown content.
    post_factum_hook = None
    if settings.post_factum_enabled:
        analyzer = get_post_factum_analyzer()
        stream_request_id = request_id
        stream_tenant_id = auth_info.tenant_id or settings.default_tenant_id

        async def post_factum_hook(text: str) -> None:
            await analyzer.analyze(
                text,
                request_id=stream_request_id,
                tenant_id=stream_tenant_id,
            )

    inspector = StreamingInspector(
        chunk_size=settings.streaming_chunk_size,
        toxicity_detector=shared_detector,
        post_factum_hook=post_factum_hook,
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
    # Reverse tokenization (EP-13/Prompt 33): hold a token split across
    # upstream chunks until it closes, decode complete ones (ADR-0007).
    # The inspector above sees the raw tokenized text — an echoed token is
    # not PII and must not block itself; restoration happens just before
    # the chunk reaches the client.
    content_detok = StreamingDetokenizer(tenant_id=tenant_id)
    reasoning_detok = StreamingDetokenizer(tenant_id=tenant_id)
    blocked = False
    flushed = False
    # Chunk metadata for synthetic tail chunks (updated from upstream chunks).
    stream_id = f"chatcmpl-{request_id.replace('-', '')}"
    stream_created = int(time.time())
    stream_model = payload.model

    def _tail_chunk(delta: dict) -> dict:
        return {
            "id": stream_id,
            "object": "chat.completion.chunk",
            "created": stream_created,
            "model": stream_model,
            "choices": [
                {"index": 0, "delta": delta, "finish_reason": None}
            ],
        }

    async def _flush_tails() -> AsyncGenerator[str, None]:
        # Release anything still held — an unclosed token is emitted verbatim
        # (a placeholder is not PII), nothing is stranded at stream end.
        # After a block nothing more must reach the client.
        nonlocal flushed
        if flushed or blocked:
            return
        flushed = True
        tail_content = await content_detok.flush()
        tail_reasoning = await reasoning_detok.flush()
        if tail_content:
            yield format_sse(_tail_chunk({"role": None, "content": tail_content}))
        if tail_reasoning:
            yield format_sse(_tail_chunk(
                {"role": None, "reasoning_content": tail_reasoning}
            ))

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
                            async for tail_event in _flush_tails():
                                yield tail_event
                            # Stream completed
                            yield format_sse({"type": "stream_complete"})
                            break
                        
                        # Parse JSON chunk
                        try:
                            chunk_data = json.loads(data_str)
                        except (json.JSONDecodeError, ValueError):
                            # Skip malformed chunks
                            continue

                        # Keep metadata for synthetic tail chunks.
                        stream_id = chunk_data.get("id") or stream_id
                        stream_created = chunk_data.get("created") or stream_created
                        stream_model = chunk_data.get("model") or stream_model

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
                                blocked = True
                                yield format_sse(scanner_blocked_event(
                                    reason=result.decision.reason,
                                    rule_ids=result.decision.rule_ids
                                ))
                                yield format_sse({"type": "stream_complete"})
                                break
                            
                            # Restore echoed tokens just before the client
                            # sees the chunk (Prompt 33). A token split
                            # across chunks yields an empty delta here — the
                            # text comes out on a later feed or the
                            # end-of-stream flush; role/finish_reason of the
                            # chunk are preserved either way.
                            out_content = await content_detok.feed(content)
                            chunk_data["choices"][0]["delta"]["content"] = out_content
                            yield format_sse(chunk_data)
                        
                        # Handle reasoning content (if any)
                        if reasoning_content:
                            # Inspect reasoning content
                            result = await inspector.inspect_chunk(reasoning_content)
                            
                            if result.blocked:
                                # Block detected - terminate stream
                                blocked = True
                                yield format_sse(scanner_blocked_event(
                                    reason=result.decision.reason,
                                    rule_ids=result.decision.rule_ids
                                ))
                                yield format_sse({"type": "stream_complete"})
                                break
                            
                            out_reasoning = await reasoning_detok.feed(reasoning_content)
                            if out_reasoning:
                                # Create reasoning chunk
                                reasoning_chunk = {
                                    "id": chunk_data["id"],
                                    "object": "chat.completion.chunk",
                                    "created": chunk_data["created"],
                                    "model": chunk_data["model"],
                                    "choices": [
                                        {
                                            "index": 0,
                                            "delta": {"role": "assistant", "reasoning_content": out_reasoning},
                                            "finish_reason": None,
                                        }
                                    ],
                                }
                                yield format_sse(reasoning_chunk)
                
                # Upstream closed without [DONE] (or a block broke the loop):
                # release held tails unless the stream was blocked.
                async for tail_event in _flush_tails():
                    yield tail_event

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