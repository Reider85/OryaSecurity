from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import structlog

from app.core.cache import decision_cache
from app.core.pdp import rule_set, scan_text, decide
from app.core.rules.base import RuleMatch, Verdict

logger = structlog.get_logger()


from app.core.pipeline.suspicion_tagger import SuspicionTagger, SuspicionResult


@dataclass
class FastPathResult:
    verdict: Verdict | None
    cache_hit: bool
    is_suspicious: bool
    latency_ms: float
    breakdown: dict[str, float]


class FastPath:
    def __init__(self, rule_set: Any = None, tagger: SuspicionTagger | None = None):
        self.rule_set = rule_set or scan_text  # Allow dependency injection
        self.tagger = tagger or SuspicionTagger(rule_set=rule_set)  # Use provided or create with rule_set

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
        suspicion_result = self.tagger.is_suspicious(text)
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