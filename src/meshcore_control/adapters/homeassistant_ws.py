from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse, urlunparse

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class HomeAssistantEvent:
    event_type: str
    data: dict[str, Any]
    time_fired: str | None = None
    context_id: str | None = None


@dataclass(frozen=True, slots=True)
class MeshCoreHaServiceCall:
    domain: str
    service: str
    service_data: dict[str, Any]


@dataclass(slots=True)
class HomeAssistantWebSocketClient:
    base_url: str
    token: str
    verify_tls: bool = True
    timeout_seconds: float = 10.0
    max_message_bytes: int = 262_144
    websocket_url_override: str | None = None
    on_subscribed: Any | None = None
    on_idle: Any | None = None
    on_authenticated: Any | None = None
    on_disconnected: Any | None = None
    _message_id: int = field(default=0, init=False)

    def websocket_url(self) -> str:
        if self.websocket_url_override:
            return self.websocket_url_override
        parsed = urlparse(self.base_url.rstrip("/"))
        if parsed.scheme == "http":
            scheme = "ws"
        elif parsed.scheme == "https":
            scheme = "wss"
        else:
            raise ValueError("Home Assistant base URL must use http or https")
        return urlunparse(
            parsed._replace(
                scheme=scheme,
                path="/api/websocket",
                params="",
                query="",
                fragment="",
            )
        )

    async def events(self, event_types: list[str]) -> AsyncIterator[HomeAssistantEvent]:
        async for websocket in self._connect_loop():
            subscriptions: dict[int, str] = {}
            try:
                await self._authenticate(websocket)
                if self.on_authenticated is not None:
                    self.on_authenticated()
                logger.info("Home Assistant WebSocket authenticated")
                for event_type in event_types:
                    command_id = await self._send(
                        websocket,
                        {"type": "subscribe_events", "event_type": event_type},
                    )
                    await self._expect_success(websocket, command_id)
                    subscriptions[command_id] = event_type
                    logger.info("Subscribed to Home Assistant event type %s", event_type)
                if self.on_subscribed is not None:
                    self.on_subscribed()

                while True:
                    try:
                        payload = await self._recv_json(
                            websocket,
                            timeout=self.timeout_seconds,
                        )
                    except TimeoutError:
                        if self.on_idle is not None:
                            self.on_idle()
                        continue
                    if payload.get("type") != "event":
                        continue
                    event = payload.get("event")
                    if not isinstance(event, dict):
                        continue
                    data = event.get("data")
                    if not isinstance(data, dict):
                        data = {}
                    event_id = payload.get("id")
                    event_type_raw = event.get("event_type")
                    event_type_name = event_type_raw if isinstance(event_type_raw, str) else ""
                    if not event_type_name and isinstance(event_id, int):
                        event_type_name = subscriptions.get(event_id, "")
                    yield HomeAssistantEvent(
                        event_type=event_type_name,
                        data=data,
                        time_fired=_optional_str(event.get("time_fired")),
                        context_id=_context_id(event.get("context")),
                    )
            except asyncio.CancelledError:
                logger.info("Home Assistant WebSocket listener cancelled")
                raise
            except Exception:
                if self.on_disconnected is not None:
                    self.on_disconnected()
                logger.warning("Home Assistant WebSocket disconnected; reconnecting")
                await asyncio.sleep(1)
                continue

    async def call_service(
        self,
        domain: str,
        service: str,
        service_data: dict[str, Any],
        *,
        return_response: bool = False,
    ) -> dict[str, Any] | None:
        async for websocket in self._connect_loop():
            await self._authenticate(websocket)
            command_id = await self._send(
                websocket,
                {
                    "type": "call_service",
                    "domain": domain,
                    "service": service,
                    "service_data": service_data,
                    "return_response": return_response,
                },
            )
            result = await self._expect_success(websocket, command_id)
            response = result.get("response") if isinstance(result, dict) else None
            return response if isinstance(response, dict) else None
        return None

    async def get_services(self) -> dict[str, Any]:
        result = await self._simple_command({"type": "get_services"})
        return dict(result) if isinstance(result, dict) else {}

    async def get_config_entries(self) -> list[dict[str, Any]]:
        result = await self._simple_command({"type": "config_entries/get"})
        if isinstance(result, list):
            return [dict(item) for item in result if isinstance(item, dict)]
        return []

    async def fire_event(self, event_type: str, event_data: dict[str, Any]) -> None:
        await self._simple_command(
            {
                "type": "fire_event",
                "event_type": event_type,
                "event_data": event_data,
            }
        )

    async def _simple_command(self, command: dict[str, Any]) -> Any:
        async for websocket in self._connect_loop():
            await self._authenticate(websocket)
            command_id = await self._send(websocket, command)
            return await self._expect_success(websocket, command_id)
        return None

    async def _connect_loop(self) -> AsyncIterator[Any]:
        try:
            import websockets
        except ImportError as exc:
            raise RuntimeError(
                "websockets is required for Home Assistant WebSocket transport"
            ) from exc

        ssl_context = None if self.verify_tls else False
        while True:
            async with websockets.connect(
                self.websocket_url(),
                open_timeout=self.timeout_seconds,
                ping_interval=20,
                ping_timeout=self.timeout_seconds,
                max_size=self.max_message_bytes,
                ssl=ssl_context,
            ) as websocket:
                yield websocket

    async def _authenticate(self, websocket: Any) -> None:
        hello = await self._recv_json(websocket, timeout=self.timeout_seconds)
        if hello.get("type") != "auth_required":
            raise RuntimeError("Home Assistant WebSocket did not request authentication")
        await websocket.send(json.dumps({"type": "auth", "access_token": self.token}))
        result = await self._recv_json(websocket, timeout=self.timeout_seconds)
        if result.get("type") != "auth_ok":
            raise RuntimeError("Home Assistant WebSocket authentication failed")

    async def _send(self, websocket: Any, payload: dict[str, Any]) -> int:
        self._message_id += 1
        command_id = self._message_id
        await websocket.send(json.dumps({"id": command_id, **payload}))
        return command_id

    async def _expect_success(self, websocket: Any, command_id: int) -> Any:
        while True:
            payload = await self._recv_json(websocket, timeout=self.timeout_seconds)
            if payload.get("type") != "result" or payload.get("id") != command_id:
                continue
            if not payload.get("success"):
                raise RuntimeError("Home Assistant WebSocket command failed")
            return payload.get("result")

    async def _recv_json(self, websocket: Any, timeout: float | None) -> dict[str, Any]:
        receive = websocket.recv()
        raw = await receive if timeout is None else await asyncio.wait_for(receive, timeout=timeout)
        if isinstance(raw, bytes):
            if len(raw) > self.max_message_bytes:
                raise ValueError("Home Assistant WebSocket message too large")
            raw = raw.decode("utf-8")
        if len(raw) > self.max_message_bytes:
            raise ValueError("Home Assistant WebSocket message too large")
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("Home Assistant WebSocket message was not an object")
        return payload


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _context_id(value: object) -> str | None:
    if isinstance(value, dict):
        return _optional_str(value.get("id"))
    return None
