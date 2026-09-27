from __future__ import annotations

import hashlib

from app.core.rules.base import RuleMatch, Verdict
from app.core.rules.loader import RuleSet
from app.config import settings


# Global RuleSet instance (singleton pattern)
rule_set = RuleSet()


def scan_text(text: str) -> tuple[list[RuleMatch], str]:
    """Scan text using the RuleSet for all enabled rules."""
    all_matches = rule_set.match(text)
    prompt_hash = hashlib.sha256(text.encode()).hexdigest()
    return all_matches, prompt_hash


def decide(matches: list[RuleMatch]) -> Verdict:
    if not matches:
        return Verdict(action="allow", reason="No rules matched")

    block_matches = [m for m in matches if m.action == "block"]
    high_severity = [
        m for m in matches if m.severity in settings.pdp_block_on_severity
    ]

    if block_matches or high_severity:
        rule_ids = ", ".join(m.rule_id for m in block_matches or high_severity)
        return Verdict(
            action="block",
            reason=f"Blocked by rules: {rule_ids}",
            rules_matched=matches,
        )

    return Verdict(action="allow", reason="Rules matched but none require blocking", rules_matched=matches)
