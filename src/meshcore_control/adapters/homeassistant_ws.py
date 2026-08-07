from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncGenerator, AsyncIterator
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse, urlunparse

logger = logging.getLogger(__name__)


class HomeAssistantWebSocketCommandError(RuntimeError):
    pass


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
            pending: dict[int, asyncio.Future[Any]] = {}
            event_queue: asyncio.Queue[HomeAssistantEvent] = asyncio.Queue(maxsize=1000)
            reader_task: asyncio.Task[None] | None = None
            try:
                await self._authenticate(websocket)
                if self.on_authenticated is not None:
                    self.on_authenticated()
                logger.info("Home Assistant WebSocket authenticated")
                reader_task = asyncio.create_task(
                    self._reader_loop(
                        websocket,
                        pending,
                        event_queue=event_queue,
                        subscriptions=subscriptions,
                        connection_name="events",
                    ),
                    name="ha-ws-events-reader",
                )
                for event_type in event_types:
                    command_id, future = await self._send_command(
                        websocket,
                        {"type": "subscribe_events", "event_type": event_type},
                        pending,
                        operation="subscribe_events",
                    )
                    await self._wait_for_result(
                        command_id,
                        future,
                        pending,
                        operation="subscribe_events",
                    )
                    subscriptions[command_id] = event_type
                    logger.info("Subscribed to Home Assistant event type %s", event_type)
                if self.on_subscribed is not None:
                    self.on_subscribed()

                while True:
                    if reader_task.done():
                        await reader_task
                    try:
                        event = await asyncio.wait_for(
                            event_queue.get(),
                            timeout=self.timeout_seconds,
                        )
                    except TimeoutError:
                        if self.on_idle is not None:
                            self.on_idle()
                        continue
                    yield event
            except asyncio.CancelledError:
                logger.info("Home Assistant WebSocket listener cancelled")
                raise
            except Exception:
                if self.on_disconnected is not None:
                    self.on_disconnected()
                logger.warning("Home Assistant WebSocket disconnected; reconnecting")
                await asyncio.sleep(1)
                continue
            finally:
                if reader_task is not None:
                    reader_task.cancel()
                    await asyncio.gather(reader_task, return_exceptions=True)
                self._fail_pending(pending, HomeAssistantWebSocketCommandError("disconnected"))

    async def call_service(
        self,
        domain: str,
        service: str,
        service_data: dict[str, Any],
        *,
        return_response: bool = False,
    ) -> dict[str, Any] | None:
        result = await self._simple_command(
            {
                "type": "call_service",
                "domain": domain,
                "service": service,
                "service_data": service_data,
                "return_response": return_response,
            },
            operation="call_service",
        )
        response = result.get("response") if isinstance(result, dict) else None
        return response if isinstance(response, dict) else None

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
            },
            operation="fire_event",
        )

    async def _simple_command(self, command: dict[str, Any], *, operation: str = "command") -> Any:
        connection = self._connect_loop()
        pending: dict[int, asyncio.Future[Any]] = {}
        reader_task: asyncio.Task[None] | None = None
        try:
            websocket = await connection.__anext__()
            await self._authenticate(websocket)
            reader_task = asyncio.create_task(
                self._reader_loop(
                    websocket,
                    pending,
                    event_queue=None,
                    subscriptions={},
                    connection_name="command",
                ),
                name=f"ha-ws-{operation}-reader",
            )
            command_id, future = await self._send_command(
                websocket,
                command,
                pending,
                operation=operation,
            )
            return await self._wait_for_result(
                command_id,
                future,
                pending,
                operation=operation,
            )
        finally:
            if reader_task is not None:
                reader_task.cancel()
                await asyncio.gather(reader_task, return_exceptions=True)
            self._fail_pending(pending, HomeAssistantWebSocketCommandError("disconnected"))
            await connection.aclose()

    async def _connect_loop(self) -> AsyncGenerator[Any]:
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

    async def _send_command(
        self,
        websocket: Any,
        payload: dict[str, Any],
        pending: dict[int, asyncio.Future[Any]],
        *,
        operation: str,
    ) -> tuple[int, asyncio.Future[Any]]:
        self._message_id += 1
        command_id = self._message_id
        future = asyncio.get_running_loop().create_future()
        pending[command_id] = future
        logger.debug(
            "Home Assistant WebSocket command sent connection=command operation=%s id=%s",
            operation,
            command_id,
        )
        try:
            await websocket.send(json.dumps({"id": command_id, **payload}))
        except Exception:
            pending.pop(command_id, None)
            if not future.done():
                future.cancel()
            raise
        return command_id, future

    async def _wait_for_result(
        self,
        command_id: int,
        future: asyncio.Future[Any],
        pending: dict[int, asyncio.Future[Any]],
        *,
        operation: str,
    ) -> Any:
        start = asyncio.get_running_loop().time()
        try:
            return await asyncio.wait_for(future, timeout=self.timeout_seconds)
        except TimeoutError:
            logger.warning(
                "Home Assistant WebSocket command timed out operation=%s id=%s timeout=%s",
                operation,
                command_id,
                self.timeout_seconds,
            )
            raise
        finally:
            pending.pop(command_id, None)
            duration = asyncio.get_running_loop().time() - start
            logger.debug(
                "Home Assistant WebSocket command finished operation=%s id=%s duration=%.3f",
                operation,
                command_id,
                duration,
            )

    async def _reader_loop(
        self,
        websocket: Any,
        pending: dict[int, asyncio.Future[Any]],
        *,
        event_queue: asyncio.Queue[HomeAssistantEvent] | None,
        subscriptions: dict[int, str],
        connection_name: str,
    ) -> None:
        try:
            while True:
                payload = await self._recv_json(websocket, timeout=None)
                payload_type = payload.get("type")
                payload_id = payload.get("id")
                logger.debug(
                    "Home Assistant WebSocket frame received connection=%s type=%s id=%s",
                    connection_name,
                    payload_type if isinstance(payload_type, str) else "unknown",
                    payload_id if isinstance(payload_id, int) else "none",
                )
                if payload_type == "result" and isinstance(payload_id, int):
                    future = pending.get(payload_id)
                    if future is None:
                        logger.debug(
                            "Home Assistant WebSocket result without waiter connection=%s id=%s",
                            connection_name,
                            payload_id,
                        )
                        continue
                    if payload.get("success"):
                        if not future.done():
                            future.set_result(payload.get("result"))
                    elif not future.done():
                        future.set_exception(
                            HomeAssistantWebSocketCommandError(
                                "Home Assistant WebSocket command failed"
                            )
                        )
                    continue
                if payload_type == "event" and event_queue is not None:
                    event = _event_from_payload(payload, subscriptions)
                    if event is not None:
                        await self._queue_event(event_queue, event, connection_name)
                    continue
                logger.debug(
                    "Home Assistant WebSocket frame ignored connection=%s type=%s",
                    connection_name,
                    payload_type if isinstance(payload_type, str) else "unknown",
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._fail_pending(pending, exc)
            raise

    def _fail_pending(
        self,
        pending: dict[int, asyncio.Future[Any]],
        exc: BaseException,
    ) -> None:
        for future in list(pending.values()):
            if not future.done():
                future.set_exception(exc)
        pending.clear()

    async def _queue_event(
        self,
        event_queue: asyncio.Queue[HomeAssistantEvent],
        event: HomeAssistantEvent,
        connection_name: str,
    ) -> None:
        try:
            await asyncio.wait_for(event_queue.put(event), timeout=1.0)
        except TimeoutError as exc:
            logger.warning(
                "Home Assistant WebSocket event queue saturated connection=%s",
                connection_name,
            )
            raise HomeAssistantWebSocketCommandError("event queue saturated") from exc

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


def _event_from_payload(
    payload: dict[str, Any],
    subscriptions: dict[int, str],
) -> HomeAssistantEvent | None:
    event = payload.get("event")
    if not isinstance(event, dict):
        return None
    data = event.get("data")
    if not isinstance(data, dict):
        data = {}
    event_id = payload.get("id")
    event_type_raw = event.get("event_type")
    event_type_name = event_type_raw if isinstance(event_type_raw, str) else ""
    if not event_type_name and isinstance(event_id, int):
        event_type_name = subscriptions.get(event_id, "")
    return HomeAssistantEvent(
        event_type=event_type_name,
        data=data,
        time_fired=_optional_str(event.get("time_fired")),
        context_id=_context_id(event.get("context")),
    )
