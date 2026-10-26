"""
Tests for OPA Rego policies.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from opa.client import OPAClient, InvalidPolicy


class TestPolicyLoading:
    """Test that all policies load correctly."""
    
    def test_all_policies_loaded(self, opa_client: OPAClient):
        """Test that all policies are loaded via API."""
        policies = opa_client.list_policies()
        expected = [
            "global/default_actions",
            "global/owasp_top10",
            "tenants/default"
        ]
        for policy_id in expected:
            assert any(p["id"] == policy_id for p in policies)
    
    def test_opa_check_syntax(self, opa_binary: str):
        """Test that policies pass OPA syntax check."""
        policies_dir = Path(__file__).parent.parent.parent.parent / "policies"
        result = subprocess.run(
            [opa_binary, "check", str(policies_dir)],
            capture_output=True,
            text=True
        )
        assert result.returncode == 0, f"OPA check failed: {result.stderr}"


class TestGlobalDefaultActions:
    """Test global/default_actions.rego policy."""
    
    def test_default_allow(self, evaluate):
        """Test default allow is true."""
        result = evaluate("scanner.global", {})
        assert result.get("allow") is True
    
    def test_block_severities(self, evaluate):
        """Test block_severities contains expected values."""
        result = evaluate("scanner.global", {})
        severities = result.get("block_severities", [])
        assert "high" in severities
        assert "critical" in severities
    
    def test_min_injection_confidence(self, evaluate):
        """Test min_injection_confidence is set."""
        result = evaluate("scanner.global", {})
        assert result.get("min_injection_confidence") == 0.85


class TestGlobalOWASPTop10:
    """Test global/owasp_top10.rego policy."""
    
    def test_owasp_top10_mapping(self, evaluate):
        """Test OWASP top 10 mapping has all entries."""
        result = evaluate("scanner.global", {})
        owasp = result.get("owasp_top10", {})
        expected = ["LLM01", "LLM02", "LLM03", "LLM04", "LLM05", 
                   "LLM06", "LLM07", "LLM08", "LLM09", "LLM10"]
        for key in expected:
            assert key in owasp
    
    def test_owasp_category_mapping(self, evaluate):
        """Test OWASP category mapping rules."""
        # Test prompt injection maps to LLM01
        input_data = {
            "detectors": {
                "ml_classifier": {
                    "top_class": "prompt_injection",
                    "confidence": 0.9
                }
            }
        }
        result = evaluate("scanner.global", input_data)
        owasp_tags = result.get("owasp_tags", [])
        assert "LLM01" in owasp_tags
    
    def test_owasp_pii_mapping(self, evaluate):
        """Test PII rules map to LLM02."""
        input_data = {
            "detectors": {
                "rules": {
                    "matches": [
                        {"rule_id": "pii_ssn_us", "severity": "high"}
                    ]
                }
            }
        }
        result = evaluate("scanner.global", input_data)
        owasp_tags = result.get("owasp_tags", [])
        assert "LLM02" in owasp_tags


class TestTenantDefault:
    """Test tenants/default.rego policy."""
    
    def test_allow_clean_prompt(self, evaluate):
        """Test clean prompt allows."""
        input_data = {
            "detectors": {
                "ml_classifier": {
                    "top_class": "benign",
                    "confidence": 0.95
                }
            }
        }
        result = evaluate("tenant_default", input_data)
        assert result.get("allow") is True
        assert len(result.get("deny", [])) == 0
    
    def test_block_prompt_injection(self, evaluate):
        """Test prompt injection blocks."""
        input_data = {
            "detectors": {
                "ml_classifier": {
                    "top_class": "prompt_injection",
                    "confidence": 0.9
                }
            }
        }
        result = evaluate("tenant_default", input_data)
        assert result.get("allow") is False
        deny_messages = result.get("deny", [])
        assert len(deny_messages) == 1
        assert "prompt injection" in deny_messages[0]
        assert "confidence 0.90" in deny_messages[0]
    
    def test_allow_low_confidence_injection(self, evaluate):
        """Test low confidence injection allows."""
        input_data = {
            "detectors": {
                "ml_classifier": {
                    "top_class": "prompt_injection",
                    "confidence": 0.5
                }
            }
        }
        result = evaluate("tenant_default", input_data)
        assert result.get("allow") is True
        assert len(result.get("deny", [])) == 0
    
    def test_block_jailbreak(self, evaluate):
        """Test jailbreak blocks."""
        input_data = {
            "detectors": {
                "ml_classifier": {
                    "top_class": "jailbreak",
                    "confidence": 0.9
                }
            }
        }
        result = evaluate("tenant_default", input_data)
        assert result.get("allow") is False
        deny_messages = result.get("deny", [])
        assert len(deny_messages) == 1
        assert "jailbreak" in deny_messages[0]
    
    def test_block_critical_rule(self, evaluate):
        """Test critical rule blocks."""
        input_data = {
            "detectors": {
                "rules": {
                    "matches": [
                        {
                            "rule_id": "secret_aws_key",
                            "severity": "critical"
                        }
                    ]
                }
            }
        }
        result = evaluate("tenant_default", input_data)
        assert result.get("allow") is False
        deny_messages = result.get("deny", [])
        assert len(deny_messages) == 1
        assert "secret_aws_key" in deny_messages[0]
        assert "critical" in deny_messages[0]
    
    def test_block_high_severity_pii(self, evaluate):
        """Test high severity PII blocks."""
        input_data = {
            "detectors": {
                "rules": {
                    "matches": [
                        {
                            "rule_id": "pii_ssn_us",
                            "severity": "high"
                        }
                    ]
                }
            }
        }
        result = evaluate("tenant_default", input_data)
        assert result.get("allow") is False
        deny_messages = result.get("deny", [])
        assert len(deny_messages) == 1
        assert "pii_ssn_us" in deny_messages[0]
        assert "high" in deny_messages[0]
    
    def test_medium_severity_not_blocked(self, evaluate):
        """Test medium severity does not block."""
        input_data = {
            "detectors": {
                "rules": {
                    "matches": [
                        {
                            "rule_id": "pii_email",
                            "severity": "medium"
                        }
                    ]
                }
            }
        }
        result = evaluate("tenant_default", input_data)
        assert result.get("allow") is True
        assert len(result.get("deny", [])) == 0
    
    def test_redact_pii(self, evaluate):
        """Test PII redaction."""
        input_data = {
            "detectors": {
                "pii": {
                    "found": [
                        {"type": "SSN"},
                        {"type": "EMAIL"}
                    ]
                }
            }
        }
        result = evaluate("tenant_default", input_data)
        redact = result.get("redact", [])
        assert "SSN" in redact
        assert "EMAIL" in redact
    
    def test_redact_from_rules(self, evaluate):
        """Test redaction from rule matches."""
        input_data = {
            "detectors": {
                "rules": {
                    "matches": [
                        {"rule_id": "pii_ssn_us"},
                        {"rule_id": "pii_email"}
                    ]
                }
            }
        }
        result = evaluate("tenant_default", input_data)
        redact = result.get("redact", [])
        assert "pii_ssn_us" in redact
        assert "pii_email" in redact
    
    def test_multiple_deny_reasons(self, evaluate):
        """Test multiple deny reasons are collected."""
        input_data = {
            "detectors": {
                "ml_classifier": {
                    "top_class": "prompt_injection",
                    "confidence": 0.9
                },
                "rules": {
                    "matches": [
                        {
                            "rule_id": "secret_aws_key",
                            "severity": "critical"
                        }
                    ]
                }
            }
        }
        result = evaluate("tenant_default", input_data)
        assert result.get("allow") is False
        deny_messages = result.get("deny", [])
        assert len(deny_messages) == 2  # One for injection, one for secret
        assert any("prompt injection" in msg for msg in deny_messages)
        assert any("secret_aws_key" in msg for msg in deny_messages)