#!/usr/bin/env python3
"""Simple test script to verify SDK functionality."""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

try:
    from llm_security_scanner import ScannerClient, AsyncScannerClient, ScanResult
    print("✅ SDK imported successfully")
    
    # Test client creation
    client = ScannerClient("http://localhost:8000", "test-api-key")
    print("✅ ScannerClient created successfully")
    
    # Test async client creation
    async_client = AsyncScannerClient("http://localhost:8000", "test-api-key")
    print("✅ AsyncScannerClient created successfully")
    
    # Test model creation
    from llm_security_scanner import RuleMatch
    rule_match = RuleMatch(
        rule_id="test_rule",
        rule_name="Test Rule",
        value_hash="abc123",
        position=[0, 10],
        severity="high",
        action="block"
    )
    print("✅ RuleMatch model created successfully")
    
    print("\n🎉 All basic tests passed!")
    print("The SDK is ready for use.")
    
except ImportError as e:
    print(f"❌ Import error: {e}")
    sys.exit(1)
except Exception as e:
    print(f"❌ Error: {e}")
    sys.exit(1)