from __future__ import annotations

import time
import uuid

import httpx
import structlog

from app.config import settings
from app.core.audit import write_audit_event
from app.core.cache import decision_cache
from app.core.pdp import decide, scan_text
from app.core.rules.base import RuleMatch
from app.models.openai import (
    ChatCompletionChoice,
    ChatCompletionMessage,
    ChatCompletionRequest,
    ChatCompletionResponse,
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
        "model": request.model,
        "messages": [{"role": m.role, "content": m.content} for m in request.messages],
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


async def proxy_chat(request: ChatCompletionRequest) -> ChatCompletionResponse:
    request_id = str(uuid.uuid4())
    start_time = time.monotonic()

    prompt_text = _extract_prompt_text(request.messages)
    prompt_matches, prompt_hash = scan_text(prompt_text)

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
        verdict = decide(prompt_matches)
        verdict_action = verdict.action
        verdict_reason = verdict.reason
        verdict_matches = [m.to_dict() for m in verdict.rules_matched]
        await decision_cache.set(prompt_text, verdict_action, verdict_reason, verdict_matches)

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
        write_audit_event(
            request_id=request_id,
            tenant_id=None,
            prompt_hash=prompt_hash,
            verdict="block",
            reason=verdict_reason,
            rules_matched=verdict_matches,
            latency_ms=latency_ms,
        )
        raise LLMProxyError(
            status_code=403,
            detail=verdict_reason,
        )

    llm_response = await _forward_to_llm(request, request_id)

    gen_text = ""
    if llm_response.choices:
        first_choice = llm_response.choices[0]
        if first_choice.message and first_choice.message.content:
            gen_text = first_choice.message.content

    gen_matches, gen_hash = scan_text(gen_text)
    gen_verdict = decide(gen_matches)
    gen_verdict_matches = [m.to_dict() for m in gen_verdict.rules_matched]

    if gen_verdict.is_block:
        masked_text = _mask_blocked_content(gen_text, gen_matches)
        if llm_response.choices:
            llm_response.choices[0].message.content = masked_text

        logger.warning(
            "proxy_generation_blocked",
            request_id=request_id,
            gen_hash=gen_hash,
            rules_matched_count=len(gen_verdict_matches),
        )

        write_audit_event(
            request_id=request_id,
            tenant_id=None,
            prompt_hash=prompt_hash,
            verdict="block",
            reason=f"Generation blocked: {gen_verdict.reason}",
            rules_matched=gen_verdict_matches,
            latency_ms=latency_ms,
            metadata={"direction": "generation"},
        )
    else:
        write_audit_event(
            request_id=request_id,
            tenant_id=None,
            prompt_hash=prompt_hash,
            verdict="allow",
            reason=verdict_reason,
            rules_matched=verdict_matches,
            latency_ms=latency_ms,
        )

    return llm_response


async def _forward_to_llm(
    request: ChatCompletionRequest, request_id: str
) -> ChatCompletionResponse:
    payload = _build_forward_payload(request)
    url = f"{settings.llm_provider_url.rstrip('/')}/v1/chat/completions"

    logger.info(
        "proxy_forward_to_llm",
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

    try:
        data = response.json()
        return ChatCompletionResponse(**data)
    except Exception as exc:
        logger.error("proxy_llm_parse_error", request_id=request_id, error=str(exc))
        raise LLMProxyError(
            status_code=502,
            detail="Failed to parse LLM provider response",
        )
