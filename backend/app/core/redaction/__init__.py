"""PII redaction components (EP-13, ADR-0007).

``vault_client`` — async Vault HTTP client for tokenization (Prompt 31).
``redactor`` / ``detokenizer`` arrive with Prompts 32-33.
"""

from app.core.redaction.vault_client import (
    VaultClient,
    VaultHealth,
    VaultResult,
    close_vault_client,
    get_vault_client,
    init_vault_client,
    set_vault_client,
)

__all__ = [
    "VaultClient",
    "VaultHealth",
    "VaultResult",
    "close_vault_client",
    "get_vault_client",
    "init_vault_client",
    "set_vault_client",
]
