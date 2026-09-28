from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Iterable

import structlog
from sqlalchemy import select

from app.config import settings
from app.db.models import AuditEvent
from app.db.session import get_session

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


def _coerce_request_id(request_id: str | uuid.UUID) -> uuid.UUID:
    """Convert a request id to UUID, generating a new one if it is not a UUID."""
    if isinstance(request_id, uuid.UUID):
        return request_id
    try:
        return uuid.UUID(str(request_id))
    except (ValueError, AttributeError, TypeError):
        logger.warning("audit_request_id_not_uuid", request_id=str(request_id))
        return uuid.uuid4()


def _normalize_rules(rules_matched: Iterable[dict] | None) -> list[dict]:
    """Keep only {rule_id, position} pairs as required by the audit schema."""
    normalized: list[dict] = []
    for rule in rules_matched or []:
        normalized.append(
            {
                "rule_id": rule.get("rule_id"),
                "position": list(rule.get("position") or []),
            }
        )
    return normalized


async def write_event(
    *,
    request_id: str | uuid.UUID,
    prompt_hash: str,
    verdict: str,
    reason: str | None = None,
    rules_matched: list[dict] | None = None,
    tenant_id: str | None = None,
    prompt_text_redacted: str | None = None,
    policy_version: str | None = None,
    latency_ms: float | None = None,
    metadata: dict | None = None,
) -> AuditEvent | None:
    """Persist a scan decision to the audit log.

    Fails open: any database problem is logged and None is returned so that
    audit storage never breaks the request path.
    """
    if not settings.audit_enabled:
        logger.info("audit_disabled", request_id=str(request_id))
        return None

    event = AuditEvent(
        request_id=_coerce_request_id(request_id),
        tenant_id=tenant_id,
        prompt_hash=prompt_hash,
        prompt_text_redacted=prompt_text_redacted,
        verdict=verdict,
        reason=reason,
        rules_matched=_normalize_rules(rules_matched),
        policy_version=policy_version or settings.policy_version,
        latency_ms=latency_ms,
    )

    try:
        async with get_session() as session:
            session.add(event)
            await session.flush()
    except Exception as exc:
        logger.warning(
            "audit_write_failed",
            request_id=str(request_id),
            verdict=verdict,
            error=str(exc),
            fail_open=True,
        )
        return None

    write_audit_event(
        request_id=str(event.request_id),
        tenant_id=event.tenant_id,
        prompt_hash=event.prompt_hash,
        verdict=event.verdict,
        reason=event.reason or "",
        rules_matched=event.rules_matched,
        latency_ms=event.latency_ms or 0.0,
        metadata=metadata,
    )
    return event


def _serialize(event: AuditEvent) -> dict[str, Any]:
    return {
        "id": event.id,
        "ts": event.ts,
        "request_id": str(event.request_id),
        "tenant_id": event.tenant_id,
        "prompt_hash": event.prompt_hash,
        "prompt_text_redacted": event.prompt_text_redacted,
        "verdict": event.verdict,
        "reason": event.reason,
        "rules_matched": event.rules_matched or [],
        "policy_version": event.policy_version,
        "latency_ms": event.latency_ms,
    }


async def query_events(
    *,
    tenant_id: str | None = None,
    verdict: str | None = None,
    prompt_hash: str | None = None,
    start_ts: datetime | None = None,
    end_ts: datetime | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Query audit events, newest first."""
    stmt = select(AuditEvent).order_by(AuditEvent.ts.desc())

    if tenant_id is not None:
        stmt = stmt.where(AuditEvent.tenant_id == tenant_id)
    if verdict is not None:
        stmt = stmt.where(AuditEvent.verdict == verdict)
    if prompt_hash is not None:
        stmt = stmt.where(AuditEvent.prompt_hash == prompt_hash)
    if start_ts is not None:
        stmt = stmt.where(AuditEvent.ts >= start_ts)
    if end_ts is not None:
        stmt = stmt.where(AuditEvent.ts <= end_ts)

    stmt = stmt.limit(limit).offset(offset)

    async with get_session() as session:
        result = await session.execute(stmt)
        return [_serialize(row) for row in result.scalars().all()]
