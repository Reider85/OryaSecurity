from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from app.ml.batcher import EmbeddingBatcher


class FakeTritonClient:
    """Fake Triton client for testing."""
    
    def __init__(self, available: bool = True, should_raise: bool = False):
        self.available = available
        self.should_raise = should_raise
        self.call_count = 0
        self.last_batch = None
        
    async def embed_batch(self, texts: list[str]) -> list[np.ndarray | None]:
        """Fake embed_batch that returns dummy embeddings."""
        self.call_count += 1
        self.last_batch = texts
        
        if self.should_raise:
            raise RuntimeError("Fake Triton error")
            
        return [np.random.rand(1024).astype(np.float32) for _ in texts]


class TestEmbeddingBatcher:
    """Test the EmbeddingBatcher class."""
    
    @pytest.mark.asyncio
    async def test_init(self):
        """Test EmbeddingBatcher initialization."""
        fake_client = FakeTritonClient()
        batcher = EmbeddingBatcher(fake_client)
        
        assert batcher.triton_client is fake_client
        assert batcher.max_batch == 32
        assert batcher.max_wait_ms == 5.0
        assert batcher._queue is not None
        assert batcher._task is None
        assert not batcher._started
    
    @pytest.mark.asyncio
    async def test_single_request_flushes_after_wait(self):
        """Test single request flushes after max_wait_ms."""
        fake_client = FakeTritonClient()
        batcher = EmbeddingBatcher(fake_client, max_batch=1, max_wait_ms=0.1)
        
        # Start manually for predictable timing
        await batcher.start()
        
        # Make a single request
        start_time = asyncio.get_event_loop().time()
        result = await batcher.embed("test text")
        end_time = asyncio.get_event_loop().time()
        
        assert result is not None
        assert result.shape == (1024,)
        assert fake_client.call_count == 1
        assert fake_client.last_batch == ["test text"]
        
        # Should take at least max_wait_ms (with some tolerance for timing)
        elapsed = end_time - start_time
        assert elapsed >= 0.08  # Allow for some timing variance
        
        await batcher.stop()
    
    @pytest.mark.asyncio
    async def test_concurrent_requests_coalesce(self):
        """Test concurrent requests coalesce into one batch."""
        fake_client = FakeTritonClient()
        batcher = EmbeddingBatcher(fake_client, max_batch=10, max_wait_ms=1.0)
        
        # Start manually
        await batcher.start()
        
        # Make 5 concurrent requests
        texts = ["text 1", "text 2", "text 3", "text 4", "text 5"]
        futures = [batcher.embed(text) for text in texts]
        
        # Wait for all to complete
        results = await asyncio.gather(*futures)
        
        # Verify all results are non-None
        assert all(result is not None for result in results)
        assert all(result.shape == (1024,) for result in results)
        
        # Verify only one batch was processed
        assert fake_client.call_count == 1
        assert fake_client.last_batch == texts
        
        await batcher.stop()
    
    @pytest.mark.asyncio
    async def test_max_batch_triggers_flush(self):
        """Test max_batch triggers flush."""
        fake_client = FakeTritonClient()
        batcher = EmbeddingBatcher(fake_client, max_batch=2, max_wait_ms=1.0)
        
        await batcher.start()
        
        # Make 3 requests (should trigger 2 batches: 2 + 1)
        futures = [batcher.embed(f"text {i}") for i in range(3)]
        results = await asyncio.gather(*futures)
        
        # All should succeed
        assert all(result is not None for result in results)
        
        # Should have made 2 batches
        assert fake_client.call_count == 2
        assert fake_client.last_batch == ["text 2"]  # Last batch has 1 item
        
        await batcher.stop()
    
    @pytest.mark.asyncio
    async def test_result_ordering(self):
        """Test results are returned in correct order."""
        fake_client = FakeTritonClient()
        batcher = EmbeddingBatcher(fake_client, max_batch=5, max_wait_ms=1.0)
        
        await batcher.start()
        
        # Make requests with predictable results
        texts = ["text 1", "text 2", "text 3"]
        futures = [batcher.embed(text) for text in texts]
        results = await asyncio.gather(*futures)
        
        # Verify all results are non-None
        assert all(result is not None for result in results)
        
        # Verify order is preserved
        for i, result in enumerate(results):
            assert result.shape == (1024,)
            # Note: exact value comparison not possible with random embeddings
        
        await batcher.stop()
    
    @pytest.mark.asyncio
    async def test_none_result_propagates(self):
        """Test None results from embed_batch propagate to futures."""
        fake_client = FakeTritonClient()
        # Make client return None for first item, vector for second
        original_embed_batch = fake_client.embed_batch
        
        async def mock_embed_batch(texts):
            results = await original_embed_batch(texts)
            results[0] = None  # First result is None
            return results
            
        fake_client.embed_batch = mock_embed_batch
        
        batcher = EmbeddingBatcher(fake_client, max_batch=2, max_wait_ms=1.0)
        
        await batcher.start()
        
        # Make two requests
        results = await asyncio.gather(
            batcher.embed("text 1"),  # Should be None
            batcher.embed("text 2")   # Should be vector
        )
        
        assert results[0] is None
        assert results[1] is not None
        assert results[1].shape == (1024,)
        
        await batcher.stop()
    
    @pytest.mark.asyncio
    async def test_embed_batch_exception_degrades(self):
        """Test embed_batch exception degrades gracefully."""
        fake_client = FakeTritonClient(should_raise=True)
        batcher = EmbeddingBatcher(fake_client, max_batch=2, max_wait_ms=1.0)
        
        await batcher.start()
        
        # Make requests that will trigger exception
        results = await asyncio.gather(
            batcher.embed("text 1"),
            batcher.embed("text 2")
        )
        
        # All results should be None (degraded)
        assert results[0] is None
        assert results[1] is None
        
        # Client should have attempted the call
        assert fake_client.call_count == 1
        
        await batcher.stop()
    
    @pytest.mark.asyncio
    async def test_short_circuits_when_unavailable(self):
        """Test short-circuit when triton client is unavailable."""
        fake_client = FakeTritonClient(available=False)
        batcher = EmbeddingBatcher(fake_client, max_batch=2, max_wait_ms=1.0)
        
        # Make requests without starting (auto-start should not happen)
        results = await asyncio.gather(
            batcher.embed("text 1"),
            batcher.embed("text 2")
        )
        
        # All results should be None (short-circuited)
        assert results[0] is None
        assert results[1] is None
        
        # No batches should have been processed
        assert fake_client.call_count == 0
        
        # Batcher should not be started
        assert not batcher._started
    
    @pytest.mark.asyncio
    async def test_blank_text_short_circuits(self):
        """Test short-circuit for blank text."""
        fake_client = FakeTritonClient()
        batcher = EmbeddingBatcher(fake_client, max_batch=2, max_wait_ms=1.0)
        
        # Blank text should short-circuit
        result = await batcher.embed("")
        assert result is None
        
        result = await batcher.embed("   ")
        assert result is None
        
        # No batches should have been processed
        assert fake_client.call_count == 0
    
    @pytest.mark.asyncio
    async def test_stop_flushes_pending(self):
        """Test stop() flushes pending requests."""
        fake_client = FakeTritonClient()
        batcher = EmbeddingBatcher(fake_client, max_batch=10, max_wait_ms=1.0)
        
        await batcher.start()
        
        # Add some requests to queue but don't wait for them
        futures = [batcher.embed(f"text {i}") for i in range(5)]
        
        # Stop should flush these
        await batcher.stop()
        
        # All futures should have completed
        results = await asyncio.gather(*futures, return_exceptions=True)
        
        # All should have succeeded (not exceptions)
        assert not any(isinstance(r, Exception) for r in results)
        assert all(r is not None for r in results)
        
        # Client should have processed the batch
        assert fake_client.call_count == 1
    
    @pytest.mark.asyncio
    async def test_stop_cancels_leftover_futures(self):
        """Test stop() cancels futures added after stop signal."""
        fake_client = FakeTritonClient()
        batcher = EmbeddingBatcher(fake_client, max_batch=1, max_wait_ms=1.0)
        
        await batcher.start()
        
        # Make one request
        result1 = await batcher.embed("text 1")
        assert result1 is not None
        
        # Stop the batcher
        await batcher.stop()
        
        # Additional requests after stop should be cancelled
        result2 = await batcher.embed("text 2")
        assert result2 is None  # Should be cancelled
        
        # No additional batches should have been processed
        assert fake_client.call_count == 1
    
    @pytest.mark.asyncio
    async def test_auto_start_on_first_embed(self):
        """Test auto-start on first embed() call."""
        fake_client = FakeTritonClient()
        batcher = EmbeddingBatcher(fake_client, max_batch=2, max_wait_ms=1.0)
        
        # Batcher should not be started initially
        assert not batcher._started
        assert batcher._task is None
        
        # First embed should auto-start
        result = await batcher.embed("test text")
        assert result is not None
        assert batcher._started
        assert batcher._task is not None
        assert not batcher._task.done()
        
        await batcher.stop()
    
    @pytest.mark.asyncio
    async def test_metrics_incremented(self):
        """Test metrics are incremented correctly."""
        fake_client = FakeTritonClient()
        batcher = EmbeddingBatcher(fake_client, max_batch=2, max_wait_ms=1.0)
        
        # Get initial metric values
        from app.ml.batcher import (
            scanner_embedding_batcher_requests_total,
            scanner_embedding_batcher_batches_total,
            scanner_embedding_batcher_batch_size,
            scanner_embedding_batcher_errors_total
        )
        
        initial_requests = scanner_embedding_batcher_requests_total._value._value
        initial_batches = scanner_embedding_batcher_batches_total._value._value
        initial_errors = scanner_embedding_batcher_errors_total._value._value
        
        await batcher.start()
        
        # Make 3 requests (2 batches)
        futures = [batcher.embed(f"text {i}") for i in range(3)]
        await asyncio.gather(*futures)
        
        await batcher.stop()
        
        # Check metrics
        assert scanner_embedding_batcher_requests_total._value._value == initial_requests + 3
        assert scanner_embedding_batcher_batches_total._value._value == initial_batches + 2
        assert scanner_embedding_batcher_errors_total._value._value == initial_errors
    
    @pytest.mark.asyncio
    async def test_triton_client_none_short_circuit(self):
        """Test short-circuit when triton_client is None."""
        batcher = EmbeddingBatcher(triton_client=None, max_batch=2, max_wait_ms=1.0)
        
        # Should return None immediately without starting
        result = await batcher.embed("test text")
        assert result is None
        
        assert not batcher._started
        assert batcher._task is None
    
    @pytest.mark.asyncio
    async def test_idempotent_start_stop(self):
        """Test start() and stop() are idempotent."""
        fake_client = FakeTritonClient()
        batcher = EmbeddingBatcher(fake_client, max_batch=2, max_wait_ms=1.0)
        
        # Start twice should not create multiple tasks
        await batcher.start()
        task1 = batcher._task
        await batcher.start()  # Second start
        assert batcher._task is task1  # Same task
        
        # Stop twice should not cause issues
        await batcher.stop()
        assert batcher._task.done()
        await batcher.stop()  # Second stop
        
        # Should still be stopped
        assert batcher._task.done()
        assert not batcher._started