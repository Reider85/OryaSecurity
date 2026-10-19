from __future__ import annotations

import re
from dataclasses import dataclass


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


# Heuristic mock markers — replace with ONNX int8 classifier in Prompt 5 (EP-37.3).
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


class ToxicityDetector:
    """Lightweight toxicity detector.

    Currently a heuristic mock (word-list based) used as a placeholder until
    the real ONNX int8 model is integrated in Prompt 5 (backend/app/ml/models/toxicity_model.py).
    """

    def __init__(self, threshold: float = 0.7):
        self.threshold = threshold

    async def check(self, text: str) -> ToxicityResult:
        """Score a text chunk for toxicity.

        Args:
            text: Input text (typically one streamed chunk).

        Returns:
            ToxicityResult with score, boolean flag and confidence.
        """
        if not text or not text.strip():
            return ToxicityResult(toxicity_score=0.0, is_toxic=False, confidence=1.0)

        matches = _TOXIC_RE.findall(text)
        # Simple heuristic: score = fraction of toxic markers found, capped at 1.0.
        score = min(len(matches) / 2.0, 1.0)
        is_toxic = score > self.threshold
        confidence = 0.6 if is_toxic else 0.9  # low confidence for mock

        return ToxicityResult(
            toxicity_score=score,
            is_toxic=is_toxic,
            confidence=confidence,
        )
