from __future__ import annotations

import pytest
import json
from pathlib import Path

from app.core.rules.base import RuleMatch, Verdict
from app.core.rules.pii import check_pii
from app.core.rules.loader import RuleSet


@pytest.fixture
def rule_set():
    """RuleSet instance with PII rules."""
    return RuleSet(Path(__file__).parent.parent.parent.parent / "rules")


class TestPIIRulesLoading:
    """Test YAML rule loading functionality."""

    def test_rule_set_loads_pii_rules(self, rule_set):
        rules = rule_set.get_rules_by_type("regex")
        pii_rules = [r for r in rules if r.id.startswith("pii_")]
        assert len(pii_rules) == 3
        rule_ids = [r.id for r in pii_rules]
        assert 'pii_ssn_us' in rule_ids
        assert 'pii_email' in rule_ids
        assert 'pii_passport_ru' in rule_ids

    def test_rule_set_stats(self, rule_set):
        stats = rule_set.get_stats()
        assert stats['total_rules'] >= 3
        assert stats['enabled_rules'] >= 3
        assert stats['disabled_rules'] == 0
        assert 'regex' in stats['rules_by_type']
        pii_rules = {k: v for k, v in stats['rules_by_id'].items() if k.startswith('pii_')}
        assert len(pii_rules) >= 3

    def test_rule_set_get_by_id(self, rule_set):
        rule = rule_set.get_rule("pii_ssn_us")
        assert rule is not None
        assert rule.id == "pii_ssn_us"
        assert rule.name == "US Social Security Number"

    def test_rule_set_get_by_severity(self, rule_set):
        high_rules = rule_set.get_rules_by_severity("high")
        assert len(high_rules) >= 2

    def test_rule_set_match_empty_text(self, rule_set):
        matches = rule_set.match("")
        assert len(matches) == 0


class TestSSN:
    """Test US SSN detection. Pattern: \\b\\d{3}-\\d{2}-\\d{4}\\b"""

    @pytest.mark.parametrize("ssn_text", [
        "123-45-6789",
        "987-65-4321",
        "000-00-0000",
        "555-12-3456",
        "001-01-0001",
        "999-99-9999",
        "My SSN is 123-45-6789",
        "SSN: 123-45-6789, please contact",
        "Contact: 123-45-6789 for verification",
        "ID number: 123-45-6789",
    ])
    def test_ssn_positive(self, rule_set, ssn_text):
        matches = rule_set.match(ssn_text)
        ssn_matches = [m for m in matches if m.rule_id == 'pii_ssn_us']
        assert len(ssn_matches) >= 1, f"SSN '{ssn_text}' should be detected"

    @pytest.mark.parametrize("text", [
        "123-45-678",       # Too short (9 digits)
        "12-345-6789",      # Wrong group sizes
        "1234567890",       # No hyphens, too many digits
        "abc-def-ghij",     # Letters
        "123-45-678a",      # Letter at end
        "123 45 6789",      # Spaces instead of hyphens
        "123.45.6789",      # Dots instead of hyphens
        "12345678",         # Too short
        "123-45-678-90",    # Extra digits
        "not-an-ssn",       # Text
    ])
    def test_ssn_negative(self, rule_set, text):
        matches = rule_set.match(text)
        ssn_matches = [m for m in matches if m.rule_id == 'pii_ssn_us']
        assert len(ssn_matches) == 0, f"SSN should not match '{text}'"

    def test_ssn_position_accuracy(self, rule_set):
        text = "My SSN is 123-45-6789"
        matches = rule_set.match(text)
        ssn_matches = [m for m in matches if m.rule_id == 'pii_ssn_us']
        assert len(ssn_matches) == 1
        assert ssn_matches[0].position == (10, 21)

    def test_multiple_ssn_matches(self, rule_set):
        text = "SSNs: 123-45-6789 and 987-65-4321"
        matches = rule_set.match(text)
        ssn_matches = [m for m in matches if m.rule_id == 'pii_ssn_us']
        assert len(ssn_matches) == 2


class TestEmail:
    """Test Email detection. Pattern: \\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Za-z]{2,}\\b"""

    @pytest.mark.parametrize("email_text", [
        "user@example.com",
        "test.email@domain.com",
        "user+tag@sub.domain.com",
        "email123@service.io",
        "test@domain.co.uk",
        "Contact me at user@example.com",
        "Email: user.name@company.org",
        "My email is user+test@domain.net",
        "support@service.io",
        "admin@sub.domain.co.uk",
    ])
    def test_email_positive(self, rule_set, email_text):
        matches = rule_set.match(email_text)
        email_matches = [m for m in matches if m.rule_id == 'pii_email']
        assert len(email_matches) >= 1, f"Email '{email_text}' should be detected"

    @pytest.mark.parametrize("text", [
        "user@domain",           # No TLD
        "@example.com",          # Missing local part
        "user@",                # Missing domain
        "user@.com",            # Missing domain name
        "user@domain com",      # Space in domain
        "user@domain,com",      # Comma in domain
        "user@domain/com",      # Slash in domain
        "user@domain:com",      # Colon in domain
        "user@domain@com",      # Two @ symbols
        "plain text",           # No email at all
        "123-45-6789",          # SSN not email
        "not-an-email",         # Just text
    ])
    def test_email_negative(self, rule_set, text):
        matches = rule_set.match(text)
        email_matches = [m for m in matches if m.rule_id == 'pii_email']
        assert len(email_matches) == 0, f"Email should not match '{text}'"

    def test_email_position_accuracy(self, rule_set):
        text = "Contact me at user@example.com"
        matches = rule_set.match(text)
        email_matches = [m for m in matches if m.rule_id == 'pii_email']
        assert len(email_matches) == 1
        assert email_matches[0].position == (14, 30)

    def test_complex_email_patterns(self, rule_set):
        test_cases = [
            "user.name+tag@sub.domain.com",
            "email123@service.io",
            "test@domain.co.uk",
            "user.name+tag@sub.domain.co.uk",
            "test@sub.sub.domain.com",
        ]
        for email in test_cases:
            matches = rule_set.match(email)
            email_matches = [m for m in matches if m.rule_id == 'pii_email']
            assert len(email_matches) >= 1, f"Complex email '{email}' should be detected"


class TestRussianPassport:
    """Test Russian passport detection. Pattern: \\b\\d{4}\\s?\\d{6}\\b"""

    @pytest.mark.parametrize("passport_text", [
        "1234567890",
        "1234 567890",
        "My passport is 1234567890",
        "Passport: 1234 567890",
        "ID: 1234567890",
        "1234 567890",
        "1234567890",
        "Passport number: 1234 567890",
        "5678 123456",
        "9999 000000",
    ])
    def test_passport_ru_positive(self, rule_set, passport_text):
        matches = rule_set.match(passport_text)
        passport_matches = [m for m in matches if m.rule_id == 'pii_passport_ru']
        assert len(passport_matches) >= 1, f"Passport '{passport_text}' should be detected"

    @pytest.mark.parametrize("text", [
        "123456789",       # 9 digits
        "12345678901",     # 11 digits
        "1234 56789",      # Wrong spacing (9 after space)
        "123-456-789",     # Hyphens not spaces
        "1234.567890",     # Dot not space
        "1234 5678a",      # Letter at end
        "a1234 567890",    # Letter at start
        "1234 567 890",    # Three parts
        "12345678 90",     # Wrong spacing
        "123456789012",    # 12 digits
        "12345678",        # 8 digits
        "not-a-passport",  # Text
    ])
    def test_passport_ru_negative(self, rule_set, text):
        matches = rule_set.match(text)
        passport_matches = [m for m in matches if m.rule_id == 'pii_passport_ru']
        assert len(passport_matches) == 0, f"Passport should not match '{text}'"

    def test_passport_with_space(self, rule_set):
        text = "My passport is 1234 567890"
        matches = rule_set.match(text)
        passport_matches = [m for m in matches if m.rule_id == 'pii_passport_ru']
        assert len(passport_matches) == 1
        assert passport_matches[0].position == (15, 26)

    def test_passport_without_space(self, rule_set):
        text = "Passport: 1234567890"
        matches = rule_set.match(text)
        passport_matches = [m for m in matches if m.rule_id == 'pii_passport_ru']
        assert len(passport_matches) == 1
        assert passport_matches[0].position == (10, 20)


class TestEdgeCases:
    """Test edge cases and special scenarios."""

    def test_empty_string(self, rule_set):
        matches = rule_set.match("")
        assert len(matches) == 0

    def test_unicode_text(self, rule_set):
        text = "Пользователь: user@example.com, ИНН: 123-45-6789"
        matches = rule_set.match(text)
        email_matches = [m for m in matches if m.rule_id == 'pii_email']
        ssn_matches = [m for m in matches if m.rule_id == 'pii_ssn_us']
        assert len(email_matches) == 1
        assert len(ssn_matches) == 1

    def test_long_text(self, rule_set):
        text = "Hello, my email is john.doe@example.com. My SSN is 123-45-6789. Passport: 1234 567890."
        matches = rule_set.match(text)
        email_matches = [m for m in matches if m.rule_id == 'pii_email']
        ssn_matches = [m for m in matches if m.rule_id == 'pii_ssn_us']
        passport_matches = [m for m in matches if m.rule_id == 'pii_passport_ru']
        assert len(email_matches) == 1
        assert len(ssn_matches) == 1
        assert len(passport_matches) == 1

    def test_multiple_pii_types(self, rule_set):
        text = "Contacts: user1@domain.com, user2@test.org, admin@service.io. IDs: 123-45-6789, 987-65-4321, 555-12-3456. Passports: 1234567890, 9876543210, 5555555555"
        matches = rule_set.match(text)
        email_matches = [m for m in matches if m.rule_id == 'pii_email']
        ssn_matches = [m for m in matches if m.rule_id == 'pii_ssn_us']
        passport_matches = [m for m in matches if m.rule_id == 'pii_passport_ru']
        assert len(email_matches) == 3
        assert len(ssn_matches) == 3
        assert len(passport_matches) == 3

    def test_no_pii_in_hash(self, rule_set):
        text = "Email: user@example.com, SSN: 123-45-6789"
        matches = rule_set.match(text)
        for match in matches:
            assert 'user@example.com' not in match.value_hash
            assert '123-45-6789' not in match.value_hash
            assert len(match.value_hash) == 16

    def test_position_start_end_middle(self, rule_set):
        text = "123-45-6789 is my SSN"
        matches = rule_set.match(text)
        assert len(matches) == 1
        assert matches[0].position == (0, 11)

        text = "My SSN is 123-45-6789"
        matches = rule_set.match(text)
        assert len(matches) == 1
        assert matches[0].position == (10, 21)

        text = "Contact me at user@example.com for info"
        matches = rule_set.match(text)
        email_matches = [m for m in matches if m.rule_id == 'pii_email']
        assert len(email_matches) == 1
        assert email_matches[0].position == (14, 30)


class TestIntegration:
    """Integration tests with the full pipeline."""

    def test_hardcoded_vs_yaml_consistency(self, rule_set):
        text = "Contact user@example.com or call 123-45-6789"
        hardcoded_matches = check_pii(text)
        yaml_matches = rule_set.match(text)
        hardcoded_ids = {m.rule_id for m in hardcoded_matches}
        yaml_ids = {m.rule_id for m in yaml_matches}
        assert hardcoded_ids == yaml_ids

    def test_rule_severity_levels(self, rule_set):
        text = "Email: user@example.com, SSN: 123-45-6789"
        matches = rule_set.match(text)
        for match in matches:
            if match.rule_id == 'pii_ssn_us':
                assert match.severity == 'high'
            elif match.rule_id == 'pii_email':
                assert match.severity == 'medium'

    def test_rule_actions(self, rule_set):
        text = "Email: user@example.com, SSN: 123-45-6789"
        matches = rule_set.match(text)
        for match in matches:
            assert match.action == 'block'

    def test_performance_large_text(self, rule_set):
        base_text = "Email: user@example.com, SSN: 123-45-6789. "
        large_text = base_text * 100
        matches = rule_set.match(large_text)
        email_matches = [m for m in matches if m.rule_id == 'pii_email']
        ssn_matches = [m for m in matches if m.rule_id == 'pii_ssn_us']
        assert len(email_matches) == 100
        assert len(ssn_matches) == 100

    def test_verdict_generation(self, rule_set):
        text = "Contact user@example.com or call 123-45-6789"
        matches = rule_set.match(text)
        verdict = Verdict(action="block", reason="PII detected", rules_matched=matches)
        assert verdict.action == "block"
        assert len(verdict.rules_matched) == 2
        assert verdict.is_block is True
