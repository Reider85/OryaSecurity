from __future__ import annotations

import asyncio
import structlog
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
import onnxruntime as ort
from transformers import AutoTokenizer

logger = structlog.get_logger()

DEFAULT_CLASSES = ["benign", "prompt_injection", "jailbreak", "suspicious"]


@dataclass
class ClassificationResult:
    """Result of a full-generation ML classification."""

    top_class: str
    confidence: float
    all_classes: dict[str, float] = field(default_factory=dict)

    @property
    def is_suspicious(self) -> bool:
        return self.top_class != "benign"

    def to_dict(self) -> dict:
        return {
            "top_class": self.top_class,
            "confidence": self.confidence,
            "all_classes": self.all_classes,
        }


class PromptClassifier:
    """ONNX prompt-injection classifier (DeBERTa-v3-small, CPU int8).

    Runs softmax over 4 classes: benign, prompt_injection, jailbreak,
    suspicious. Unavailable (graceful) when model/tokenizer files are absent.
    """

    def __init__(
        self,
        model_path: str,
        tokenizer_path: str,
        classes: Optional[list[str]] = None,
        max_length: int = 512,
        flag_threshold: float = 0.85,
    ):
        self.model_path = Path(model_path)
        self.tokenizer_path = Path(tokenizer_path)
        self.classes = classes or list(DEFAULT_CLASSES)
        self.max_length = max_length
        self.flag_threshold = flag_threshold

        self._session: Optional[ort.InferenceSession] = None
        self._tokenizer = None
        self._available = False

        try:
            self._load()
        except FileNotFoundError as exc:
            logger.warning("classifier_model_missing", error=str(exc), fallback="disabled")
        except Exception as exc:  # noqa: BLE001 — degrade, never crash startup
            logger.warning("classifier_model_load_failed", error=str(exc), fallback="disabled")

    def _load(self) -> None:
        if not self.model_path.exists():
            raise FileNotFoundError(f"Classifier model not found: {self.model_path}")
        if not self.tokenizer_path.exists():
            raise FileNotFoundError(f"Classifier tokenizer not found: {self.tokenizer_path}")

        self._session = ort.InferenceSession(
            str(self.model_path),
            providers=["CPUExecutionProvider"],
        )
        self._tokenizer = AutoTokenizer.from_pretrained(str(self.tokenizer_path))
        self._available = True
        logger.info("classifier_model_loaded", model_path=str(self.model_path))

    @property
    def available(self) -> bool:
        return self._available

    def _classify_sync(self, text: str) -> ClassificationResult:
        """Blocking inference — run via executor."""
        if self._session is None or self._tokenizer is None:
            raise RuntimeError("Classifier model not loaded")

        inputs = self._tokenizer(
            text,
            return_tensors="np",
            max_length=self.max_length,
            truncation=True,
            padding="max_length",
        )
        inputs = {k: v.astype(np.int64) for k, v in inputs.items()}

        outputs = self._session.run(None, inputs)
        logits = outputs[0][0]

        exp = np.exp(logits - np.max(logits))
        probs = exp / exp.sum()

        all_classes = {
            name: float(probs[i]) for i, name in enumerate(self.classes)
        }
        top_idx = int(np.argmax(probs))
        return ClassificationResult(
            top_class=self.classes[top_idx],
            confidence=float(probs[top_idx]),
            all_classes=all_classes,
        )

    async def classify(self, text: str) -> Optional[ClassificationResult]:
        """Classify full generation text. Returns None when unavailable."""
        if not self._available:
            return None
        if not text or not text.strip():
            return None
        try:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(None, self._classify_sync, text)
        except Exception as exc:  # noqa: BLE001 — degrade gracefully
            logger.warning("classifier_failed", error=str(exc))
            return None

    def should_flag(self, result: ClassificationResult) -> bool:
        """Flag non-benign classifications above the confidence threshold."""
        return result.is_suspicious and result.confidence > self.flag_threshold


# Global singleton
_shared_classifier: Optional[PromptClassifier] = None


def get_shared_classifier() -> Optional[PromptClassifier]:
    return _shared_classifier


def set_shared_classifier(classifier: Optional[PromptClassifier]) -> None:
    global _shared_classifier
    _shared_classifier = classifier
