"""Microsoft Presidio NER provider for non-standard PII (EP-13/Prompt 35).

Bridges the Presidio analyzer (:mod:`app.core.redaction.presidio_config`) into
the existing NER hook of :class:`app.core.detectors.pii.PIIDetector`, so the
combined detector keeps a single API — ``detect(text) -> list[RuleMatch]`` —
and the redactor/audit layers need no changes.

* :class:`PresidioNerProvider` is a plain ``Callable[[str], list[RuleMatch]]``
  (the ``NERProvider`` type); Presidio ``RecognizerResult`` objects are mapped
  onto rule ids/severities the rest of the scanner understands.
* :func:`make_detector` is the factory wiring regex rules + Presidio NER
  together, gated by ``settings.presidio_enabled`` (ALPHA default: off).

Everything here is fail-soft: a missing ``ner`` extra or a failing engine
yields no NER matches and detection stays regex-only — the hook in
``PIIDetector`` already swallows provider exceptions as a second line of
defence.
"""

from __future__ import annotations

import structlog
from typing import Any, Optional

from app.config import settings
from app.core.detectors.pii import PIIDetector
from app.core.redaction.presidio_config import (
    ENTITY_MEDICAL_CONDITION,
    ENTITY_RU_INN,
    ENTITY_RU_PASSPORT,
    PRESIDIO_AVAILABLE,
    get_or_build_presidio_engine,
    is_valid_inn,
)
from app.core.rules.base import RuleMatch, _hash_value
from app.core.rules.loader import RuleSet

try:  # OTel spans become real once Prompt 40 lands (opentelemetry deps)
    from opentelemetry import trace

    _tracer = trace.get_tracer(__name__)
except ImportError:  # pragma: no cover - no-op tracer until OTel is installed
    from contextlib import contextmanager

    class _NoopSpan:
        def set_attribute(self, *_args: Any, **_kwargs: Any) -> None:
            pass

    class _NoopTracer:
        @contextmanager
        def start_as_current_span(self, *_args: Any, **_kwargs: Any):
            yield _NoopSpan()

    _tracer = _NoopTracer()

logger = structlog.get_logger(__name__)

# entity -> (rule_id, rule_name, severity, action, ner_priority)
# Lower priority value wins when two NER hits overlap (see ``_merge_matches``):
# ИНН is more specific than a passport-shaped digit run, medical terms are the
# lowest-priority signal.
_ENTITY_RULES: dict[str, tuple[str, str, str, str, int]] = {
    ENTITY_RU_INN: ("pii_inn_ru", "Russian INN", "high", "block", 1),
    ENTITY_RU_PASSPORT: ("pii_passport_ru", "Russian Passport Number", "high", "block", 2),
    ENTITY_MEDICAL_CONDITION: (
        "pii_medical_condition",
        "Medical Condition",
        "medium",
        "log_only",
        3,
    ),
}


class PresidioNerProvider:
    """Adapt a Presidio ``AnalyzerEngine`` to the ``NERProvider`` hook.

    Args:
        engine: Presidio analyzer built by
            :func:`app.core.redaction.presidio_config.build_presidio_engine`;
            ``None`` disables NER (provider becomes a no-op).
        language: analysis language; defaults to ``settings.presidio_language``.
        min_score: minimum Presidio confidence to accept a hit; defaults to
            ``settings.presidio_min_score``.
    """

    def __init__(
        self,
        engine: Optional[Any],
        language: Optional[str] = None,
        min_score: Optional[float] = None,
    ):
        self.engine = engine
        self.language = language or settings.presidio_language
        self.min_score = settings.presidio_min_score if min_score is None else min_score

    def __call__(self, text: str) -> list[RuleMatch]:
        if not text or self.engine is None:
            return []

        with _tracer.start_as_current_span("pii_ner.analyze") as span:
            span.set_attribute("scanner.text_len", len(text))
            try:
                results = self.engine.analyze(
                    text=text,
                    language=self.language,
                    score_threshold=self.min_score,
                )
            except Exception:  # noqa: BLE001 - NER must never break detection
                logger.warning("presidio_analyze_failed", exc_info=True)
                span.set_attribute("scanner.ner_error", True)
                return []

            matches: list[RuleMatch] = []
            for result in results:
                rule = _ENTITY_RULES.get(getattr(result, "entity_type", ""))
                if rule is None:
                    continue
                score = getattr(result, "score", 0.0) or 0.0
                if score < self.min_score:
                    continue
                start = getattr(result, "start", -1)
                end = getattr(result, "end", -1)
                if end <= start or start < 0 or end > len(text):
                    continue
                value = text[start:end]
                rule_id, rule_name, severity, action, priority = rule
                if rule_id == "pii_inn_ru" and not is_valid_inn(value):
                    continue
                matches.append(
                    RuleMatch(
                        rule_id=rule_id,
                        rule_name=rule_name,
                        value_hash=_hash_value(value),
                        position=(start, end),
                        severity=severity,
                        action=action,
                    )
                )

            matches.sort(key=lambda m: (_priority(m.rule_id), m.position[0]))
            span.set_attribute("scanner.ner_hits", len(matches))
            logger.debug("presidio_analyzed", hits=len(matches), language=self.language)
            return matches


def _priority(rule_id: str) -> int:
    for entity, rule in _ENTITY_RULES.items():
        if rule[0] == rule_id:
            return rule[4]
    return 99


def make_detector(
    rule_set: Optional[RuleSet] = None,
    presidio_engine: Optional[Any] = None,
    *,
    enabled: Optional[bool] = None,
) -> PIIDetector:
    """Build a :class:`PIIDetector` combining YAML regex rules with Presidio NER.

    Args:
        rule_set: optional rule set override (defaults to the global one).
        presidio_engine: prebuilt engine (tests, startup injection). When
            ``None`` and NER is enabled the shared engine is built lazily.
        enabled: override for ``settings.presidio_enabled``.
    """
    active = settings.presidio_enabled if enabled is None else enabled

    engine = presidio_engine
    if active and engine is None:
        if not PRESIDIO_AVAILABLE:
            logger.warning(
                "presidio_enabled_but_not_installed",
                hint='pip install -e ".[ner]" — falling back to regex-only',
            )
        else:
            engine = get_or_build_presidio_engine()
            if engine is None:
                logger.warning("presidio_engine_unavailable_regex_only")

    ner = PresidioNerProvider(engine) if engine is not None else None
    return PIIDetector(rule_set=rule_set, ner=ner)
