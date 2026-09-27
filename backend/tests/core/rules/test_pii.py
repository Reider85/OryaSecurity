from __future__ import annotations

import pytest
import json
from pathlib import Path

from app.core.rules.base import RuleMatch
from app.core.rules.loader import (
    load_pii_rules,
    match_pii_with_rules,
    get_pii_rules,
    check_pii_with_yaml
)


@pytest.fixture
def pii_rules_path():
    """Path to the PII rules YAML file."""
    return Path(__file__).parent.parent.parent.parent / "rules" / "pii.yaml"


@pytest.fixture
def pii_samples():
    """Load PII test samples from JSON fixture."""
    fixture_path = Path(__file__).parent.parent / "fixtures" / "pii_samples.json"
    with open(fixture_path, 'r', encoding='utf-8') as f:
        return json.load(f)


class TestPIIRulesLoading:
    """Test YAML rule loading functionality."""

    def test_load_pii_rules_success(self, pii_rules_path):
        """Test successful loading of PII rules from YAML."""
        rules = load_pii_rules(str(pii_rules_path))
        
        assert len(rules) == 3
        rule_ids = [rule['id'] for rule in rules]
        assert 'pii_ssn_us' in rule_ids
        assert 'pii_email' in rule_ids
        assert 'pii_passport_ru' in rule_ids

    def test_load_pii_rules_missing_file(self):
        """Test fallback when YAML file is missing."""
        rules = load_pii_rules("nonexistent.yaml")
        assert rules == []

    def test_get_pii_rules_with_yaml(self, pii_rules_path):
        """Test getting PII rules with YAML file available."""
        rules = get_pii_rules(str(pii_rules_path))
        
        assert len(rules) == 3
        for rule in rules:
            assert all(key in rule for key in ['id', 'name', 'type', 'pattern', 'severity', 'action', 'version'])

    def test_get_pii_rules_fallback(self):
        """Test fallback to hardcoded rules when YAML is missing."""
        rules = get_pii_rules("nonexistent.yaml")
        
        assert len(rules) == 3
        rule_ids = [rule['id'] for rule in rules]
        assert 'pii_ssn_us' in rule_ids
        assert 'pii_email' in rule_ids
        assert 'pii_passport_ru' in rule_ids

    def test_match_pii_with_rules_empty_rules(self):
        """Test matching with empty rules list."""
        matches = match_pii_with_rules("test text", [])
        assert matches == []

    def test_match_pii_with_rules_invalid_pattern(self):
        """Test matching with invalid regex pattern."""
        rules = [{
            'id': 'test_rule',
            'name': 'Test Rule',
            'type': 'regex',
            'pattern': '[invalid',
            'severity': 'medium',
            'action': 'block'
        }]
        
        matches = match_pii_with_rules("test text", rules)
        assert matches == []  # Invalid pattern should not match anything


class TestSSN:
    """Test US SSN detection."""

    def test_ssn_positive(self, pii_samples):
        """Test SSN detection with positive examples."""
        rules = get_pii_rules()
        
        for ssn in pii_samples['positive']['ssn']:
            matches = match_pii_with_rules(ssn, rules)
            assert len(matches) >= 1, f"SSN '{ssn}' should be detected"
            
            # Verify the specific rule matched
            ssn_matches = [m for m in matches if m.rule_id == 'pii_ssn_us']
            assert len(ssn_matches) >= 1
            
            # Verify hash contains PII but not the actual value
            assert '123-45-6789' not in ssn_matches[0].value_hash
            assert len(ssn_matches[0].value_hash) == 16  # SHA256 truncated to 16 chars

    def test_ssn_negative(self, pii_samples):
        """Test SSN detection with negative examples."""
        rules = get_pii_rules()
        
        for text in pii_samples['negative']['ssn']:
            matches = match_pii_with_rules(text, rules)
            ssn_matches = [m for m in matches if m.rule_id == 'pii_ssn_us']
            assert len(ssn_matches) == 0, f"SSN should not match '{text}'"

    def test_ssn_position_accuracy(self, pii_samples):
        """Test SSN position accuracy in text."""
        rules = get_pii_rules()
        
        text = "My SSN is 123-45-6789"
        matches = match_pii_with_rules(text, rules)
        ssn_matches = [m for m in matches if m.rule_id == 'pii_ssn_us']
        
        assert len(ssn_matches) == 1
        match = ssn_matches[0]
        assert match.position == (12, 23)  # Position of "123-45-6789"

    def test_multiple_ssn_matches(self):
        """Test multiple SSN matches in same text."""
        rules = get_pii_rules()
        text = "SSNs: 123-45-6789 and 987-65-4321"
        matches = match_pii_with_rules(text, rules)
        ssn_matches = [m for m in matches if m.rule_id == 'pii_ssn_us']
        
        assert len(ssn_matches) == 2


class TestEmail:
    """Test Email detection."""

    def test_email_positive(self, pii_samples):
        """Test email detection with positive examples."""
        rules = get_pii_rules()
        
        for email in pii_samples['positive']['email']:
            matches = match_pii_with_rules(email, rules)
            email_matches = [m for m in matches if m.rule_id == 'pii_email']
            assert len(email_matches) >= 1, f"Email '{email}' should be detected"

    def test_email_negative(self, pii_samples):
        """Test email detection with negative examples."""
        rules = get_pii_rules()
        
        for text in pii_samples['negative']['email']:
            matches = match_pii_with_rules(text, rules)
            email_matches = [m for m in matches if m.rule_id == 'pii_email']
            assert len(email_matches) == 0, f"Email should not match '{text}'"

    def test_email_position_accuracy(self, pii_samples):
        """Test email position accuracy in text."""
        rules = get_pii_rules()
        
        text = "Contact me at user@example.com"
        matches = match_pii_with_rules(text, rules)
        email_matches = [m for m in matches if m.rule_id == 'pii_email']
        
        assert len(email_matches) == 1
        match = email_matches[0]
        assert match.position == (14, 28)  # Position of "user@example.com"

    def test_complex_email_patterns(self):
        """Test complex email patterns with subdomains and special characters."""
        rules = get_pii_rules()
        
        test_cases = [
            "user.name+tag@sub.domain.com",
            "email123@service.io",
            "support@localhost",
            "test@domain.co.uk"
        ]
        
        for email in test_cases:
            matches = match_pii_with_rules(email, rules)
            email_matches = [m for m in matches if m.rule_id == 'pii_email']
            assert len(email_matches) >= 1, f"Complex email '{email}' should be detected"


class TestRussianPassport:
    """Test Russian passport detection."""

    def test_passport_ru_positive(self, pii_samples):
        """Test Russian passport detection with positive examples."""
        rules = get_pii_rules()
        
        for passport in pii_samples['positive']['passport_ru']:
            matches = match_pii_with_rules(passport, rules)
            passport_matches = [m for m in matches if m.rule_id == 'pii_passport_ru']
            assert len(passport_matches) >= 1, f"Passport '{passport}' should be detected"

    def test_passport_ru_negative(self, pii_samples):
        """Test Russian passport detection with negative examples."""
        rules = get_pii_rules()
        
        for text in pii_samples['negative']['passport_ru']:
            matches = match_pii_with_rules(text, rules)
            passport_matches = [m for m in matches if m.rule_id == 'pii_passport_ru']
            assert len(passport_matches) == 0, f"Passport should not match '{text}'"

    def test_passport_with_space(self):
        """Test passport with space between numbers."""
        rules = get_pii_rules()
        
        matches = match_pii_with_rules("My passport is 1234 567890", rules)
        passport_matches = [m for m in matches if m.rule_id == 'pii_passport_ru']
        
        assert len(passport_matches) == 1
        assert passport_matches[0].position == (17, 27)  # Position of "1234 567890"

    def test_passport_without_space(self):
        """Test passport without space between numbers."""
        rules = get_pii_rules()
        
        matches = match_pii_with_rules("Passport: 1234567890", rules)
        passport_matches = [m for m in matches if m.rule_id == 'pii_passport_ru']
        
        assert len(passport_matches) == 1
        assert passport_matches[0].position == (10, 20)  # Position of "1234567890"


class TestEdgeCases:
    """Test edge cases and special scenarios."""

    def test_empty_string(self, pii_samples):
        """Test empty string returns no matches."""
        rules = get_pii_rules()
        matches = match_pii_with_rules(pii_samples['edge_cases']['empty'], rules)
        assert len(matches) == 0

    def test_unicode_text(self, pii_samples):
        """Test text with Unicode characters."""
        rules = get_pii_rules()
        matches = match_pii_with_rules(pii_samples['edge_cases']['unicode'], rules)
        
        # Should detect both email and SSN
        email_matches = [m for m in matches if m.rule_id == 'pii_email']
        ssn_matches = [m for m in matches if m.rule_id == 'pii_ssn_us']
        
        assert len(email_matches) == 1
        assert len(ssn_matches) == 1

    def test_long_text(self, pii_samples):
        """Test long text with multiple PII."""
        rules = get_pii_rules()
        matches = match_pii_with_rules(pii_samples['edge_cases']['long_text'], rules)
        
        # Should detect email, SSN, and passport
        email_matches = [m for m in matches if m.rule_id == 'pii_email']
        ssn_matches = [m for m in matches if m.rule_id == 'pii_ssn_us']
        passport_matches = [m for m in matches if m.rule_id == 'pii_passport_ru']
        
        assert len(email_matches) == 1
        assert len(ssn_matches) == 1
        assert len(passport_matches) == 1

    def test_multiple_pii_types(self, pii_samples):
        """Test text with multiple PII types."""
        rules = get_pii_rules()
        matches = match_pii_with_rules(pii_samples['edge_cases']['multiple_matches'], rules)
        
        # Should detect 3 emails, 2 SSNs, 2 passports
        email_matches = [m for m in matches if m.rule_id == 'pii_email']
        ssn_matches = [m for m in matches if m.rule_id == 'pii_ssn_us']
        passport_matches = [m for m in matches if m.rule_id == 'pii_passport_ru']
        
        assert len(email_matches) == 3
        assert len(ssn_matches) == 2
        assert len(passport_matches) == 2

    def test_no_pii_in_hash(self, pii_samples):
        """Test that actual PII values are not stored in hash."""
        rules = get_pii_rules()
        
        text = "Email: user@example.com, SSN: 123-45-6789"
        matches = match_pii_with_rules(text, rules)
        
        for match in matches:
            assert 'user@example.com' not in match.value_hash
            assert '123-45-6789' not in match.value_hash
            assert len(match.value_hash) == 16  # SHA256 truncated to 16 chars

    def test_position_start_end_middle(self, pii_samples):
        """Test PII detection at different text positions."""
        rules = get_pii_rules()
        
        # Start position
        matches = match_pii_with_rules(pii_samples['edge_cases']['position_start'], rules)
        assert len(matches) == 1
        assert matches[0].position == (0, 11)  # "123-45-6789"
        
        # End position
        matches = match_pii_with_rules(pii_samples['edge_cases']['position_end'], rules)
        assert len(matches) == 1
        assert matches[0].position == (13, 24)  # "123-45-6789"
        
        # Middle position
        matches = match_pii_with_rules(pii_samples['edge_cases']['position_middle'], rules)
        assert len(matches) == 1
        assert matches[0].position == (13, 25)  # "user@example.com"


class TestIntegration:
    """Integration tests with the full pipeline."""

    def test_check_pii_with_yaml_integration(self):
        """Test integration with check_pii_with_yaml function."""
        text = "Contact user@example.com or call 123-45-6789"
        matches = check_pii_with_yaml(text)
        
        assert len(matches) == 2
        
        # Verify both PII types are detected
        email_matches = [m for m in matches if m.rule_id == 'pii_email']
        ssn_matches = [m for m in matches if m.rule_id == 'pii_ssn_us']
        
        assert len(email_matches) == 1
        assert len(ssn_matches) == 1
        
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
        rules = get_pii_rules()
        
        text = "Email: user@example.com, SSN: 123-45-6789"
        matches = match_pii_with_rules(text, rules)
        
        for match in matches:
            if match.rule_id == 'pii_ssn_us':
                assert match.severity == 'high'
            elif match.rule_id == 'pii_email':
                assert match.severity == 'medium'
            elif match.rule_id == 'pii_passport_ru':
                assert match.severity == 'high'

    def test_rule_actions(self):
        """Test that rule actions are correctly assigned."""
        rules = get_pii_rules()
        
        text = "Email: user@example.com, SSN: 123-45-6789"
        matches = match_pii_with_rules(text, rules)
        
        for match in matches:
            assert match.action == 'block'  # All rules should block

    def test_performance_large_text(self):
        """Test performance with large text containing many PII."""
        rules = get_pii_rules()
        
        # Create large text with repeated PII
        base_text = "Email: user@example.com, SSN: 123-45-6789. "
        large_text = base_text * 100  # 300 PII instances
        
        matches = match_pii_with_rules(large_text, rules)
        
        # Should detect 100 emails and 100 SSNs
        email_matches = [m for m in matches if m.rule_id == 'pii_email']
        ssn_matches = [m for m in matches if m.rule_id == 'pii_ssn_us']
        
        assert len(email_matches) == 100
        assert len(ssn_matches) == 100