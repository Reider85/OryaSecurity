"""Streaming rule engine for chunk-based inspection with boundary detection.

Extends the standard PII/secrets rules to handle text split across chunks.
"""

from __future__ import annotations

from typing import Optional

from app.core.metrics import scanner_rules_matched_total
from app.core.rules.base import RuleMatch
from app.core.rules.loader import RuleSet
from app.core.pdp import rule_set as global_rule_set

# Number of trailing characters from the previous buffer reused for boundary detection.
DEFAULT_PREV_TAIL_SIZE = 20


def match_on_buffer(
    buffer: str,
    prev_tail: str = "",
    rule_set: Optional[RuleSet] = None,
) -> list[RuleMatch]:
    """Run PII/secrets regex rules over prev_tail+buffer, keeping only matches
    that overlap the current buffer (boundary detection).

    Args:
        buffer: Current chunk of text being inspected.
        prev_tail: Trailing characters from the previous buffer (for boundary detection).
        rule_set: Optional RuleSet to use; defaults to global rule_set from pdp.

    Returns:
        List of RuleMatch objects for matches overlapping the buffer.
        Positions are relative to the combined text (prev_tail + buffer).
        Matches entirely within prev_tail are filtered out to avoid duplicates.
    """
    if rule_set is None:
        rule_set = global_rule_set

    # Combine prev_tail and buffer for boundary detection
    combined_text = prev_tail + buffer

    # Match against all enabled rules
    all_matches = rule_set.match(combined_text)

    # Filter matches: keep only those that overlap with the current buffer
    buffer_matches = []
    for match in all_matches:
        # Keep matches that end after the prev_tail boundary
        # (i.e., matches that overlap with the current buffer)
        if match.position[1] > len(prev_tail):
            buffer_matches.append(match)

    # Increment metrics for each matched rule (consistent with scan_text behavior)
    for match in buffer_matches:
        scanner_rules_matched_total.labels(rule_id=match.rule_id).inc()

    return buffer_matches