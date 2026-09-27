from __future__ import annotations

import re
import hashlib

from app.core.rules.base import RuleMatch

_SSN_PATTERN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_EMAIL_PATTERN = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_PASSPORT_RU_PATTERN = re.compile(r"\b\d{4}\s?\d{6}\b")


def _hash_value(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:16]


def check_pii(text: str) -> list[RuleMatch]:
    matches: list[RuleMatch] = []

    for m in _SSN_PATTERN.finditer(text):
        matches.append(
            RuleMatch(
                rule_id="pii_ssn_us",
                rule_name="US Social Security Number",
                value_hash=_hash_value(m.group()),
                position=(m.start(), m.end()),
                severity="high",
                action="block",
            )
        )

    for m in _EMAIL_PATTERN.finditer(text):
        matches.append(
            RuleMatch(
                rule_id="pii_email",
                rule_name="Email Address",
                value_hash=_hash_value(m.group()),
                position=(m.start(), m.end()),
                severity="medium",
                action="block",
            )
        )

    for m in _PASSPORT_RU_PATTERN.finditer(text):
        matches.append(
            RuleMatch(
                rule_id="pii_passport_ru",
                rule_name="Russian Passport Number",
                value_hash=_hash_value(m.group()),
                position=(m.start(), m.end()),
                severity="high",
                action="block",
            )
        )

    return matches
