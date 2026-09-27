from __future__ import annotations

import re
import hashlib

from app.core.rules.base import RuleMatch

_AWS_KEY_PATTERN = re.compile(r"\bAKIA[0-9A-Z]{16}\b")
_JWT_PATTERN = re.compile(r"\beyJ[a-zA-Z0-9_-]+\.eyJ[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+\b")


def _hash_value(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:16]


def _validate_luhn(number_str: str) -> bool:
    digits = [int(d) for d in number_str if d.isdigit()]
    if len(digits) < 13:
        return False
    checksum = 0
    reverse = digits[::-1]
    for i, d in enumerate(reverse):
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        checksum += d
    return checksum % 10 == 0


_CREDIT_CARD_PATTERN = re.compile(r"\b(?:4[0-9]{12}(?:[0-9]{3})?|5[1-5][0-9]{14}|3[47][0-9]{13})\b")


def check_secrets(text: str) -> list[RuleMatch]:
    matches: list[RuleMatch] = []

    for m in _AWS_KEY_PATTERN.finditer(text):
        matches.append(
            RuleMatch(
                rule_id="secret_aws_key",
                rule_name="AWS Access Key",
                value_hash=_hash_value(m.group()),
                position=(m.start(), m.end()),
                severity="critical",
                action="block",
            )
        )

    for m in _JWT_PATTERN.finditer(text):
        matches.append(
            RuleMatch(
                rule_id="secret_jwt",
                rule_name="JSON Web Token",
                value_hash=_hash_value(m.group()),
                position=(m.start(), m.end()),
                severity="high",
                action="block",
            )
        )

    for m in _CREDIT_CARD_PATTERN.finditer(text):
        if _validate_luhn(m.group()):
            matches.append(
                RuleMatch(
                    rule_id="secret_credit_card",
                    rule_name="Credit Card Number",
                    value_hash=_hash_value(m.group()),
                    position=(m.start(), m.end()),
                    severity="critical",
                    action="block",
                )
            )

    return matches
