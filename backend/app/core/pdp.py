from __future__ import annotations

import hashlib

from app.core.rules.base import RuleMatch, Verdict
from app.core.rules.loader import RuleSet
from app.config import settings
from app.core.metrics import scanner_rules_matched_total


# Global RuleSet instance (singleton pattern)
rule_set = RuleSet()


def scan_text(text: str) -> tuple[list[RuleMatch], str]:
    """Scan text using the RuleSet for all enabled rules."""
    all_matches = rule_set.match(text)
    prompt_hash = hashlib.sha256(text.encode()).hexdigest()
    
    # Increment rules matched counter for each matched rule
    for match in all_matches:
        scanner_rules_matched_total.labels(rule_id=match.rule_id).inc()
    
    return all_matches, prompt_hash


def decide(matches: list[RuleMatch]) -> Verdict:
    if not matches:
        return Verdict(action="allow", reason="No rules matched")

    block_matches = [m for m in matches if m.action == "block"]
    log_only_matches = [m for m in matches if m.action == "log_only"]
    high_severity = [
        m for m in matches if m.severity in settings.pdp_block_on_severity
    ]

    # If any block rules match or high severity matches → BLOCK
    if block_matches or high_severity:
        rule_ids = ", ".join(m.rule_id for m in block_matches or high_severity)
        return Verdict(
            action="block",
            reason=f"Blocked by rules: {rule_ids}",
            rules_matched=matches,
        )

    # If only log_only rules match → ALLOW (but log the matches)
    if log_only_matches and not block_matches and not high_severity:
        rule_ids = ", ".join(m.rule_id for m in log_only_matches)
        return Verdict(
            action="allow",
            reason=f"Rules matched for logging: {rule_ids}",
            rules_matched=matches,
        )

    # Default case: allow (no blocking rules)
    return Verdict(action="allow", reason="Rules matched but none require blocking", rules_matched=matches)
