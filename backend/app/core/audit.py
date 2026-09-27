from __future__ import annotations

import structlog

logger = structlog.get_logger()


def write_audit_event(
    *,
    request_id: str,
    tenant_id: str | None,
    prompt_hash: str,
    verdict: str,
    reason: str,
    rules_matched: list[dict],
    latency_ms: float,
    metadata: dict | None = None,
) -> None:
    logger.info(
        "audit_event",
        request_id=request_id,
        tenant_id=tenant_id,
        prompt_hash=prompt_hash,
        verdict=verdict,
        reason=reason,
        rules_matched_count=len(rules_matched),
        latency_ms=round(latency_ms, 2),
        metadata=metadata or {},
    )
