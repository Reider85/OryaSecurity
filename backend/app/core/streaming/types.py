from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class BlockDecision:
    """Decision to block a chunk."""
    reason: str
    position: int | None = None
    rule_ids: list[str] | None = None


@dataclass
class InspectResult:
    """Result of inspecting a chunk."""
    blocked: bool = False
    decision: BlockDecision | None = None
    accumulated_text: str = ""
    matches: list[dict[str, Any]] | None = None

    @classmethod
    def allow(cls, accumulated_text: str = "") -> "InspectResult":
        return cls(blocked=False, accumulated_text=accumulated_text)

    @classmethod
    def block(cls, decision: BlockDecision, accumulated_text: str = "") -> "InspectResult":
        return cls(
            blocked=True,
            decision=decision,
            accumulated_text=accumulated_text,
            matches=decision.rule_ids if decision.rule_ids else None
        )