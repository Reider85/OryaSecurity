#!/usr/bin/env python3
"""Validation script for LLM Security Scanner SDK."""

import sys
import os

# Add the current directory to the Python path
sys.path.insert(0, os.path.dirname(__file__))

def test_imports():
    """Test that all modules can be imported."""
    try:
        from llm_security_scanner import ScannerClient, AsyncScannerClient
        from llm_security_scanner import ScanResult, ChatCompletionResponse, RuleMatch
        from llm_security_scanner import ScannerError, AuthenticationError, RateLimitError, ScannerAPIError
        print("✅ All imports successful")
        return True
    except ImportError as e:
        print(f"❌ Import failed: {e}")
        return False

def test_client_creation():
    """Test client creation."""
    try:
        from llm_security_scanner import ScannerClient, AsyncScannerClient
        
        # Test sync client
        sync_client = ScannerClient("http://localhost:8000", "test-api-key")
        print("✅ ScannerClient created")
        
        # Test async client
        async_client = AsyncScannerClient("http://localhost:8000", "test-api-key")
        print("✅ AsyncScannerClient created")
        
        return True
    except Exception as e:
        print(f"❌ Client creation failed: {e}")
        return False

def test_model_creation():
    """Test model creation."""
    try:
        from llm_security_scanner import ScanResult, RuleMatch, ChatCompletionResponse
        
        # Test RuleMatch
        rule_match = RuleMatch(
            rule_id="test_rule",
            rule_name="Test Rule",
            value_hash="abc123",
            position=[0, 10],
            severity="high",
            action="block"
        )
        print("✅ RuleMatch created")
        
        # Test ScanResult
        scan_result = ScanResult(
            verdict="allow",
            reason="No issues",
            latency_ms=25.5,
            request_id="123e4567-e89b-12d3-a456-426614174000",
            rules_matched=[rule_match],
            cache_hit=False
        )
        print("✅ ScanResult created")
        
        return True
    except Exception as e:
        print(f"❌ Model creation failed: {e}")
        return False

def test_error_classes():
    """Test error classes."""
    try:
        from llm_security_scanner import ScannerError, AuthenticationError, RateLimitError, ScannerAPIError
        
        # Test error creation
        try:
            raise AuthenticationError("Invalid API key")
        except AuthenticationError:
            print("✅ AuthenticationError works")
        
        try:
            raise RateLimitError("Rate limit exceeded", 60)
        except RateLimitError as e:
            print("✅ RateLimitError works")
            if e.retry_after == 60:
                print("✅ Retry-after attribute works")
        
        try:
            raise ScannerAPIError("API error", 500, "Internal server error")
        except ScannerAPIError as e:
            print("✅ ScannerAPIError works")
            if e.status_code == 500:
                print("✅ Status code attribute works")
        
        return True
    except Exception as e:
        print(f"❌ Error classes test failed: {e}")
        return False

def main():
    """Run all tests."""
    print("🔍 Testing LLM Security Scanner SDK...")
    print("=" * 50)
    
    tests = [
        test_imports,
        test_client_creation,
        test_model_creation,
        test_error_classes,
    ]
    
    passed = 0
    total = len(tests)
    
    for test in tests:
        if test():
            passed += 1
        print()
    
    print("=" * 50)
    print(f"📊 Results: {passed}/{total} tests passed")
    
    if passed == total:
        print("🎉 All tests passed! The SDK is ready.")
        return 0
    else:
        print("❌ Some tests failed. Please check the implementation.")
        return 1

if __name__ == "__main__":
    sys.exit(main())