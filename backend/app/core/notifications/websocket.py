from __future__ import annotations

import asyncio
import contextlib
from typing import Any, Optional

import structlog
from fastapi import WebSocket

from app.core.metrics import scanner_ws_connections, scanner_ws_notifications_total

logger = structlog.get_logger()


class ConnectionManager:
    """Tracks active WebSocket connections and broadcasts notifications.

    Broadcasts are best-effort: a failing connection is closed and dropped,
    never raising into the caller (post-factum analysis must not break on
    notification problems).
    """

    def __init__(self) -> None:
        self._connections: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    @property
    def connections(self) -> set[WebSocket]:
        return self._connections

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self._connections.add(websocket)
        scanner_ws_connections.set(len(self._connections))
        logger.info("ws_connected", total_connections=len(self._connections))

    async def disconnect(self, websocket: WebSocket) -> None:
        async with self._lock:
            self._connections.discard(websocket)
        scanner_ws_connections.set(len(self._connections))
        logger.info("ws_disconnected", total_connections=len(self._connections))

    async def broadcast(self, message: dict[str, Any]) -> None:
        """Send a JSON message to every connected client.

        Dead/broken sockets are closed and removed. Failures on individual
        connections never propagate to the caller.
        """
        if not self._connections:
            return

        message_type = str(message.get("type", "unknown"))
        scanner_ws_notifications_total.labels(type=message_type).inc()

        async with self._lock:
            targets = list(self._connections)

        stale: list[WebSocket] = []
        for ws in targets:
            try:
                await ws.send_json(message)
            except Exception as exc:  # noqa: BLE001 — drop broken sockets
                logger.warning("ws_broadcast_failed", error=str(exc))
                stale.append(ws)

        if stale:
            async with self._lock:
                for ws in stale:
                    self._connections.discard(ws)
                    with contextlib.suppress(Exception):
                        await ws.close()
            scanner_ws_connections.set(len(self._connections))


# Global singleton
_connection_manager: Optional[ConnectionManager] = None


def get_connection_manager() -> ConnectionManager:
    global _connection_manager
    if _connection_manager is None:
        _connection_manager = ConnectionManager()
    return _connection_manager


def set_connection_manager(manager: Optional[ConnectionManager]) -> None:
    global _connection_manager
    _connection_manager = manager
