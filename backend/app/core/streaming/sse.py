from __future__ import annotations

import json
from typing import Any

SSE_DONE = "data: [DONE]\n\n"


def format_sse(data: dict[str, Any] | str) -> str:
    """Format data as SSE event."""
    if isinstance(data, dict):
        return f"data: {json.dumps(data)}\n\n"
    elif isinstance(data, str):
        return f"data: {data}\n\n"
    else:
        raise ValueError("SSE data must be dict or string")


def scanner_blocked_event(reason: str, position: int | None = None, rule_ids: list[str] | None = None) -> dict[str, Any]:
    """Create scanner_blocked SSE event."""
    event = {
        "type": "scanner_blocked",
        "reason": reason,
    }
    if position is not None:
        event["position"] = position
    if rule_ids:
        event["rule_ids"] = rule_ids
    return event


def sse_response_headers(request_id: str) -> dict[str, str]:
    """Headers for SSE response."""
    return {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Request-Id": request_id,
    }