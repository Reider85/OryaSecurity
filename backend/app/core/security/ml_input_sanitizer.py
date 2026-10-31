from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Mapping

import structlog

from app.core.metrics import (
    scanner_ml_input_patterns_total,
    scanner_ml_input_sanitized_total,
)

logger = structlog.get_logger()

REMOVED_PLACEHOLDER = "[REMOVED_PATTERN]"

# Prompt-injection-like patterns stripped from ML input (ALPHA Prompt 48).
# Each entry: canonical pattern name -> compiled regex. Matching is
# case-insensitive; matches are replaced by REMOVED_PLACEHOLDER so positions
# in the surrounding text stay stable for the classifier/embedding.
DEFAULT_PATTERNS: dict[str, re.Pattern[str]] = {
    "ignore_previous_instructions": re.compile(
        r"\bignore\s+(?:all\s+|any\s+)?"
        r"(?:previous|prior|above|earlier|preceding)\s+"
        r"(?:instructions|prompts|messages|rules)\b",
        re.IGNORECASE,
    ),
    "new_instructions_marker": re.compile(
        r"\bnew\s+instructions?\s*:",
        re.IGNORECASE,
    ),
    "system_prompt_extraction": re.compile(
        r"\b(?:reveal|show|print|repeat|output|dump|display)\b"
        r"[\s\S]{0,40}?"
        r"\b(?:your|the)\s+(?:system|hidden|initial|original)\s+"
        r"(?:prompt|instructions|message|rules)\b",
        re.IGNORECASE,
    ),
    "role_override_dan": re.compile(
        r"\byou\s+are\s+(?:now\s+)?(?:dan|do\s+anything\s+now)\b"
        r"|\bdan\s+mode\b"
        r"|\bjailbroken\s+(?:mode|ai)\b",
        re.IGNORECASE,
    ),
    "developer_mode": re.compile(
        r"\b(?:developer|god)\s+mode\b",
        re.IGNORECASE,
    ),
    "instruction_delimiters": re.compile(
        r"<<\s*sys\s*>>"
        r"|<<\s*/?\s*system\s*>>"
        r"|\[\s*inst\s*\]"
        r"|\[\s*/\s*inst\s*\]"
        r"|<\|im_start\|>"
        r"|<\|im_end\|>"
        r"|<\|system\|>",
        re.IGNORECASE,
    ),
    "safety_override": re.compile(
        r"\b(?:bypass|disable|turn\s+off|circumvent|override)\b"
        r"[\s\S]{0,30}?"
        r"\b(?:your\s+(?:safety\s+(?:guidelines?|instructions|filters?|settings)"
        r"|restrictions?|limitations?)"
        r"|safety\s+(?:guidelines?|instructions|filters?|settings)"
        r"|content\s+polic(?:y|ies)|guardrails?)\b",
        re.IGNORECASE,
    ),
    "disregard_rules": re.compile(
        r"\bdisregard\b"
        r"[\s\S]{0,30}?"
        r"\b(?:previous|prior|above|all|your|these)\s+"
        r"(?:instructions|rules|guidelines|prompts|restrictions)\b",
        re.IGNORECASE,
    ),
    "pretend_roleplay": re.compile(
        r"\b(?:pretend|act)\s+"
        r"(?:to\s+be\s+|as\s+)?"
        r"(?:you(?:'re|\s+are)\s+)?"
        r"(?:an?\s+)?"
        r"(?:evil|unrestricted|uncensored|jailbroken|without\s+rules)\b",
        re.IGNORECASE,
    ),
}


@dataclass(frozen=True)
class SanitizedText:
    """Result of ML input sanitization.

    Attributes:
        sanitized_text: Text safe to feed into embedding/classifier.
        removed_chars: Number of characters deleted (zero-width, control
            chars, pattern matches). Placeholder insertions and NFKC
            expansions are not counted.
        suspicious_patterns: Unique names of the prompt-injection-like
            patterns that matched, in DEFAULT_PATTERNS order.
    """

    sanitized_text: str
    removed_chars: int
    suspicious_patterns: list[str] = field(default_factory=list)


class MLInputSanitizer:
    """Input sanitization before ML inference (ALPHA Prompt 48).

    Threat: an attacker embeds adversarial text that looks benign to the ML
    classifier but that the LLM interprets as a jailbreak.

    Mitigation pipeline:
    1. Normalize newlines (``\\r\\n`` / ``\\r`` -> ``\\n``)
    2. Drop format/zero-width characters (Unicode category ``Cf``)
    3. Drop control and surrogate characters except ``\\n`` and ``\\t``
    4. Unicode NFKC normalization
    5. Detect prompt-injection-like patterns and replace them with a
       placeholder in the ML input (original text is untouched elsewhere)

    ``sanitize`` never raises: on any internal error the input is returned
    unchanged so a sanitizer bug cannot take the slow path down.

    Args:
        patterns: Optional custom name -> compiled regex mapping.
        placeholder: Replacement text for matched pattern spans.
    """

    def __init__(
        self,
        *,
        patterns: Mapping[str, re.Pattern[str]] | None = None,
        placeholder: str = REMOVED_PLACEHOLDER,
    ):
        self.patterns: dict[str, re.Pattern[str]] = (
            dict(DEFAULT_PATTERNS) if patterns is None else dict(patterns)
        )
        self.placeholder = placeholder

    def sanitize(self, text: str) -> SanitizedText:
        """Sanitize text destined for embedding/classifier inference.

        Args:
            text: Raw input text.

        Returns:
            SanitizedText with the cleaned text, the number of deleted
            characters and the names of matched suspicious patterns.
        """
        try:
            if not isinstance(text, str):
                logger.warning(
                    "ml_sanitizer_non_str_input",
                    type=type(text).__name__,
                )
                return SanitizedText(
                    sanitized_text=str(text) if text is not None else "",
                    removed_chars=0,
                    suspicious_patterns=[],
                )

            if not text:
                return SanitizedText(sanitized_text="", removed_chars=0, suspicious_patterns=[])

            removed = 0
            reasons: list[str] = []

            # Step 1: newline normalization. Every \r is a removed character
            # (CRLF collapses to LF; a lone CR is replaced by LF).
            newline_removed = text.count("\r")
            normalized = text.replace("\r\n", "\n").replace("\r", "\n")

            # Step 2: zero-width / format characters (Cf).
            after_format = "".join(ch for ch in normalized if unicodedata.category(ch) != "Cf")
            format_removed = len(normalized) - len(after_format)

            # Step 3: control (Cc) and surrogate (Cs) chars, keep \n and \t.
            after_control = "".join(
                ch
                for ch in after_format
                if ch in ("\n", "\t")
                or unicodedata.category(ch) not in ("Cc", "Cs")
            )
            control_removed = len(after_format) - len(after_control)

            # Step 4: Unicode NFKC normalization.
            nfkc = unicodedata.normalize("NFKC", after_control)
            nfkc_changed = nfkc != after_control

            # Step 5: prompt-injection-like pattern detection + replacement.
            current = nfkc
            patterns_matched: list[str] = []
            pattern_removed = 0
            for name, pattern in self.patterns.items():
                matches = list(pattern.finditer(current))
                if not matches:
                    continue
                patterns_matched.append(name)
                pattern_removed += sum(len(m.group(0)) for m in matches)
                current = pattern.sub(self.placeholder, current)

            removed = newline_removed + format_removed + control_removed + pattern_removed

            if format_removed:
                reasons.append("zero_width")
            if newline_removed or control_removed:
                reasons.append("control_chars")
            if nfkc_changed:
                reasons.append("nfkc")
            if patterns_matched:
                reasons.append("injection_pattern")

            for reason in reasons:
                scanner_ml_input_sanitized_total.labels(reason=reason).inc()
            for name in patterns_matched:
                scanner_ml_input_patterns_total.labels(pattern=name).inc()

            if patterns_matched or removed:
                logger.info(
                    "ml_input_sanitized",
                    removed_chars=removed,
                    suspicious_patterns=patterns_matched,
                    changed=current != text,
                )

            return SanitizedText(
                sanitized_text=current,
                removed_chars=removed,
                suspicious_patterns=patterns_matched,
            )
        except Exception:
            logger.warning("ml_input_sanitizer_failed", exc_info=True)
            return SanitizedText(
                sanitized_text=text if isinstance(text, str) else "",
                removed_chars=0,
                suspicious_patterns=[],
            )


def get_shared_sanitizer() -> MLInputSanitizer:
    """Get the shared ML input sanitizer (stateless, one instance is enough)."""
    return _shared_sanitizer


_shared_sanitizer = MLInputSanitizer()


__all__ = [
    "DEFAULT_PATTERNS",
    "REMOVED_PLACEHOLDER",
    "MLInputSanitizer",
    "SanitizedText",
    "get_shared_sanitizer",
]
