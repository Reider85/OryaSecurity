from __future__ import annotations

import pytest
import json
import tempfile
import asyncio
from pathlib import Path
from unittest.mock import patch, AsyncMock

from app.core.rules.base import Rule, RuleMatch
from app.core.rules.loader import RuleSet


@pytest.fixture
def temp_rules_dir():
    """Create a temporary directory with test rule files."""
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        
        # Create valid rules file
        valid_rules = {
            "rules": [
                {
                    "id": "test_rule_1",
                    "name": "Test Rule 1",
                    "type": "regex",
                    "pattern": r"\btest\b",
                    "severity": "high",
                    "action": "block",
                    "version": "1.0.0"
                },
                {
                    "id": "test_rule_2",
                    "name": "Test Rule 2",
                    "type": "regex",
                    "pattern": r"\bexample\b",
                    "severity": "medium",
                    "action": "log_only",
                    "version": "1.0.0"
                }
            ]
        }
        
        with open(temp_path / "valid_rules.yaml", "w") as f:
            json.dump(valid_rules, f)
        
        # Create invalid rules file (missing required field)
        invalid_rules = {
            "rules": [
                {
                    "id": "invalid_rule",
                    "name": "Invalid Rule",
                    "type": "regex",
                    # Missing required field 'pattern'
                    "severity": "high",
                    "action": "block",
                    "version": "1.0.0"
                }
            ]
        }
        
        with open(temp_path / "invalid_rules.yaml", "w") as f:
            json.dump(invalid_rules, f)
        
        # Create rules file with disabled rule
        disabled_rules = {
            "rules": [
                {
                    "id": "disabled_rule",
                    "name": "Disabled Rule",
                    "type": "regex",
                    "pattern": r"\bdisabled\b",
                    "severity": "low",
                    "action": "allow",
                    "version": "1.0.0",
                    "enabled": False
                }
            ]
        }
        
        with open(temp_path / "disabled_rules.yaml", "w") as f:
            json.dump(disabled_rules, f)
        
        yield temp_path


@pytest.fixture
def rule_set(temp_rules_dir):
    """Create a RuleSet instance with test rules."""
    return RuleSet(str(temp_rules_dir))


class TestRuleSet:
    """Test the RuleSet class functionality."""

    def test_init_loads_rules(self, temp_rules_dir):
        """Test that RuleSet loads rules on initialization."""
        rule_set = RuleSet(str(temp_rules_dir))
        
        assert len(rule_set.rules) == 3  # 2 valid + 1 disabled
        assert len(rule_set.rules_by_id) == 3
        
        # Check that disabled rule is loaded but not enabled
        disabled_rule = rule_set.get_rule("disabled_rule")
        assert disabled_rule is not None
        assert not disabled_rule.enabled

    def test_match_with_enabled_rules(self, rule_set):
        """Test matching text against enabled rules."""
        matches = rule_set.match("This is a test and an example")
        
        # Should match both test_rule_1 and test_rule_2
        assert len(matches) == 2
        
        test_matches = [m for m in matches if m.rule_id == "test_rule_1"]
        example_matches = [m for m in matches if m.rule_id == "test_rule_2"]
        
        assert len(test_matches) == 1
        assert len(example_matches) == 1
        
        # Check positions
        assert test_matches[0].position == (10, 14)  # "test"
        assert example_matches[0].position == (19, 26)  # "example"

    def test_match_with_disabled_rules(self, rule_set):
        """Test that disabled rules are not matched."""
        matches = rule_set.match("This text contains disabled")
        
        # Should not match disabled_rule
        disabled_matches = [m for m in matches if m.rule_id == "disabled_rule"]
        assert len(disabled_matches) == 0

    def test_get_rule_by_id(self, rule_set):
        """Test getting a specific rule by ID."""
        rule = rule_set.get_rule("test_rule_1")
        
        assert rule is not None
        assert rule.id == "test_rule_1"
        assert rule.name == "Test Rule 1"
        assert rule.severity == "high"
        assert rule.action == "block"

    def test_get_nonexistent_rule(self, rule_set):
        """Test getting a non-existent rule."""
        rule = rule_set.get_rule("nonexistent_rule")
        assert rule is None

    def test_get_rules_by_type(self, rule_set):
        """Test getting rules by type."""
        regex_rules = rule_set.get_rules_by_type("regex")
        
        assert len(regex_rules) == 3  # All rules are regex type
        assert all(rule.type == "regex" for rule in regex_rules)

    def test_get_rules_by_severity(self, rule_set):
        """Test getting rules by severity."""
        high_severity = rule_set.get_rules_by_severity("high")
        medium_severity = rule_set.get_rules_by_severity("medium")
        low_severity = rule_set.get_rules_by_severity("low")
        
        assert len(high_severity) == 1
        assert len(medium_severity) == 1
        assert len(low_severity) == 1
        
        assert high_severity[0].severity == "high"
        assert medium_severity[0].severity == "medium"
        assert low_severity[0].severity == "low"

    def test_get_stats(self, rule_set):
        """Test getting rule statistics."""
        stats = rule_set.get_stats()
        
        assert stats["total_rules"] == 3
        assert stats["enabled_rules"] == 2
        assert stats["disabled_rules"] == 1
        assert stats["rules_by_type"]["regex"] == 3
        assert stats["rules_by_severity"]["high"] == 1
        assert stats["rules_by_severity"]["medium"] == 1
        assert stats["rules_by_severity"]["low"] == 1
        assert "test_rule_1" in stats["rules_by_id"]
        assert "test_rule_2" in stats["rules_by_id"]
        assert "disabled_rule" in stats["rules_by_id"]

    def test_invalid_yaml_file(self, temp_rules_dir):
        """Test handling of invalid YAML files."""
        # Create invalid YAML
        with open(temp_rules_dir / "broken.yaml", "w") as f:
            f.write("invalid: yaml: content: [")
        
        rule_set = RuleSet(str(temp_rules_dir))
        
        # Should still load valid files
        assert len(rule_set.rules) >= 2  # Should still have valid rules

    def test_missing_rules_directory(self):
        """Test handling of missing rules directory."""
        rule_set = RuleSet("nonexistent_directory")
        
        # Should not crash and should have empty rules
        assert len(rule_set.rules) == 0
        assert len(rule_set.rules_by_id) == 0

    def test_empty_rules_file(self, temp_rules_dir):
        """Test handling of empty rules file."""
        with open(temp_rules_dir / "empty.yaml", "w") as f:
            f.write("rules: []")
        
        rule_set = RuleSet(str(temp_rules_dir))
        
        # Should not crash and should have other rules
        assert len(rule_set.rules) >= 2  # Should still have other rules


class TestRuleSetHotReload:
    """Test hot-reload functionality."""

    @pytest.mark.asyncio
    async def test_reload_rules(self, temp_rules_dir):
        """Test reloading rules."""
        rule_set = RuleSet(str(temp_rules_dir))
        
        # Get initial rule count
        initial_count = len(rule_set.rules)
        
        # Add a new rule file
        new_rules = {
            "rules": [
                {
                    "id": "new_rule",
                    "name": "New Rule",
                    "type": "regex",
                    "pattern": r"\bnew\b",
                    "severity": "high",
                    "action": "block",
                    "version": "1.0.0"
                }
            ]
        }
        
        with open(temp_rules_dir / "new_rules.yaml", "w") as f:
            json.dump(new_rules, f)
        
        # Reload rules
        await rule_set.reload_rules()
        
        # Should have new rule
        assert len(rule_set.rules) == initial_count + 1
        assert rule_set.get_rule("new_rule") is not None

    @pytest.mark.asyncio
    async def test_reload_specific_rule(self, temp_rules_dir):
        """Test reloading a specific rule."""
        rule_set = RuleSet(str(temp_rules_dir))
        
        # Get original rule
        original_rule = rule_set.get_rule("test_rule_1")
        assert original_rule is not None
        
        # Modify the rule in the file
        valid_rules = {
            "rules": [
                {
                    "id": "test_rule_1",
                    "name": "Updated Test Rule",
                    "type": "regex",
                    "pattern": r"\bupdated\b",
                    "severity": "critical",
                    "action": "block",
                    "version": "1.1.0"
                },
                {
                    "id": "test_rule_2",
                    "name": "Test Rule 2",
                    "type": "regex",
                    "pattern": r"\bexample\b",
                    "severity": "medium",
                    "action": "log_only",
                    "version": "1.0.0"
                }
            ]
        }
        
        with open(temp_rules_dir / "valid_rules.yaml", "w") as f:
            json.dump(valid_rules, f)
        
        # Reload specific rule
        success = rule_set.reload_rule("test_rule_1")
        
        assert success is True
        
        # Check updated rule
        updated_rule = rule_set.get_rule("test_rule_1")
        assert updated_rule.name == "Updated Test Rule"
        assert updated_rule.pattern == r"\bupdated\b"
        assert updated_rule.severity == "critical"
        assert updated_rule.version == "1.1.0"

    def test_hot_reload_start_stop(self, temp_rules_dir):
        """Test starting and stopping hot-reload."""
        rule_set = RuleSet(str(temp_rules_dir))
        
        # Start hot-reload
        rule_set.start_hot_reload()
        assert rule_set.observer is not None
        
        # Stop hot-reload
        rule_set.stop_hot_reload()
        assert rule_set.observer is None


class TestRuleDataclass:
    """Test the Rule dataclass functionality."""

    def test_rule_creation(self):
        """Test creating a Rule instance."""
        rule = Rule(
            id="test_rule",
            name="Test Rule",
            type="regex",
            pattern=r"\btest\b",
            severity="high",
            action="block",
            version="1.0.0"
        )
        
        assert rule.id == "test_rule"
        assert rule.name == "Test Rule"
        assert rule.type == "regex"
        assert rule.pattern == r"\btest\b"
        assert rule.severity == "high"
        assert rule.action == "block"
        assert rule.version == "1.0.0"
        assert rule.enabled is True
        assert rule.compiled_pattern is not None

    def test_rule_disabled(self):
        """Test disabled rule doesn't compile pattern."""
        rule = Rule(
            id="disabled_rule",
            name="Disabled Rule",
            type="regex",
            pattern=r"\btest\b",
            severity="high",
            action="block",
            version="1.0.0",
            enabled=False
        )
        
        assert rule.enabled is False
        assert rule.compiled_pattern is None

    def test_rule_compilation_error(self):
        """Test rule with invalid regex pattern."""
        rule = Rule(
            id="invalid_rule",
            name="Invalid Rule",
            type="regex",
            pattern="[invalid",
            severity="high",
            action="block",
            version="1.0.0"
        )
        
        # Should have compiled pattern that never matches
        assert rule.compiled_pattern is not None


class TestIntegration:
    """Integration tests with the full pipeline."""

    def test_rule_set_with_real_rules(self):
        """Test RuleSet with actual rule files."""
        # Use the real rules directory
        rule_set = RuleSet("rules")
        
        # Should load the existing rules
        assert len(rule_set.rules) > 0
        
        # Test matching with real PII
        matches = rule_set.match("My SSN is 123-45-6789")
        
        # Should detect SSN
        ssn_matches = [m for m in matches if m.rule_id == "pii_ssn_us"]
        assert len(ssn_matches) == 1
        
        # Check that PII is hashed in the match
        assert "123-45-6789" not in ssn_matches[0].value_hash
        assert len(ssn_matches[0].value_hash) == 16  # SHA256 truncated to 16 chars

    def test_performance_large_text(self, rule_set):
        """Test performance with large text."""
        # Create large text with repeated patterns
        base_text = "test example "
        large_text = base_text * 1000  # 2000 patterns
        
        import time
        start_time = time.time()
        
        matches = rule_set.match(large_text)
        
        end_time = time.time()
        processing_time = end_time - start_time
        
        # Should detect 1000 test and 1000 example patterns
        test_matches = [m for m in matches if m.rule_id == "test_rule_1"]
        example_matches = [m for m in matches if m.rule_id == "test_rule_2"]
        
        assert len(test_matches) == 1000
        assert len(example_matches) == 1000
        
        # Should process in reasonable time (less than 1 second for 2000 patterns)
        assert processing_time < 1.0

    def test_edge_cases(self, rule_set):
        """Test edge cases and special scenarios."""
        # Empty string
        matches = rule_set.match("")
        assert len(matches) == 0
        
        # Unicode text
        matches = rule_set.match("Unicode text: 你好, test, example")
        assert len(matches) == 2  # Should still match test and example
        
        # Very long text
        long_text = "a" * 10000 + " test example " + "b" * 10000
        matches = rule_set.match(long_text)
        assert len(matches) == 2  # Should still match test and example
        
        # Overlapping matches
        matches = rule_set.match("test test")
        test_matches = [m for m in matches if m.rule_id == "test_rule_1"]
        assert len(test_matches) == 2  # Should match both occurrences


class TestErrorHandling:
    """Test error handling scenarios."""

    def test_schema_validation_error(self, temp_rules_dir):
        """Test schema validation error handling."""
        # Create rule with invalid severity
        invalid_rules = {
            "rules": [
                {
                    "id": "invalid_severity",
                    "name": "Invalid Severity",
                    "type": "regex",
                    "pattern": r"\btest\b",
                    "severity": "invalid_severity",
                    "action": "block",
                    "version": "1.0.0"
                }
            ]
        }
        
        with open(temp_rules_dir / "invalid_severity.yaml", "w") as f:
            json.dump(invalid_rules, f)
        
        rule_set = RuleSet(str(temp_rules_dir))
        
        # Should skip invalid rule
        assert rule_set.get_rule("invalid_severity") is None

    def test_missing_schema_file(self, temp_rules_dir):
        """Test handling of missing schema file."""
        # Move schema file temporarily
        schema_path = Path(__file__).parent.parent.parent / "app" / "core" / "rules" / "schema.json"
        backup_path = schema_path.with_suffix(".json.backup")
        
        try:
            # Rename schema file to simulate missing
            schema_path.rename(backup_path)
            
            rule_set = RuleSet(str(temp_rules_dir))
            
            # Should still work without schema validation
            assert len(rule_set.rules) > 0
            
        finally:
            # Restore schema file
            backup_path.rename(schema_path)

    def test_corrupt_yaml_file(self, temp_rules_dir):
        """Test handling of corrupt YAML file."""
        # Create corrupt YAML file
        with open(temp_rules_dir / "corrupt.yaml", "w") as f:
            f.write("corrupt: yaml: [")
        
        rule_set = RuleSet(str(temp_rules_dir))
        
        # Should not crash and should load other rules
        assert len(rule_set.rules) >= 2