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
from .classifier_explainability import (
    ClassifierExplainer,
    get_shared_explainer,
    set_shared_explainer,
    explain_classification,
)

# Triton client (EP-08) - imported from ml package to avoid circular deps
from ...ml.triton_client import TritonEmbeddingClient, get_shared_triton_embedding_client, set_shared_triton_embedding_client

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
    "ClassifierExplainer",
    "get_shared_explainer",
    "set_shared_explainer",
    "explain_classification",
    # Triton client
    "TritonEmbeddingClient",
    "get_shared_triton_embedding_client", 
    "set_shared_triton_embedding_client",
]
