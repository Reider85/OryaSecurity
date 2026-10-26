from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

import structlog

from app.core.pipeline.fast_path import FastPath, FastPathResult
from app.core.pipeline.slow_path import SlowPath, SlowPathResult
from app.core.metrics import scanner_path_distribution

logger = structlog.get_logger()


@dataclass
class OrchestratorResult:
    """Result from pipeline orchestrator combining fast and slow path analysis."""
    
    path: str  # "cache_hit" | "fast" | "slow"
    fast: FastPathResult
    slow: Optional[SlowPathResult] = None
    latency_ms: float = 0.0
    
    def to_dict(self) -> dict[str, any]:
        """Convert to dict for serialization."""
        return {
            "path": self.path,
            "fast": {
                "verdict": self.fast.verdict.action if self.fast.verdict else None,
                "cache_hit": self.fast.cache_hit,
                "is_suspicious": self.fast.is_suspicious,
                "latency_ms": self.fast.latency_ms,
            },
            "slow": self.slow.to_dict() if self.slow else None,
            "latency_ms": self.latency_ms,
        }


class PipelineOrchestrator:
    """Two-tier pipeline orchestrator: fast path → (if suspicious) slow path.
    
    Routes prompts through fast path (cache lookup, rules, suspicion detection),
    then slow path (embedding, vector search, ML) if suspicious.
    Increments scanner_path_distribution metric per request.
    
    Args:
        fast_path: Optional FastPath instance (defaults to global)
        slow_path: Optional SlowPath instance (defaults to global)
    """
    
    def __init__(self, fast_path: Optional[FastPath] = None, slow_path: Optional[SlowPath] = None):
        self.fast_path = fast_path or FastPath()
        self.slow_path = slow_path
    
    def _get_slow_path(self) -> SlowPath:
        """Get shared slow path instance (lazy initialization)."""
        if self.slow_path is None:
            from app.core.pipeline.slow_path import get_slow_path
            self.slow_path = get_slow_path()
        return self.slow_path
    
    async def process(self, text: str) -> OrchestratorResult:
        """Process text through two-tier pipeline.
        
        Args:
            text: Input text to analyze
            
        Returns:
            OrchestratorResult with path type and component results
        """
        start_time = time.perf_counter()
        
        # Fast path processing
        fast_result = await self.fast_path.process(text)
        
        # Determine path type and increment metric
        if fast_result.cache_hit:
            path = "cache_hit"
        elif not fast_result.is_suspicious:
            path = "fast"
        else:
            # Suspicious → proceed to slow path
            slow_path_instance = self._get_slow_path()
            slow_result = await slow_path_instance.process(text)
            path = "slow"
            
            # Combine results
            result = OrchestratorResult(
                path=path,
                fast=fast_result,
                slow=slow_result,
                latency_ms=(time.perf_counter() - start_time) * 1000,
            )
            
            logger.info(
                "orchestrator_slow_path_complete",
                path=path,
                fast_verdict=fast_result.verdict.action if fast_result.verdict else None,
                slow_threat_score=slow_result.aggregated.get("threat_score", 0.0),
                latency_ms=round(result.latency_ms, 2),
            )
            
            # Increment metric for this path
            scanner_path_distribution.labels(path=path).inc()
            
            return result
        
        # Fast path only results
        result = OrchestratorResult(
            path=path,
            fast=fast_result,
            latency_ms=(time.perf_counter() - start_time) * 1000,
        )
        
        logger.info(
            "orchestrator_fast_path_complete",
            path=path,
            verdict=fast_result.verdict.action if fast_result.verdict else None,
            latency_ms=round(result.latency_ms, 2),
        )
        
        # Increment metric for this path
        scanner_path_distribution.labels(path=path).inc()
        
        return result


# Global orchestrator instance (lazy initialization)
_orchestrator: Optional[PipelineOrchestrator] = None


def get_orchestrator() -> PipelineOrchestrator:
    """Get global orchestrator instance (lazy initialization)."""
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = PipelineOrchestrator()
        logger.info("orchestrator_initialized")
    return _orchestrator


def set_orchestrator(orchestrator: Optional[PipelineOrchestrator]) -> None:
    """Set global orchestrator instance."""
    global _orchestrator
    _orchestrator = orchestrator
    logger.info("orchestrator_updated")