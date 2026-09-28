from __future__ import annotations

import hashlib
import uuid
from typing import Any

import pytest

from app.core.redactor import redact, _label_for, _placeholder
from app.core.rules.base import RuleMatch


class TestRedact:
    def test_returns_unchanged_when_matches_none(self) -> None:
        text = "Hello world"
        assert redact(text, None) == text
        assert redact(text, []) == text

    def test_returns_unchanged_when_no_redaction_enabled(self) -> None:
        from app.config import settings
        original_value = settings.redact_audit_prompt
        settings.redact_audit_prompt = False
        
        try:
            text = "SSN: 123-45-6789"
            matches = [RuleMatch(
                rule_id="pii_ssn_us",
                rule_name="SSN",
                value_hash="abc",
                position=(5, 14),
                severity="high",
                action="block"
            )]
            assert redact(text, matches) == text
        finally:
            settings.redact_audit_prompt = original_value

    def test_ssn_replaced(self) -> None:
        text = "SSN is 123-45-6789 here"
        matches = [RuleMatch(
            rule_id="pii_ssn_us",
            rule_name="SSN",
            value_hash="abc",
            position=(7, 17),
            severity="high",
            action="block"
        )]
        result = redact(text, matches)
        assert "<SSN_HASH_" in result
        assert "123-45-6789" not in result

    def test_email_replaced(self) -> None:
        text = "Email: test@example.com"
        matches = [RuleMatch(
            rule_id="pii_email",
            rule_name="Email",
            value_hash="abc",
            position=(7, 20),
            severity="medium",
            action="block"
        )]
        result = redact(text, matches)
        assert "<EMAIL_HASH_" in result
        assert "test@example.com" not in result

    def test_passport_ru_replaced(self) -> None:
        text = "Passport: 1234 567890"
        matches = [RuleMatch(
            rule_id="pii_passport_ru",
            rule_name="Passport",
            value_hash="abc",
            position=(9, 20),
            severity="high",
            action="block"
        )]
        result = redact(text, matches)
        assert "<PASSPORT_RU_HASH_" in result
        assert "1234 567890" not in result

    def test_aws_key_replaced(self) -> None:
        text = "Key: AKIAIOSFODNN7EXAMPLE"
        matches = [RuleMatch(
            rule_id="secret_aws_key",
            rule_name="AWS Key",
            value_hash="abc",
            position=(6, 26),
            severity="critical",
            action="block"
        )]
        result = redact(text, matches)
        assert "<AWS_KEY_HASH_" in result
        assert "AKIAIOSFODNN7EXAMPLE" not in result

    def test_jwt_replaced(self) -> None:
        jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
        text = f"Token: {jwt}"
        matches = [RuleMatch(
            rule_id="secret_jwt",
            rule_name="JWT",
            value_hash="abc",
            position=(7, 7 + len(jwt)),
            severity="high",
            action="block"
        )]
        result = redact(text, matches)
        assert "<JWT_HASH_" in result
        assert jwt not in result

    def test_credit_card_replaced(self) -> None:
        text = "Card: 4111111111111111"
        matches = [RuleMatch(
            rule_id="secret_credit_card",
            rule_name="Credit Card",
            value_hash="abc",
            position=(6, 22),
            severity="critical",
            action="block"
        )]
        result = redact(text, matches)
        assert "<CREDIT_CARD_HASH_" in result
        assert "4111111111111111" not in result

    def test_multiple_replacements(self) -> None:
        text = "SSN: 123-45-6789 and email test@example.com"
        matches = [
            RuleMatch(
                rule_id="pii_ssn_us",
                rule_name="SSN",
                value_hash="abc",
                position=(5, 14),
                severity="high",
                action="block"
            ),
            RuleMatch(
                rule_id="pii_email",
                rule_name="Email",
                value_hash="def",
                position=(25, 38),
                severity="medium",
                action="block"
            )
        ]
        result = redact(text, matches)
        assert "<SSN_HASH_" in result
        assert "<EMAIL_HASH_" in result
        assert "123-45-6789" not in result
        assert "test@example.com" not in result

    def test_overlapping_matches(self) -> None:
        text = "1234567890"
        matches = [
            RuleMatch(
                rule_id="pii_ssn_us",
                rule_name="SSN",
                value_hash="abc",
                position=(0, 5),
                severity="high",
                action="block"
            ),
            RuleMatch(
                rule_id="pii_email",
                rule_name="Email",
                value_hash="def",
                position=(3, 8),
                severity="medium",
                action="block"
            )
        ]
        result = redact(text, matches)
        # Only one placeholder should appear
        assert result.count("_HASH_") == 1
        assert "12345" not in result or "45678" not in result

    def test_position_accuracy(self) -> None:
        text = "Start SSN: 123-45-6789 middle End"
        matches = [RuleMatch(
            rule_id="pii_ssn_us",
            rule_name="SSN",
            value_hash="abc",
            position=(11, 21),
            severity="high",
            action="block"
        )]
        result = redact(text, matches)
        assert result.startswith("Start ")
        assert result.endswith(" middle End")
        assert "<SSN_HASH_" in result

    def test_non_block_action_still_redacted(self) -> None:
        text = "Email: test@example.com"
        matches = [RuleMatch(
            rule_id="pii_email",
            rule_name="Email",
            value_hash="abc",
            position=(7, 20),
            severity="medium",
            action="log_only"
        )]
        result = redact(text, matches)
        assert "<EMAIL_HASH_" in result
        assert "test@example.com" not in result

    def test_original_text_not_leaked(self) -> None:
        original = "SSN: 123-45-6789"
        matches = [RuleMatch(
            rule_id="pii_ssn_us",
            rule_name="SSN",
            value_hash="abc",
            position=(5, 14),
            severity="high",
            action="block"
        )]
        result = redact(original, matches)
        assert "123-45-6789" not in result
        assert original != result

    def test_hash_consistency(self) -> None:
        text = "123-45-6789"
        matches = [RuleMatch(
            rule_id="pii_ssn_us",
            rule_name="SSN",
            value_hash="abc",
            position=(0, len(text)),
            severity="high",
            action="block"
        )]
        result = redact(text, matches)
        # Extract hash from placeholder
        import re
        match = re.search(r"<SSN_HASH_([a-f0-9]+)>", result)
        assert match
        hash8 = match.group(1)
        expected = hashlib.sha256(text.encode()).hexdigest()[:8]
        assert hash8 == expected

    def test_empty_text(self) -> None:
        assert redact("", []) == ""
        assert redact("", [RuleMatch("test", "test", "abc", (0, 0), "low", "block")]) == ""

    def test_unknown_rule_id_derived_label(self) -> None:
        text = "Custom PII: 12345"
        matches = [RuleMatch(
            rule_id="pii_custom_ssn",
            rule_name="Custom SSN",
            value_hash="abc",
            position=(13, 18),
            severity="high",
            action="block"
        )]
        result = redact(text, matches)
        assert "<CUSTOM_SSN_HASH_" in result
        assert "12345" not in result

    def test_long_text(self) -> None:
        # Create a long text with PII at the end
        base_text = "This is a very long text with no PII in it. " * 100
        pii_text = "SSN: 123-45-6789"
        text = base_text + pii_text
        
        matches = [RuleMatch(
            rule_id="pii_ssn_us",
            rule_name="SSN",
            value_hash="abc",
            position=(len(base_text), len(text)),
            severity="high",
            action="block"
        )]
        result = redact(text, matches)
        assert "<SSN_HASH_" in result
        assert "123-45-6789" not in result
        assert base_text in result  # Original text should be preserved except PII

    def test_position_out_of_bounds(self) -> None:
        text = "Short text"
        matches = [RuleMatch(
            rule_id="pii_ssn_us",
            rule_name="SSN",
            value_hash="abc",
            position=(0, 100),  # Way out of bounds
            severity="high",
            action="block"
        )]
        result = redact(text, matches)
        # Should return original text since position is invalid
        assert result == text

    def test_partial_overlap_position(self) -> None:
        text = "1234567890"
        matches = [
            RuleMatch(
                rule_id="pii_ssn_us",
                rule_name="SSN",
                value_hash="abc",
                position=(0, 5),
                severity="high",
                action="block"
            ),
            RuleMatch(
                rule_id="pii_email",
                rule_name="Email",
                value_hash="def",
                position=(5, 10),
                severity="medium",
                action="block"
            )
        ]
        result = redact(text, matches)
        # Both should be replaced since they don't overlap
        assert result.count("_HASH_") == 2


class TestLabelFor:
    def test_known_labels(self) -> None:
        assert _label_for("pii_ssn_us") == "SSN"
        assert _label_for("pii_email") == "EMAIL"
        assert _label_for("pii_passport_ru") == "PASSPORT_RU"
        assert _label_for("secret_aws_key") == "AWS_KEY"
        assert _label_for("secret_jwt") == "JWT"
        assert _label_for("secret_credit_card") == "CREDIT_CARD"

    def test_fallback_strips_pii_prefix(self) -> None:
        assert _label_for("pii_custom_ssn") == "CUSTOM_SSN"
        assert _label_for("pii_foo_bar") == "FOO_BAR"

    def test_fallback_strips_secret_prefix(self) -> None:
        assert _label_for("secret_custom_key") == "CUSTOM_KEY"
        assert _label_for("secret_foo_bar") == "FOO_BAR"

    def test_no_prefix(self) -> None:
        assert _label_for("custom_rule") == "CUSTOM_RULE"
        assert _label_for("another_rule") == "ANOTHER_RULE"

    def test_replaces_dashes(self) -> None:
        assert _label_for("pii_foo-bar") == "FOO_BAR"
        assert _label_for("secret_foo-bar") == "FOO_BAR"


class TestPlaceholder:
    def test_placeholder_format(self) -> None:
        text = "123-45-6789"
        placeholder = _placeholder(text, 0, len(text), "SSN")
        assert placeholder.startswith("<SSN_HASH_")
        assert placeholder.endswith(">")
        hash_part = placeholder[10:-1]  # Extract hash part
        assert len(hash_part) == 8
        assert all(c in "0123456789abcdef" for c in hash_part)

    def test_hash_consistency(self) -> None:
        text = "test"
        placeholder1 = _placeholder(text, 0, len(text), "TEST")
        placeholder2 = _placeholder(text, 0, len(text), "TEST")
        assert placeholder1 == placeholder2
        # Verify it matches expected hash
        expected_hash = hashlib.sha256(text.encode()).hexdigest()[:8]
        assert placeholder1 == f"<TEST_HASH_{expected_hash}>"

    def test_different_values_different_hashes(self) -> None:
        text1 = "123-45-6789"
        text2 = "987-65-4321"
        placeholder1 = _placeholder(text1, 0, len(text1), "SSN")
        placeholder2 = _placeholder(text2, 0, len(text2), "SSN")
        assert placeholder1 != placeholder2