"""Modular detectors for the streaming and two-tier pipeline."""

from .toxicity import ToxicityDetector, ToxicityResult, get_shared_toxicity_detector, set_shared_toxicity_detector
from .streaming_rules import match_on_buffer, DEFAULT_PREV_TAIL_SIZE

__all__ = [
    "ToxicityDetector",
    "ToxicityResult",
    "get_shared_toxicity_detector",
    "set_shared_toxicity_detector",
    "match_on_buffer",
    "DEFAULT_PREV_TAIL_SIZE",
]
