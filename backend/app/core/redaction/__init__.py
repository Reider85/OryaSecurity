"""PII redaction components (EP-13, ADR-0007).

``vault_client`` — async Vault HTTP client for tokenization (Prompt 31).
``redactor`` — Vault-based PII tokenization for the outbound path (Prompt 32).
``detokenizer`` arrives with Prompt 33.
"""

from app.core.redaction.redactor import (
    PIIRedactor,
    RedactedText,
    RedactionOutcome,
    TokenInfo,
    VAULT_UNAVAILABLE_REASON,
    redact_messages_for_llm,
)
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
    "PIIRedactor",
    "RedactedText",
    "RedactionOutcome",
    "TokenInfo",
    "VAULT_UNAVAILABLE_REASON",
    "redact_messages_for_llm",
    "VaultClient",
    "VaultHealth",
    "VaultResult",
    "close_vault_client",
    "get_vault_client",
    "init_vault_client",
    "set_vault_client",
]
