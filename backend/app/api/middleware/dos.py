from __future__ import annotations

import json
import structlog
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.audit import write_event
from app.core.dos_protection import DoSProtector, extract_prompt_from_json_body, hash_prompt
from app.config import settings

logger = structlog.get_logger()


class DoSMiddleware:
    """ASGI middleware for DoS protection on prompt inputs.
    
    Checks POST JSON request bodies for prompt length, token count, and complexity.
    Returns 413 with JSON response for rejected prompts.
    """
    
    def __init__(self, app: ASGIApp):
        self.app = app
        self.dos_protector = DoSProtector(
            max_prompt_chars=settings.dos_max_prompt_chars,
            max_prompt_tokens=settings.dos_max_prompt_tokens,
            complexity_threshold=settings.dos_complexity_threshold,
        )
    
    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not settings.dos_enabled:
            await self.app(scope, receive, send)
            return
        
        if scope["type"] != "http" or scope["method"] != "POST":
            await self.app(scope, receive, send)
            return
        
        # Check content type for JSON
        content_type = scope.get("headers", {})
        content_type_str = None
        for header in content_type:
            if header[0].decode("ascii").lower() == "content-type":
                content_type_str = header[1].decode("ascii")
                break
        
        if not content_type_str or not content_type_str.startswith("application/json"):
            await self.app(scope, receive, send)
            return
        
        # Buffer request body
        body = b""
        more_body = True
        while more_body:
            message = await receive()
            if message["type"] == "http.disconnect":
                # Client disconnected during upload
                return
            body += message.get("body", b"")
            more_body = message.get("more_body", False)
        
        # Extract prompt from JSON
        prompt = extract_prompt_from_json_body(body)
        if prompt is None:
            # No prompt found in JSON body, pass through
            await self.app(scope, receive, send)
            return
        
        # Check prompt with DoS protector
        result = self.dos_protector.check(prompt)
        
        if result.ok:
            # Prompt passed DoS check, replay body to app
            async def replay_receive():
                # Yield the buffered body first
                yield {"type": "http.request", "body": body, "more_body": False}
                # Then any additional messages (should be none for POST)
                while True:
                    try:
                        msg = await receive()
                        yield msg
                    except Exception:
                        break
            
            await self.app(scope, replay_receive, send)
        else:
            # Prompt failed DoS check, return 413 response
            await self._send_dos_response(send, result, prompt)
    
    async def _send_dos_response(self, send: Send, result, prompt: str) -> None:
        """Send 413 Payload Too Large response with audit event."""
        import time
        import uuid
        
        request_id = str(uuid.uuid4())
        prompt_hash = hash_prompt(prompt)
        
        response_body = {
            "error": {
                "message": "Request rejected for security reasons",
                "type": "dos_protection",
                "reason": result.reason,
                "complexity_score": result.complexity_score,
            }
        }
        
        response_headers = [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(json.dumps(response_body))).encode()),
            (b"x-dos-reason", result.reason.encode()),
        ]
        
        response_message = {
            "type": "http.response.start",
            "status": 413,
            "headers": response_headers,
        }
        await send(response_message)
        
        body_message = {
            "type": "http.response.body",
            "body": json.dumps(response_body).encode(),
            "more_body": False,
        }
        await send(body_message)
        
        # Log audit event asynchronously (fail-open)
        try:
            await write_event(
                request_id=request_id,
                prompt_hash=prompt_hash,
                verdict="block",
                reason=result.reason,
                rules_matched=[],
                metadata={
                    "dos_check": {
                        "complexity_score": result.complexity_score,
                        "length": result.length,
                        "estimated_tokens": result.estimated_tokens,
                    }
                },
            )
            logger.info(
                "dos_protection_blocked",
                request_id=request_id,
                prompt_hash=prompt_hash,
                reason=result.reason,
                complexity_score=result.complexity_score,
            )
        except Exception:
            logger.warning(
                "dos_audit_write_failed",
                request_id=request_id,
                prompt_hash=prompt_hash,
                reason=result.reason,
                exc_info=True,
            )


__all__ = ["DoSMiddleware"]