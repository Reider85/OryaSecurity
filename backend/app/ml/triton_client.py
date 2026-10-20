from __future__ import annotations

import asyncio
import hashlib
import structlog
from pathlib import Path
from typing import Optional

import numpy as np
try:
    import tritonclient.grpc as grpcclient
    from tritonclient.grpc import InferenceServerClient, InferInput, InferRequestedOutput
except ImportError:
    # Fallback for environments without tritonclient
    grpcclient = None
    InferenceServerClient = None
    InferInput = None
    InferRequestedOutput = None
from transformers import AutoTokenizer

logger = structlog.get_logger()


class TritonEmbeddingClient:
    """Triton Inference Server client for multilingual-e5 embeddings.

    Wraps gRPC communication with Triton to serve multilingual-e5-large
    embeddings with mean pooling + L2 normalization. Tokenization is done
    client-side to match the Qdrant seeding logic.

    Falls back gracefully if Triton is unavailable at startup or runtime.
    """
    def __init__(self, *args, **kwargs):
        if grpcclient is None:
            raise ImportError("tritonclient[grpc] is required for TritonEmbeddingClient. Install with: uv pip install tritonclient[grpc]")
        super().__init__(*args, **kwargs)

    def __init__(
        self,
        triton_url: str,
        model_name: str,
        tokenizer_path: str,
        embedding_dim: int = 1024,
        max_length: int = 512,
        prefix: str = "query: ",
        timeout_ms: int = 2000,
    ):
        self.triton_url = triton_url
        self.model_name = model_name
        self.tokenizer_path = Path(tokenizer_path)
        self.embedding_dim = embedding_dim
        self.max_length = max_length
        self.prefix = prefix
        self.timeout_ms = timeout_ms

        self._client: Optional[InferenceServerClient] = None
        self._tokenizer = None
        self._available = False

        try:
            self._load()
        except Exception as exc:  # noqa: BLE001 — degrade, never crash startup
            logger.warning("triton_embedding_client_init_failed", error=str(exc), fallback="disabled")

    def _load(self) -> None:
        """Initialize Triton client and tokenizer."""
        # Connect to Triton
        self._client = InferenceServerClient(url=self.triton_url, verbose=False)

        # Check Triton readiness
        if not self._client.is_server_live():
            raise RuntimeError(f"Triton server not available at {self.triton_url}")
        if not self._client.is_model_ready(model_name=self.model_name):
            raise RuntimeError(f"Model '{self.model_name}' not ready on Triton")

        # Load tokenizer
        self._tokenizer = AutoTokenizer.from_pretrained(str(self.tokenizer_path))
        self._available = True
        logger.info("triton_embedding_client_loaded", triton_url=self.triton_url, model_name=self.model_name)

    @property
    def available(self) -> bool:
        """Return True if Triton is available and model is ready."""
        if not self._available or not self._client:
            return False
        try:
            return self._client.is_server_live() and self._client.is_model_ready(model_name=self.model_name)
        except Exception:  # noqa: BLE001 — network error, assume unavailable
            return False

    def _tokenize_sync(self, text: str) -> dict[str, np.ndarray]:
        """Tokenize text and return numpy arrays for Triton."""
        prefixed = f"{self.prefix}{text}"
        inputs = self._tokenizer(
            prefixed,
            return_tensors="np",
            max_length=self.max_length,
            truncation=True,
            padding=True,
        )
        # Convert to int64 for Triton (matching config.pbtxt)
        return {
            "input_ids": inputs["input_ids"].astype(np.int64),
            "attention_mask": inputs["attention_mask"].astype(np.int64),
        }

    def _pool_and_norm(self, hidden: np.ndarray, mask: Optional[np.ndarray] = None) -> np.ndarray:
        """Apply mean pooling + L2 normalization to match existing EmbeddingClient logic."""
        if mask is not None:
            mask_expanded = np.expand_dims(mask, -1).astype(hidden.dtype)
            summed = np.sum(hidden * mask_expanded, axis=1)
            counts = np.clip(np.sum(mask_expanded, axis=1), a_min=1e-9, a_max=None)
            pooled = summed / counts
        else:
            pooled = np.mean(hidden, axis=1)

        # L2 normalize (e5 convention)
        norm = np.linalg.norm(pooled, axis=1, keepdims=True)
        pooled = pooled / np.clip(norm, a_min=1e-9, a_max=None)
        return pooled[0].astype(np.float32)

    async def embed(self, text: str) -> Optional[np.ndarray]:
        """Embed a single text. Returns None when Triton is unavailable."""
        if not self._available or not text or not text.strip():
            return None

        try:
            # Tokenize in executor to avoid blocking
            loop = asyncio.get_running_loop()
            tokenized = await loop.run_in_executor(None, self._tokenize_sync, text)

            # Prepare Triton inference inputs
            inputs = [
                InferInput(
                    name="input_ids",
                    shape=tokenized["input_ids"].shape,
                    datatype="INT64",
                    data=tokenized["input_ids"].tobytes(),
                ),
                InferInput(
                    name="attention_mask",
                    shape=tokenized["attention_mask"].shape,
                    datatype="INT64",
                    data=tokenized["attention_mask"].tobytes(),
                ),
            ]

            outputs = [InferRequestedOutput("last_hidden_state")]

            # Run inference
            results = self._client.infer(
                model_name=self.model_name,
                inputs=inputs,
                outputs=outputs,
                client_timeout=self.timeout_ms,
            )

            # Process output
            hidden = results.as_numpy("last_hidden_state")
            mask = tokenized["attention_mask"]
            return self._pool_and_norm(hidden, mask)

        except Exception as exc:  # noqa: BLE001 — degrade gracefully
            logger.warning("triton_embedding_failed", error=str(exc), text_hash=hashlib.sha256(text.encode()).hexdigest()[:8])
            return None

    async def embed_batch(self, texts: list[str]) -> list[Optional[np.ndarray]]:
        """Embed multiple texts in a single Triton batch request."""
        if not self._available or not texts:
            return [None] * len(texts)

        try:
            # Tokenize all texts
            loop = asyncio.get_running_loop()
            tokenized_list = await loop.run_in_executor(None, lambda: list([self._tokenize_sync, text] for text in texts))

            # Batch them (simple batching, no padding optimization)
            batch_size = len(texts)
            max_seq = max(t["input_ids"].shape[1] for t in tokenized_list)
            
            batch_inputs = {
                "input_ids": np.zeros((batch_size, max_seq), dtype=np.int64),
                "attention_mask": np.zeros((batch_size, max_seq), dtype=np.int64),
            }

            for i, tokenized in enumerate(tokenized_list):
                seq_len = tokenized["input_ids"].shape[1]
                batch_inputs["input_ids"][i, :seq_len] = tokenized["input_ids"][0, :seq_len]
                batch_inputs["attention_mask"][i, :seq_len] = tokenized["attention_mask"][0, :seq_len]

            # Prepare Triton inputs
            inputs = [
                InferInput(
                    name="input_ids",
                    shape=batch_inputs["input_ids"].shape,
                    datatype="INT64",
                    data=batch_inputs["input_ids"].tobytes(),
                ),
                InferInput(
                    name="attention_mask",
                    shape=batch_inputs["attention_mask"].shape,
                    datatype="INT64",
                    data=batch_inputs["attention_mask"].tobytes(),
                ),
            ]

            outputs = [InferRequestedOutput("last_hidden_state")]

            # Run batch inference
            results = self._client.infer(
                model_name=self.model_name,
                inputs=inputs,
                outputs=outputs,
                client_timeout=self.timeout_ms,
            )

            # Process batch output
            hidden = results.as_numpy("last_hidden_state")
            mask = batch_inputs["attention_mask"]
            
            # Apply pooling per sequence
            embeddings = []
            for i in range(batch_size):
                seq_mask = mask[i]
                seq_hidden = hidden[i:i+1]  # Keep batch dim
                embedding = self._pool_and_norm(seq_hidden, seq_mask)
                embeddings.append(embedding)

            return embeddings

        except Exception as exc:  # noqa: BLE001 — degrade gracefully
            logger.warning("triton_batch_embedding_failed", error=str(exc))
            return [None] * len(texts)


# Global singleton
_shared_triton_embedding_client: Optional[TritonEmbeddingClient] = None


def get_shared_triton_embedding_client() -> Optional[TritonEmbeddingClient]:
    return _shared_triton_embedding_client


def set_shared_triton_embedding_client(client: Optional[TritonEmbeddingClient]) -> None:
    global _shared_triton_embedding_client
    _shared_triton_embedding_client = client