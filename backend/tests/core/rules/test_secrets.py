from __future__ import annotations

import pytest
import json
from pathlib import Path

from app.core.rules.base import RuleMatch
from app.core.rules.loader import (
    load_secrets_rules,
    match_secrets_with_rules,
    get_secrets_rules,
    check_secrets_with_yaml
)


@pytest.fixture
def secrets_rules_path():
    """Path to the secrets rules YAML file."""
    return Path(__file__).parent.parent.parent.parent / "rules" / "secrets.yaml"


@pytest.fixture
def secrets_samples():
    """Load secrets test samples from JSON fixture."""
    fixture_path = Path(__file__).parent.parent / "fixtures" / "secrets_samples.json"
    with open(fixture_path, 'r', encoding='utf-8') as f:
        return json.load(f)


class TestSecretsRulesLoading:
    """Test YAML rule loading functionality."""

    def test_load_secrets_rules_success(self, secrets_rules_path):
        """Test successful loading of secrets rules from YAML."""
        rules = load_secrets_rules(str(secrets_rules_path))
        
        assert len(rules) == 3
        rule_ids = [rule['id'] for rule in rules]
        assert 'secret_aws_key' in rule_ids
        assert 'secret_jwt' in rule_ids
        assert 'secret_credit_card' in rule_ids

    def test_load_secrets_rules_missing_file(self):
        """Test fallback when YAML file is missing."""
        rules = load_secrets_rules("nonexistent.yaml")
        assert rules == []

    def test_get_secrets_rules_with_yaml(self, secrets_rules_path):
        """Test getting secrets rules with YAML file available."""
        rules = get_secrets_rules(str(secrets_rules_path))
        
        assert len(rules) == 3
        for rule in rules:
            assert all(key in rule for key in ['id', 'name', 'type', 'pattern', 'severity', 'action', 'version'])

    def test_get_secrets_rules_fallback(self):
        """Test fallback to hardcoded rules when YAML is missing."""
        rules = get_secrets_rules("nonexistent.yaml")
        
        assert len(rules) == 3
        rule_ids = [rule['id'] for rule in rules]
        assert 'secret_aws_key' in rule_ids
        assert 'secret_jwt' in rule_ids
        assert 'secret_credit_card' in rule_ids

    def test_match_secrets_with_rules_empty_rules(self):
        """Test matching with empty rules list."""
        matches = match_secrets_with_rules("test text", [])
        assert matches == []

    def test_match_secrets_with_rules_invalid_pattern(self):
        """Test matching with invalid regex pattern."""
        rules = [{
            'id': 'test_rule',
            'name': 'Test Rule',
            'type': 'regex',
            'pattern': '[invalid',
            'severity': 'medium',
            'action': 'block'
        }]
        
        matches = match_secrets_with_rules("test text", rules)
        assert matches == []  # Invalid pattern should not match anything


class TestAWSKey:
    """Test AWS Access Key detection."""

    def test_aws_key_positive(self, secrets_samples):
        """Test AWS key detection with positive examples."""
        rules = get_secrets_rules()
        
        for aws_key in secrets_samples['positive']['aws_key']:
            matches = match_secrets_with_rules(aws_key, rules)
            assert len(matches) >= 1, f"AWS key '{aws_key}' should be detected"
            
            # Verify the specific rule matched
            aws_matches = [m for m in matches if m.rule_id == 'secret_aws_key']
            assert len(aws_matches) >= 1
            
            # Verify hash contains PII but not the actual value
            assert 'AKIAIOSFODNN7EXAMPLE' not in aws_matches[0].value_hash
            assert len(aws_matches[0].value_hash) == 16  # SHA256 truncated to 16 chars

    def test_aws_key_negative(self, secrets_samples):
        """Test AWS key detection with negative examples."""
        rules = get_secrets_rules()
        
        for text in secrets_samples['negative']['aws_key']:
            matches = match_secrets_with_rules(text, rules)
            aws_matches = [m for m in matches if m.rule_id == 'secret_aws_key']
            assert len(aws_matches) == 0, f"AWS key should not match '{text}'"

    def test_aws_key_position_accuracy(self, secrets_samples):
        """Test AWS key position accuracy in text."""
        rules = get_secrets_rules()
        
        text = "My key is AKIAIOSFODNN7EXAMPLE"
        matches = match_secrets_with_rules(text, rules)
        aws_matches = [m for m in matches if m.rule_id == 'secret_aws_key']
        
        assert len(aws_matches) == 1
        match = aws_matches[0]
        assert match.position == (12, 29)  # Position of "AKIAIOSFODNN7EXAMPLE"

    def test_multiple_aws_key_matches(self):
        """Test multiple AWS key matches in same text."""
        rules = get_secrets_rules()
        text = "Keys: AKIAIOSFODNN7EXAMPLE and AKIAABCDEFGHIJKLMNOPQ"
        matches = match_secrets_with_rules(text, rules)
        aws_matches = [m for m in matches if m.rule_id == 'secret_aws_key']
        
        assert len(aws_matches) == 2


class TestJWT:
    """Test JWT detection."""

    def test_jwt_positive(self, secrets_samples):
        """Test JWT detection with positive examples."""
        rules = get_secrets_rules()
        
        for jwt in secrets_samples['positive']['jwt']:
            matches = match_secrets_with_rules(jwt, rules)
            jwt_matches = [m for m in matches if m.rule_id == 'secret_jwt']
            assert len(jwt_matches) >= 1, f"JWT '{jwt}' should be detected"

    def test_jwt_negative(self, secrets_samples):
        """Test JWT detection with negative examples."""
        rules = get_secrets_rules()
        
        for text in secrets_samples['negative']['jwt']:
            matches = match_secrets_with_rules(text, rules)
            jwt_matches = [m for m in matches if m.rule_id == 'secret_jwt']
            assert len(jwt_matches) == 0, f"JWT should not match '{text}'"

    def test_jwt_position_accuracy(self, secrets_samples):
        """Test JWT position accuracy in text."""
        rules = get_secrets_rules()
        
        text = "Token: eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIiwiaWF0IjoxNTE2MjM5MDIyfQ.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
        matches = match_secrets_with_rules(text, rules)
        jwt_matches = [m for m in matches if m.rule_id == 'secret_jwt']
        
        assert len(jwt_matches) == 1
        match = jwt_matches[0]
        # JWT is long, so position should be around the token
        assert match.position[0] > 7  # After "Token: "
        assert match.position[1] <= len(text)

    def test_multiple_jwt_matches(self):
        """Test multiple JWT matches in same text."""
        rules = get_secrets_rules()
        text = "JWTs: eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIiwiaWF0IjoxNTE2MjM5MDIyfQ.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c and eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJpc3MiOiJPbmxpbmUgSldUIEJhY2tlbmQgTXVzaWMiLCJleHAiOjE5ODM4MTIzODZ9.flE3twM4s0iVf7p5c6L7gQ6R8T9U0V1W2X3Y4Z5A6B7C8D9E0F1G2H3I4J5K6L7M8"
        matches = match_secrets_with_rules(text, rules)
        jwt_matches = [m for m in matches if m.rule_id == 'secret_jwt']
        
        assert len(jwt_matches) == 2


class TestCreditCard:
    """Test Credit Card detection."""

    def test_credit_card_positive(self, secrets_samples):
        """Test credit card detection with positive examples."""
        rules = get_secrets_rules()
        
        for credit_card in secrets_samples['positive']['credit_card']:
            matches = match_secrets_with_rules(credit_card, rules)
            credit_card_matches = [m for m in matches if m.rule_id == 'secret_credit_card']
            assert len(credit_card_matches) >= 1, f"Credit card '{credit_card}' should be detected"

    def test_credit_card_negative(self, secrets_samples):
        """Test credit card detection with negative examples."""
        rules = get_secrets_rules()
        
        for text in secrets_samples['negative']['credit_card']:
            matches = match_secrets_with_rules(text, rules)
            credit_card_matches = [m for m in matches if m.rule_id == 'secret_credit_card']
            assert len(credit_card_matches) == 0, f"Credit card should not match '{text}'"

    def test_credit_card_position_accuracy(self, secrets_samples):
        """Test credit card position accuracy in text."""
        rules = get_secrets_rules()
        
        text = "Card: 4539578763621486"
        matches = match_secrets_with_rules(text, rules)
        credit_card_matches = [m for m in matches if m.rule_id == 'secret_credit_card']
        
        assert len(credit_card_matches) == 1
        match = credit_card_matches[0]
        assert match.position == (7, 23)  # Position of "4539578763621486"

    def test_multiple_credit_card_matches(self):
        """Test multiple credit card matches in same text."""
        rules = get_secrets_rules()
        text = "Cards: 4539578763621486, 5425233430109903, 378282246310005"
        matches = match_secrets_with_rules(text, rules)
        credit_card_matches = [m for m in matches if m.rule_id == 'secret_credit_card']
        
        assert len(credit_card_matches) == 3


class TestLuhnValidation:
    """Test Luhn validation function (imported from secrets.py)."""

    def test_luhn_valid_cards(self):
        """Test Luhn validation with valid credit card numbers."""
        from app.core.rules.secrets import _validate_luhn
        
        # Valid Visa cards
        assert _validate_luhn("4539578763621486") == True
        assert _validate_luhn("4916338506082832") == True
        
        # Valid Mastercard cards
        assert _validate_luhn("5425233430109903") == True
        assert _validate_luhn("5185883214864013") == True
        
        # Valid Amex cards
        assert _validate_luhn("378282246310005") == True
        assert _validate_luhn("371449635398431") == True

    def test_luhn_invalid_cards(self):
        """Test Luhn validation with invalid credit card numbers."""
        from app.core.rules.secrets import _validate_luhn
        
        # Invalid cards (valid pattern but invalid Luhn)
        assert _validate_luhn("4539578763621487") == False  # Valid Visa + last digit changed
        assert _validate_luhn("4916338506082833") == False  # Valid Visa + last digit changed
        assert _validate_luhn("5425233430109904") == False  # Valid MC + last digit changed
        assert _validate_luhn("5185883214864014") == False  # Valid MC + last digit changed
        assert _validate_luhn("378282246310006") == False  # Valid Amex + last digit changed
        assert _validate_luhn("371449635398432") == False  # Valid Amex + last digit changed

    def test_luhn_invalid_patterns(self):
        """Test Luhn validation with invalid patterns."""
        from app.core.rules.secrets import _validate_luhn
        
        # Too short
        assert _validate_luhn("1234") == False
        assert _validate_luhn("12345") == False
        
        # Too long
        assert _validate_luhn("12345678901234567") == False
        
        # Non-numeric
        assert _validate_luhn("abcd1234") == False
        
        # Empty string
        assert _validate_luhn("") == False


class TestEdgeCases:
    """Test edge cases and special scenarios."""

    def test_empty_string(self, secrets_samples):
        """Test empty string returns no matches."""
        rules = get_secrets_rules()
        matches = match_secrets_with_rules(secrets_samples['edge_cases']['empty'], rules)
        assert len(matches) == 0

    def test_unicode_text(self, secrets_samples):
        """Test text with Unicode characters."""
        rules = get_secrets_rules()
        matches = match_secrets_with_rules(secrets_samples['edge_cases']['unicode'], rules)
        
        # Should detect AWS key, JWT, and credit card
        aws_matches = [m for m in matches if m.rule_id == 'secret_aws_key']
        jwt_matches = [m for m in matches if m.rule_id == 'secret_jwt']
        credit_card_matches = [m for m in matches if m.rule_id == 'secret_credit_card']
        
        assert len(aws_matches) == 1
        assert len(jwt_matches) == 1
        assert len(credit_card_matches) == 1

    def test_long_text(self, secrets_samples):
        """Test long text with multiple secrets."""
        rules = get_secrets_rules()
        matches = match_secrets_with_rules(secrets_samples['edge_cases']['long_text'], rules)
        
        # Should detect AWS key, JWT, and credit card
        aws_matches = [m for m in matches if m.rule_id == 'secret_aws_key']
        jwt_matches = [m for m in matches if m.rule_id == 'secret_jwt']
        credit_card_matches = [m for m in matches if m.rule_id == 'secret_credit_card']
        
        assert len(aws_matches) == 1
        assert len(jwt_matches) == 1
        assert len(credit_card_matches) == 1

    def test_multiple_secrets_types(self, secrets_samples):
        """Test text with multiple secrets types."""
        rules = get_secrets_rules()
        matches = match_secrets_with_rules(secrets_samples['edge_cases']['multiple_matches'], rules)
        
        # Should detect 2 AWS keys, 2 JWTs, 3 credit cards
        aws_matches = [m for m in matches if m.rule_id == 'secret_aws_key']
        jwt_matches = [m for m in matches if m.rule_id == 'secret_jwt']
        credit_card_matches = [m for m in matches if m.rule_id == 'secret_credit_card']
        
        assert len(aws_matches) == 2
        assert len(jwt_matches) == 2
        assert len(credit_card_matches) == 3

    def test_no_secrets_in_hash(self, secrets_samples):
        """Test that actual secret values are not stored in hash."""
        rules = get_secrets_rules()
        
        text = "AWS: AKIAIOSFODNN7EXAMPLE, JWT: eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIiwiaWF0IjoxNTE2MjM5MDIyfQ.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c, Card: 4539578763621486"
        matches = match_secrets_with_rules(text, rules)
        
        for match in matches:
            assert 'AKIAIOSFODNN7EXAMPLE' not in match.value_hash
            assert 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9' not in match.value_hash
            assert '4539578763621486' not in match.value_hash
            assert len(match.value_hash) == 16  # SHA256 truncated to 16 chars

    def test_position_start_end_middle(self, secrets_samples):
        """Test secrets detection at different text positions."""
        rules = get_secrets_rules()
        
        # Start position
        matches = match_secrets_with_rules(secrets_samples['edge_cases']['position_start'], rules)
        assert len(matches) == 1
        assert matches[0].position == (0, 17)  # "AKIAIOSFODNN7EXAMPLE"
        
        # End position
        matches = match_secrets_with_rules(secrets_samples['edge_cases']['position_end'], rules)
        assert len(matches) == 1
        assert matches[0].position == (12, 29)  # "AKIAIOSFODNN7EXAMPLE"
        
        # Middle position
        matches = match_secrets_with_rules(secrets_samples['edge_cases']['position_middle'], rules)
        assert len(matches) == 1
        assert matches[0].position == (31, 48)  # "AKIAIOSFODNN7EXAMPLE"


class TestIntegration:
    """Integration tests with the full pipeline."""

    def test_check_secrets_with_yaml_integration(self):
        """Test integration with check_secrets_with_yaml function."""
        text = "AWS key AKIAIOSFODNN7EXAMPLE and JWT eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIiwiaWF0IjoxNTE2MjM5MDIyfQ.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
        matches = check_secrets_with_yaml(text)
        
        assert len(matches) == 2
        
        # Verify both secrets types are detected
        aws_matches = [m for m in matches if m.rule_id == 'secret_aws_key']
        jwt_matches = [m for m in matches if m.rule_id == 'secret_jwt']
        
        assert len(aws_matches) == 1
        assert len(jwt_matches) == 1
        
        # Verify RuleMatch structure
        for match in matches:
            assert isinstance(match, RuleMatch)
            assert hasattr(match, 'rule_id')
            assert hasattr(match, 'rule_name')
            assert hasattr(match, 'value_hash')
            assert hasattr(match, 'position')
            assert hasattr(match, 'severity')
            assert hasattr(match, 'action')

    def test_rule_severity_levels(self):
        """Test that severity levels are correctly assigned."""
        rules = get_secrets_rules()
        
        text = "AWS key AKIAIOSFODNN7EXAMPLE, JWT eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIiwiaWF0IjoxNTE2MjM5MDIyfQ.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c, Card 4539578763621486"
        matches = match_secrets_with_rules(text, rules)
        
        for match in matches:
            if match.rule_id == 'secret_aws_key':
                assert match.severity == 'critical'
            elif match.rule_id == 'secret_jwt':
                assert match.severity == 'high'
            elif match.rule_id == 'secret_credit_card':
                assert match.severity == 'critical'

    def test_rule_actions(self):
        """Test that rule actions are correctly assigned."""
        rules = get_secrets_rules()
        
        text = "AWS key AKIAIOSFODNN7EXAMPLE, JWT eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIiwiaWF0IjoxNTE2MjM5MDIyfQ.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c, Card 4539578763621486"
        matches = match_secrets_with_rules(text, rules)
        
        for match in matches:
            assert match.action == 'block'  # All rules should block

    def test_performance_large_text(self):
        """Test performance with large text containing many secrets."""
        rules = get_secrets_rules()
        
        # Create large text with repeated secrets
        base_text = "AWS: AKIAIOSFODNN7EXAMPLE. JWT: eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIiwiaWF0IjoxNTE2MjM5MDIyfQ.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c. Card: 4539578763621486. "
        large_text = base_text * 100  # 300 secrets instances
        
        matches = match_secrets_with_rules(large_text, rules)
        
        # Should detect 100 AWS keys, 100 JWTs, and 100 credit cards
        aws_matches = [m for m in matches if m.rule_id == 'secret_aws_key']
        jwt_matches = [m for m in matches if m.rule_id == 'secret_jwt']
        credit_card_matches = [m for m in matches if m.rule_id == 'secret_credit_card']
        
        assert len(aws_matches) == 100
        assert len(jwt_matches) == 100
        assert len(credit_card_matches) == 100