from __future__ import annotations

import pytest
import json
from pathlib import Path

from app.core.rules.base import RuleMatch, Verdict
from app.core.rules.secrets import check_secrets, _validate_luhn
from app.core.rules.loader import RuleSet


@pytest.fixture
def rule_set():
    """RuleSet instance with secrets rules."""
    return RuleSet(Path(__file__).parent.parent.parent.parent / "rules")


class TestSecretsRulesLoading:
    """Test YAML rule loading functionality."""

    def test_rule_set_loads_secrets_rules(self, rule_set):
        rules = rule_set.get_rules_by_type("regex")
        secrets_rules = [r for r in rules if r.id.startswith("secret_")]
        assert len(secrets_rules) == 3
        rule_ids = [r.id for r in secrets_rules]
        assert 'secret_aws_key' in rule_ids
        assert 'secret_jwt' in rule_ids
        assert 'secret_credit_card' in rule_ids

    def test_rule_set_stats(self, rule_set):
        stats = rule_set.get_stats()
        assert stats['total_rules'] >= 3
        assert stats['enabled_rules'] >= 3
        assert stats['disabled_rules'] == 0
        assert 'regex' in stats['rules_by_type']
        secrets_rules = {k: v for k, v in stats['rules_by_id'].items() if k.startswith('secret_')}
        assert len(secrets_rules) >= 3

    def test_rule_set_get_by_id(self, rule_set):
        rule = rule_set.get_rule("secret_aws_key")
        assert rule is not None
        assert rule.id == "secret_aws_key"
        assert rule.name == "AWS Access Key"

    def test_rule_set_get_by_severity(self, rule_set):
        critical_rules = rule_set.get_rules_by_severity("critical")
        high_rules = rule_set.get_rules_by_severity("high")
        assert len(critical_rules) >= 1
        assert len(high_rules) >= 1


class TestAWSKey:
    """Test AWS Access Key detection. Pattern: \\bAKIA[0-9A-Z]{16}\\b (20 chars total)"""

    @pytest.mark.parametrize("aws_key_text", [
        "AKIA1234567890ABCDEF",      # AKIA + 16 chars = 20 total
        "AKIAABCDEFGHIJKLMNOP",       # AKIA + 16 chars
        "AKIAIOSFODNN7EXAMPLE",       # AKIA + 16 chars
        "My key is AKIA1234567890ABCDEF",
        "Access key: AKIAABCDEFGHIJKLMNOP",
        "AWS: AKIAIOSFODNN7EXAMPLE",
    ])
    def test_aws_key_positive(self, rule_set, aws_key_text):
        matches = rule_set.match(aws_key_text)
        aws_matches = [m for m in matches if m.rule_id == 'secret_aws_key']
        assert len(aws_matches) >= 1, f"AWS key '{aws_key_text}' should be detected"

    @pytest.mark.parametrize("text", [
        "AKIA1234567890ABC",          # 19 chars (15 after AKIA)
        "AKIA1234567890ABCD12345",    # 23 chars
        "akia1234567890abcd1234",     # Lowercase
        "AKIA-1234567890ABCD12",      # Hyphen after AKIA
        "AKIA 1234567890ABCD12",      # Space after AKIA
        "not-an-aws-key",             # Text
        "12345678901234567890",       # Numbers only
    ])
    def test_aws_key_negative(self, rule_set, text):
        matches = rule_set.match(text)
        aws_matches = [m for m in matches if m.rule_id == 'secret_aws_key']
        assert len(aws_matches) == 0, f"AWS key should not match '{text}'"

    def test_aws_key_position_accuracy(self, rule_set):
        text = "My AWS key is AKIA1234567890ABCDEF"
        matches = rule_set.match(text)
        aws_matches = [m for m in matches if m.rule_id == 'secret_aws_key']
        assert len(aws_matches) == 1
        assert aws_matches[0].position == (14, 34)

    def test_multiple_aws_key_matches(self, rule_set):
        text = "Keys: AKIA1234567890ABCDEF and AKIAABCDEFGHIJKLMNOP"
        matches = rule_set.match(text)
        aws_matches = [m for m in matches if m.rule_id == 'secret_aws_key']
        assert len(aws_matches) == 2


class TestJWT:
    """Test JWT detection. Pattern: \\beyJ...\\.eyJ...\\.[a-zA-Z0-9_-]+\\b"""

    @pytest.mark.parametrize("jwt_text", [
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIiwiaWF0IjoxNTE2MjM5MDIyfQ.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c",
        "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJpc3MiOiJPbmxpbmUgSldUIEJhY2tlbmQgTXVzaWMiLCJleHAiOjE5ODM4MTIzODZ9.flE3twM4s0iVf7p5c6L7gQ6R8T9U0V1W2X3Y4Z5A6B7C8D9E0F1G2H3I4J5K6L7M8",
        "eyJhbGciOiJIUzI1NiJ9.eyJ0ZXN0IjoiZGF0YSJ9.abc123",
        "Token: eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIiwiaWF0IjoxNTE2MjM5MDIyfQ.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c",
        "JWT: eyJhbGciOiJIUzI1NiJ9.eyJ0ZXN0IjoiZGF0YSJ9.abc123def456",
    ])
    def test_jwt_positive(self, rule_set, jwt_text):
        matches = rule_set.match(jwt_text)
        jwt_matches = [m for m in matches if m.rule_id == 'secret_jwt']
        assert len(jwt_matches) >= 1, f"JWT should be detected in '{jwt_text[:50]}...'"

    @pytest.mark.parametrize("text", [
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9",  # Only header, no dots
        "not-a-jwt.at.all",                          # Not JWT format
        "a.b.c",                                    # Too short
        "header.payload.signature.extra",            # 4 segments
        "plain text with no tokens",                # Text
        "123-45-6789",                              # SSN
        "AKIA1234567890ABCD1234",                   # AWS key
    ])
    def test_jwt_negative(self, rule_set, text):
        matches = rule_set.match(text)
        jwt_matches = [m for m in matches if m.rule_id == 'secret_jwt']
        assert len(jwt_matches) == 0, f"JWT should not match '{text}'"

    def test_jwt_position_accuracy(self, rule_set):
        jwt_token = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIiwiaWF0IjoxNTE2MjM5MDIyfQ.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
        text = f"Token: {jwt_token}"
        matches = rule_set.match(text)
        jwt_matches = [m for m in matches if m.rule_id == 'secret_jwt']
        assert len(jwt_matches) == 1
        assert jwt_matches[0].position[0] == 7

    def test_multiple_jwt_matches(self, rule_set):
        jwt1 = "eyJhbGciOiJIUzI1NiJ9.eyJ0ZXN0IjoiZGF0YSJ9.abc123"
        jwt2 = "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJpc3MiOiJ0ZXN0In0.def456"
        text = f"JWTs: {jwt1} and {jwt2}"
        matches = rule_set.match(text)
        jwt_matches = [m for m in matches if m.rule_id == 'secret_jwt']
        assert len(jwt_matches) == 2


class TestCreditCard:
    """Test Credit Card detection via regex (YAML rule has no Luhn)."""

    @pytest.mark.parametrize("card_text", [
        "4539578763621486",       # Visa 16 digits
        "5425233430109903",       # Mastercard 16 digits
        "378282246310005",        # Amex 15 digits
        "4916338506082832",       # Visa 16 digits
        "371449635398431",        # Amex 15 digits
        "Card: 4539578763621486",
        "Visa: 4916338506082832",
        "Number 5425233430109903",
    ])
    def test_credit_card_positive(self, rule_set, card_text):
        matches = rule_set.match(card_text)
        cc_matches = [m for m in matches if m.rule_id == 'secret_credit_card']
        assert len(cc_matches) >= 1, f"Credit card should match '{card_text}'"

    @pytest.mark.parametrize("text", [
        "1234567890123456",   # Not a valid card prefix
        "453957876362148",    # Too short (15 digits, not starting with 3/37)
        "45395787636214867",  # Too long
        "abc5395787636214",   # Letters
        "not-a-card",         # Text
        "1234",               # Too short
    ])
    def test_credit_card_negative(self, rule_set, text):
        matches = rule_set.match(text)
        cc_matches = [m for m in matches if m.rule_id == 'secret_credit_card']
        assert len(cc_matches) == 0, f"Credit card should not match '{text}'"

    def test_credit_card_position_accuracy(self, rule_set):
        text = "Card: 4539578763621486"
        matches = rule_set.match(text)
        cc_matches = [m for m in matches if m.rule_id == 'secret_credit_card']
        assert len(cc_matches) == 1
        assert cc_matches[0].position == (6, 22)

    def test_multiple_credit_card_matches(self, rule_set):
        text = "Cards: 4539578763621486, 5425233430109903, 378282246310005"
        matches = rule_set.match(text)
        cc_matches = [m for m in matches if m.rule_id == 'secret_credit_card']
        assert len(cc_matches) == 3


class TestLuhnValidation:
    """Test Luhn validation function (separate from YAML rules)."""

    def test_luhn_valid_cards(self):
        assert _validate_luhn("4539578763621486") is True
        assert _validate_luhn("4916338506082832") is True
        assert _validate_luhn("5425233430109903") is True
        assert _validate_luhn("5185883214860004") is True
        assert _validate_luhn("378282246310005") is True
        assert _validate_luhn("371449635398431") is True

    def test_luhn_invalid_cards(self):
        assert _validate_luhn("4539578763621487") is False
        assert _validate_luhn("4916338506082833") is False
        assert _validate_luhn("5425233430109904") is False
        assert _validate_luhn("5185883214860005") is False
        assert _validate_luhn("378282246310006") is False
        assert _validate_luhn("371449635398432") is False

    def test_luhn_invalid_patterns(self):
        assert _validate_luhn("1234") is False
        assert _validate_luhn("12345") is False
        assert _validate_luhn("12345678901234567") is False
        assert _validate_luhn("abcd1234") is False
        assert _validate_luhn("") is False


class TestEdgeCases:
    """Test edge cases and special scenarios."""

    def test_empty_string(self, rule_set):
        matches = rule_set.match("")
        assert len(matches) == 0

    def test_unicode_text(self, rule_set):
        text = "Секреты: AWS AKIA1234567890ABCDEF, JWT eyJhbGciOiJIUzI1NiJ9.eyJ0ZXN0IjoiZGF0YSJ9.abc123, Карта: 4539578763621486"
        matches = rule_set.match(text)
        aws_matches = [m for m in matches if m.rule_id == 'secret_aws_key']
        jwt_matches = [m for m in matches if m.rule_id == 'secret_jwt']
        cc_matches = [m for m in matches if m.rule_id == 'secret_credit_card']
        assert len(aws_matches) == 1
        assert len(jwt_matches) == 1
        assert len(cc_matches) == 1

    def test_long_text(self, rule_set):
        text = "AWS: AKIA1234567890ABCDEF. JWT: eyJhbGciOiJIUzI1NiJ9.eyJ0ZXN0IjoiZGF0YSJ9.abc123. Card: 4539578763621486."
        matches = rule_set.match(text)
        aws_matches = [m for m in matches if m.rule_id == 'secret_aws_key']
        jwt_matches = [m for m in matches if m.rule_id == 'secret_jwt']
        cc_matches = [m for m in matches if m.rule_id == 'secret_credit_card']
        assert len(aws_matches) == 1
        assert len(jwt_matches) == 1
        assert len(cc_matches) == 1

    def test_multiple_secrets_types(self, rule_set):
        text = "AWS: AKIA1234567890ABCDEF, AKIAABCDEFGHIJKLMNOP. JWT: eyJhbGciOiJIUzI1NiJ9.eyJ0ZXN0IjoiZGF0YSJ9.abc123, eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJpc3MiOiJ0ZXN0In0.def456. Cards: 4539578763621486, 5425233430109903, 378282246310005."
        matches = rule_set.match(text)
        aws_matches = [m for m in matches if m.rule_id == 'secret_aws_key']
        jwt_matches = [m for m in matches if m.rule_id == 'secret_jwt']
        cc_matches = [m for m in matches if m.rule_id == 'secret_credit_card']
        assert len(aws_matches) == 2
        assert len(jwt_matches) == 2
        assert len(cc_matches) == 3

    def test_no_secrets_in_hash(self, rule_set):
        text = "AWS: AKIA1234567890ABCDEF, JWT: eyJhbGciOiJIUzI1NiJ9.eyJ0ZXN0IjoiZGF0YSJ9.abc123, Card: 4539578763621486"
        matches = rule_set.match(text)
        for match in matches:
            assert 'AKIA1234567890ABCDEF' not in match.value_hash
            assert 'eyJhbGciOiJIUzI1NiJ9' not in match.value_hash
            assert '4539578763621486' not in match.value_hash
            assert len(match.value_hash) == 16

    def test_position_start_end_middle(self, rule_set):
        text = "AKIA1234567890ABCDEF is my key"
        matches = rule_set.match(text)
        aws_matches = [m for m in matches if m.rule_id == 'secret_aws_key']
        assert len(aws_matches) == 1
        assert aws_matches[0].position == (0, 20)

        text = "My key is AKIA1234567890ABCDEF"
        matches = rule_set.match(text)
        aws_matches = [m for m in matches if m.rule_id == 'secret_aws_key']
        assert len(aws_matches) == 1
        assert aws_matches[0].position == (10, 30)

        text = "AWS key: AKIA1234567890ABCDEF, remember it"
        matches = rule_set.match(text)
        aws_matches = [m for m in matches if m.rule_id == 'secret_aws_key']
        assert len(aws_matches) == 1
        assert aws_matches[0].position == (9, 29)


class TestIntegration:
    """Integration tests with the full pipeline."""

    def test_hardcoded_vs_yaml_consistency(self, rule_set):
        text = "JWT eyJhbGciOiJIUzI1NiJ9.eyJ0ZXN0IjoiZGF0YSJ9.abc123"
        hardcoded_matches = check_secrets(text)
        yaml_matches = rule_set.match(text)
        hardcoded_ids = {m.rule_id for m in hardcoded_matches}
        yaml_ids = {m.rule_id for m in yaml_matches}
        assert hardcoded_ids.issubset(yaml_ids)

    def test_rule_severity_levels(self, rule_set):
        text = "AWS: AKIA1234567890ABCD1234, JWT: eyJhbGciOiJIUzI1NiJ9.eyJ0ZXN0IjoiZGF0YSJ9.abc123, Card: 4539578763621486"
        matches = rule_set.match(text)
        for match in matches:
            if match.rule_id == 'secret_aws_key':
                assert match.severity == 'critical'
            elif match.rule_id == 'secret_jwt':
                assert match.severity == 'high'
            elif match.rule_id == 'secret_credit_card':
                assert match.severity == 'critical'

    def test_rule_actions(self, rule_set):
        text = "AWS: AKIA1234567890ABCD1234, JWT: eyJhbGciOiJIUzI1NiJ9.eyJ0ZXN0IjoiZGF0YSJ9.abc123, Card: 4539578763621486"
        matches = rule_set.match(text)
        for match in matches:
            assert match.action == 'block'

    def test_performance_large_text(self, rule_set):
        base_text = "AWS: AKIA1234567890ABCDEF. JWT: eyJhbGciOiJIUzI1NiJ9.eyJ0ZXN0IjoiZGF0YSJ9.abc123. Card: 4539578763621486. "
        large_text = base_text * 100
        matches = rule_set.match(large_text)
        aws_matches = [m for m in matches if m.rule_id == 'secret_aws_key']
        jwt_matches = [m for m in matches if m.rule_id == 'secret_jwt']
        cc_matches = [m for m in matches if m.rule_id == 'secret_credit_card']
        assert len(aws_matches) == 100
        assert len(jwt_matches) == 100
        assert len(cc_matches) == 100

    def test_verdict_generation(self, rule_set):
        text = "AWS key AKIA1234567890ABCDEF"
        matches = rule_set.match(text)
        verdict = Verdict(action="block", reason="Secrets detected", rules_matched=matches)
        assert verdict.action == "block"
        assert len(verdict.rules_matched) == 1
        assert verdict.is_block is True
