from __future__ import annotations

import pytest
from unittest.mock import patch, AsyncMock

from app.core.pipeline.orchestrator import PipelineOrchestrator, OrchestratorResult, get_orchestrator, set_orchestrator
from app.core.pipeline.fast_path import FastPathResult
from app.core.pipeline.slow_path import SlowPathResult
from app.core.rules.base import Verdict


class TestOrchestrator:
    """Test pipeline orchestrator path routing and metrics."""
    
    @pytest.mark.asyncio
    async def test_cache_hit_path(self, orchestrator_instance: PipelineOrchestrator, fake_redis):
        """Test cache hit returns cache_hit path and increments metric."""
        # Setup fake cache data
        cached_data = {
            "verdict": "allow",
            "reason": "cached_clean",
            "rules_matched": [],
            "ts": 1234567890.0,
        }
        fake_redis.set("test_hash", '{"verdict": "allow", "reason": "cached_clean", "rules_matched": [], "ts": 1234567890.0, "expires_at": 1234567890.0 + 300}')

        # Mock fast_path to return cache hit result directly
        fast_result = FastPathResult(
            verdict=Verdict(action="allow", reason="cached_clean", rules_matched=[]),
            cache_hit=True,
            is_suspicious=False,
            latency_ms=1.5,
            breakdown={"total_ms": 1.5},
        )
        
        with patch.object(orchestrator_instance.fast_path, "process", return_value=fast_result):
            result = await orchestrator_instance.process("test prompt")

        # Verify result
        assert result.path == "cache_hit"
        assert result.fast.cache_hit is True
        assert result.fast.verdict.action == "allow"
        assert result.slow is None
        
        # Verify metric increment (check that counter was called)
        from app.core.metrics import scanner_path_distribution
        samples = list(scanner_path_distribution.collect())[0].samples
        cache_hit_samples = [s for s in samples if s.labels.get("path") == "cache_hit"]
        assert len(cache_hit_samples) > 0

    @pytest.mark.asyncio
    async def test_fast_path_clean(self, orchestrator_instance: PipelineOrchestrator):
        """Test clean prompt goes through fast path only."""
        # Mock fast_path to return clean result
        fast_result = FastPathResult(
            verdict=Verdict(action="allow", reason="clean", rules_matched=[]),
            cache_hit=False,
            is_suspicious=False,
            latency_ms=2.0,
            breakdown={"total_ms": 2.0},
        )
        
        with patch.object(orchestrator_instance.fast_path, "process", return_value=fast_result):
            result = await orchestrator_instance.process("clean prompt")

        # Verify result
        assert result.path == "fast"
        assert result.fast.cache_hit is False
        assert result.fast.verdict.action == "allow"
        assert result.slow is None
        
        # Verify metric increment
        from app.core.metrics import scanner_path_distribution
        samples = list(scanner_path_distribution.collect())[0].samples
        fast_samples = [s for s in samples if s.labels.get("path") == "fast"]
        assert len(fast_samples) > 0

    @pytest.mark.asyncio
    async def test_slow_path_invoked(self, orchestrator_instance: PipelineOrchestrator):
        """Test suspicious prompt invokes slow path."""
        # Mock fast_path to return suspicious result
        fast_result = FastPathResult(
            verdict=None,
            cache_hit=False,
            is_suspicious=True,
            latency_ms=3.0,
            breakdown={"total_ms": 3.0},
        )
        
        # Mock slow_path to return result
        slow_result = SlowPathResult(
            embedding=None,
            similar_attacks=[],
            classification=None,
            aggregated={"threat_score": 0.5},
            degraded_components=[],
            latency_ms=10.0,
            breakdown={"total_ms": 10.0},
        )
        
        with patch.object(orchestrator_instance.fast_path, "process", return_value=fast_result):
            with patch.object(orchestrator_instance._get_slow_path(), "process", return_value=slow_result):
                result = await orchestrator_instance.process("suspicious prompt")

        # Verify result
        assert result.path == "slow"
        assert result.fast.is_suspicious is True
        assert result.slow is not None
        assert result.slow.aggregated.get("threat_score") == 0.5
        
        # Verify metric increment
        from app.core.metrics import scanner_path_distribution
        samples = list(scanner_path_distribution.collect())[0].samples
        slow_samples = [s for s in samples if s.labels.get("path") == "slow"]
        assert len(slow_samples) > 0

    @pytest.mark.asyncio
    async def test_orchestrator_result_serialization(self):
        """Test OrchestratorResult serialization."""
        fast_result = FastPathResult(
            verdict=Verdict(action="allow", reason="clean", rules_matched=["rule1"]),
            cache_hit=False,
            is_suspicious=False,
            latency_ms=1.0,
            breakdown={"total_ms": 1.0},
        )
        
        result = OrchestratorResult(
            path="fast",
            fast=fast_result,
            latency_ms=1.5,
        )
        
        serialized = result.to_dict()
        assert serialized["path"] == "fast"
        assert serialized["fast"]["verdict"] == "allow"
        assert serialized["fast"]["cache_hit"] is False
        assert serialized["slow"] is None
        assert serialized["latency_ms"] == 1.5

    def test_global_orchestrator_singleton(self):
        """Test global orchestrator singleton pattern."""
        # Get default instance
        orchestrator1 = get_orchestrator()
        assert isinstance(orchestrator1, PipelineOrchestrator)
        
        # Should return same instance
        orchestrator2 = get_orchestrator()
        assert orchestrator1 is orchestrator2
        
        # Set custom instance
        custom = PipelineOrchestrator()
        set_orchestrator(custom)
        
        # Should return custom instance
        orchestrator3 = get_orchestrator()
        assert orchestrator3 is custom
        
        # Reset to default
        set_orchestrator(None)
        orchestrator4 = get_orchestrator()
        assert isinstance(orchestrator4, PipelineOrchestrator)
        assert orchestrator1 is orchestrator4

    @pytest.mark.asyncio
    async def test_metrics_incremented(self, orchestrator_instance: PipelineOrchestrator):
        """Test that scanner_path_distribution metric is incremented per request."""
        # Mock fast_path to return clean result
        fast_result = FastPathResult(
            verdict=Verdict(action="allow", reason="clean", rules_matched=[]),
            cache_hit=False,
            is_suspicious=False,
            latency_ms=1.0,
            breakdown={"total_ms": 1.0},
        )
        
        with patch.object(orchestrator_instance.fast_path, "process", return_value=fast_result):
            # Process multiple requests
            await orchestrator_instance.process("prompt1")
            await orchestrator_instance.process("prompt2")
            
            # Verify metric increments
            from app.core.metrics import scanner_path_distribution
            samples = list(scanner_path_distribution.collect())[0].samples
            fast_count = len([s for s in samples if s.labels.get("path") == "fast"])
            assert fast_count >= 2