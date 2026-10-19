from __future__ import annotations

import asyncio

import pytest

from app.core.detectors.toxicity import ToxicityDetector, ToxicityResult
from app.core.streaming.inspector import StreamingInspector
from app.core.streaming.types import InspectResult


class _StubToxicityDetector:
    """Controllable stub for tests."""

    def __init__(self, score: float, is_toxic: bool):
        self._result = ToxicityResult(
            toxicity_score=score, is_toxic=is_toxic, confidence=0.9
        )

    async def check(self, text: str) -> ToxicityResult:
        return self._result


class TestStreamingInspector:
    """Chunk-based streaming inspector (N=8 token buffer)."""

    @pytest.mark.asyncio
    async def test_buffer_below_chunk_size_no_block(self):
        """Chunks below chunk_size are buffered without triggering checks."""
        inspector = StreamingInspector(chunk_size=8)
        result = await inspector.inspect_chunk("My SSN is 123-45-6789")

        assert result.blocked is False
        assert result.decision is None
        assert result.accumulated_text == "My SSN is 123-45-6789"

    @pytest.mark.asyncio
    async def test_block_when_buffer_full_with_pii(self):
        """Block is returned once the buffer fills and PII is present."""
        inspector = StreamingInspector(chunk_size=3)

        await inspector.inspect_chunk("Hello ")
        await inspector.inspect_chunk("my SSN is ")
        result = await inspector.inspect_chunk("123-45-6789")

        assert result.blocked is True
        assert result.decision is not None
        assert "pii_ssn_us" in result.decision.reason
        assert result.decision.rule_ids is not None
        assert "pii_ssn_us" in result.decision.rule_ids

    @pytest.mark.asyncio
    async def test_boundary_detection_across_buffers(self):
        """PII split across buffer rotations is detected via prev_tail."""
        inspector = StreamingInspector(chunk_size=2, prev_tail_size=20)

        # First buffer: partial SSN at the end
        await inspector.inspect_chunk("SSN is 123-45-")
        r1 = await inspector.inspect_chunk("67")
        assert r1.blocked is False

        # Second buffer completes the SSN; prev_tail carries "123-45-" forward
        await inspector.inspect_chunk("89 and more")
        r2 = await inspector.inspect_chunk(" text")
        assert r2.blocked is True
        assert r2.decision is not None
        assert "pii_ssn_us" in r2.decision.reason

    @pytest.mark.asyncio
    async def test_finalize_flushes_remaining_buffer_with_block(self):
        """Finalize runs checks on leftover buffer and returns block."""
        inspector = StreamingInspector(chunk_size=8)

        await inspector.inspect_chunk("My SSN is 123-45-6789")
        result = await inspector.finalize()

        assert result.blocked is True
        assert result.decision is not None
        assert "pii_ssn_us" in result.decision.reason

    @pytest.mark.asyncio
    async def test_finalize_allows_clean_text(self):
        """Finalize on clean leftover buffer returns allow."""
        inspector = StreamingInspector(chunk_size=8)

        await inspector.inspect_chunk("Hello world")
        result = await inspector.finalize()

        assert result.blocked is False
        assert result.accumulated_text == "Hello world"

    @pytest.mark.asyncio
    async def test_toxicity_block_via_stub(self):
        """Toxicity detector stub can force a block."""
        inspector = StreamingInspector(
            chunk_size=2,
            toxicity_detector=_StubToxicityDetector(score=0.9, is_toxic=True),
        )

        await inspector.inspect_chunk("some ")
        result = await inspector.inspect_chunk("text")

        assert result.blocked is True
        assert result.decision is not None
        assert "toxicity_score" in result.decision.reason
        assert result.decision.rule_ids == ["toxicity"]

    @pytest.mark.asyncio
    async def test_toxicity_pass_when_not_toxic(self):
        """Toxicity detector with is_toxic=False does not block."""
        inspector = StreamingInspector(
            chunk_size=2,
            toxicity_detector=_StubToxicityDetector(score=0.1, is_toxic=False),
        )

        await inspector.inspect_chunk("some ")
        result = await inspector.inspect_chunk("text")

        assert result.blocked is False

    @pytest.mark.asyncio
    async def test_post_factum_hook_called_on_finalize(self):
        """Post-factum hook is scheduled on finalize."""
        called = asyncio.Event()
        received: list[str] = []

        async def hook(text: str) -> None:
            received.append(text)
            called.set()

        inspector = StreamingInspector(chunk_size=8, post_factum_hook=hook)
        await inspector.inspect_chunk("Hello world")
        await inspector.finalize()

        # Wait for the background task to complete
        await asyncio.wait_for(called.wait(), timeout=2.0)
        assert received == ["Hello world"]

    @pytest.mark.asyncio
    async def test_post_factum_hook_timeout_logged_not_raised(self):
        """Hook timeout is swallowed (logged), does not raise."""

        async def slow_hook(text: str) -> None:
            await asyncio.sleep(10)

        inspector = StreamingInspector(chunk_size=8, post_factum_hook=slow_hook)
        await inspector.inspect_chunk("Hello")
        result = await inspector.finalize()

        assert result.blocked is False

    @pytest.mark.asyncio
    async def test_blocked_state_sticky(self):
        """Once blocked, subsequent inspect_chunk calls keep returning block."""
        inspector = StreamingInspector(chunk_size=2)

        await inspector.inspect_chunk("SSN ")
        blocked = await inspector.inspect_chunk("123-45-6789")
        assert blocked.blocked is True

        after = await inspector.inspect_chunk("more")
        assert after.blocked is True
        assert after.decision is not None
        assert after.decision.reason == "already_blocked"

    @pytest.mark.asyncio
    async def test_accumulated_text_grows(self):
        """Accumulated text includes every chunk regardless of buffering."""
        inspector = StreamingInspector(chunk_size=8)

        await inspector.inspect_chunk("Hello ")
        await inspector.inspect_chunk("world")
        await inspector.inspect_chunk("!")

        assert inspector.accumulated_text == "Hello world!"

    @pytest.mark.asyncio
    async def test_get_prompt_hash_changes(self):
        """Prompt hash changes as more text is accumulated."""
        inspector = StreamingInspector(chunk_size=8)

        await inspector.inspect_chunk("Hello")
        hash1 = inspector.get_prompt_hash()

        await inspector.inspect_chunk(" world")
        hash2 = inspector.get_prompt_hash()

        assert hash1 != hash2

    @pytest.mark.asyncio
    async def test_get_matches_from_accumulated(self):
        """get_matches scans full accumulated text."""
        inspector = StreamingInspector(chunk_size=8)

        await inspector.inspect_chunk("My email is test@example.com")
        matches = inspector.get_matches()

        assert any(m.get("rule_id") == "pii_email" for m in matches)

    @pytest.mark.asyncio
    async def test_chunk_size_configurable(self):
        """Custom chunk_size is respected."""
        inspector = StreamingInspector(chunk_size=1)
        result = await inspector.inspect_chunk("SSN 123-45-6789")
        assert result.blocked is True
