from __future__ import annotations

import asyncio
import hashlib
import structlog
from typing import Any, Awaitable, Callable, Optional

from app.core.metrics import scanner_stream_blocks_total, scanner_stream_chunks_total
from app.core.pdp import decide
from app.core.detectors.streaming_rules import match_on_buffer, DEFAULT_PREV_TAIL_SIZE
from app.core.streaming.types import BlockDecision, InspectResult

logger = structlog.get_logger()

# Default number of "tokens" (inspect_chunk calls) buffered before running checks.
DEFAULT_CHUNK_SIZE = 8

# Timeout for async post-factum deep analysis.
POST_FACTUM_TIMEOUT_SECONDS = 5.0


class StreamingInspector:
    """Chunk-based streaming inspector.

    Buffers up to ``chunk_size`` incoming "tokens" (each ``inspect_chunk`` call
    counts as one token), then runs a fast regex check (PII/secrets) plus an
    optional lightweight ML toxicity classifier on the joined buffer. After the
    stream completes, ``finalize`` flushes any remaining buffer and schedules
    an async post-factum deep analysis via an injectable hook.
    """

    def __init__(
        self,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        prev_tail_size: int = DEFAULT_PREV_TAIL_SIZE,
        toxicity_detector: Optional[Any] = None,
        post_factum_hook: Optional[
            Callable[[str], Awaitable[None]]
        ] = None,
    ):
        self.chunk_size = max(1, chunk_size)
        self.prev_tail_size = max(0, prev_tail_size)
        self.toxicity_detector = toxicity_detector
        self.post_factum_hook = post_factum_hook

        self.buffer: list[str] = []
        self.accumulated_text: str = ""
        self.prev_tail: str = ""
        self._blocked: bool = False

    async def inspect_chunk(self, text: str) -> InspectResult:
        """Buffer a token and run checks once the buffer is full.

        Args:
            text: One streamed token/chunk from the LLM.

        Returns:
            InspectResult: allow (buffer not yet full or no matches) or block.
        """
        scanner_stream_chunks_total.inc()

        if self._blocked:
            # Already blocked earlier in the stream — keep returning block.
            return InspectResult.block(
                BlockDecision(reason="already_blocked"),
                accumulated_text=self.accumulated_text,
            )

        self.buffer.append(text)
        self.accumulated_text += text

        if len(self.buffer) < self.chunk_size:
            return InspectResult.allow(accumulated_text=self.accumulated_text)

        return await self._run_checks()

    async def _run_checks(self) -> InspectResult:
        """Run regex + toxicity checks on the current buffer."""
        if not self.buffer:
            return InspectResult.allow(accumulated_text=self.accumulated_text)

        joined = "".join(self.buffer)
        inspection_text = self.prev_tail + joined

        # Fast regex check (PII, secrets) with boundary support via prev_tail.
        # Use dedicated streaming_rules module to avoid duplicate matches from prev_tail
        matches = match_on_buffer(joined, self.prev_tail)

        if matches:
            verdict = decide(matches)
            if verdict.action == "block":
                self._blocked = True
                block_match = next(
                    (m for m in matches if m.action == "block"), matches[0]
                )
                # Translate match position in inspection_text back to
                # accumulated_text coordinates (inspection_text starts with prev_tail).
                window_start = len(self.accumulated_text) - len(joined)
                rel_pos = max(0, block_match.position[0] - len(self.prev_tail))
                abs_pos = window_start + rel_pos
                decision = BlockDecision(
                    reason=verdict.reason,
                    position=abs_pos,
                    rule_ids=[m.rule_id for m in matches],
                )
                scanner_stream_blocks_total.labels(reason_type="rules").inc()
                return InspectResult.block(
                    decision, accumulated_text=self.accumulated_text
                )

        # Lightweight ML toxicity check (optional collaborator).
        if self.toxicity_detector is not None:
            tox = await self.toxicity_detector.check(joined)
            if tox.is_toxic:
                self._blocked = True
                scanner_stream_blocks_total.labels(reason_type="toxicity").inc()
                decision = BlockDecision(
                    reason=f"toxicity_score={tox.toxicity_score:.2f}",
                    position=len(self.accumulated_text) - len(joined),
                    rule_ids=["toxicity"],
                )
                return InspectResult.block(
                    decision, accumulated_text=self.accumulated_text
                )

        # No block — rotate buffer, keep a tail for boundary detection.
        self.prev_tail = (self.prev_tail + joined)[-self.prev_tail_size :]
        self.buffer = []
        return InspectResult.allow(accumulated_text=self.accumulated_text)

    async def finalize(self) -> InspectResult:
        """Flush remaining buffer and schedule async post-factum analysis.

        Returns:
            InspectResult: block if the leftover buffer trips a rule/toxicity,
            otherwise allow with the full accumulated text.
        """
        if self._blocked:
            return InspectResult.block(
                BlockDecision(reason="already_blocked"),
                accumulated_text=self.accumulated_text,
            )

        # Flush any tokens still sitting in the buffer.
        result = await self._run_checks()
        if result.blocked:
            return result

        # Schedule post-factum deep analysis in the background (Prompt 6 will
        # supply the real hook; default is a no-op with a log line).
        if self.post_factum_hook is not None:
            full_text = self.accumulated_text

            async def _run_post_factum() -> None:
                try:
                    await asyncio.wait_for(
                        self.post_factum_hook(full_text),
                        timeout=POST_FACTUM_TIMEOUT_SECONDS,
                    )
                except asyncio.TimeoutError:
                    logger.warning(
                        "post_factum_timeout",
                        timeout_seconds=POST_FACTUM_TIMEOUT_SECONDS,
                    )
                except Exception as exc:  # noqa: BLE001 — never crash the stream
                    logger.warning("post_factum_error", error=str(exc))

            asyncio.create_task(_run_post_factum())

        return InspectResult.allow(accumulated_text=self.accumulated_text)

    def get_prompt_hash(self) -> str:
        """Get hash of accumulated text for caching."""
        return hashlib.sha256(self.accumulated_text.encode()).hexdigest()

    def get_matches(self) -> list[dict[str, Any]]:
        """Get all matches from accumulated text."""
        # Use streaming_rules with empty prev_tail to scan full accumulated text
        matches = match_on_buffer(self.accumulated_text, "")
        return [m.to_dict() for m in matches]
