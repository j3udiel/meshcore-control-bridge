from __future__ import annotations

import asyncio
import logging
import sqlite3
from dataclasses import dataclass

import pytest

from meshcore_control.adapters.homeassistant import HomeAssistantStatus
from meshcore_control.adapters.homeassistant_ws import HomeAssistantEvent
from meshcore_control.app import BridgeService
from meshcore_control.auth.authorization import AuthorizedUser, Authorizer
from meshcore_control.auth.roles import Role
from meshcore_control.commands.registry import CommandContext, CommandDefinition
from meshcore_control.commands.router import CommandRouter
from meshcore_control.config import AppConfig, StatusEntityConfig
from meshcore_control.homeassistant_app import unidentified_testing_sender_id
from meshcore_control.plugins import build_registry
from meshcore_control.security.deduplication import Deduplicator
from meshcore_control.security.rate_limit import RateLimiter
from meshcore_control.storage.database import connect_database
from meshcore_control.storage.repositories import AuditRepository
from meshcore_control.transport.homeassistant_meshcore import (
    HomeAssistantMeshCoreSettings,
    HomeAssistantMeshCoreTransport,
)


@dataclass
class FakeHA:
    available: bool = True

    async def check_available(self) -> HomeAssistantStatus:
        return HomeAssistantStatus(available=self.available, message="OK")

    async def get_config(self) -> dict[str, object]:
        return {"version": "2026.8.0", "location_name": "Home"}

    async def get_state(self, entity_or_alias: str) -> dict[str, object]:
        if entity_or_alias == "sensor.test_temperature":
            return {"state": "23.1", "attributes": {"unit_of_measurement": "C"}}
        raise KeyError(entity_or_alias)


class FakeHaWsClient:
    def __init__(self, events: list[HomeAssistantEvent]) -> None:
        self._events = events
        self.service_calls: list[tuple[str, str, dict[str, object], bool]] = []
        self.config_entries = [{"domain": "meshcore", "entry_id": "entry-one"}]
        self.idle_marks = 0

    async def events(self, _event_types: list[str]):
        for event in self._events:
            yield event

    async def get_config_entries(self) -> list[dict[str, object]]:
        return self.config_entries

    async def call_service(
        self,
        domain: str,
        service: str,
        service_data: dict[str, object],
        *,
        return_response: bool = False,
    ) -> None:
        self.service_calls.append((domain, service, service_data, return_response))


def meshcore_event(
    text: str,
    *,
    channel_index: int = 1,
    message_id: str = "ctx-1",
) -> HomeAssistantEvent:
    return HomeAssistantEvent(
        event_type="meshcore_message",
        data={
            "message_type": "channel",
            "channel_idx": channel_index,
            "message": text,
            "sender_name": "visible-name-not-auth",
        },
        time_fired="2026-08-02T10:00:00+00:00",
        context_id=message_id,
    )


def build_unidentified_service(
    connection: sqlite3.Connection,
    events: list[HomeAssistantEvent],
    *,
    allow_testing: bool = True,
) -> tuple[BridgeService, FakeHaWsClient]:
    channel_index = 1
    client = FakeHaWsClient(events)
    transport = HomeAssistantMeshCoreTransport(
        settings=HomeAssistantMeshCoreSettings(
            channel_index=channel_index,
            ha_base_url="http://supervisor/core",
            ha_token="supervisor-token-not-real",
            ha_websocket_url="ws://supervisor/core/websocket",
            require_stable_sender=not allow_testing,
            allow_channel_without_sender=allow_testing,
        ),
        websocket_client=client,  # type: ignore[arg-type]
    )
    registry = build_registry()
    registry.register(
        CommandDefinition(
            name="write-test",
            aliases=(),
            group="test",
            usage="!write-test",
            help_text="Synthetic write command for authorization tests.",
            minimum_role=Role.home,
            confirmation_required=False,
            handler=_write_test,
        )
    )
    users = {}
    if allow_testing:
        sender_id = unidentified_testing_sender_id(channel_index)
        users[sender_id] = AuthorizedUser(
            sender_id,
            "unidentified-channel-testing",
            Role.readonly,
        )
    config = AppConfig(
        status_entities={
            "temperature": StatusEntityConfig(
                entity_id="sensor.test_temperature",
                label="Temp",
            )
        }
    )
    router = CommandRouter(
        registry=registry,
        authorizer=Authorizer(users),
        audit=AuditRepository(connection),
        services={"registry": registry, "config": config, "homeassistant": FakeHA()},
        prefix="!",
    )
    service = BridgeService(
        transport=transport,
        router=router,
        deduplicator=Deduplicator(connection, window_seconds=300),
        rate_limiter=RateLimiter(max_commands=10, window_seconds=60),
        channel_index=channel_index,
    )
    return service, client


async def _write_test(_context: CommandContext, _args: list[str]) -> str:
    return "write executed"


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("!ping", "pong"),
        ("!help", "MeshCore Bridge"),
        ("!estado ha", "Version: 2026.8.0"),
        ("!estado", "Temp: 23.1 C"),
    ],
)
def test_unidentified_testing_allows_readonly_commands(
    tmp_path, command: str, expected: str
) -> None:
    service, client = build_unidentified_service(
        connect_database(str(tmp_path / "audit.db")),
        [meshcore_event(command)],
    )

    outbound = asyncio.run(service.process_message(asyncio.run(service.transport.receive())))

    assert outbound is not None
    assert expected in outbound.text
    assert client.service_calls[-1][2]["channel_idx"] == 1


def test_unidentified_testing_rejects_write_command(tmp_path) -> None:
    service, client = build_unidentified_service(
        connect_database(str(tmp_path / "audit.db")),
        [meshcore_event("!write-test")],
    )

    outbound = asyncio.run(service.process_message(asyncio.run(service.transport.receive())))

    assert outbound is not None
    assert outbound.text == "No autorizado."
    assert client.service_calls[-1][2]["message"] == "No autorizado."


def test_unidentified_testing_disabled_rejects_same_message(tmp_path) -> None:
    service, _client = build_unidentified_service(
        connect_database(str(tmp_path / "audit.db")),
        [meshcore_event("!ping")],
        allow_testing=False,
    )

    async def receive_with_timeout() -> object:
        return await asyncio.wait_for(service.transport.receive(), timeout=0.05)

    with pytest.raises(TimeoutError):
        asyncio.run(receive_with_timeout())


def test_unidentified_sender_only_applies_to_configured_channel(tmp_path) -> None:
    service, _client = build_unidentified_service(
        connect_database(str(tmp_path / "audit.db")),
        [meshcore_event("!ping", channel_index=2)],
    )

    async def receive_with_timeout() -> object:
        return await asyncio.wait_for(service.transport.receive(), timeout=0.05)

    with pytest.raises(TimeoutError):
        asyncio.run(receive_with_timeout())


def test_help_only_lists_readonly_commands_for_unidentified_testing(tmp_path) -> None:
    service, _client = build_unidentified_service(
        connect_database(str(tmp_path / "audit.db")),
        [meshcore_event("!help")],
    )

    outbound = asyncio.run(service.process_message(asyncio.run(service.transport.receive())))

    assert outbound is not None
    assert "!ping" in outbound.text
    assert "!help" in outbound.text
    assert "!estado" in outbound.text
    assert "!estado ha" in outbound.text
    assert "!write-test" not in outbound.text


def test_unidentified_logs_are_structured_without_private_text(tmp_path, caplog) -> None:
    caplog.set_level(logging.INFO)
    service, _client = build_unidentified_service(
        connect_database(str(tmp_path / "audit.db")),
        [meshcore_event("!ping secret text should not be logged")],
    )

    asyncio.run(service.process_message(asyncio.run(service.transport.receive())))

    logs = "\n".join(record.getMessage() for record in caplog.records)
    assert "MeshCore channel message received channel=1 identity=unidentified" in logs
    assert "Command accepted command=ping authorization=allowed" in logs
    assert "secret text should not be logged" not in logs
    assert "visible-name-not-auth" not in logs
