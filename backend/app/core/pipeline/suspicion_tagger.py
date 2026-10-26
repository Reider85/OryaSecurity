from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.core.pdp import rule_set, scan_text


@dataclass
class SuspicionResult:
    is_suspicious: bool
    reasons: list[str]


# Precompile base64 pattern for performance
BASE64_PATTERN = re.compile(r'[A-Za-z0-9+/]{200,}={0,2}')


class SuspicionTagger:
    def __init__(self, rule_set: Any = None, token_threshold: int = 200, min_tokens: int = 3):
        self.rule_set = rule_set or scan_text
        self.token_threshold = token_threshold
        self.min_tokens = min_tokens

    def _count_tokens_heuristic(self, text: str) -> int:
        """Fast token count heuristic using word splitting (sub 0.01ms)."""
        return len(text.split())

    def is_suspicious(self, text: str) -> SuspicionResult:
        """
        Check if prompt is suspicious based on:
        - Length > 200 tokens
        - Any regex match
        - Contains "ignore previous instructions" → suspicious
        - Contains base64 encoded data > 200 chars → suspicious
        - Empty or very short prompt (< 3 tokens) → not suspicious
        """
        reasons = []
        is_suspicious = False

        # Length check
        token_count = self._count_tokens_heuristic(text)
        if token_count > self.token_threshold:
            reasons.append(f"length={token_count}")
            is_suspicious = True

        # Regex matches
        matches, _ = self.rule_set(text)
        if matches:
            reasons.append(f"regex_matches={len(matches)}")
            is_suspicious = True

        # Keyword checks
        if "ignore previous instructions" in text.lower():
            reasons.append("ignore_instructions")
            is_suspicious = True

        # Base64 encoded data check
        if BASE64_PATTERN.search(text):
            reasons.append("base64_data")
            is_suspicious = True

        # Edge cases: very short prompts
        if token_count < self.min_tokens:
            reasons.append("too_short")
            is_suspicious = False  # Not suspicious, just filter out

        return SuspicionResult(is_suspicious=is_suspicious, reasons=reasons)


# Global instance for easy reuse
suspicion_tagger = SuspicionTagger()