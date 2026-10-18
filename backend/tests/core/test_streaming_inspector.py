from __future__ import annotations

import pytest
from app.core.streaming.inspector import StreamingInspector
from app.core.streaming.types import InspectResult


class TestStreamingInspector:
    """Test the minimal streaming inspector."""
    
    @pytest.mark.asyncio
    async def test_inspector_allow_clean_text(self):
        """Test inspector allows clean text."""
        inspector = StreamingInspector()
        result = await inspector.inspect_chunk("Hello world")
        
        assert result.blocked is False
        assert result.decision is None
        assert result.accumulated_text == "Hello world"
    
    @pytest.mark.asyncio
    async def test_inspector_block_pii(self):
        """Test inspector blocks PII."""
        inspector = StreamingInspector()
        
        # Test with SSN
        result = await inspector.inspect_chunk("My SSN is 123-45-6789")
        
        assert result.blocked is True
        assert result.decision is not None
        assert "pii_ssn_us" in result.decision.reason
        assert result.decision.rule_ids is not None
    
    @pytest.mark.asyncio
    async def test_inspector_boundary_detection(self):
        """Test inspector detects PII across chunk boundaries."""
        inspector = StreamingInspector()
        
        # First chunk
        result1 = await inspector.inspect_chunk("My SSN is 123-45-")
        assert result1.blocked is False
        
        # Second chunk completes SSN
        result2 = await inspector.inspect_chunk("6789")
        assert result2.blocked is True
        assert result2.decision is not None
        assert "pii_ssn_us" in result2.decision.reason
    
    @pytest.mark.asyncio
    async def test_inspector_finalize(self):
        """Test finalize method."""
        inspector = StreamingInspector()
        
        # Add some text
        await inspector.inspect_chunk("Hello world")
        
        # Finalize
        result = await inspector.finalize()
        
        assert result.blocked is False
        assert result.accumulated_text == "Hello world"
    
    @pytest.mark.asyncio
    async def test_inspector_window_size(self):
        """Test that inspector respects window size."""
        inspector = StreamingInspector(chunk_window_size=100)
        
        # Add text larger than window
        long_text = "Hello world " * 20  # 220 characters
        result = await inspector.inspect_chunk(long_text)
        
        # Should still work (just truncated window)
        assert result.accumulated_text == long_text
    
    @pytest.mark.asyncio
    async def test_inspector_multiple_chunks(self):
        """Test inspector with multiple chunks."""
        inspector = StreamingInspector()
        
        # Add multiple chunks
        await inspector.inspect_chunk("Hello ")
        await inspector.inspect_chunk("world ")
        await inspector.inspect_chunk("how are ")
        await inspector.inspect_chunk("you?")
        
        # Check accumulated text
        assert inspector.accumulated_text == "Hello world how are you?"
        
        # Final inspection
        result = await inspector.finalize()
        assert result.blocked is False
        assert result.accumulated_text == "Hello world how are you?"
    
    @pytest.mark.asyncio
    async def test_inspector_pii_in_middle(self):
        """Test inspector detects PII in middle of text."""
        inspector = StreamingInspector()
        
        # Add clean text first
        await inspector.inspect_chunk("Hello my name is ")
        
        # Add PII
        result = await inspector.inspect_chunk("John and my SSN is 123-45-6789")
        
        assert result.blocked is True
        assert result.decision is not None
        assert "pii_ssn_us" in result.decision.reason
    
    @pytest.mark.asyncio
    async def test_inspector_get_prompt_hash(self):
        """Test prompt hash generation."""
        inspector = StreamingInspector()
        
        # Add text
        await inspector.inspect_chunk("Hello world")
        
        # Get hash
        hash1 = inspector.get_prompt_hash()
        
        # Add more text
        await inspector.inspect_chunk(" How are you?")
        
        # Get new hash
        hash2 = inspector.get_prompt_hash()
        
        # Hashes should be different
        assert hash1 != hash2
    
    @pytest.mark.asyncio
    async def test_inspector_get_matches(self):
        """Test getting matches from accumulated text."""
        inspector = StreamingInspector()
        
        # Add text with PII
        await inspector.inspect_chunk("My email is test@example.com and SSN is 123-45-6789")
        
        # Get matches
        matches = inspector.get_matches()
        
        # Should have matches for email and SSN
        assert len(matches) >= 2
        match_types = [m.get("rule_id") for m in matches]
        assert "pii_email" in match_types
        assert "pii_ssn_us" in match_types