from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import numpy as np

# Mock dependencies before importing
patch('app.core.detectors.vector_search.get_shared_vector_searcher').start()
patch('app.core.detectors.ml_classifier.get_shared_classifier').start()
patch('app.ml.batcher.get_shared_embedding_batcher').start()
patch('app.core.detectors.embedding.get_shared_embedding_client').start()
patch('app.core.cache_embedding.embedding_cache').start()

from app.core.pipeline.slow_path import SlowPath, SlowPathResult, CircuitBreaker
from app.core.detectors.vector_search import SearchResult
from app.core.detectors.ml_classifier import ClassificationResult
from app.core.cache_embedding import embedding_cache


@pytest.fixture
def fake_redis():
    """Fake Redis client using fakeredis for unit tests."""
    import fakeredis
    
    # Create fake Redis client
    client = fakeredis.FakeRedis(decode_responses=True)
    
    yield client
    
    # Cleanup
    client.close()


@pytest.fixture
def mock_embedding_batcher():
    """Mock embedding batcher."""
    mock = AsyncMock()
    mock.embed.return_value = np.random.rand(1024).astype(np.float32)
    return mock


@pytest.fixture
def mock_vector_searcher():
    """Mock vector searcher."""
    mock = AsyncMock()
    hits = [
        SearchResult(
            id="test_attack_1",
            score=0.95,
            payload={"type": "prompt_injection", "severity": "high"}
        )
    ]
    mock.search.return_value = hits
    mock.search_or_raise.return_value = hits
    return mock


@pytest.fixture
def mock_classifier():
    """Mock ML classifier."""
    mock = AsyncMock()
    mock.classify.return_value = ClassificationResult(
        top_class="prompt_injection",
        confidence=0.92,
        all_classes={"benign": 0.08, "prompt_injection": 0.92, "jailbreak": 0.0, "suspicious": 0.0}
    )
    return mock


@pytest.fixture
def slow_path_instance(
    mock_embedding_batcher,
    mock_vector_searcher,
    mock_classifier
):
    """SlowPath instance with mocked dependencies."""
    return SlowPath(
        embedding_batcher=mock_embedding_batcher,
        vector_searcher=mock_vector_searcher,
        classifier=mock_classifier
    )


class TestSlowPath:
    """Test suite for SlowPath class."""

    @pytest.mark.asyncio
    async def test_happy_path_full_pipeline(self, slow_path_instance: SlowPath):
        """Test complete slow path pipeline with all components."""
        result = await slow_path_instance.process("test prompt with suspicious content")

        # Verify result structure
        assert isinstance(result, SlowPathResult)
        assert result.embedding is not None
        assert len(result.similar_attacks) == 1
        assert result.classification is not None
        assert result.classification.top_class == "prompt_injection"
        assert result.classification.confidence == 0.92
        assert result.aggregated["threat_score"] > 0.0
        assert "threat" in result.aggregated["recommended_evidence"][0]["type"]
        assert result.latency_ms > 0
        assert "embed_ms" in result.breakdown
        assert "vector_ms" in result.breakdown
        assert "classifier_ms" in result.breakdown
        assert "aggregate_ms" in result.breakdown
        assert "total_ms" in result.breakdown
        assert result.breakdown["total_ms"] == result.latency_ms

    @pytest.mark.asyncio
    async def test_embedding_cache_hit(self, slow_path_instance: SlowPath, fake_redis):
        """Test embedding cache hit (no batcher call)."""
        # Pre-populate cache
        test_embedding = np.random.rand(1024).astype(np.float32)
        fake_redis.set("test_hash", test_embedding.tobytes())

        with patch.object(slow_path_instance, "_get_embedding_batcher") as mock_batcher:
            result = await slow_path_instance.process("test prompt")

            # Cache hit should not call batcher
            mock_batcher.assert_not_called()
            assert result.embedding is not None
            assert len(result.similar_attacks) == 1  # Vector search still runs

    @pytest.mark.asyncio
    async def test_embedding_unavailable(self, slow_path_instance: SlowPath):
        """Test embedding unavailable (vector search skipped)."""
        # Mock embedding to return None
        with patch.object(slow_path_instance, "_get_embedding", return_value=None):
            result = await slow_path_instance.process("test prompt")

            assert result.embedding is None
            assert len(result.similar_attacks) == 0
            assert "embedding" in result.degraded_components
            assert result.classification is not None  # Classifier still runs

    @pytest.mark.asyncio
    async def test_vector_search_unavailable(self, slow_path_instance: SlowPath):
        """Test vector searcher unavailable (returns None)."""
        # Mock vector search to return None
        with patch.object(slow_path_instance._get_vector_searcher(), "search_or_raise", return_value=None):
            result = await slow_path_instance.process("test prompt")

            assert len(result.similar_attacks) == 0
            assert "vector_search" in result.degraded_components
            assert result.classification is not None  # Classifier still runs

    @pytest.mark.asyncio
    async def test_classifier_unavailable(self, slow_path_instance: SlowPath):
        """Test classifier unavailable (returns None)."""
        # Mock classifier to return None
        with patch.object(slow_path_instance._get_classifier(), "classify", return_value=None):
            result = await slow_path_instance.process("test prompt")

            assert result.classification is None
            assert "ml_classifier" in result.degraded_components
            assert len(result.similar_attacks) == 1  # Vector search still works

    @pytest.mark.asyncio
    async def test_all_components_degraded(self, slow_path_instance: SlowPath):
        """Test all components degraded gracefully."""
        # Mock all components to return None
        with patch.object(slow_path_instance, "_get_embedding", return_value=None), \
             patch.object(slow_path_instance._get_vector_searcher(), "search_or_raise", return_value=None), \
             patch.object(slow_path_instance._get_classifier(), "classify", return_value=None):
            
            result = await slow_path_instance.process("test prompt")

            assert result.embedding is None
            assert len(result.similar_attacks) == 0
            assert result.classification is None
            assert result.degraded_components == ["embedding", "vector_search", "ml_classifier"]
            assert result.aggregated["threat_score"] == 0.0
            assert result.latency_ms > 0  # Should still complete without crashing

    @pytest.mark.asyncio
    async def test_clean_prompt_no_threat(self, slow_path_instance: SlowPath):
        """Test clean prompt (no vector hits, benign classification)."""
        # Mock vector search to return no hits above threshold
        mock_searcher = slow_path_instance._get_vector_searcher()
        low_score_hits = [SearchResult(id="low_score", score=0.8, payload={"type": "benign"})]
        mock_searcher.search.return_value = low_score_hits
        mock_searcher.search_or_raise.return_value = low_score_hits

        # Mock classifier to return benign
        mock_classifier = slow_path_instance._get_classifier()
        mock_classifier.classify.return_value = ClassificationResult(
            top_class="benign",
            confidence=0.95,
            all_classes={"benign": 0.95, "prompt_injection": 0.02, "jailbreak": 0.02, "suspicious": 0.01}
        )

        result = await slow_path_instance.process("clean prompt")

        assert result.aggregated["threat_score"] == 0.0
        assert result.aggregated["classifier_top_class"] == "benign"
        assert result.aggregated["classifier_is_suspicious"] is False
        assert result.aggregated["recommended_evidence"] == []

    @pytest.mark.asyncio
    async def test_aggregation_with_vector_and_ml_threat(self, slow_path_instance: SlowPath):
        """Test aggregation with both vector and ML threats."""
        # Mock vector search with high score
        mock_searcher = slow_path_instance._get_vector_searcher()
        high_threat_hits = [SearchResult(id="high_threat", score=0.95, payload={"type": "jailbreak"})]
        mock_searcher.search.return_value = high_threat_hits
        mock_searcher.search_or_raise.return_value = high_threat_hits

        # Mock classifier with prompt injection
        mock_classifier = slow_path_instance._get_classifier()
        mock_classifier.classify.return_value = ClassificationResult(
            top_class="prompt_injection",
            confidence=0.90,
            all_classes={"benign": 0.10, "prompt_injection": 0.90, "jailbreak": 0.0, "suspicious": 0.0}
        )

        result = await slow_path_instance.process("threat prompt")

        assert result.aggregated["threat_score"] > 0.0
        assert result.aggregated["vector_max_score"] == 0.95
        assert result.aggregated["vector_hits_above_threshold"] == 1
        assert "jailbreak" in result.aggregated["vector_threat_types"]
        assert result.aggregated["classifier_top_class"] == "prompt_injection"
        assert len(result.aggregated["recommended_evidence"]) == 2
        assert any(e["type"] == "vector_search" for e in result.aggregated["recommended_evidence"])
        assert any(e["type"] == "ml_classifier" for e in result.aggregated["recommended_evidence"])

    @pytest.mark.asyncio
    async def test_circuit_breaker_open_behavior(self, slow_path_instance: SlowPath):
        """Test circuit breaker opens after max failures."""
        # Mock component to always fail
        async def failing_function():
            raise Exception("Component failure")

        cb = CircuitBreaker("test_component", fail_max=3, reset_timeout=0.1)
        
        # Trigger failures
        for _ in range(3):
            await cb.call(failing_function)

        # Should now be open and skip calls
        result = await cb.call(failing_function)
        assert result is None
        assert cb.state == "open"

    @pytest.mark.asyncio
    async def test_circuit_breaker_half_open_reset(self, slow_path_instance: SlowPath):
        """Test circuit breaker half-open reset after timeout."""
        # Mock component to fail then succeed
        call_count = 0
        async def failing_then_succeeding_function():
            nonlocal call_count
            call_count += 1
            if call_count <= 3:
                raise Exception("Failure")
            return "success"

        cb = CircuitBreaker("test_component", fail_max=3, reset_timeout=0.1)
        
        # Trigger failures
        for _ in range(3):
            await cb.call(failing_then_succeeding_function)
        assert cb.state == "open"

        # Wait for timeout
        import time
        time.sleep(0.2)  # Wait past reset timeout

        # Next call should be half-open and succeed
        result = await cb.call(failing_then_succeeding_function)
        assert result == "success"
        assert cb.state == "closed"  # Success should reset

    @pytest.mark.asyncio
    async def test_empty_text_handling(self, slow_path_instance: SlowPath):
        """Test handling of empty text."""
        result = await slow_path_instance.process("")

        # Should still process (classifier might return benign, embedding might be None)
        assert isinstance(result, SlowPathResult)
        assert result.latency_ms > 0

    @pytest.mark.asyncio
    async def test_dependency_injection(self):
        """Test that custom dependencies can be injected."""
        custom_batcher = AsyncMock()
        custom_searcher = AsyncMock()
        custom_searcher.search_or_raise.return_value = []
        custom_classifier = AsyncMock()

        custom_slow_path = SlowPath(
            embedding_batcher=custom_batcher,
            vector_searcher=custom_searcher,
            classifier=custom_classifier
        )

        result = await custom_slow_path.process("test")

        # Should use injected dependencies, not globals
        custom_batcher.embed.assert_called_once()
        custom_searcher.search_or_raise.assert_called_once()
        custom_classifier.classify.assert_called_once()


class TestSlowPathResult:
    """Test suite for SlowPathResult dataclass."""

    def test_slow_path_result_to_dict(self):
        """Test SlowPathResult serialization."""
        result = SlowPathResult(
            embedding=np.random.rand(1024),
            similar_attacks=[
                SearchResult(id="test", score=0.9, payload={"type": "test"})
            ],
            classification=ClassificationResult(
                top_class="benign",
                confidence=0.95,
                all_classes={"benign": 0.95}
            ),
            aggregated={"threat_score": 0.5},
            degraded_components=["embedding"],
            latency_ms=100.5,
            breakdown={"total_ms": 100.5}
        )

        result_dict = result.to_dict()
        
        assert result_dict["embedding_present"] is True
        assert result_dict["similar_attacks_count"] == 1
        assert result_dict["classification"]["top_class"] == "benign"
        assert result_dict["aggregated"]["threat_score"] == 0.5
        assert result_dict["degraded_components"] == ["embedding"]
        assert result_dict["latency_ms"] == 100.5


class TestCircuitBreaker:
    """Test suite for CircuitBreaker class."""

    @pytest.mark.asyncio
    async def test_circuit_breaker_success(self):
        """Test circuit breaker with successful calls."""
        async def ok_func():
            return "success"

        cb = CircuitBreaker("test")
        result = await cb.call(ok_func)
        
        assert result == "success"
        assert cb.state == "closed"

    @pytest.mark.asyncio
    async def test_circuit_breaker_failure(self):
        """Test circuit breaker with failing calls."""
        async def failing_func():
            raise ValueError("test error")

        cb = CircuitBreaker("test", fail_max=2)
        
        # First call should work
        result1 = await cb.call(failing_func)
        assert result1 is None
        
        # Second call should open breaker
        result2 = await cb.call(failing_func)
        assert result2 is None
        assert cb.state == "open"

    @pytest.mark.asyncio
    async def test_circuit_breaker_half_open(self):
        """Test circuit breaker half-open state."""
        async def failing_func():
            raise ValueError("test error")

        cb = CircuitBreaker("test", fail_max=2, reset_timeout=0.1)
        
        # Open the breaker
        await cb.call(failing_func)
        await cb.call(failing_func)
        assert cb.state == "open"

        # Wait for timeout
        import time
        time.sleep(0.2)
        
        # Next call should be half-open
        result = await cb.call(failing_func)
        assert result is None
        assert cb.state == "half_open"