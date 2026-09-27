#!/usr/bin/env python3
"""Simple validation script for LLM Security Scanner SDK."""

import sys
import os

# Add the current directory to the Python path
sys.path.insert(0, os.path.dirname(__file__))

try:
    from llm_security_scanner import ScannerClient, AsyncScannerClient
    from llm_security_scanner import ScanResult, ChatCompletionResponse, RuleMatch
    from llm_security_scanner import ScannerError, AuthenticationError, RateLimitError, ScannerAPIError
    print("✅ All imports successful")
    
    # Test client creation
    sync_client = ScannerClient("http://localhost:8000", "test-api-key")
    print("✅ ScannerClient created")
    
    async_client = AsyncScannerClient("http://localhost:8000", "test-api-key")
    print("✅ AsyncScannerClient created")
    
    # Test model creation
    rule_match = RuleMatch(
        rule_id="test_rule",
        rule_name="Test Rule",
        value_hash="abc123",
        position=[0, 10],
        severity="high",
        action="block"
    )
    print("✅ RuleMatch created")
    
    scan_result = ScanResult(
        verdict="allow",
        reason="No issues",
        latency_ms=25.5,
        request_id="123e4567-e89b-12d3-a456-426614174000",
        rules_matched=[rule_match],
        cache_hit=False
    )
    print("✅ ScanResult created")
    
    print("🎉 All tests passed! The SDK is ready.")
    
except ImportError as e:
    print(f"❌ Import failed: {e}")
    sys.exit(1)
except Exception as e:
    print(f"❌ Test failed: {e}")
    sys.exit(1)