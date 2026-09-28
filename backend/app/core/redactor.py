from __future__ import annotations

import hashlib
import structlog
from typing import Iterable

from app.core.rules.base import RuleMatch
from app.config import settings

logger = structlog.get_logger()

_LABEL_MAP = {
    "pii_ssn_us": "SSN",
    "pii_email": "EMAIL",
    "pii_passport_ru": "PASSPORT_RU",
    "secret_aws_key": "AWS_KEY",
    "secret_jwt": "JWT",
    "secret_credit_card": "CREDIT_CARD",
}


def _label_for(rule_id: str) -> str:
    """Get label for a rule ID, with fallback to derived name."""
    if rule_id in _LABEL_MAP:
        return _LABEL_MAP[rule_id]
    
    # Fallback: strip prefix and uppercase
    if rule_id.startswith("pii_"):
        return rule_id[4:].upper().replace("-", "_")
    elif rule_id.startswith("secret_"):
        return rule_id[7:].upper().replace("-", "_")
    else:
        return rule_id.upper().replace("-", "_")


def _placeholder(text: str, start: int, end: int, label: str) -> str:
    """Generate placeholder for redacted text."""
    value = text[start:end]
    hash8 = hashlib.sha256(value.encode()).hexdigest()[:8]
    return f"<{label}_HASH_{hash8}>"


def redact(text: str, matches: Iterable[RuleMatch] | None = None) -> str:
    """Redact PII from text for audit storage.
    
    Args:
        text: Original text to redact
        matches: List of RuleMatch objects with positions of PII to redact
        
    Returns:
        Text with PII replaced by placeholders, or original text if no matches
    """
    if not settings.redact_audit_prompt or not matches:
        return text
    
    # Convert to list and filter to matches with valid positions
    matches_list = [m for m in matches if m.position and m.position[0] >= 0 and m.position[1] > m.position[0]]
    
    if not matches_list:
        return text
    
    # Sort by start position (ascending) for overlap detection
    matches_list.sort(key=lambda m: m.position[0])
    
    result = text
    applied_spans = []  # Track applied spans to avoid overlap
    
    # Apply replacements from right to left to avoid offset invalidation
    for match in reversed(matches_list):
        start, end = match.position
        if start > len(result) or end > len(result):
            continue  # Skip if position is out of bounds
        
        # Check if this span is fully contained in an already-applied span
        is_overlapped = any(
            applied_start <= start and end <= applied_end
            for applied_start, applied_end in applied_spans
        )
        
        if is_overlapped:
            continue
        
        # Get label and create placeholder
        label = _label_for(match.rule_id)
        placeholder = _placeholder(result, start, end, label)
        
        # Apply replacement
        result = result[:start] + placeholder + result[end:]
        applied_spans.append((start, end))
    
    logger.debug(
        "redactor_redacted",
        count=len(applied_spans),
        labels=[_label_for(m.rule_id) for m in matches_list]
    )
    
    return result