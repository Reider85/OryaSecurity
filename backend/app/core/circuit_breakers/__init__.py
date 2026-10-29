"""Per-component circuit breakers (EP-14 / ADR-0008)."""

from .ml_classifier import (
    MLUnavailableFailClosed,
    MLClassifierCircuitBreaker,
    get_ml_classifier_breaker,
    set_ml_classifier_breaker,
)
from .vector_db import (
    VectorDBCircuitBreaker,
    get_vector_db_breaker,
    set_vector_db_breaker,
)

__all__ = [
    "MLUnavailableFailClosed",
    "MLClassifierCircuitBreaker",
    "get_ml_classifier_breaker",
    "set_ml_classifier_breaker",
    "VectorDBCircuitBreaker",
    "get_vector_db_breaker",
    "set_vector_db_breaker",
]
