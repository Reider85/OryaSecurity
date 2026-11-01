"""Shared OpenAPI response definitions for v2 endpoints (EP-41.6).

Documents the *actual* wire formats produced by the handlers:
- ``{"detail": "..."}`` for FastAPI ``HTTPException`` (401/403/422),
- ``{"error": "..."}`` for JSON error responses (429/500/503),
- ``{"error": {"message", "type", ...}}`` for DoS middleware (413) and
  scanner SSE error events.
"""

from __future__ import annotations

from typing import Any

# --- reusable schemas -------------------------------------------------------

DETAIL_ERROR_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["detail"],
    "properties": {
        "detail": {
            "type": "string",
            "description": "Human-readable error message.",
            "examples": ["Invalid API key"],
        }
    },
}

PLAIN_ERROR_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["error"],
    "properties": {
        "error": {
            "type": "string",
            "description": "Human-readable error message.",
            "examples": ["Rate limit check failed"],
        }
    },
}

DOS_ERROR_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["error"],
    "properties": {
        "error": {
            "type": "object",
            "required": ["message", "type"],
            "properties": {
                "message": {"type": "string", "examples": ["Request rejected for security reasons"]},
                "type": {"type": "string", "examples": ["dos_protection"]},
                "reason": {"type": "string", "examples": ["max_length_exceeded"]},
                "complexity_score": {"type": "integer", "examples": [4200]},
            },
        }
    },
}


def _json(schema: dict[str, Any], example: dict[str, Any] | None = None) -> dict[str, Any]:
    content: dict[str, Any] = {"schema": schema}
    if example is not None:
        content["examples"] = {"example": {"value": example}}
    return {"content": {"application/json": content}}


# --- shared response entries ------------------------------------------------

UNAUTHORIZED: dict[str, Any] = {
    "description": "Missing or invalid Authorization credential.",
    **_json(DETAIL_ERROR_SCHEMA, {"detail": "Invalid API key"}),
}

FORBIDDEN: dict[str, Any] = {
    "description": "Credential is valid but not permitted (inactive key, policy block).",
    **_json(DETAIL_ERROR_SCHEMA, {"detail": "API key is inactive"}),
}

PAYLOAD_TOO_LARGE: dict[str, Any] = {
    "description": "Prompt rejected by DoS protection (length/complexity).",
    **_json(DOS_ERROR_SCHEMA, {"error": {
        "message": "Request rejected for security reasons",
        "type": "dos_protection",
        "reason": "max_length_exceeded",
        "complexity_score": 4200,
    }}),
}

RATE_LIMITED: dict[str, Any] = {
    "description": (
        "Rate limit exceeded. Includes `Retry-After` and "
        "`X-RateLimit-Limit` / `X-RateLimit-Remaining` headers."
    ),
    "headers": {
        "Retry-After": {"schema": {"type": "integer"}, "description": "Seconds to wait."},
        "X-RateLimit-Limit": {"schema": {"type": "integer"}},
        "X-RateLimit-Remaining": {"schema": {"type": "integer"}},
    },
    **_json(PLAIN_ERROR_SCHEMA, {"error": "Rate limit check failed"}),
}

INTERNAL_ERROR: dict[str, Any] = {
    "description": "Unexpected server error.",
    **_json(PLAIN_ERROR_SCHEMA, {"error": "Internal server error"}),
}

SERVICE_UNAVAILABLE: dict[str, Any] = {
    "description": "Downstream component (classifier / embedding service) unavailable.",
    **_json(PLAIN_ERROR_SCHEMA, {"error": "Classification service unavailable"}),
}


def common_error_responses(
    *,
    include_413: bool = False,
    unavailable_description: str | None = None,
) -> dict[int, dict[str, Any]]:
    """Build the standard 4xx/5xx response map for a v2 endpoint."""
    responses: dict[int, dict[str, Any]] = {
        401: UNAUTHORIZED,
        403: FORBIDDEN,
        429: RATE_LIMITED,
        500: INTERNAL_ERROR,
        503: SERVICE_UNAVAILABLE if unavailable_description is None else {
            "description": unavailable_description,
            **_json(PLAIN_ERROR_SCHEMA, {"error": "Service unavailable"}),
        },
    }
    if include_413:
        responses[413] = PAYLOAD_TOO_LARGE
    return responses
