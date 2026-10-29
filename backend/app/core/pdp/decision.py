"""Async PDP decision: OPA + local rules engine (EP-12, ADR-0004).

Merger semantics:
  * OPA ``deny`` non-empty                -> BLOCK (policy reasons win)
  * OPA allow + local block/high-severity -> BLOCK (defense in depth)
  * OPA unreachable (fail-open)           -> local :decide()` only (degrade)
  * OPA unreachable (fail-closed)         -> BLOCK with ``opa_unavailable``

Every decision carries the active ``policy_version`` so the audit log can
record which bundle produced it (ALPHA Prompt 28).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

import structlog

from app.config import settings
from app.core.metrics import scanner_pdp_decisions_total, scanner_policy_version_info
from app.core.pdp import decide
from app.core.pdp.opa_client import get_opa_client
from app.core.rules.base import RuleMatch, Verdict
from app.core.observability import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer(__name__)

DecisionSource = Literal["opa", "local", "local_fallback"]


async def get_active_policy_version() -> str:
    """Active bundle version (for cache keys / audit on non-decision paths)."""
    client = get_opa_client()
    if client is None:
        return settings.policy_version
    return await client.get_active_policy_version()


@dataclass
class PDPResult:
    verdict: Verdict
    policy_version: str
    source: DecisionSource
    opa_deny_reasons: list[str] = field(default_factory=list)


def build_opa_input(
    matches: list[RuleMatch],
    *,
    policy_version: str,
    tenant_id: str | None = None,
    request_id: str | None = None,
    ml: dict[str, Any] | None = None,
) -> dict:
    """Build the Rego input document (schema: tests/policies/test_rego.py)."""
    detectors: dict[str, Any] = {
        "rules": {"matches": [m.to_dict() for m in matches]},
        "pii": {
            "found": [
                {"type": m.rule_id, "rule_id": m.rule_id, "severity": m.severity}
                for m in matches
                if m.rule_id.startswith("pii_")
            ]
        },
    }
    if ml:
        detectors["ml_classifier"] = ml

    return {
        "policy_version": policy_version,
        "tenant_id": tenant_id or settings.default_tenant_id,
        "request_id": request_id,
        "detectors": detectors,
    }


def _record(version: str, previous: str | None, result: PDPResult) -> None:
    scanner_pdp_decisions_total.labels(
        source=result.source, action=result.verdict.action
    ).inc()
    if previous and previous != version:
        scanner_policy_version_info.labels(version=previous).set(0)
    scanner_policy_version_info.labels(version=version).set(1)


_active_version: str | None = None


async def decide_with_opa(
    matches: list[RuleMatch],
    *,
    tenant_id: str | None = None,
    request_id: str | None = None,
    ml: dict[str, Any] | None = None,
) -> PDPResult:
    """Decide with OPA when enabled, else with the local rules engine.

    Never raises: OPA failures degrade to the local ``decide()`` (fail-open)
    or to BLOCK (fail-closed), per ``SCANNER_PDP_OPA_FAIL_OPEN``.
    """
    with tracer.start_as_current_span("pdp_decision") as span:
        span.set_attribute("tenant_id", tenant_id)
        span.set_attribute("request_id", request_id)
        global _active_version

    client = get_opa_client()
    if client is None:
        result = PDPResult(
            verdict=decide(matches),
            policy_version=settings.policy_version,
            source="local",
        )
        span.set_attribute("verdict", result.verdict.action)
        span.set_attribute("source", result.source)
        span.set_attribute("policy_version", result.policy_version)
        _record(result.policy_version, _active_version, result)
        _active_version = result.policy_version
        return result

    policy_version = await client.get_active_policy_version()
    input_doc = build_opa_input(
        matches,
        policy_version=policy_version,
        tenant_id=tenant_id,
        request_id=request_id,
        ml=ml,
    )
    decision = await client.evaluate(input_doc)

    if not decision.ok:
        if settings.pdp_opa_fail_open:
            verdict = decide(matches)
            source: DecisionSource = "local_fallback"
            logger.warning(
                "pdp_opa_fallback",
                error=decision.error,
                breaker=client.breaker_state,
                request_id=request_id,
            )
        else:
            verdict = Verdict(
                action="block",
                reason="PDP unavailable (fail-closed)",
                rules_matched=matches,
            )
            source = "local_fallback"
            logger.warning(
                "pdp_opa_fail_closed",
                error=decision.error,
                breaker=client.breaker_state,
                request_id=request_id,
            )
    elif decision.deny_reasons:
        verdict = Verdict(
            action="block",
            reason="Blocked by policy: " + "; ".join(decision.deny_reasons),
            rules_matched=matches,
        )
        source = "opa"
    else:
        verdict = decide(matches)
        source = "opa"

    result = PDPResult(
        verdict=verdict,
        policy_version=policy_version,
        source=source,
        opa_deny_reasons=decision.deny_reasons,
    )
    span.set_attribute("verdict", result.verdict.action)
    span.set_attribute("source", result.source)
    span.set_attribute("policy_version", result.policy_version)
    _record(policy_version, _active_version, result)
    _active_version = policy_version
    return result
