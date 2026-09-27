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

    def test_verdict_is_block_property(self) -> None:
        v = Verdict(action="block", reason="test")
        assert v.is_block is True
        v2 = Verdict(action="allow", reason="test")
        assert v2.is_block is False
