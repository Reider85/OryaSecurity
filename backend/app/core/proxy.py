from __future__ import annotations

import json
import time
import uuid

import httpx
import structlog
from fastapi.responses import StreamingResponse

from app.config import settings
from app.core.audit import write_audit_event, write_event
from app.core.cache import decision_cache
from app.core.pdp import decide_with_opa, get_active_policy_version, scan_text
from app.core.rules.base import RuleMatch
from app.core.redactor import redact
from app.core.redaction.redactor import VAULT_UNAVAILABLE_REASON, redact_messages_for_llm
from app.models.openai import (
    ChatCompletionChoice,
    ChatCompletionMessage,
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatCompletionStreamResponse,
    ChatCompletionStreamChoice,
    ChatCompletionChoiceDelta,
    ChatCompletionUsage,
)

logger = structlog.get_logger()


class LLMProxyError(Exception):
    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


def _extract_prompt_text(messages: list[ChatCompletionMessage]) -> str:
    parts = []
    for msg in messages:
        if msg.content:
            parts.append(msg.content)
    return "\n".join(parts)


def _mask_blocked_content(text: str, matches: list[RuleMatch]) -> str:
    if not matches:
        return text
    sorted_matches = sorted(matches, key=lambda m: m.position[0], reverse=True)
    masked = text
    for m in sorted_matches:
        start, end = m.position
        if start <= len(masked) and end <= len(masked):
            masked = masked[:start] + "<REDACTED>" + masked[end:]
    return masked


def _build_forward_payload(request: ChatCompletionRequest) -> dict:
    payload: dict = {
        "model": request.model,  # Allow client to specify model
        "messages": [{"role": m.role, "content": m.content} for m in request.messages],
        "stream": request.stream,
    }
    if request.temperature is not None:
        payload["temperature"] = request.temperature
    if request.max_tokens is not None:
        payload["max_tokens"] = request.max_tokens
    if request.top_p is not None:
        payload["top_p"] = request.top_p
    if request.frequency_penalty is not None:
        payload["frequency_penalty"] = request.frequency_penalty
    if request.presence_penalty is not None:
        payload["presence_penalty"] = request.presence_penalty
    if request.stop is not None:
        payload["stop"] = request.stop
    return payload


async def proxy_chat(request: ChatCompletionRequest, tenant_id: str | None = None) -> ChatCompletionResponse:
    request_id = str(uuid.uuid4())
    start_time = time.monotonic()

    prompt_text = _extract_prompt_text(request.messages)
    prompt_matches, prompt_hash = scan_text(prompt_text)

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
            prompt_matches, tenant_id=tenant_id, request_id=request_id
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

    latency_ms = (time.monotonic() - start_time) * 1000

    logger.info(
        "proxy_prompt_inspection",
        request_id=request_id,
        prompt_hash=prompt_hash,
        verdict=verdict_action,
        rules_matched_count=len(verdict_matches),
        latency_ms=round(latency_ms, 2),
        cache_hit=cache_hit,
    )

    if verdict_action == "block":
        await write_event(
            request_id=request_id,
            tenant_id=tenant_id,
            prompt_hash=prompt_hash,
            verdict="block",
            reason=verdict_reason,
            rules_matched=verdict_matches,
            policy_version=policy_version,
            prompt_text_redacted=redact(prompt_text, prompt_matches),
            latency_ms=latency_ms,
        )
        raise LLMProxyError(
            status_code=403,
            detail=verdict_reason,
        )

    # Tokenize PII/secrets before the prompt reaches the LLM (EP-13/Prompt 32).
    # Fail-closed per ADR-0008: Vault configured but unable to mint a token
    # means the request must not be forwarded.
    redaction = await redact_messages_for_llm(request.messages, tenant_id)
    if not redaction.ok:
        await write_event(
            request_id=request_id,
            tenant_id=tenant_id,
            prompt_hash=prompt_hash,
            verdict="block",
            reason=VAULT_UNAVAILABLE_REASON,
            rules_matched=verdict_matches,
            policy_version=policy_version,
            prompt_text_redacted=redact(prompt_text, prompt_matches),
            latency_ms=latency_ms,
        )
        raise LLMProxyError(
            status_code=403,
            detail=VAULT_UNAVAILABLE_REASON,
        )

    await write_event(
        request_id=request_id,
        tenant_id=tenant_id,
        prompt_hash=prompt_hash,
        verdict="allow",
        reason=verdict_reason,
        rules_matched=verdict_matches,
        policy_version=policy_version,
        prompt_text_redacted=redact(prompt_text, prompt_matches),
        latency_ms=latency_ms,
    )

    llm_response = await _forward_to_llm(request, request_id)

    gen_text = ""
    gen_reasoning = ""
    if llm_response.choices:
        first_choice = llm_response.choices[0]
        if first_choice.message and first_choice.message.content:
            gen_text = first_choice.message.content
        if first_choice.message and first_choice.message.reasoning_content:
            gen_reasoning = first_choice.message.reasoning_content

    # Scan both content and reasoning for leaks
    gen_matches, gen_hash = scan_text(gen_text)
    reasoning_matches, reasoning_hash = scan_text(gen_reasoning) if gen_reasoning else ([], None)
    all_matches = gen_matches + reasoning_matches
    gen_pdp_result = await decide_with_opa(
        all_matches, tenant_id=tenant_id, request_id=request_id
    )
    gen_verdict = gen_pdp_result.verdict
    gen_verdict_matches = [m.to_dict() for m in gen_verdict.rules_matched]

    if gen_verdict.is_block:
        # Mask both content and reasoning if blocked
        masked_text = _mask_blocked_content(gen_text, gen_matches)
        masked_reasoning = _mask_blocked_content(gen_reasoning, reasoning_matches) if gen_reasoning else None
        
        if llm_response.choices:
            llm_response.choices[0].message.content = masked_text
            if masked_reasoning is not None:
                llm_response.choices[0].message.reasoning_content = masked_reasoning

        logger.warning(
            "proxy_generation_blocked",
            request_id=request_id,
            gen_hash=gen_hash,
            reasoning_hash=reasoning_hash,
            rules_matched_count=len(gen_verdict_matches),
        )

        await write_event(
            request_id=request_id,
            tenant_id=tenant_id,
            prompt_hash=prompt_hash,
            verdict="block",
            reason=f"Generation blocked: {gen_verdict.reason}",
            rules_matched=gen_verdict_matches,
            policy_version=gen_pdp_result.policy_version,
            prompt_text_redacted=redact(prompt_text, prompt_matches),
            latency_ms=latency_ms,
            metadata={"direction": "generation"},
        )
    else:
        await write_event(
            request_id=request_id,
            tenant_id=tenant_id,
            prompt_hash=prompt_hash,
            verdict="allow",
            reason=verdict_reason,
            rules_matched=verdict_matches,
            policy_version=policy_version,
            prompt_text_redacted=redact(prompt_text, prompt_matches),
            latency_ms=latency_ms,
        )

    return llm_response


async def proxy_chat_stream(request: ChatCompletionRequest, tenant_id: str | None = None) -> StreamingResponse:
    request_id = str(uuid.uuid4())
    start_time = time.monotonic()

    prompt_text = _extract_prompt_text(request.messages)
    prompt_matches, prompt_hash = scan_text(prompt_text)

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
            prompt_matches, tenant_id=tenant_id, request_id=request_id
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

    latency_ms = (time.monotonic() - start_time) * 1000

    logger.info(
        "proxy_prompt_inspection",
        request_id=request_id,
        prompt_hash=prompt_hash,
        verdict=verdict_action,
        rules_matched_count=len(verdict_matches),
        latency_ms=round(latency_ms, 2),
        cache_hit=cache_hit,
    )

    if verdict_action == "block":
        await write_event(
            request_id=request_id,
            tenant_id=tenant_id,
            prompt_hash=prompt_hash,
            verdict="block",
            reason=verdict_reason,
            rules_matched=verdict_matches,
            policy_version=policy_version,
            prompt_text_redacted=redact(prompt_text, prompt_matches),
            latency_ms=latency_ms,
        )
        raise LLMProxyError(
            status_code=403,
            detail=verdict_reason,
        )

    # Tokenize PII/secrets before the prompt reaches the LLM (EP-13/Prompt 32).
    # Fail-closed per ADR-0008 (same as the non-streaming path).
    redaction = await redact_messages_for_llm(request.messages, tenant_id)
    if not redaction.ok:
        await write_event(
            request_id=request_id,
            tenant_id=tenant_id,
            prompt_hash=prompt_hash,
            verdict="block",
            reason=VAULT_UNAVAILABLE_REASON,
            rules_matched=verdict_matches,
            policy_version=policy_version,
            prompt_text_redacted=redact(prompt_text, prompt_matches),
            latency_ms=latency_ms,
        )
        raise LLMProxyError(
            status_code=403,
            detail=VAULT_UNAVAILABLE_REASON,
        )

    await write_event(
        request_id=request_id,
        tenant_id=tenant_id,
        prompt_hash=prompt_hash,
        verdict="allow",
        reason=verdict_reason,
        rules_matched=verdict_matches,
        policy_version=policy_version,
        prompt_text_redacted=redact(prompt_text, prompt_matches),
        latency_ms=latency_ms,
    )

    return await _forward_to_llm_stream(request, request_id)


async def _forward_to_llm(
    request: ChatCompletionRequest, request_id: str
) -> ChatCompletionResponse:
    payload = _build_forward_payload(request)
    path = settings.llm_api_path if settings.llm_api_path.startswith("/") else f"/{settings.llm_api_path}"
    url = f"{settings.llm_provider_url.rstrip('/')}{path}"

    logger.info(
        "proxy_forward_to_llm",
        request_id=request_id,
        url=url,
        model=request.model,
        stream=payload.get("stream", False),
    )

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                url,
                json=payload,
                timeout=settings.llm_timeout_seconds,
                headers={"X-Request-Id": request_id}
                if not settings.llm_api_key
                else {"X-Request-Id": request_id, "Authorization": f"Bearer {settings.llm_api_key}"},
            )
    except httpx.TimeoutException:
        logger.error("proxy_llm_timeout", request_id=request_id, url=url)
        raise LLMProxyError(
            status_code=502,
            detail=f"LLM provider timeout after {settings.llm_timeout_seconds}s",
        )
    except httpx.HTTPError as exc:
        logger.error("proxy_llm_error", request_id=request_id, error=str(exc))
        raise LLMProxyError(
            status_code=502,
            detail=f"LLM provider error: {exc}",
        )

    if response.status_code != 200:
        logger.error(
            "proxy_llm_bad_status",
            request_id=request_id,
            status_code=response.status_code,
            body=response.text[:500],
        )
        raise LLMProxyError(
            status_code=502,
            detail=f"LLM provider returned {response.status_code}",
        )

    try:
        data = response.json()
        # Handle reasoning_content if present
        if "choices" in data:
            for choice in data["choices"]:
                if "message" in choice and isinstance(choice["message"], dict):
                    # Ensure reasoning_content is preserved
                    if "reasoning_content" not in choice["message"]:
                        choice["message"]["reasoning_content"] = None
        
        return ChatCompletionResponse(**data)
    except Exception as exc:
        logger.error("proxy_llm_parse_error", request_id=request_id, error=str(exc))
        raise LLMProxyError(
            status_code=502,
            detail="Failed to parse LLM provider response",
        )


async def _forward_to_llm_stream(
    request: ChatCompletionRequest, request_id: str
) -> StreamingResponse:
    payload = _build_forward_payload(request)
    path = settings.llm_api_path if settings.llm_api_path.startswith("/") else f"/{settings.llm_api_path}"
    url = f"{settings.llm_provider_url.rstrip('/')}{path}"

    logger.info(
        "proxy_forward_to_llm_stream",
        request_id=request_id,
        url=url,
        model=request.model,
    )

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                url,
                json=payload,
                timeout=settings.llm_timeout_seconds,
                headers={"X-Request-Id": request_id}
                if not settings.llm_api_key
                else {"X-Request-Id": request_id, "Authorization": f"Bearer {settings.llm_api_key}"},
            )
    except httpx.TimeoutException:
        logger.error("proxy_llm_timeout", request_id=request_id, url=url)
        raise LLMProxyError(
            status_code=502,
            detail=f"LLM provider timeout after {settings.llm_timeout_seconds}s",
        )
    except httpx.HTTPError as exc:
        logger.error("proxy_llm_error", request_id=request_id, error=str(exc))
        raise LLMProxyError(
            status_code=502,
            detail=f"LLM provider error: {exc}",
        )

    if response.status_code != 200:
        logger.error(
            "proxy_llm_bad_status",
            request_id=request_id,
            status_code=response.status_code,
            body=response.text[:500],
        )
        raise LLMProxyError(
            status_code=502,
            detail=f"LLM provider returned {response.status_code}",
        )

    # Process SSE stream
    async def generate_stream():
        current_id = f"chatcmpl-{uuid.uuid4().hex}"
        current_created = int(time.time())
        current_model = payload.get("model", "unknown")
        accumulated_content = ""
        accumulated_reasoning = ""
        
        async for line in response.aiter_lines():
            line = line.strip()
            if not line:
                continue
                
            if line.startswith("data: "):
                data_str = line[6:]
                if data_str == "[DONE]":
                    # Final chunk
                    yield f"data: {json.dumps({'id': current_id, 'object': 'chat.completion.chunk', 'created': current_created, 'model': current_model, 'choices': []})}\n\n"
                    break
                
                try:
                    data = json.loads(data_str)
                    choices = data.get("choices", [])
                    for choice in choices:
                        if choice.get("delta"):
                            delta = choice["delta"]
                            content = delta.get("content", "")
                            reasoning_content = delta.get("reasoning_content", "")
                            
                            accumulated_content += content
                            accumulated_reasoning += reasoning_content
                            
                            # Create stream response
                            stream_response = ChatCompletionStreamResponse(
                                id=current_id,
                                object="chat.completion.chunk",
                                created=current_created,
                                model=current_model,
                                choices=[
                                    ChatCompletionStreamChoice(
                                        index=choice.get("index", 0),
                                        delta=ChatCompletionChoiceDelta(
                                            role=delta.get("role"),
                                            content=content,
                                            reasoning_content=reasoning_content if reasoning_content else None,
                                        ),
                                        finish_reason=choice.get("finish_reason"),
                                    )
                                ]
                            )
                            
                            yield f"data: {stream_response.model_dump_json()}\n\n"
                            
                            # If this is the final chunk, send the stop signal
                            if choice.get("finish_reason"):
                                # Scan accumulated content for leaks
                                gen_matches, gen_hash = scan_text(accumulated_content)
                                reasoning_matches, reasoning_hash = scan_text(accumulated_reasoning) if accumulated_reasoning else ([], None)
                                all_matches = gen_matches + reasoning_matches
                                gen_pdp_result = await decide_with_opa(
                                    all_matches, tenant_id=tenant_id, request_id=request_id
                                )
                                gen_verdict = gen_pdp_result.verdict
                                
                                if gen_verdict.is_block:
                                    # Mask both content and reasoning if blocked
                                    masked_content = _mask_blocked_content(accumulated_content, gen_matches)
                                    masked_reasoning = _mask_blocked_content(accumulated_reasoning, reasoning_matches) if accumulated_reasoning else None
                                    
                                    # Send final chunk with masked content
                                    final_stream_response = ChatCompletionStreamResponse(
                                        id=current_id,
                                        object="chat.completion.chunk",
                                        created=current_created,
                                        model=current_model,
                                        choices=[
                                            ChatCompletionStreamChoice(
                                                index=choice.get("index", 0),
                                                delta=ChatCompletionChoiceDelta(
                                                    role=None,
                                                    content=masked_content,
                                                    reasoning_content=masked_reasoning,
                                                ),
                                                finish_reason=choice.get("finish_reason"),
                                            )
                                        ]
                                    )
                                else:
                                    # Send final chunk with accumulated content
                                    final_stream_response = ChatCompletionStreamResponse(
                                        id=current_id,
                                        object="chat.completion.chunk",
                                        created=current_created,
                                        model=current_model,
                                        choices=[
                                            ChatCompletionStreamChoice(
                                                index=choice.get("index", 0),
                                                delta=ChatCompletionChoiceDelta(
                                                    role=None,
                                                    content=accumulated_content,
                                                    reasoning_content=accumulated_reasoning if accumulated_reasoning else None,
                                                ),
                                                finish_reason=choice.get("finish_reason"),
                                            )
                                        ]
                                    )
                                # Write audit event for successful stream completion
                                await write_event(
                                    request_id=request_id,
                                    tenant_id=tenant_id,
                                    prompt_hash=prompt_hash,
                                    verdict="allow",
                                    reason="Prompt passed inspection",
                                    rules_matched=verdict_matches,
                                    policy_version=policy_version,
                                    prompt_text_redacted=redact(prompt_text, prompt_matches),
                                    latency_ms=(time.monotonic() - start_time) * 1000,
                                    metadata={"direction": "stream", "cache_hit": cache_hit},
                                )
                                yield f"data: {final_stream_response.model_dump_json()}\n\n"
                                break
                                
                except json.JSONDecodeError:
                    logger.warning("proxy_stream_invalid_json", request_id=request_id, line=line)
                    continue
    
    return StreamingResponse(
        generate_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Request-Id": request_id,
        },
    )