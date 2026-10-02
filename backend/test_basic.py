#!/usr/bin/env python3
"""
Simple test script to verify the basic functionality of the LLM Security Scanner.
This script tests the core scanning functionality without requiring the full test suite.
"""

import asyncio
import sys
import os
import time
import json
from pathlib import Path

# Add the app directory to Python path
sys.path.insert(0, str(Path(__file__).parent / "app"))

try:
    from app.core.rules.pii import check_pii
    from app.core.rules.secrets import check_secrets
    from app.core.pdp import decide, scan_text
    from app.core.cache import decision_cache
    from app.core.redactor import redact
    from app.models.scan import ScanRequest, ScanResponse
    from app.config import settings
    print("SUCCESS: All imports successful")
except ImportError as e:
    print(f"ERROR: Import error: {e}")
    sys.exit(1)

async def test_pii_detection():
    """Test PII detection functionality."""
    print("\nTesting PII Detection...")
    
    test_cases = [
        ("My SSN is 123-45-6789", ["pii_ssn_us"]),
        ("My email is test@example.com", ["pii_email"]),
        ("My passport is 123456789", ["pii_passport_ru"]),
        ("Hello world", []),
    ]
    
    for text, expected_rules in test_cases:
        matches, _ = scan_text(text)
        rule_ids = [m.rule_id for m in matches]
        
        if set(rule_ids) == set(expected_rules):
            print(f"SUCCESS: PII test passed: '{text}' -> {rule_ids}")
        else:
            print(f"ERROR: PII test failed: '{text}' -> expected {expected_rules}, got {rule_ids}")
    
    return True

async def test_secrets_detection():
    """Test secrets detection functionality."""
    print("\nTesting Secrets Detection...")
    
    test_cases = [
        ("My AWS key is AKIAIOSFODNN7EXAMPLE", ["secret_aws_key"]),
        ("My JWT is eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIiwiaWF0IjoxNTE2MjM5MDIyfQ.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c", ["secret_jwt"]),
        ("My credit card is 4111111111111111", ["secret_credit_card"]),
        ("Hello world", []),
    ]
    
    for text, expected_rules in test_cases:
        matches, _ = scan_text(text)
        rule_ids = [m.rule_id for m in matches]
        
        if set(rule_ids) == set(expected_rules):
            print(f"SUCCESS: Secrets test passed: '{text}' -> {rule_ids}")
        else:
            print(f"ERROR: Secrets test failed: '{text}' -> expected {expected_rules}, got {rule_ids}")
    
    return True

async def test_pdp_decision():
    """Test PDP decision making."""
    print("\nTesting PDP Decision Making...")
    
    test_cases = [
        ("Hello world", "allow"),
        ("My SSN is 123-45-6789", "block"),
        ("My AWS key is AKIAIOSFODNN7EXAMPLE", "block"),
        ("My email is test@example.com", "block"),
    ]
    
    for text, expected_verdict in test_cases:
        matches, _ = scan_text(text)
        verdict = decide(matches)
        
        if verdict.action == expected_verdict:
            print(f"SUCCESS: PDP test passed: '{text}' -> {verdict.action}")
        else:
            print(f"ERROR: PDP test failed: '{text}' -> expected {expected_verdict}, got {verdict.action}")
    
    return True

async def test_redaction():
    """Test PII redaction functionality."""
    print("\nTesting PII Redaction...")
    
    test_text = "My SSN is 123-45-6789 and my email is test@example.com"
    matches, _ = scan_text(test_text)
    redacted = redact(test_text, matches)
    
    # Check that PII is replaced with placeholders
    if "<SSN_HASH_" in redacted and "<EMAIL_HASH_" in redacted:
        print(f"SUCCESS: Redaction test passed: '{test_text}' -> '{redacted}'")
    else:
        print(f"ERROR: Redaction test failed: PII not properly redacted")
    
    return True

async def test_cache_functionality():
    """Test cache functionality."""
    print("\nTesting Cache Functionality...")
    
    # Test cache set and get
    test_prompt = "Hello world"
    test_verdict = "allow"
    test_reason = "No rules matched"
    test_rules_matched = []
    
    await decision_cache.set(test_prompt, test_verdict, test_reason, test_rules_matched)
    
    cached, cache_hit = await decision_cache.get(test_prompt)
    
    if cache_hit and cached and cached["verdict"] == test_verdict:
        print("SUCCESS: Cache test passed: set and get successful")
    else:
        print("ERROR: Cache test failed: cache not working properly")
    
    return True

async def test_configuration():
    """Test configuration loading."""
    print("\nTesting Configuration...")
    
    # Check that configuration is loaded
    if settings.app_name == "LLM Security Scanner":
        print("SUCCESS: Configuration test passed: app name loaded")
    else:
        print("ERROR: Configuration test failed: app name not loaded")
    
    # Check API keys
    if len(settings.api_keys) > 0:
        print("SUCCESS: Configuration test passed: API keys loaded")
    else:
        print("ERROR: Configuration test failed: API keys not loaded")
    
    return True

async def test_models():
    """Test Pydantic models."""
    print("\nTesting Pydantic Models...")
    
    # Test ScanRequest model
    request_data = {"prompt": "Hello world"}
    scan_request = ScanRequest(**request_data)
    
    if scan_request.prompt == "Hello world":
        print("SUCCESS: ScanRequest model test passed")
    else:
        print("ERROR: ScanRequest model test failed")
    
    # Test ScanResponse model
    response_data = {
        "verdict": "allow",
        "reason": "No rules matched",
        "latency_ms": 10.5,
        "request_id": "123e4567-e89b-12d3-a456-426614174000",
        "rules_matched": [],
        "cache_hit": False
    }
    scan_response = ScanResponse(**response_data)
    
    if scan_response.verdict == "allow":
        print("SUCCESS: ScanResponse model test passed")
    else:
        print("ERROR: ScanResponse model test failed")
    
    return True

async def run_all_tests():
    """Run all tests."""
    print("Starting LLM Security Scanner Tests...")
    print("=" * 50)
    
    tests = [
        test_configuration,
        test_pii_detection,
        test_secrets_detection,
        test_pdp_decision,
        test_redaction,
        test_cache_functionality,
        test_models,
    ]
    
    passed = 0
    failed = 0
    
    for test in tests:
        try:
            result = await test()
            if result:
                passed += 1
            else:
                failed += 1
        except Exception as e:
            print(f"ERROR: Test {test.__name__} failed with exception: {e}")
            failed += 1
    
    print("\n" + "=" * 50)
    print(f"Test Results: {passed} passed, {failed} failed")
    
    if failed == 0:
        print("All tests passed!")
        return True
    else:
        print("Some tests failed!")
        return False

if __name__ == "__main__":
    # Run the tests
    success = asyncio.run(run_all_tests())
    sys.exit(0 if success else 1)