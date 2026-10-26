from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import structlog

from app.core.cache import decision_cache
from app.core.pdp import rule_set, scan_text, decide
from app.core.rules.base import RuleMatch, Verdict

logger = structlog.get_logger()


@dataclass
class SuspicionResult:
    is_suspicious: bool
    reasons: list[str]


@dataclass
class FastPathResult:
    verdict: Verdict | None
    cache_hit: bool
    is_suspicious: bool
    latency_ms: float
    breakdown: dict[str, float]


class FastPath:
    def __init__(self, rule_set: Any = None):
        self.rule_set = rule_set or scan_text  # Allow dependency injection
        self._token_threshold = 200
        self._min_tokens = 3

    def _count_tokens_heuristic(self, text: str) -> int:
        """Fast token count heuristic using word splitting (sub 0.01ms)."""
        return len(text.split())

    def _is_suspicious_by_length(self, text: str) -> bool:
        """Check if text is suspicious based on length."""
        token_count = self._count_tokens_heuristic(text)
        return token_count > self._token_threshold

    def _check_suspicion(self, text: str) -> SuspicionResult:
        """Suspicion tagger logic: length + regex match count."""
        reasons = []
        is_suspicious = False

        # Length check
        if self._is_suspicious_by_length(text):
            reasons.append(f"length={self._count_tokens_heuristic(text)}")
            is_suspicious = True

        # Regex match count
        matches, _ = self.rule_set(text)
        if matches:
            reasons.append(f"regex_matches={len(matches)}")
            is_suspicious = True

        # Edge cases: very short prompts
        if self._count_tokens_heuristic(text) < self._min_tokens:
            reasons.append("too_short")
            is_suspicious = False  # Not suspicious, just filter out

        return SuspicionResult(is_suspicious=is_suspicious, reasons=reasons)

    async def process(self, text: str) -> FastPathResult:
        """
        Process prompt through fast path:
        1. Decision cache lookup
        2. Rule engine (regex)
        3. Suspicion tagger (length, regex match count)
        """
        start_time = time.time()

        # Step 1: Decision cache lookup
        cache_start = time.time()
        cached_data, cache_hit = await decision_cache.get(text)
        cache_latency = time.time() - cache_start

        if cache_hit and cached_data is not None:
            # Return cached verdict immediately
            verdict = Verdict(
                action=cached_data["verdict"],
                reason=cached_data["reason"],
                rules_matched=[RuleMatch(**match) for match in cached_data["rules_matched"]],
            )
            total_latency = time.time() - start_time
            return FastPathResult(
                verdict=verdict,
                cache_hit=True,
                is_suspicious=False,  # Cached result is final
                latency_ms=total_latency * 1000,
                breakdown={"cache_ms": cache_latency * 1000, "total_ms": total_latency * 1000},
            )

        # Step 2: Rule engine (regex)
        rule_start = time.time()
        matches, prompt_hash = self.rule_set(text)
        rule_latency = time.time() - rule_start

        # Step 3: Suspicion tagger
        suspicion_start = time.time()
        suspicion_result = self._check_suspicion(text)
        suspicion_latency = time.time() - suspicion_start

        # Decision logic
        if not suspicion_result.is_suspicious:
            # No regex match + short → ALLOW (no slow path)
            verdict = Verdict(action="allow", reason="fast_path_clean", rules_matched=[])
            total_latency = time.time() - start_time
            return FastPathResult(
                verdict=verdict,
                cache_hit=False,
                is_suspicious=False,
                latency_ms=total_latency * 1000,
                breakdown={
                    "cache_ms": cache_latency * 1000,
                    "rule_ms": rule_latency * 1000,
                    "suspicion_ms": suspicion_latency * 1000,
                    "total_ms": total_latency * 1000,
                },
            )

        # Suspicious → continue to slow path
        verdict = None
        total_latency = time.time() - start_time
        return FastPathResult(
            verdict=verdict,
            cache_hit=False,
            is_suspicious=True,
            latency_ms=total_latency * 1000,
            breakdown={
                "cache_ms": cache_latency * 1000,
                "rule_ms": rule_latency * 1000,
                "suspicion_ms": suspicion_latency * 1000,
                "total_ms": total_latency * 1000,
            },
        )


# Global instance for easy reuse
fast_path = FastPath()