from __future__ import annotations

import asyncio
import hashlib
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional

import structlog

from app.config import settings
from app.core.cache_embedding import embedding_cache
from app.core.metrics import (
    scanner_post_factum_degraded_total,
    scanner_post_factum_duration_seconds,
    scanner_post_factum_total,
)
from app.core.pdp import decide, scan_text

logger = structlog.get_logger()

_SEVERITY_RANK = {"low": 0, "medium": 1, "high": 2, "critical": 3}


def _max_severity(severities: list[str]) -> str:
    if not severities:
        return settings.post_factum_flag_severity
    return max(severities, key=lambda s: _SEVERITY_RANK.get(s, 0))


@dataclass
class PostFactumFinding:
    """Single threat finding from post-factum deep analysis."""

    source: str  # rules | toxicity | vector_search | ml_classifier
    severity: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "severity": self.severity,
            "details": self.details,
        }


@dataclass
class PostFactumResult:
    """Aggregated outcome of post-factum analysis."""

    flagged: bool
    severity: Optional[str]
    findings: list[PostFactumFinding]
    latency_ms: float
    prompt_hash: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "flagged": self.flagged,
            "severity": self.severity,
            "findings": [f.to_dict() for f in self.findings],
            "latency_ms": self.latency_ms,
            "prompt_hash": self.prompt_hash,
        }


AuditWriter = Callable[..., Awaitable[Any]]
Notifier = Callable[[dict[str, Any]], Awaitable[None]]


async def _default_notifier(message: dict[str, Any]) -> None:
    from app.core.notifications.websocket import get_connection_manager

    await get_connection_manager().broadcast(message)


class PostFactumAnalyzer:
    """Async post-factum deep analysis of a completed LLM generation.

    Runs (in the background, after the stream's [DONE]):
      1. Rules re-scan of the full generation (regex PII/secrets)
      2. Toxicity check on the full generation
      3. Embedding of the full generation
      4. Vector search against the prompt-attacks collection (Qdrant)
      5. ML classifier (prompt_injection / jailbreak / suspicious)

    When any threat is found the analyzer writes an audit event with
    ``verdict="post_factum_flag"`` and broadcasts a WebSocket notification.
    Already-shown content is NEVER retracted — notify only.

    Unavailable backends (model files missing, Qdrant down) degrade
    gracefully: the corresponding step is skipped and a degraded metric is
    incremented. The analyzer never raises to its caller.
    """

    def __init__(
        self,
        embedding_client: Any = None,
        vector_searcher: Any = None,
        classifier: Any = None,
        toxicity_detector: Any = None,
        audit_writer: Optional[AuditWriter] = None,
        notifier: Optional[Notifier] = None,
    ):
        self._embedding_client = embedding_client
        self._vector_searcher = vector_searcher
        self._classifier = classifier
        self._toxicity_detector = toxicity_detector
        self._audit_writer = audit_writer
        self._notifier = notifier

    # -- lazy defaults (resolve shared singletons at call time) -------------

    def _get_embedding_client(self) -> Any:
        if self._embedding_client is not None:
            return self._embedding_client
        from app.core.detectors.embedding import get_shared_embedding_client

        return get_shared_embedding_client()

    def _get_vector_searcher(self) -> Any:
        if self._vector_searcher is not None:
            return self._vector_searcher
        from app.core.detectors.vector_search import get_shared_vector_searcher

        return get_shared_vector_searcher()

    def _get_classifier(self) -> Any:
        if self._classifier is not None:
            return self._classifier
        from app.core.detectors.ml_classifier import get_shared_classifier

        return get_shared_classifier()

    def _get_toxicity_detector(self) -> Any:
        if self._toxicity_detector is not None:
            return self._toxicity_detector
        from app.core.detectors.toxicity import get_shared_toxicity_detector

        return get_shared_toxicity_detector()

    async def _get_audit_writer(self) -> AuditWriter:
        if self._audit_writer is not None:
            return self._audit_writer
        from app.core.audit import write_event

        return write_event

    async def _get_notifier(self) -> Notifier:
        return self._notifier if self._notifier is not None else _default_notifier

    # -- detector steps ------------------------------------------------------

    def _check_rules(self, text: str) -> list[PostFactumFinding]:
        findings: list[PostFactumFinding] = []
        matches, _ = scan_text(text)
        if not matches:
            return findings
        verdict = decide(matches)
        if verdict.action == "block":
            severities = [m.severity for m in matches if m.action == "block"] or [
                m.severity for m in matches
            ]
            findings.append(
                PostFactumFinding(
                    source="rules",
                    severity=_max_severity(list(severities)),
                    details={
                        "reason": verdict.reason,
                        "rule_ids": [m.rule_id for m in matches],
                    },
                )
            )
        return findings

    async def _check_toxicity(self, text: str) -> list[PostFactumFinding]:
        detector = self._get_toxicity_detector()
        if detector is None:
            return []
        try:
            result = await detector.check(text)
        except Exception as exc:  # noqa: BLE001 — degrade
            logger.warning("post_factum_toxicity_failed", error=str(exc))
            return []
        if result.is_toxic:
            return [
                PostFactumFinding(
                    source="toxicity",
                    severity="medium",
                    details={
                        "toxicity_score": result.toxicity_score,
                        "confidence": result.confidence,
                    },
                )
            ]
        return []

    async def _check_vector_search(
        self, text: str
    ) -> tuple[list[PostFactumFinding], Optional[Any]]:
        # Try cache first
        embedding = await embedding_cache.get(text)
        if embedding is not None:
            # Cache hit - use cached embedding
            pass
        else:
            # Cache miss - get from embedding client
            embedding_client = self._get_embedding_client()
            if embedding_client is None or not getattr(embedding_client, "available", False):
                scanner_post_factum_degraded_total.labels(component="embedding").inc()
                return [], None

            embedding = await embedding_client.embed(text)
            if embedding is None:
                scanner_post_factum_degraded_total.labels(component="embedding").inc()
                return [], None
            
            # Store in cache
            await embedding_cache.set(text, embedding)

        searcher = self._get_vector_searcher()
        if searcher is None:
            scanner_post_factum_degraded_total.labels(component="vector_search").inc()
            return [], embedding

        hits = await searcher.search(embedding)
        findings: list[PostFactumFinding] = []
        for hit in hits:
            if hit.score < settings.vector_match_threshold:
                continue
            payload_severity = str(hit.payload.get("severity") or "") or None
            findings.append(
                PostFactumFinding(
                    source="vector_search",
                    severity=payload_severity
                    if payload_severity in _SEVERITY_RANK
                    else settings.post_factum_flag_severity,
                    details={
                        "vector_id": hit.id,
                        "score": hit.score,
                        "attack_type": hit.payload.get("type"),
                    },
                )
            )
            break  # one vector hit is enough to flag the generation
        return findings, embedding

    async def _check_classifier(self, text: str) -> list[PostFactumFinding]:
        classifier = self._get_classifier()
        if classifier is None or not getattr(classifier, "available", False):
            scanner_post_factum_degraded_total.labels(component="ml_classifier").inc()
            return []
        try:
            result = await classifier.classify(text)
        except Exception as exc:  # noqa: BLE001 — degrade
            logger.warning("post_factum_classifier_failed", error=str(exc))
            return []
        if result is None:
            scanner_post_factum_degraded_total.labels(component="ml_classifier").inc()
            return []
        if not classifier.should_flag(result):
            return []
        severity = "high" if result.top_class in ("prompt_injection", "jailbreak") else "medium"
        return [
            PostFactumFinding(
                source="ml_classifier",
                severity=severity,
                details={
                    "top_class": result.top_class,
                    "confidence": result.confidence,
                    "all_classes": result.all_classes,
                },
            )
        ]

    # -- main entry point ----------------------------------------------------

    async def analyze(
        self,
        text: str,
        *,
        request_id: str,
        tenant_id: Optional[str] = None,
    ) -> PostFactumResult:
        """Run deep analysis on the full generation. Never raises."""
        start = time.perf_counter()
        prompt_hash = hashlib.sha256(text.encode()).hexdigest()

        try:
            findings: list[PostFactumFinding] = []
            findings.extend(self._check_rules(text))
            findings.extend(await self._check_toxicity(text))
            vector_findings, _ = await self._check_vector_search(text)
            findings.extend(vector_findings)
            findings.extend(await self._check_classifier(text))

            latency_ms = (time.perf_counter() - start) * 1000
            result = PostFactumResult(
                flagged=bool(findings),
                severity=_max_severity([f.severity for f in findings]) if findings else None,
                findings=findings,
                latency_ms=latency_ms,
                prompt_hash=prompt_hash,
            )

            if result.flagged:
                scanner_post_factum_total.labels(outcome="flagged").inc()
                await self._on_flagged(result, request_id=request_id, tenant_id=tenant_id)
            else:
                scanner_post_factum_total.labels(outcome="clean").inc()

            scanner_post_factum_duration_seconds.observe(latency_ms / 1000)
            logger.info(
                "post_factum_complete",
                request_id=str(request_id),
                flagged=result.flagged,
                severity=result.severity,
                findings_count=len(result.findings),
                latency_ms=round(latency_ms, 2),
            )
            return result

        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — post-factum must never crash
            latency_ms = (time.perf_counter() - start) * 1000
            scanner_post_factum_total.labels(outcome="error").inc()
            scanner_post_factum_duration_seconds.observe(latency_ms / 1000)
            logger.warning(
                "post_factum_error",
                request_id=str(request_id),
                error=str(exc),
            )
            return PostFactumResult(
                flagged=False,
                severity=None,
                findings=[],
                latency_ms=latency_ms,
                prompt_hash=prompt_hash,
            )

    async def _on_flagged(
        self,
        result: PostFactumResult,
        *,
        request_id: str,
        tenant_id: Optional[str],
    ) -> None:
        """Write the audit event and notify SecOps over WebSocket."""
        audit_writer = await self._get_audit_writer()
        rules_matched = [
            {"rule_id": rid, "position": []}
            for finding in result.findings
            if finding.source == "rules"
            for rid in finding.details.get("rule_ids", [])
        ]
        reason_parts = [
            f"{f.source}:{f.details.get('top_class') or f.details.get('attack_type') or f.details.get('reason') or 'flagged'}"
            for f in result.findings
        ]

        try:
            await audit_writer(
                request_id=request_id,
                prompt_hash=result.prompt_hash,
                verdict="post_factum_flag",
                severity=result.severity,
                reason=f"Post-factum flag: {'; '.join(reason_parts)}",
                rules_matched=rules_matched or None,
                tenant_id=tenant_id,
                latency_ms=result.latency_ms,
                metadata={
                    "event": "post_factum_flag",
                    "direction": "response",
                    "findings": [f.to_dict() for f in result.findings],
                },
            )
        except Exception as exc:  # noqa: BLE001 — audit is fail-open
            logger.warning("post_factum_audit_failed", error=str(exc))

        notifier = await self._get_notifier()
        try:
            await notifier(
                {
                    "type": "post_factum_flag",
                    "request_id": str(request_id),
                    "tenant_id": tenant_id,
                    "severity": result.severity,
                    "prompt_hash": result.prompt_hash,
                    "findings": [f.to_dict() for f in result.findings],
                    "latency_ms": round(result.latency_ms, 2),
                    "ts": datetime.now(timezone.utc).isoformat(),
                }
            )
        except Exception as exc:  # noqa: BLE001 — notification is best-effort
            logger.warning("post_factum_notify_failed", error=str(exc))


# Global singleton
_analyzer: Optional[PostFactumAnalyzer] = None


def get_post_factum_analyzer() -> PostFactumAnalyzer:
    global _analyzer
    if _analyzer is None:
        _analyzer = PostFactumAnalyzer()
    return _analyzer


def set_post_factum_analyzer(analyzer: Optional[PostFactumAnalyzer]) -> None:
    global _analyzer
    _analyzer = analyzer
