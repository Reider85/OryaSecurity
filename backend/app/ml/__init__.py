"""ML models and utilities for the LLM Security Scanner."""

__version__ = "0.1.0"

# Triton client (EP-08)
from .triton_client import TritonEmbeddingClient, get_shared_triton_embedding_client, set_shared_triton_embedding_client

__all__ = [
    "TritonEmbeddingClient",
    "get_shared_triton_embedding_client", 
    "set_shared_triton_embedding_client",
]