from __future__ import annotations

import asyncio
import re
import structlog
from dataclasses import dataclass
from typing import Optional

from app.config import settings
from app.ml.models.toxicity_model import get_toxicity_model

logger = structlog.get_logger()


@dataclass
class ToxicityResult:
    """Result of a toxicity check."""

    toxicity_score: float
    is_toxic: bool
    confidence: float

    def to_dict(self) -> dict:
        return {
            "toxicity_score": self.toxicity_score,
            "is_toxic": self.is_toxic,
            "confidence": self.confidence,
        }


# Heuristic fallback markers — used when ONNX model is not available
_TOXIC_MARKERS = [
    "kill",
    "hate",
    "idiot",
    "stupid",
    "shut up",
    "damn",
    "hell",
]

_TOXIC_RE = re.compile(
    r"\b(" + "|".join(re.escape(m) for m in _TOXIC_MARKERS) + r")\b",
    re.IGNORECASE,
)


def _heuristic_check(text: str, threshold: float = 0.7) -> ToxicityResult:
    """Heuristic toxicity check (fallback when ML model unavailable).
    
    Uses simple word matching for development/testing when ONNX model is not loaded.
    """
    if not text or not text.strip():
        return ToxicityResult(toxicity_score=0.0, is_toxic=False, confidence=1.0)

    matches = _TOXIC_RE.findall(text)
    # Simple heuristic: score = fraction of toxic markers found, capped at 1.0
    score = min(len(matches) / 2.0, 1.0)
    is_toxic = score > threshold
    confidence = 0.6 if is_toxic else 0.9  # lower confidence for heuristic

    return ToxicityResult(
        toxicity_score=score,
        is_toxic=is_toxic,
        confidence=confidence,
    )


# Global shared detector instance
_shared_detector: Optional[ToxicityDetector] = None


def get_shared_toxicity_detector() -> Optional[ToxicityDetector]:
    """Get the global shared toxicity detector instance."""
    return _shared_detector


def set_shared_toxicity_detector(detector: Optional[ToxicityDetector]) -> None:
    """Set the global shared toxicity detector instance."""
    global _shared_detector
    _shared_detector = detector


class ToxicityDetector:
    """Lightweight toxicity detector with ML and fallback support.

    Uses ONNX model when available, falls back to heuristic word matching
    when model files are missing or dependencies are not installed.
    """

    def __init__(self, threshold: float = 0.7):
        self.threshold = threshold
        self._model = get_toxicity_model()
        
        # Log mode on initialization
        if self._model is not None:
            logger.info("toxicity_detector_ml_mode")
        else:
            logger.warning("toxicity_detector_fallback_mode")

    async def check(self, text: str) -> ToxicityResult:
        """Score a text chunk for toxicity.

        Args:
            text: Input text (typically one streamed chunk, 8-32 tokens).

        Returns:
            ToxicityResult with score, boolean flag and confidence.
        """
        if not text or not text.strip():
            return ToxicityResult(toxicity_score=0.0, is_toxic=False, confidence=1.0)

        # Use ML model if available
        if self._model is not None:
            try:
                # Offload to thread to avoid blocking event loop
                loop = asyncio.get_event_loop()
                score, confidence = await loop.run_in_executor(
                    None, self._model.predict, text
                )
                is_toxic = score > self.threshold
                return ToxicityResult(
                    toxicity_score=score,
                    is_toxic=is_toxic,
                    confidence=confidence,
                )
            except Exception as e:
                # Fallback to heuristic on ML failure
                logger.warning(
                    "toxicity_model_failed",
                    error=str(e),
                    fallback="heuristic"
                )
                return _heuristic_check(text, self.threshold)

        # Fallback to heuristic when no model available
        return _heuristic_check(text, self.threshold)