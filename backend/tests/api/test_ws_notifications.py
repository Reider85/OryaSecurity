from __future__ import annotations

import asyncio

import pytest
from fastapi import WebSocket
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.config import settings
from app.core.jwt import create_jwt
from app.core.notifications.websocket import (
    ConnectionManager,
    get_connection_manager,
    set_connection_manager,
)


class _FakeWebSocket:
    """Hand-rolled WebSocket stub recording sent JSON payloads."""

    def __init__(self, fail_send: bool = False):
        self.sent: list[dict] = []
        self.accepted = False
        self.closed = False
        self.close_code: int | None = None
        self._fail_send = fail_send

    async def accept(self) -> None:
        self.accepted = True

    async def send_json(self, message: dict) -> None:
        if self._fail_send:
            raise RuntimeError("connection lost")
        self.sent.append(message)

    async def close(self, code: int = 1000) -> None:
        self.closed = True
        self.close_code = code


class TestConnectionManager:
    @pytest.mark.asyncio
    async def test_connect_registers_connection(self):
        manager = ConnectionManager()
        ws = _FakeWebSocket()

        await manager.connect(ws)

        assert ws.accepted is True
        assert ws in manager.connections

    @pytest.mark.asyncio
    async def test_disconnect_removes_connection(self):
        manager = ConnectionManager()
        ws = _FakeWebSocket()
        await manager.connect(ws)

        await manager.disconnect(ws)

        assert ws not in manager.connections

    @pytest.mark.asyncio
    async def test_broadcast_reaches_all_connections(self):
        manager = ConnectionManager()
        ws1, ws2 = _FakeWebSocket(), _FakeWebSocket()
        await manager.connect(ws1)
        await manager.connect(ws2)

        message = {"type": "post_factum_flag", "severity": "high"}
        await manager.broadcast(message)

        assert message in ws1.sent
        assert message in ws2.sent

    @pytest.mark.asyncio
    async def test_broadcast_drops_broken_connection(self):
        manager = ConnectionManager()
        healthy = _FakeWebSocket()
        broken = _FakeWebSocket(fail_send=True)
        await manager.connect(healthy)
        await manager.connect(broken)

        await manager.broadcast({"type": "post_factum_flag"})

        assert healthy.sent == [{"type": "post_factum_flag"}]
        assert broken not in manager.connections
        assert broken.closed is True
        assert healthy in manager.connections

    @pytest.mark.asyncio
    async def test_broadcast_with_no_connections_is_noop(self):
        manager = ConnectionManager()
        await manager.broadcast({"type": "post_factum_flag"})  # must not raise


class TestWSNotificationsEndpoint:
    def test_missing_api_key_rejected(self, client: TestClient):
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect("/v2/ws/notifications"):
                pass
        assert exc_info.value.code == 4401

    def test_invalid_token_rejected(self, client: TestClient):
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect(
                "/v2/ws/notifications?api_key=eyJ_invalid.token.here"
            ):
                pass
        assert exc_info.value.code == 4401

    def test_garbage_token_rejected(self, client: TestClient):
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect("/v2/ws/notifications?api_key=not-a-token"):
                pass
        assert exc_info.value.code == 4401

    def test_valid_jwt_connects_and_disconnects_cleanly(self, client: TestClient):
        token = create_jwt("ws-tenant")
        manager = get_connection_manager()
        before = len(manager.connections)

        with client.websocket_connect(
            f"/v2/ws/notifications?api_key={token}"
        ) as ws:
            assert len(manager.connections) == before + 1
            # Client can send a ping; server ignores payloads and stays open.
            ws.send_text("ping")

        assert len(manager.connections) == before

    def test_disabled_via_settings_rejects(self, client: TestClient, monkeypatch):
        monkeypatch.setattr(settings, "ws_notifications_enabled", False)
        token = create_jwt("ws-tenant")

        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect(
                f"/v2/ws/notifications?api_key={token}"
            ):
                pass
        assert exc_info.value.code == 4403
