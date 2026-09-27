#!/usr/bin/env python3
"""Simple test script to verify rate limiter functionality."""

import asyncio
import sys
import os

# Add the current directory to Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.core.rate_limit import RateLimiter


async def test_rate_limiter():
    """Test basic rate limiter functionality."""
    print("Testing RateLimiter...")
    
    # Create rate limiter with 5 RPS
    rate_limiter = RateLimiter(max_rps=5)
    
    # Test within limit
    print("Testing within limit...")
    for i in range(5):
        await rate_limiter.check("test_key")
        print(f"Request {i+1} allowed")
    
    # Test rate limit exceeded
    print("Testing rate limit exceeded...")
    try:
        await rate_limiter.check("test_key")
        print("ERROR: Should have been rate limited!")
        return False
    except Exception as e:
        print(f"Correctly rate limited: {e}")
    
    # Test sliding window
    print("Testing sliding window...")
    await asyncio.sleep(1.1)  # Wait for window to slide
    try:
        await rate_limiter.check("test_key")
        print("Correctly allowed after window slide")
    except Exception as e:
        print(f"ERROR: Should have been allowed after window slide: {e}")
        return False
    
    print("All tests passed!")
    return True


if __name__ == "__main__":
    success = asyncio.run(test_rate_limiter())
    sys.exit(0 if success else 1)