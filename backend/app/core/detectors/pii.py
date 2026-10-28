"""PII/secrets detection for the Vault redaction path (EP-13/Prompt 32, ADR-0007).

Thin wrapper around the YAML ``RuleSet`` (the same engine the scan pipeline
uses) with an optional NER hook for non-regex PII. Prompt 35 wires Microsoft
Presidio into that hook; until then detection is regex-only.

The detector returns plain ``RuleMatch`` objects so the redactor (and later
the audit layer) can reuse the existing position/severity/action semantics
without inventing a parallel data model.
"""

from __future__ import annotations

from typing import Callable, Optional

from app.core.rules.base import RuleMatch
from app.core.rules.loader import RuleSet

# Optional NER provider: takes raw text, returns RuleMatch-like detections.
# Presidio (Prompt 35) plugs in here.
NERProvider = Callable[[str], list[RuleMatch]]


class PIIDetector:
    """Detect PII and secrets in text.

    Args:
        rule_set: YAML rule set to match against. Defaults to the global
            singleton from ``app.core.pdp`` (lazily, to avoid a circular
            import at module load time).
        ner: Optional callable for model-based detection (Presidio, Prompt 35).
            Its matches are merged with the regex matches; NER hits that
            overlap a regex hit are dropped (regex is deterministic and wins).
    """

    def __init__(
        self,
        rule_set: Optional[RuleSet] = None,
        ner: Optional[NERProvider] = None,
    ):
        self._rule_set = rule_set
        self.ner = ner

    @property
    def rule_set(self) -> RuleSet:
        if self._rule_set is None:
            from app.core.pdp import rule_set as global_rule_set

            self._rule_set = global_rule_set
        return self._rule_set

    def detect(self, text: str) -> list[RuleMatch]:
        """Return all PII/secrets matches in *text* (regex + optional NER)."""
        if not text:
            return []

        matches = list(self.rule_set.match(text))

        if self.ner is not None:
            try:
                ner_matches = self.ner(text)
            except Exception:  # noqa: BLE001 - NER must never break detection
                ner_matches = []
            matches = _merge_matches(matches, ner_matches)

        return matches


def _overlaps(a: tuple[int, int], b: tuple[int, int]) -> bool:
    return a[0] < b[1] and b[0] < a[1]


def _merge_matches(regex: list[RuleMatch], ner: list[RuleMatch]) -> list[RuleMatch]:
    """Merge NER detections into regex matches, dropping overlapping NER hits."""
    merged = list(regex)
    for match in ner:
        if any(_overlaps(match.position, m.position) for m in regex):
            continue
        # Skip NER hits that overlap another accepted NER hit
        if any(_overlaps(match.position, m.position) for m in merged):
            continue
        merged.append(match)
    merged.sort(key=lambda m: m.position[0])
    return merged
