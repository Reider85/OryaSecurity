"""Modular detectors for the streaming and two-tier pipeline."""

from .toxicity import ToxicityDetector, ToxicityResult, get_shared_toxicity_detector, set_shared_toxicity_detector
from .streaming_rules import match_on_buffer, DEFAULT_PREV_TAIL_SIZE
from .embedding import EmbeddingClient, get_shared_embedding_client, set_shared_embedding_client
from .vector_search import VectorSearcher, SearchResult, get_shared_vector_searcher, set_shared_vector_searcher
from .ml_classifier import (
    PromptClassifier,
    ClassificationResult,
    get_shared_classifier,
    set_shared_classifier,
)

__all__ = [
    "ToxicityDetector",
    "ToxicityResult",
    "get_shared_toxicity_detector",
    "set_shared_toxicity_detector",
    "match_on_buffer",
    "DEFAULT_PREV_TAIL_SIZE",
    "EmbeddingClient",
    "get_shared_embedding_client",
    "set_shared_embedding_client",
    "VectorSearcher",
    "SearchResult",
    "get_shared_vector_searcher",
    "set_shared_vector_searcher",
    "PromptClassifier",
    "ClassificationResult",
    "get_shared_classifier",
    "set_shared_classifier",
]
