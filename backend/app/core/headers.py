"""Centralized X-Scanner-* response headers.

The scanner exposes a stable set of response headers so that reverse-proxy
clients can make routing decisions without parsing the response body:

- ``X-Scanner-Verdict``   — ``allow`` or ``block``
- ``X-Scanner-Reason``    — short human-readable reason for the verdict
- ``X-Scanner-Latency-Ms``— scanner processing time in milliseconds
- ``X-Scanner-Request-Id``— UUID v4 used for end-to-end tracing
- ``X-Scanner-Cache``     — ``HIT`` or ``MISS`` of the decision cache

Architecture
------------
Endpoints do not build headers themselves. They record what the scanner
decided on ``request.state`` via :func:`set_scanner_context`, and
:class:`ScannerHeadersMiddleware` turns that state into response headers once
the response is ready. This keeps header names in a single place and makes it
impossible for two endpoints to drift apart.

The middleware always emits ``X-Scanner-Request-Id`` and
``X-Scanner-Latency-Ms`` because both are meaningful for every route, including
infrastructure endpoints such as ``/health`` and ``/metrics``. The remaining
headers are only emitted when the request actually passed through the scanning
pipeline; reporting ``X-Scanner-Verdict: allow`` on a health check would be a
lie, so those headers are omitted instead.
"""

from __future__ import annotations

import time
import uuid
from typing import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

# Header names are part of the public contract with clients — keep them stable.
HEADER_VERDICT = "X-Scanner-Verdict"
HEADER_REASON = "X-Scanner-Reason"
HEADER_LATENCY = "X-Scanner-Latency-Ms"
HEADER_REQUEST_ID = "X-Scanner-Request-Id"
HEADER_CACHE = "X-Scanner-Cache"

# Longest reason we are willing to put in a header. Reasons are generated from
# rule ids and stay well below this, but truncating keeps a pathological rule
# name from breaking header parsing.
MAX_REASON_LENGTH = 200

# Attribute used to stash the middleware start timestamp on request.state.
_STATE_START_TIME = "scanner_start_time"
# Attribute holding the request id for the whole request lifecycle.
_STATE_REQUEST_ID = "scanner_request_id"


def set_scanner_context(
    request: Request,
    *,
    verdict: str,
    reason: str | None = None,
    latency_ms: float | None = None,
    request_id: str | None = None,
    cache: str | None = None,
) -> None:
    """Record a scanner decision for the current request.

    Call this from an endpoint that ran the scan pipeline. The values are not
    written to the response directly — the middleware picks them up on the way
    out and applies the headers.

    ``verdict`` is normalized to lower case so a caller cannot accidentally
    emit ``Block`` and break client comparisons.
    """
    request.state.scanner_verdict = verdict.lower()
    if reason is not None:
        request.state.scanner_reason = _sanitize_reason(reason)
    if latency_ms is not None:
        request.state.scanner_latency_ms = round(float(latency_ms), 3)
    if request_id is not None:
        request.state.scanner_request_id = str(request_id)
    if cache is not None:
        request.state.scanner_cache = _normalize_cache(cache)


def _sanitize_reason(reason: str) -> str:
    """Make a reason string safe to place in an HTTP header.

    Three things can make a reason unsafe, and all are handled here:

    * Newlines/carriage returns, which would allow header injection. Any
      whitespace run is collapsed to a single space.
    * Length, truncated to :data:`MAX_REASON_LENGTH` with an ASCII ellipsis.
    * Non-latin-1 characters. Starlette encodes header values as latin-1 and
      *raises* on anything outside it, so a reason containing e.g. a Cyrillic
      rule name would turn a 200 response into a 500. Out-of-range characters
      are replaced with ``?``, which keeps the header usable for diagnosis
      without letting a cosmetic problem break the request.
    """
    cleaned = " ".join(reason.split())
    cleaned = cleaned.encode("latin-1", errors="replace").decode("latin-1")
    if len(cleaned) > MAX_REASON_LENGTH:
        # Three ASCII dots rather than a typographic ellipsis: U+2026 is itself
        # outside latin-1 and would be replaced with '?'.
        cleaned = cleaned[: MAX_REASON_LENGTH - 3] + "..."
    return cleaned


def _normalize_cache(cache: str) -> str:
    """Normalize the cache marker to the documented HIT/MISS vocabulary."""
    return "HIT" if str(cache).strip().upper() == "HIT" else "MISS"


class ScannerHeadersMiddleware(BaseHTTPMiddleware):
    """Attach X-Scanner-* headers to every response.

    The middleware is deliberately tolerant: an endpoint that never called
    :func:`set_scanner_context` still gets a request id and a latency header,
    while verdict headers appear only for requests that were actually scanned.
    """

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request_id = str(uuid.uuid4())
        setattr(request.state, _STATE_START_TIME, time.perf_counter())
        setattr(request.state, _STATE_REQUEST_ID, request_id)

        response = await call_next(request)

        # Latency covers the endpoint plus any middleware already inside us.
        start_time = getattr(request.state, _STATE_START_TIME, None)
        if start_time is not None:
            elapsed_ms = (time.perf_counter() - start_time) * 1000

            # An endpoint-measured latency measures scanner work specifically,
            # which is the more useful number; fall back to total request time
            # for routes that do not measure it themselves.
            endpoint_latency = getattr(request.state, "scanner_latency_ms", None)
            latency = endpoint_latency if endpoint_latency is not None else elapsed_ms

            response.headers[HEADER_LATENCY] = f"{round(float(latency), 3)}"

        response.headers[HEADER_REQUEST_ID] = str(
            getattr(request.state, _STATE_REQUEST_ID, request_id)
        )

        verdict = getattr(request.state, "scanner_verdict", None)
        if verdict is not None:
            response.headers[HEADER_VERDICT] = verdict

        reason = getattr(request.state, "scanner_reason", None)
        if reason is not None:
            response.headers[HEADER_REASON] = _sanitize_reason(reason)

        cache = getattr(request.state, "scanner_cache", None)
        if cache is not None:
            response.headers[HEADER_CACHE] = _normalize_cache(cache)

        return response


def scanner_headers(
    *,
    verdict: str,
    reason: str | None = None,
    latency_ms: float | None = None,
    request_id: str | None = None,
    cache: str | None = None,
) -> dict[str, str]:
    """Build scanner headers for endpoints that must return their own Response.

    :func:`set_scanner_context` plus the middleware is the preferred path, since
    it keeps header names in one place. This helper exists for the cases where
    an endpoint has to return a pre-built ``Response`` object anyway and
    merging the headers in explicitly is clearer than round-tripping through
    ``request.state``.
    """
    headers: dict[str, str] = {HEADER_VERDICT: verdict.lower()}
    if reason is not None:
        headers[HEADER_REASON] = _sanitize_reason(reason)
    if latency_ms is not None:
        headers[HEADER_LATENCY] = f"{round(float(latency_ms), 3)}"
    if request_id is not None:
        headers[HEADER_REQUEST_ID] = str(request_id)
    if cache is not None:
        headers[HEADER_CACHE] = _normalize_cache(cache)
    return headers


__all__ = [
    "HEADER_VERDICT",
    "HEADER_REASON",
    "HEADER_LATENCY",
    "HEADER_REQUEST_ID",
    "HEADER_CACHE",
    "MAX_REASON_LENGTH",
    "ScannerHeadersMiddleware",
    "scanner_headers",
    "set_scanner_context",
]
