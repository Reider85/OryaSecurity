"""PII/secrets detection for the Vault redaction path (EP-13/Prompt 32, ADR-0007).

Thin wrapper around the YAML ``RuleSet`` (the same engine the scan pipeline
uses) with an optional NER hook for non-regex PII. Prompt 35 plugs Microsoft
Presidio into that hook (see :mod:`app.core.detectors.pii_ner`); without the
``ner`` extra detection is regex-only.

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
            Its matches are merged with the regex matches; regex is
            deterministic and wins on overlap, except that a *blocking* NER
            hit supersedes the digit-run heuristic rules it overlaps
            (``pii_phone``, ``pii_passport_ru`` — see :data:`HEURISTIC_RULE_IDS`)
            so a checksum-validated ИНН is never downgraded to ``pii_phone``.
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


# Rules that match any digit run (and therefore produce low-information hits):
# they are heuristics, not specific evidence. A blocking NER hit with a
# validated value (e.g. ИНН check-sum) may supersede them; specific regex
# evidence (SSN, email, secrets) never yields to NER.
HEURISTIC_RULE_IDS = frozenset({"pii_phone", "pii_passport_ru"})


def _merge_matches(regex: list[RuleMatch], ner: list[RuleMatch]) -> list[RuleMatch]:
    """Merge NER detections into regex matches.

    Rules, in order:

    * regex hits are always kept unless explicitly superseded (below);
    * a blocking NER hit whose entire overlap consists of heuristic digit-run
      regex hits (``HEURISTIC_RULE_IDS``) replaces those hits;
    * otherwise an overlapping NER hit is dropped — regex is deterministic and
      wins (two signals on the same span are reported once);
    * NER hits overlapping an already accepted NER hit are dropped (first wins,
      callers should order NER input by entity priority);
    * result is sorted by start position.
    """
    merged = list(regex)
    for match in ner:
        overlapping = [m for m in regex if _overlaps(match.position, m.position)]
        if overlapping:
            heuristic_only = all(m.rule_id in HEURISTIC_RULE_IDS for m in overlapping)
            if match.action != "block" or not heuristic_only:
                continue
            # Blocking NER (validated value) supersedes digit-run heuristics.
            superseded = {id(m) for m in overlapping}
            merged = [m for m in merged if id(m) not in superseded]
        if any(_overlaps(match.position, m.position) for m in merged):
            continue
        merged.append(match)
    merged.sort(key=lambda m: m.position[0])
    return merged

