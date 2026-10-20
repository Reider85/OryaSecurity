from __future__ import annotations

import asyncio
import structlog
from pathlib import Path
from typing import Optional

import numpy as np
import onnxruntime as ort
from transformers import AutoTokenizer

logger = structlog.get_logger()


class EmbeddingClient:
    """ONNX embedding client (multilingual-e5, CPU int8).

    Wraps an exported ONNX graph of intfloat/multilingual-e5-large with
    mean pooling + L2 normalization. Inference runs in a worker thread to
    keep the event loop free. If model/tokenizer files are missing the
    client marks itself unavailable so callers can degrade gracefully.
    """

    def __init__(
        self,
        model_path: str,
        tokenizer_path: str,
        embedding_dim: int = 1024,
        max_length: int = 512,
        prefix: str = "query: ",
    ):
        self.model_path = Path(model_path)
        self.tokenizer_path = Path(tokenizer_path)
        self.embedding_dim = embedding_dim
        self.max_length = max_length
        self.prefix = prefix

        self._session: Optional[ort.InferenceSession] = None
        self._tokenizer = None
        self._available = False

        try:
            self._load()
        except FileNotFoundError as exc:
            logger.warning("embedding_model_missing", error=str(exc), fallback="disabled")
        except Exception as exc:  # noqa: BLE001 — degrade, never crash startup
            logger.warning("embedding_model_load_failed", error=str(exc), fallback="disabled")

    def _load(self) -> None:
        if not self.model_path.exists():
            raise FileNotFoundError(f"Embedding model not found: {self.model_path}")
        if not self.tokenizer_path.exists():
            raise FileNotFoundError(f"Embedding tokenizer not found: {self.tokenizer_path}")

        self._session = ort.InferenceSession(
            str(self.model_path),
            providers=["CPUExecutionProvider"],
        )
        self._tokenizer = AutoTokenizer.from_pretrained(str(self.tokenizer_path))
        self._available = True
        logger.info("embedding_model_loaded", model_path=str(self.model_path))

    @property
    def available(self) -> bool:
        return self._available

    def _embed_sync(self, text: str) -> np.ndarray:
        """Blocking inference — run via executor."""
        if self._session is None or self._tokenizer is None:
            raise RuntimeError("Embedding model not loaded")

        prefixed = f"{self.prefix}{text}"
        inputs = self._tokenizer(
            prefixed,
            return_tensors="np",
            max_length=self.max_length,
            truncation=True,
            padding=True,
        )
        inputs = {k: v.astype(np.int64) for k, v in inputs.items() if k in ("input_ids", "attention_mask")}

        outputs = self._session.run(None, inputs)
        # Expected outputs: last_hidden_state [1, seq, dim], attention_mask [1, seq]
        hidden = outputs[0]
        mask = inputs.get("attention_mask")
        if mask is None and len(outputs) > 1:
            mask = outputs[1]

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
        """Embed a single text. Returns None when the model is unavailable."""
        if not self._available:
            return None
        if not text or not text.strip():
            return None
        try:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(None, self._embed_sync, text)
        except Exception as exc:  # noqa: BLE001 — degrade gracefully
            logger.warning("embedding_failed", error=str(exc))
            return None


# Global singleton
_shared_embedding_client: Optional[EmbeddingClient] = None


def get_shared_embedding_client() -> Optional[EmbeddingClient]:
    return _shared_embedding_client


def set_shared_embedding_client(client: Optional[EmbeddingClient]) -> None:
    global _shared_embedding_client
    _shared_embedding_client = client
