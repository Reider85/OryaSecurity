from __future__ import annotations

import contextlib

import jwt as pyjwt
import structlog
from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from app.config import settings
from app.core.auth import authenticate_api_key
from app.core.jwt import decode_jwt, is_jwt_candidate
from app.core.notifications.websocket import get_connection_manager

router = APIRouter(prefix="/v2", tags=["v2", "notifications"])
logger = structlog.get_logger()


async def _authenticate_token(token: str) -> str:
    """Validate a raw token (JWT or API key), returning the tenant_id.

    Raises:
        ValueError: when the token is invalid or inactive.
    """
    if is_jwt_candidate(token):
        try:
            claims = decode_jwt(token)
        except pyjwt.InvalidTokenError as exc:
            raise ValueError("Invalid or expired token") from exc
        tenant_id = claims.get("tenant_id") or claims.get("sub")
        if not tenant_id:
            raise ValueError("Token missing tenant claim")
        return str(tenant_id)

    try:
        auth_info = await authenticate_api_key(token)
    except Exception as exc:  # noqa: BLE001 — map any auth failure to ValueError
        raise ValueError(str(exc)) from exc
    return auth_info.tenant_id


@router.websocket("/ws/notifications")
async def ws_notifications(
    websocket: WebSocket,
    api_key: str | None = Query(default=None),
) -> None:
    """SecOps live notifications (post-factum flags).

    Auth: ``?api_key=<JWT or raw API key>``. Invalid tokens are rejected
    with close code 4401 before the socket is accepted.
    """
    if not settings.ws_notifications_enabled:
        await websocket.close(code=4403)
        return

    if not api_key:
        await websocket.close(code=4401)
        return

    try:
        tenant_id = await _authenticate_token(api_key)
    except ValueError:
        logger.warning("ws_auth_failed")
        await websocket.close(code=4401)
        return

    manager = get_connection_manager()
    await manager.connect(websocket)
    try:
        # Keep the connection open; clients may send pings — ignore payloads.
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        await manager.disconnect(websocket)
        with contextlib.suppress(Exception):
            logger.info("ws_closed", tenant_id=tenant_id)
