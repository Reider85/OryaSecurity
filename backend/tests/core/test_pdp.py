from __future__ import annotations

from app.core.pdp import scan_text, decide
from app.core.rules.base import Verdict


class TestScanText:
    def test_clean_text_no_matches(self) -> None:
        matches, prompt_hash = scan_text("Hello world")
        assert len(matches) == 0
        assert len(prompt_hash) == 64

    def test_ssn_detected(self) -> None:
        matches, _ = scan_text("SSN: 123-45-6789")
        assert any(m.rule_id == "pii_ssn_us" for m in matches)

    def test_email_detected(self) -> None:
        matches, _ = scan_text("Email: test@example.com")
        assert any(m.rule_id == "pii_email" for m in matches)

    def test_aws_key_detected(self) -> None:
        matches, _ = scan_text("Key: AKIAIOSFODNN7EXAMPLE")
        assert any(m.rule_id == "secret_aws_key" for m in matches)

    def test_jwt_detected(self) -> None:
        jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
        matches, _ = scan_text(f"Token: {jwt}")
        assert any(m.rule_id == "secret_jwt" for m in matches)

    def test_credit_card_valid_luhn(self) -> None:
        matches, _ = scan_text("Card: 4111111111111111")
        assert any(m.rule_id == "secret_credit_card" for m in matches)

    def test_credit_card_invalid_luhn(self) -> None:
        matches, _ = scan_text("Card: 4111111111111112")
        assert not any(m.rule_id == "secret_credit_card" for m in matches)

    def test_multiple_matches(self) -> None:
        matches, _ = scan_text("SSN 123-45-6789 and email test@test.com")
        assert len(matches) >= 2

    def test_prompt_hash_is_sha256(self) -> None:
        _, prompt_hash = scan_text("test")
        assert len(prompt_hash) == 64
        assert all(c in "0123456789abcdef" for c in prompt_hash)


class TestDecide:
    def test_no_matches_allows(self) -> None:
        verdict = decide([])
        assert verdict.action == "allow"
        assert verdict.reason == "No rules matched"

    def test_block_matches_blocks(self) -> None:
        from app.core.rules.base import RuleMatch

        matches = [
            RuleMatch(
                rule_id="pii_ssn_us",
                rule_name="SSN",
                value_hash="abc",
                position=(0, 11),
                severity="high",
                action="block",
            )
        ]
        verdict = decide(matches)
        assert verdict.action == "block"
        assert "pii_ssn_us" in verdict.reason

    def test_log_only_matches_allows_with_logging(self) -> None:
        from app.core.rules.base import RuleMatch

        matches = [
            RuleMatch(
                rule_id="pii_email",
                rule_name="Email",
                value_hash="abc",
                position=(0, 19),
                severity="medium",
                action="log_only",
            )
        ]
        verdict = decide(matches)
        assert verdict.action == "allow"
        assert "pii_email" in verdict.reason
        assert "Rules matched for logging" in verdict.reason

    def test_log_only_and_block_mixed_blocks(self) -> None:
        from app.core.rules.base import RuleMatch

        matches = [
            RuleMatch(
                rule_id="pii_email",
                rule_name="Email",
                value_hash="abc",
                position=(0, 19),
                severity="medium",
                action="log_only",
            ),
            RuleMatch(
                rule_id="pii_ssn_us",
                rule_name="SSN",
                value_hash="def",
                position=(20, 31),
                severity="high",
                action="block",
            )
        ]
        verdict = decide(matches)
        assert verdict.action == "block"
        assert "pii_ssn_us" in verdict.reason

    def test_high_severity_blocks_even_without_block_action(self) -> None:
        from app.core.rules.base import RuleMatch

        matches = [
            RuleMatch(
                rule_id="pii_passport_ru",
                rule_name="RU Passport",
                value_hash="abc",
                position=(0, 13),
                severity="critical",  # This should block even without action="block"
                action="log_only",
            )
        ]
        verdict = decide(matches)
        assert verdict.action == "block"
        assert "pii_passport_ru" in verdict.reason

    def test_multiple_log_only_matches_allows_with_logging(self) -> None:
        from app.core.rules.base import RuleMatch

        matches = [
            RuleMatch(
                rule_id="pii_email",
                rule_name="Email",
                value_hash="abc",
                position=(0, 19),
                severity="low",
                action="log_only",
            ),
            RuleMatch(
                rule_id="pii_phone",
                rule_name="Phone",
                value_hash="def",
                position=(20, 30),
                severity="low",
                action="log_only",
            )
        ]
        verdict = decide(matches)
        assert verdict.action == "allow"
        assert "Rules matched for logging" in verdict.reason
        assert "pii_email" in verdict.reason or "pii_phone" in verdict.reason

    def test_block_and_high_severity_priority_blocks(self) -> None:
        from app.core.rules.base import RuleMatch

        matches = [
            RuleMatch(
                rule_id="pii_email",
                rule_name="Email",
                value_hash="abc",
                position=(0, 19),
                severity="low",
                action="log_only",
            ),
            RuleMatch(
                rule_id="pii_ssn_us",
                rule_name="SSN",
                value_hash="def",
                position=(20, 31),
                severity="high",
                action="log_only",  # High severity should block even with log_only action
            )
        ]
        verdict = decide(matches)
        assert verdict.action == "block"
        assert "pii_ssn_us" in verdict.reason

    def test_normal_allow_rules_allows(self) -> None:
        from app.core.rules.base import RuleMatch

        matches = [
            RuleMatch(
                rule_id="suspicious_keyword",
                rule_name="Suspicious Keyword",
                value_hash="abc",
                position=(0, 20),
                severity="low",
                action="allow",  # Normal allow rule
            )
        ]
        verdict = decide(matches)
        assert verdict.action == "allow"
        assert verdict.reason == "Rules matched but none require blocking"

    def test_verdict_is_block_property(self) -> None:
        v = Verdict(action="block", reason="test")
        assert v.is_block is True
        v2 = Verdict(action="allow", reason="test")
        assert v2.is_block is False

    def test_rules_matched_preserved_in_all_cases(self) -> None:
        from app.core.rules.base import RuleMatch

        matches = [
            RuleMatch(
                rule_id="pii_email",
                rule_name="Email",
                value_hash="abc",
                position=(0, 19),
                severity="medium",
                action="log_only",
            )
        ]
        
        verdict = decide(matches)
        assert len(verdict.rules_matched) == 1
        assert verdict.rules_matched[0].rule_id == "pii_email"

    def test_integration_with_real_log_only_rules(self) -> None:
        """Test PDP with actual log_only rules from the rule system."""
        # Test with phone number (log_only rule)
        matches, _ = scan_text("My phone is +1234567890")
        log_only_matches = [m for m in matches if m.action == "log_only"]
        block_matches = [m for m in matches if m.action == "block"]
        
        # Should find phone number match
        assert any(m.rule_id == "pii_phone" for m in matches)
        
        # Decision should allow since only log_only matches
        verdict = decide(matches)
        assert verdict.action == "allow"
        assert "Rules matched for logging" in verdict.reason

    def test_integration_mixed_real_rules_blocks(self) -> None:
        """Test PDP with mixed real rules (block + log_only)."""
        # Test with SSN (block) and phone (log_only)
        matches, _ = scan_text("SSN: 123-45-6789 and phone: +1234567890")
        
        # Should find both SSN and phone matches
        assert any(m.rule_id == "pii_ssn_us" for m in matches)
        assert any(m.rule_id == "pii_phone" for m in matches)
        
        # Decision should block because SSN is block action
        verdict = decide(matches)
        assert verdict.action == "block"
        assert "pii_ssn_us" in verdict.reason

    def test_integration_high_severity_blocks_log_only(self) -> None:
        """Test that high severity rules block even with log_only action."""
        # Test with internal ID (log_only, low severity) and AWS key (critical, block)
        matches, _ = scan_text("Internal: INT-ABC-123456 and AWS: AKIAIOSFODNN7EXAMPLE")
        
        # Should find both matches
        assert any(m.rule_id == "secret_internal_id" for m in matches)
        assert any(m.rule_id == "secret_aws_key" for m in matches)
        
        # Decision should block because AWS key is critical severity
        verdict = decide(matches)
        assert verdict.action == "block"
        assert "secret_aws_key" in verdict.reason
