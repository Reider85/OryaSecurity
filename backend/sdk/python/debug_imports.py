#!/usr/bin/env python3
"""Debug script to check imports step by step."""

import sys
import os

# Add the current directory to the Python path
sys.path.insert(0, os.path.dirname(__file__))

print("Python path:", sys.path)
print("Current directory:", os.getcwd())

try:
    import llm_security_scanner
    print("✅ llm_security_scanner module imported")
    
    from llm_security_scanner import ScannerClient
    print("✅ ScannerClient imported")
    
    from llm_security_scanner import AsyncScannerClient
    print("✅ AsyncScannerClient imported")
    
    from llm_security_scanner import ScanResult
    print("✅ ScanResult imported")
    
    from llm_security_scanner import ChatCompletionResponse
    print("✅ ChatCompletionResponse imported")
    
    from llm_security_scanner import RuleMatch
    print("✅ RuleMatch imported")
    
    from llm_security_scanner import ScannerError, AuthenticationError, RateLimitError, ScannerAPIError
    print("✅ All error classes imported")
    
    print("🎉 All imports successful!")
    
except ImportError as e:
    print(f"❌ Import failed: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
except Exception as e:
    print(f"❌ Unexpected error: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)