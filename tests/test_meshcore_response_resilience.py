from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import pytest

from meshcore_control.adapters.homeassistant_ws import HomeAssistantWebSocketClient
from meshcore_control.app import BridgeService, _trim_lora_response
from meshcore_control.auth.authorization import AuthorizedUser, Authorizer
from meshcore_control.auth.roles import Role
from meshcore_control.bridge_health import BridgeHealthState
from meshcore_control.commands.router import CommandRouter
from meshcore_control.config import AppConfig
from meshcore_control.models import InboundMessage, OutboundMessage
from meshcore_control.plugins import build_registry
from meshcore_control.security.deduplication import Deduplicator
from meshcore_control.security.rate_limit import RateLimiter
from meshcore_control.storage.audit_flow import AuditFlow
from meshcore_control.storage.database import connect_database
from meshcore_control.storage.normalized_audit import (
    NormalizedAuditRepository,
    NormalizedAuditSettings,
)
from meshcore_control.storage.repositories import AuditRepository


class QueueTransport:
    name = "fake"

    def __init__(
        self,
        messages: list[InboundMessage],
        *,
        failures: list[BaseException] | None = None,
    ) -> None:
        self._messages = list(messages)
        self._failures = list(failures or [])
        self.sent: list[OutboundMessage] = []
        self.closed = False

    async def receive(self) -> InboundMessage:
        if not self._messages:
            await asyncio.Event().wait()
        return self._messages.pop(0)

    async def send(self, message: OutboundMessage) -> None:
        if self._failures:
            raise self._failures.pop(0)
        self.sent.append(message)

    async def close(self) -> None:
        self.closed = True


def _message(text: str, *, message_id: str) -> InboundMessage:
    return InboundMessage(
        transport="fake",
        message_id=message_id,
        sender_id="sender-1",
        channel_index=1,
        text=text,
    )


def _service(
    connection: sqlite3.Connection,
    transport: QueueTransport,
    *,
    health: BridgeHealthState | None = None,
    response_max_bytes: int = 180,
) -> BridgeService:
    registry = build_registry()
    audit = AuditRepository(connection)
    normalized = NormalizedAuditRepository(
        connection,
        NormalizedAuditSettings.standalone(
            key_hex="00" * 32,
        ),
    )
    audit_flow = AuditFlow(connection=connection, legacy=audit, normalized=normalized)
    router = CommandRouter(
        registry=registry,
        authorizer=Authorizer({"sender-1": AuthorizedUser("sender-1", "tester", Role.admin)}),
        audit=audit,
        audit_flow=audit_flow,
        services={"registry": registry, "config": AppConfig()},
        prefix="!",
    )
    return BridgeService(
        transport=transport,
        router=router,
        deduplicator=Deduplicator(connection, window_seconds=300),
        audit_flow=audit_flow,
        rate_limiter=RateLimiter(max_commands=10, window_seconds=60),
        channel_index=1,
        bridge_health=health,
        meshcore_response_max_bytes=response_max_bytes,
    )


def test_meshcore_response_timeout_does_not_escape_process_message(tmp_path) -> None:
    health = BridgeHealthState()
    transport = QueueTransport([], failures=[TimeoutError()])
    service = _service(connect_database(str(tmp_path / "audit.db")), transport, health=health)

    outbound = asyncio.run(service.process_message(_message("!ping", message_id="timeout-1")))

    assert outbound is not None
    assert outbound.text == "pong"
    assert transport.sent == []
    assert health.snapshot().last_failure_reason == "transport_timeout"


def test_next_meshcore_command_processes_after_response_timeout(tmp_path) -> None:
    health = BridgeHealthState()
    transport = QueueTransport(
        [],
        failures=[TimeoutError()],
    )
    service = _service(connect_database(str(tmp_path / "audit.db")), transport, health=health)

    first = asyncio.run(service.process_message(_message("!ping", message_id="timeout-1")))
    second = asyncio.run(service.process_message(_message("!ping", message_id="timeout-2")))

    assert first is not None
    assert second is not None
    assert second.text == "pong"
    assert [message.text for message in transport.sent] == ["pong"]


@pytest.mark.asyncio
async def test_run_forever_continues_after_meshcore_response_timeout(tmp_path) -> None:
    transport = QueueTransport(
        [
            _message("!ping", message_id="timeout-1"),
            _message("!ping", message_id="timeout-2"),
        ],
        failures=[TimeoutError()],
    )
    service = _service(connect_database(str(tmp_path / "audit.db")), transport)

    task = asyncio.create_task(service.run_forever())
    try:
        for _ in range(20):
            if transport.sent:
                break
            await asyncio.sleep(0.01)
        assert [message.text for message in transport.sent] == ["pong"]
        assert not task.done()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def test_homeassistant_service_error_does_not_escape_process_message(tmp_path) -> None:
    transport = QueueTransport([], failures=[RuntimeError("service failed")])
    service = _service(connect_database(str(tmp_path / "audit.db")), transport)

    outbound = asyncio.run(service.process_message(_message("!ping", message_id="service-error")))

    assert outbound is not None
    assert outbound.text == "pong"
    assert transport.sent == []


def test_send_failure_log_does_not_include_message_text(
    tmp_path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    transport = QueueTransport([], failures=[TimeoutError()])
    service = _service(connect_database(str(tmp_path / "audit.db")), transport)

    with caplog.at_level(logging.WARNING):
        asyncio.run(
            service.process_message(_message("!ping secret-token-value", message_id="log-1"))
        )

    assert "secret-token-value" not in caplog.text
    assert "reason=transport_timeout" in caplog.text


def test_response_failed_audit_is_best_effort_after_send_timeout(tmp_path) -> None:
    db_path = tmp_path / "audit.db"
    connection = connect_database(str(db_path))
    transport = QueueTransport([], failures=[TimeoutError()])
    service = _service(connection, transport)

    asyncio.run(service.process_message(_message("!ping", message_id="timeout-1")))

    event_types = [
        row["event_type"]
        for row in connection.execute(
            "SELECT event_type FROM normalized_audit_events ORDER BY id"
        ).fetchall()
    ]
    assert "response.failed" in event_types


def test_help_response_for_meshcore_is_limited_by_utf8_bytes(tmp_path) -> None:
    transport = QueueTransport([])
    service = _service(
        connect_database(str(tmp_path / "audit.db")),
        transport,
        response_max_bytes=90,
    )

    outbound = asyncio.run(service.process_message(_message("!help", message_id="help-1")))

    assert outbound is not None
    assert len(outbound.text.encode("utf-8")) <= 90
    assert outbound.text.encode("utf-8").decode("utf-8") == outbound.text


def test_lora_truncation_does_not_split_unicode_codepoint() -> None:
    text = "Linea\n" + ("á" * 100)

    trimmed = _trim_lora_response(text, max_bytes=37)

    assert len(trimmed.encode("utf-8")) <= 37
    assert trimmed.encode("utf-8").decode("utf-8") == trimmed


@dataclass
class FakeConnect:
    websockets: Iterator[FakeWebSocket]

    def __call__(self, *args: object, **kwargs: object) -> FakeWebSocketContext:
        return FakeWebSocketContext(next(self.websockets))


@dataclass
class FakeWebSocketContext:
    websocket: FakeWebSocket

    async def __aenter__(self) -> FakeWebSocket:
        return self.websocket

    async def __aexit__(self, *args: object) -> None:
        self.websocket.closed = True


class FakeWebSocket:
    def __init__(
        self,
        frames: list[dict[str, Any]],
        *,
        recv_error_after_frames: BaseException | None = None,
    ) -> None:
        self.frames = [json.dumps(frame) for frame in frames]
        self.recv_error_after_frames = recv_error_after_frames
        self.sent: list[dict[str, Any]] = []
        self.closed = False
        self.active_recv = 0
        self.max_concurrent_recv = 0

    async def send(self, raw: str) -> None:
        payload = json.loads(raw)
        assert "access_token" not in raw or payload["access_token"] == "token"
        self.sent.append(payload)

    async def recv(self) -> str:
        self.active_recv += 1
        self.max_concurrent_recv = max(self.max_concurrent_recv, self.active_recv)
        try:
            await asyncio.sleep(0)
            if not self.frames:
                if self.recv_error_after_frames is not None:
                    raise self.recv_error_after_frames
                await asyncio.Event().wait()
            return self.frames.pop(0)
        finally:
            self.active_recv -= 1


def _auth_frames() -> list[dict[str, Any]]:
    return [{"type": "auth_required"}, {"type": "auth_ok"}]


@pytest.mark.asyncio
async def test_call_service_ignores_interleaved_event_and_correlates_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    websocket = FakeWebSocket(
        [
            *_auth_frames(),
            {
                "type": "event",
                "id": 1,
                "event": {
                    "event_type": "meshcore_message",
                    "data": {"message": "ignored"},
                },
            },
            {"type": "result", "id": 999, "success": True, "result": {}},
            {"type": "result", "id": 1, "success": True, "result": {"response": {"ok": True}}},
        ]
    )
    import websockets

    monkeypatch.setattr(websockets, "connect", FakeConnect(iter([websocket])))
    client = HomeAssistantWebSocketClient(
        base_url="http://homeassistant.local:8123",
        token="token",
        timeout_seconds=0.2,
    )

    response = await client.call_service(
        "meshcore",
        "send_channel_message",
        {"channel_idx": 1, "message": "pong"},
        return_response=True,
    )

    assert response == {"ok": True}
    assert websocket.max_concurrent_recv == 1


@pytest.mark.asyncio
async def test_two_concurrent_call_service_operations_receive_their_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = FakeWebSocket(
        [*_auth_frames(), {"type": "result", "id": 1, "success": True, "result": {}}]
    )
    second = FakeWebSocket(
        [*_auth_frames(), {"type": "result", "id": 2, "success": True, "result": {}}]
    )
    import websockets

    monkeypatch.setattr(websockets, "connect", FakeConnect(iter([first, second])))
    client = HomeAssistantWebSocketClient(
        base_url="http://homeassistant.local:8123",
        token="token",
        timeout_seconds=0.2,
    )

    await asyncio.gather(
        client.call_service("meshcore", "send_channel_message", {"message": "one"}),
        client.call_service("meshcore", "send_channel_message", {"message": "two"}),
    )

    assert first.max_concurrent_recv == 1
    assert second.max_concurrent_recv == 1


@pytest.mark.asyncio
async def test_call_service_timeout_cleans_up_reader_and_waiter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    websocket = FakeWebSocket(_auth_frames())
    import websockets

    monkeypatch.setattr(websockets, "connect", FakeConnect(iter([websocket])))
    client = HomeAssistantWebSocketClient(
        base_url="http://homeassistant.local:8123",
        token="token",
        timeout_seconds=0.01,
    )

    with pytest.raises(TimeoutError):
        await client.call_service("meshcore", "send_channel_message", {"message": "one"})

    assert websocket.max_concurrent_recv == 1


@pytest.mark.asyncio
async def test_call_service_disconnect_fails_pending_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    websocket = FakeWebSocket(_auth_frames(), recv_error_after_frames=ConnectionError("closed"))
    import websockets

    monkeypatch.setattr(websockets, "connect", FakeConnect(iter([websocket])))
    client = HomeAssistantWebSocketClient(
        base_url="http://homeassistant.local:8123",
        token="token",
        timeout_seconds=0.2,
    )

    with pytest.raises(ConnectionError):
        await client.call_service("meshcore", "send_channel_message", {"message": "one"})

    assert websocket.max_concurrent_recv == 1


@pytest.mark.asyncio
async def test_events_reader_delivers_events_without_losing_subscription_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    websocket = FakeWebSocket(
        [
            *_auth_frames(),
            {"type": "result", "id": 1, "success": True, "result": {}},
            {
                "type": "event",
                "id": 1,
                "event": {
                    "event_type": "meshcore_message",
                    "data": {"message_type": "channel", "message": "!ping"},
                },
            },
        ]
    )
    import websockets

    monkeypatch.setattr(websockets, "connect", FakeConnect(iter([websocket])))
    client = HomeAssistantWebSocketClient(
        base_url="http://homeassistant.local:8123",
        token="token",
        timeout_seconds=0.2,
    )

    iterator = client.events(["meshcore_message"])
    event = await iterator.__anext__()
    await iterator.aclose()

    assert event.event_type == "meshcore_message"
    assert event.data["message"] == "!ping"
    assert websocket.max_concurrent_recv == 1
