from __future__ import annotations

import asyncio
import time
from typing import Optional

import numpy as np
import structlog
from prometheus_client import Counter, Histogram

logger = structlog.get_logger()

# Metrics
scanner_embedding_batcher_requests_total = Counter(
    "scanner_embedding_batcher_requests_total",
    "Total embedding batcher requests"
)
scanner_embedding_batcher_batches_total = Counter(
    "scanner_embedding_batcher_batches_total",
    "Total embedding batches processed"
)
scanner_embedding_batcher_batch_size = Histogram(
    "scanner_embedding_batcher_batch_size",
    "Batch size distribution",
    buckets=[1, 2, 4, 8, 16, 32]
)
scanner_embedding_batcher_inference_seconds = Histogram(
    "scanner_embedding_batcher_inference_seconds",
    "Embedding batch inference latency"
)
scanner_embedding_batcher_errors_total = Counter(
    "scanner_embedding_batcher_errors_total",
    "Total embedding batcher errors"
)

# Sentinel for stopping the batch loop
_STOP = object()


class EmbeddingBatcher:
    """Micro-batcher for embedding inference requests.
    
    Collects individual text embedding requests into batches and processes them
    through the underlying TritonEmbeddingClient.embed_batch() method. Uses futures
    to return results in the same order as requests were made.
    
    Flushes when either:
    - Maximum batch size (32) is reached
    - Maximum wait time (5ms) elapses after first request in batch
    
    Args:
        triton_client: TritonEmbeddingClient instance
        max_batch: Maximum number of requests per batch (default: 32)
        max_wait_ms: Maximum wait time in milliseconds before flushing (default: 5.0)
    """
    
    def __init__(
        self,
        triton_client,
        max_batch: int = 32,
        max_wait_ms: float = 5.0
    ):
        self.triton_client = triton_client
        self.max_batch = max_batch
        self.max_wait_ms = max_wait_ms
        
        self._queue: asyncio.Queue = asyncio.Queue()
        self._task: Optional[asyncio.Task] = None
        self._started = False
        
    async def start(self) -> None:
        """Start the background batch processing loop. Idempotent."""
        if self._task is not None and not self._task.done():
            return  # Already started
            
        self._task = asyncio.create_task(self._batch_loop())
        self._started = True
        logger.info("embedding_batcher_started", max_batch=self.max_batch, max_wait_ms=self.max_wait_ms)
    
    async def stop(self) -> None:
        """Stop the batch processing loop gracefully.
        
        Puts a sentinel value to signal the loop to stop, waits for it to complete,
        and cancels any remaining futures.
        """
        if self._task is None or self._task.done():
            return
            
        # Signal the loop to stop
        await self._queue.put((_STOP, asyncio.Future()))
        await self._task
        
        # Cancel any remaining futures in the queue
        while not self._queue.empty():
            try:
                _, future = self._queue.get_nowait()
                if not future.done():
                    future.set_exception(asyncio.CancelledError("embedding_batcher_stopped"))
            except asyncio.QueueEmpty:
                break
                
        self._started = False
        logger.info("embedding_batcher_stopped")
    
    async def embed(self, text: str) -> Optional[np.ndarray]:
        """Embed a single text using micro-batching.
        
        Args:
            text: Text to embed
            
        Returns:
            Embedding vector (1024-dim np.float32) or None if:
            - Text is empty/blank
            - Triton client is None or unavailable
            - Batcher is stopped
            - Embedding fails (degraded gracefully)
        """
        # Short-circuit: no client, unavailable, or empty text
        if (
            self.triton_client is None or 
            not getattr(self.triton_client, "available", False) or
            not text or not text.strip()
        ):
            return None
            
        # Auto-start on first request if not started
        if not self._started:
            await self.start()
            
        # Create future and put in queue
        future = asyncio.get_running_loop().create_future()
        await self._queue.put((text, future))
        
        # Wait for result
        try:
            result = await future
            return result
        except asyncio.CancelledError:
            # Future was cancelled (batcher stopped)
            return None
    
    async def _collect_batch(self) -> Optional[list[tuple[str, asyncio.Future]]]:
        """Collect a batch of requests from the queue.
        
        Returns:
            List of (text, future) tuples or None if stop signal received.
        """
        batch = []
        
        # Wait for first item (sentinel check)
        first_item = await self._queue.get()
        if first_item[0] is _STOP:
            # Stop signal received, return None to exit loop
            return None
            
        batch.append(first_item)
        
        # Set deadline for batch collection
        deadline = time.monotonic() + self.max_wait_ms / 1000.0
        
        # Collect more items up to max_batch or deadline
        while len(batch) < self.max_batch:
            timeout = deadline - time.monotonic()
            if timeout <= 0:
                break
                
            try:
                item = await asyncio.wait_for(self._queue.get(), timeout)
                if item[0] is _STOP:
                    # Stop signal received, add current batch and return with stop flag
                    return batch
                batch.append(item)
            except asyncio.TimeoutError:
                # Deadline reached, return current batch
                break
                
        return batch
    
    async def _batch_loop(self) -> None:
        """Background loop that collects batches and processes them."""
        logger.debug("embedding_batcher_loop_started")
        
        while True:
            batch = await self._collect_batch()
            if batch is None:
                break
                
            # If we got a batch with stop signal, process it then exit
            if batch and batch[-1][0] is _STOP:
                # Remove the sentinel from batch
                batch_without_stop = batch[:-1]
                if batch_without_stop:
                    await self._process_batch(batch_without_stop)
                break
            
            if batch:
                await self._process_batch(batch)
        
        logger.debug("embedding_batcher_loop_stopped")
    
    async def _process_batch(self, batch: list[tuple[str, asyncio.Future]]) -> None:
        """Process a batch of embedding requests.
        
        Args:
            batch: List of (text, future) tuples
        """
        start_time = time.monotonic()
        texts = [text for text, _ in batch]
        
        try:
            # Call embed_batch on triton client
            results = await self.triton_client.embed_batch(texts)
            
            # Record metrics
            latency = time.monotonic() - start_time
            scanner_embedding_batcher_requests_total.inc(len(texts))
            scanner_embedding_batcher_batches_total.inc()
            scanner_embedding_batcher_batch_size.observe(len(texts))
            scanner_embedding_batcher_inference_seconds.observe(latency)
            
            # Distribute results to futures
            for i, (_, future) in enumerate(batch):
                if i < len(results):
                    result = results[i]
                    if result is not None and not future.done():
                        future.set_result(result)
                    elif result is None and not future.done():
                        # Triton client degraded gracefully, propagate None
                        future.set_result(result)
                elif not future.done():
                    # Shouldn't happen, but future didn't get a result
                    future.set_result(None)
                    
            logger.debug(
                "embedding_batch_flushed",
                batch_size=len(texts),
                latency_ms=round(latency * 1000, 2),
                results_with_none=sum(1 for r in results if r is None)
            )
            
        except Exception as exc:
            # Log error and set None on all futures
            logger.warning(
                "embedding_batch_failed",
                error=str(exc),
                batch_size=len(texts)
            )
            scanner_embedding_batcher_errors_total.inc()
            
            for _, future in batch:
                if not future.done():
                    future.set_result(None)  # Degrade gracefully


# Global singleton
_shared_embedding_batcher: Optional[EmbeddingBatcher] = None


def get_shared_embedding_batcher() -> Optional[EmbeddingBatcher]:
    """Get the shared embedding batcher instance."""
    return _shared_embedding_batcher


def set_shared_embedding_batcher(batcher: Optional[EmbeddingBatcher]) -> None:
    """Set the shared embedding batcher instance."""
    global _shared_embedding_batcher
    _shared_embedding_batcher = batcher