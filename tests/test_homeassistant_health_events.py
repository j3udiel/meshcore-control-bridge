from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from meshcore_control.bridge_health import BridgeHealthState
from meshcore_control.homeassistant_health_events import (
    HEALTH_EVENT_TYPE,
    HomeAssistantHealthEventPublisher,
)


class FakeHealthEventClient:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.events: list[tuple[str, dict[str, Any]]] = []

    async def fire_event(self, event_type: str, event_data: dict[str, Any]) -> None:
        await asyncio.sleep(0)
        if self.fail:
            raise RuntimeError("websocket down")
        self.events.append((event_type, dict(event_data)))


def _health(tmp_path: Path) -> BridgeHealthState:
    health = BridgeHealthState(healthcheck_path=str(tmp_path / "health.json"))
    health.configure(
        telegram_enabled=True,
        forward_telegram_to_meshcore=True,
        forward_meshcore_to_telegram=True,
        forward_confirmation_enabled=False,
    )
    return health


@pytest.mark.asyncio
async def test_health_event_initial_after_start(tmp_path: Path) -> None:
    health = _health(tmp_path)
    client = FakeHealthEventClient()
    publisher = HomeAssistantHealthEventPublisher(
        health=health,
        client=client,
        channel_index=1,
        heartbeat_seconds=60,
        coalesce_seconds=0,
    )

    publisher.start()
    await asyncio.sleep(0.05)
    await publisher.stop()

    assert client.events
    event_type, payload = client.events[0]
    assert event_type == HEALTH_EVENT_TYPE
    assert payload["schema_version"] == 1
    assert payload["version"] == health.version
    assert payload["channel"] == 1
    assert payload["forwarding"] == {
        "telegram_to_meshcore": True,
        "meshcore_to_telegram": True,
        "confirmation": False,
    }


@pytest.mark.asyncio
async def test_health_event_payload_contract(tmp_path: Path) -> None:
    health = _health(tmp_path)
    health.set_meshcore_connected(True)
    health.set_telegram_polling("connected")
    health.record_tg_to_mc(success=True)
    health.record_mc_to_tg(success=False, reason="transport_error")
    client = FakeHealthEventClient()
    publisher = HomeAssistantHealthEventPublisher(
        health=health,
        client=client,
        channel_index=7,
        coalesce_seconds=0,
    )

    await publisher.publish_if_needed(force=True)

    payload = client.events[0][1]
    assert payload == {
        "schema_version": 1,
        "status": "ok",
        "version": health.version,
        "uptime_seconds": payload["uptime_seconds"],
        "meshcore": "connected",
        "telegram": "connected",
        "channel": 7,
        "forwarding": {
            "telegram_to_meshcore": True,
            "meshcore_to_telegram": True,
            "confirmation": False,
        },
        "database": {
            "audit": "ok",
            "telegram": "ok",
        },
        "counters": {
            "tg_to_mc_success": 1,
            "tg_to_mc_failed": 0,
            "mc_to_tg_success": 0,
            "mc_to_tg_failed": 1,
            "commands_processed": 0,
        },
        "last_activity": {
            "telegram_to_meshcore": payload["last_activity"]["telegram_to_meshcore"],
            "meshcore_to_telegram": None,
        },
        "last_error": {
            "timestamp": payload["last_error"]["timestamp"],
            "reason": "transport_error",
        },
    }
    assert isinstance(payload["uptime_seconds"], int)
    assert payload["last_activity"]["telegram_to_meshcore"].endswith("Z")
    assert payload["last_error"]["timestamp"].endswith("Z")


@pytest.mark.asyncio
async def test_health_event_payload_has_no_sensitive_fields(tmp_path: Path) -> None:
    health = _health(tmp_path)
    client = FakeHealthEventClient()
    publisher = HomeAssistantHealthEventPublisher(
        health=health,
        client=client,
        channel_index=1,
        coalesce_seconds=0,
    )

    await publisher.publish_if_needed(force=True)

    serialized = str(client.events[0][1])
    for forbidden in [
        "token",
        "chat_id",
        "user_id",
        "sender_id",
        "pubkey",
        "message_id",
        "correlation",
        "entity_id",
        "/data",
    ]:
        assert forbidden not in serialized


@pytest.mark.asyncio
async def test_health_event_deduplicates_unchanged_snapshot(tmp_path: Path) -> None:
    health = _health(tmp_path)
    client = FakeHealthEventClient()
    publisher = HomeAssistantHealthEventPublisher(health=health, client=client, channel_index=1)

    assert await publisher.publish_if_needed(force=True) is True
    assert await publisher.publish_if_needed(force=False) is False

    assert len(client.events) == 1


@pytest.mark.asyncio
async def test_health_event_heartbeat_forces_unchanged_snapshot(tmp_path: Path) -> None:
    health = _health(tmp_path)
    client = FakeHealthEventClient()
    publisher = HomeAssistantHealthEventPublisher(
        health=health,
        client=client,
        channel_index=1,
        heartbeat_seconds=15,
    )

    assert await publisher.publish_if_needed(force=True) is True
    assert await publisher.publish_if_needed(force=True) is True

    assert len(client.events) == 2


@pytest.mark.asyncio
async def test_health_event_coalesces_rapid_counter_changes(tmp_path: Path) -> None:
    health = _health(tmp_path)
    client = FakeHealthEventClient()
    publisher = HomeAssistantHealthEventPublisher(
        health=health,
        client=client,
        channel_index=1,
        heartbeat_seconds=60,
        coalesce_seconds=0.05,
    )

    publisher.start()
    await asyncio.sleep(0.02)
    health.record_command_processed()
    health.record_command_processed()
    health.record_command_processed()
    await asyncio.sleep(0.12)
    await publisher.stop()

    assert client.events[-1][1]["counters"]["commands_processed"] == 3
    assert len(client.events) <= 3


@pytest.mark.asyncio
async def test_health_event_critical_degraded_change_publishes(tmp_path: Path) -> None:
    health = _health(tmp_path)
    client = FakeHealthEventClient()
    publisher = HomeAssistantHealthEventPublisher(
        health=health,
        client=client,
        channel_index=1,
        heartbeat_seconds=60,
        coalesce_seconds=1,
    )

    publisher.start()
    await asyncio.sleep(0.05)
    health.set_audit_db_health("degraded", reason="database_locked")
    await asyncio.sleep(0.05)
    await publisher.stop()

    assert any(event[1]["status"] == "degraded" for event in client.events)


@pytest.mark.asyncio
async def test_health_event_publish_failure_does_not_raise(tmp_path: Path) -> None:
    health = _health(tmp_path)
    client = FakeHealthEventClient(fail=True)
    publisher = HomeAssistantHealthEventPublisher(
        health=health,
        client=client,
        channel_index=1,
    )

    assert await publisher.publish_if_needed(force=True) is False
    assert health.snapshot().last_failure_reason == "transport_error"


@pytest.mark.asyncio
async def test_health_event_publisher_single_task(tmp_path: Path) -> None:
    health = _health(tmp_path)
    client = FakeHealthEventClient()
    publisher = HomeAssistantHealthEventPublisher(
        health=health,
        client=client,
        channel_index=1,
    )

    publisher.start()
    task = publisher._task
    publisher.start()
    assert publisher._task is task
    await publisher.stop()


@pytest.mark.asyncio
async def test_health_event_publisher_shutdown_clean(tmp_path: Path) -> None:
    health = _health(tmp_path)
    client = FakeHealthEventClient()
    publisher = HomeAssistantHealthEventPublisher(
        health=health,
        client=client,
        channel_index=1,
    )

    publisher.start()
    await publisher.stop()
    health.record_command_processed()
    await asyncio.sleep(0)

    assert publisher._task is None
