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
from .vault import (
    VAULT_UNAVAILABLE_REASON,
    VaultCircuitBreaker,
    VaultUnavailableFailClosed,
    get_vault_breaker,
    set_vault_breaker,
)

__all__ = [
    "MLUnavailableFailClosed",
    "MLClassifierCircuitBreaker",
    "get_ml_classifier_breaker",
    "set_ml_classifier_breaker",
    "VAULT_UNAVAILABLE_REASON",
    "VaultCircuitBreaker",
    "VaultUnavailableFailClosed",
    "get_vault_breaker",
    "set_vault_breaker",
    "VectorDBCircuitBreaker",
    "get_vector_db_breaker",
    "set_vector_db_breaker",
]
