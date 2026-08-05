from __future__ import annotations

import asyncio
import contextlib
import json
from typing import Any

from meshcore_control.adapters.homeassistant_ws import HomeAssistantWebSocketClient


def test_websocket_event_listener_stays_connected_without_events() -> None:
    async def scenario() -> None:
        import websockets

        state = {"connections": 0, "subscriptions": 0}
        ready = asyncio.Event()

        async def handler(websocket: Any, *_args: object) -> None:
            state["connections"] += 1
            await websocket.send(json.dumps({"type": "auth_required"}))
            auth = json.loads(await websocket.recv())
            assert auth["type"] == "auth"
            await websocket.send(json.dumps({"type": "auth_ok"}))
            subscribe = json.loads(await websocket.recv())
            assert subscribe["type"] == "subscribe_events"
            state["subscriptions"] += 1
            await websocket.send(
                json.dumps({"id": subscribe["id"], "type": "result", "success": True})
            )
            ready.set()
            await asyncio.sleep(0.25)
            assert websocket.close_code is None

        async with websockets.serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            client = HomeAssistantWebSocketClient(
                base_url="http://unused",
                token="test-token-not-real",
                timeout_seconds=0.05,
                websocket_url_override=f"ws://127.0.0.1:{port}",
            )
            iterator = client.events(["meshcore_message"]).__aiter__()
            task = asyncio.create_task(iterator.__anext__())
            await asyncio.wait_for(ready.wait(), timeout=1)
            await asyncio.sleep(0.15)

            assert state["connections"] == 1
            assert state["subscriptions"] == 1
            assert not task.done()

            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    asyncio.run(scenario())


def test_websocket_event_listener_marks_idle_health_without_reconnecting() -> None:
    async def scenario() -> None:
        import websockets

        state = {"connections": 0, "subscriptions": 0, "idle": 0}
        ready = asyncio.Event()

        async def handler(websocket: Any, *_args: object) -> None:
            state["connections"] += 1
            await websocket.send(json.dumps({"type": "auth_required"}))
            await websocket.recv()
            await websocket.send(json.dumps({"type": "auth_ok"}))
            subscribe = json.loads(await websocket.recv())
            state["subscriptions"] += 1
            await websocket.send(
                json.dumps({"id": subscribe["id"], "type": "result", "success": True})
            )
            ready.set()
            await asyncio.sleep(0.25)
            assert websocket.close_code is None

        async with websockets.serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            client = HomeAssistantWebSocketClient(
                base_url="http://unused",
                token="test-token-not-real",
                timeout_seconds=0.05,
                websocket_url_override=f"ws://127.0.0.1:{port}",
                on_idle=lambda: state.__setitem__("idle", state["idle"] + 1),
            )
            iterator = client.events(["meshcore_message"]).__aiter__()
            task = asyncio.create_task(iterator.__anext__())
            await asyncio.wait_for(ready.wait(), timeout=1)
            await asyncio.sleep(0.18)

            assert state["connections"] == 1
            assert state["subscriptions"] == 1
            assert state["idle"] >= 1
            assert not task.done()

            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    asyncio.run(scenario())


def test_websocket_event_listener_reconnects_after_real_close() -> None:
    async def scenario() -> None:
        import websockets

        state = {"connections": 0, "subscriptions": 0}

        async def handler(websocket: Any, *_args: object) -> None:
            state["connections"] += 1
            await websocket.send(json.dumps({"type": "auth_required"}))
            auth = json.loads(await websocket.recv())
            assert auth["type"] == "auth"
            await websocket.send(json.dumps({"type": "auth_ok"}))
            subscribe = json.loads(await websocket.recv())
            assert subscribe["type"] == "subscribe_events"
            state["subscriptions"] += 1
            await websocket.send(
                json.dumps({"id": subscribe["id"], "type": "result", "success": True})
            )
            if state["connections"] == 1:
                await websocket.close(code=1001, reason="server restart")
                return
            await websocket.send(
                json.dumps(
                    {
                        "id": subscribe["id"],
                        "type": "event",
                        "event": {
                            "event_type": "meshcore_message",
                            "data": {"message_type": "channel"},
                        },
                    }
                )
            )
            await asyncio.sleep(0.05)

        async with websockets.serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            client = HomeAssistantWebSocketClient(
                base_url="http://unused",
                token="test-token-not-real",
                timeout_seconds=0.2,
                websocket_url_override=f"ws://127.0.0.1:{port}",
            )
            event = await asyncio.wait_for(
                client.events(["meshcore_message"]).__aiter__().__anext__(),
                timeout=2,
            )

        assert event.event_type == "meshcore_message"
        assert state["connections"] == 2
        assert state["subscriptions"] == 2

    asyncio.run(scenario())


def test_websocket_fire_event_uses_homeassistant_command() -> None:
    async def scenario() -> None:
        import websockets

        received: dict[str, Any] = {}

        async def handler(websocket: Any, *_args: object) -> None:
            await websocket.send(json.dumps({"type": "auth_required"}))
            await websocket.recv()
            await websocket.send(json.dumps({"type": "auth_ok"}))
            command = json.loads(await websocket.recv())
            received.update(command)
            await websocket.send(
                json.dumps({"id": command["id"], "type": "result", "success": True})
            )

        async with websockets.serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            client = HomeAssistantWebSocketClient(
                base_url="http://unused",
                token="test-token-not-real",
                timeout_seconds=0.2,
                websocket_url_override=f"ws://127.0.0.1:{port}",
            )
            await client.fire_event(
                "meshcore_control_bridge_health",
                {"status": "ok", "version": "0.1.18"},
            )

        assert received["type"] == "fire_event"
        assert received["event_type"] == "meshcore_control_bridge_health"
        assert received["event_data"] == {"status": "ok", "version": "0.1.18"}

    asyncio.run(scenario())
