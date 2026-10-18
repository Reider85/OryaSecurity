from __future__ import annotations

import hashlib
from typing import Any

from app.core.pdp import decide, scan_text
from app.core.rules.base import RuleMatch
from app.core.streaming.types import BlockDecision, InspectResult


class StreamingInspector:
    """Minimal streaming inspector using existing rule engine."""

    def __init__(self, chunk_window_size: int = 512, prev_tail_size: int = 20):
        self.accumulated_text = ""
        self.chunk_window_size = chunk_window_size
        self.prev_tail_size = prev_tail_size

    async def inspect_chunk(self, text: str) -> InspectResult:
        """Inspect a chunk and return result.
        
        Args:
            text: Text chunk from LLM
            
        Returns:
            InspectResult: allow/block decision with accumulated text and matches
        """
        # Accumulate text
        self.accumulated_text += text
        
        # Create inspection window: accumulated text + previous tail for boundary detection
        inspection_text = self.accumulated_text
        
        # For boundary detection, include previous tail if available
        if len(self.accumulated_text) > self.chunk_window_size:
            # Use window from accumulated text
            inspection_text = self.accumulated_text[-self.chunk_window_size:]
        
        # Scan the inspection window
        matches, _ = scan_text(inspection_text)
        
        if not matches:
            # No matches found
            return InspectResult.allow(accumulated_text=self.accumulated_text)
        
        # Make decision using existing rule engine
        verdict = decide(matches)
        
        if verdict.action == "block":
            # Convert matches to rule_ids for BlockDecision
            rule_ids = [m.rule_id for m in matches]
            decision = BlockDecision(
                reason=verdict.reason,
                rule_ids=rule_ids
            )
            return InspectResult.block(decision, accumulated_text=self.accumulated_text)
        
        # Allow but log matches
        return InspectResult.allow(accumulated_text=self.accumulated_text)

    async def finalize(self) -> InspectResult:
        """Final inspection after stream completes.
        
        Returns:
            InspectResult: allow result (stub for post-factum analysis)
        """
        # Stub for post-factum analysis (to be implemented in Prompt 6)
        # For now, just return allow with accumulated text
        return InspectResult.allow(accumulated_text=self.accumulated_text)

    def get_prompt_hash(self) -> str:
        """Get hash of accumulated text for caching."""
        return hashlib.sha256(self.accumulated_text.encode()).hexdigest()

    def get_matches(self) -> list[dict[str, Any]]:
        """Get all matches from accumulated text."""
        matches, _ = scan_text(self.accumulated_text)
        return [m.to_dict() for m in matches]