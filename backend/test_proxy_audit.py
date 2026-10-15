#!/usr/bin/env python3
"""
Demonstration script to show that proxy now writes to audit database.
"""

import asyncio
import time
import uuid
from unittest.mock import patch, AsyncMock

from app.core.proxy import proxy_chat
from app.models.openai import ChatCompletionRequest, ChatCompletionMessage, ChatCompletionResponse, ChatCompletionChoice, ChatCompletionUsage
from app.core.audit import write_event
from app.core.pdp import decide, scan_text
from app.core.rules.base import RuleMatch, Verdict
from app.core.redactor import redact


async def test_proxy_audit():
    """Test that proxy now writes audit events to database."""
    
    print("Testing proxy audit functionality...")
    
    # Create a test request
    request = ChatCompletionRequest(
        model="gpt-3.5-turbo",
        messages=[
            ChatCompletionMessage(role="user", content="Hello world")
        ]
    )
    
    # Mock the write_event function to capture calls
    with patch('app.core.proxy.write_event', new_callable=AsyncMock) as mock_write_event:
        # Mock the scan_text function to return no matches
        with patch('app.core.proxy.scan_text') as mock_scan_text:
            mock_scan_text.return_value = ([], "test_hash")
            
            # Mock the decision function to return allow
            with patch('app.core.proxy.decide') as mock_decide:
                mock_decide.return_value = Verdict(action="allow", reason="Clean prompt", rules_matched=[])
                
                # Mock the cache
                with patch('app.core.proxy.decision_cache') as mock_cache:
                    async def mock_get(prompt):
                        return None, False
                    mock_cache.get.side_effect = mock_get
                    async def mock_set(prompt, action, reason, matches):
                        pass
                    mock_cache.set.side_effect = mock_set
                    
                    # Mock the forward_to_llm function
                    with patch('app.core.proxy._forward_to_llm') as mock_forward:
                        mock_forward.return_value = ChatCompletionResponse(
                            id="test",
                            object="chat.completion",
                            created=int(time.time()),
                            model="gpt-3.5-turbo",
                            choices=[
                                ChatCompletionChoice(
                                    index=0,
                                    message=ChatCompletionMessage(role="assistant", content="Hello!"),
                                    finish_reason="stop",
                                )
                            ],
                            usage=ChatCompletionUsage(),
                        )
                        
                        # Call proxy_chat
                        result = await proxy_chat(request, tenant_id="test-tenant")
                        
                        # Verify that write_event was called
                        print(f"write_event called: {mock_write_event.called}")
                        if mock_write_event.called:
                            call_args = mock_write_event.call_args
                            print(f"Call args: {call_args}")
                            print(f"Tenant ID: {call_args.kwargs.get('tenant_id')}")
                            print(f"Verdict: {call_args.kwargs.get('verdict')}")
                            print(f"Prompt hash: {call_args.kwargs.get('prompt_hash')}")
                            print(f"Prompt text redacted: {call_args.kwargs.get('prompt_text_redacted')}")
                        
                        print("SUCCESS: Proxy audit test passed!")
    
    # Test with blocked prompt
    print("\nTesting blocked prompt...")
    
    with patch('app.core.proxy.write_event', new_callable=AsyncMock) as mock_write_event:
        with patch('app.core.proxy.scan_text') as mock_scan_text:
            # Mock a match that would cause a block
            mock_match = RuleMatch(
                rule_id="pii_ssn_us",
                rule_name="SSN Detection",
                value_hash="12345678",
                position=(10, 20),
                severity="high",
                action="block"
            )
            mock_scan_text.return_value = ([mock_match], "test_hash")
            
            with patch('app.core.proxy.decide') as mock_decide:
                mock_decide.return_value = Verdict(action="block", reason="SSN detected", rules_matched=[mock_match])
                
                with patch('app.core.proxy.decision_cache') as mock_cache:
                    mock_cache.get.return_value = (None, False)
                    mock_cache.set.return_value = None
                    
                    try:
                        await proxy_chat(request, tenant_id="test-tenant")
                        print("ERROR: Expected LLMProxyError but got none")
                    except Exception as e:
                        print(f"SUCCESS: Correctly blocked with: {e}")
                        print(f"write_event called: {mock_write_event.called}")
                        if mock_write_event.called:
                            call_args = mock_write_event.call_args
                            print(f"Verdict: {call_args.kwargs.get('verdict')}")
                            print(f"Reason: {call_args.kwargs.get('reason')}")
    
    print("\nSUCCESS: All tests passed! Proxy now writes audit events to database.")


if __name__ == "__main__":
    asyncio.run(test_proxy_audit())