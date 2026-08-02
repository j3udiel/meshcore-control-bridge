from __future__ import annotations

import asyncio
import sqlite3
from dataclasses import dataclass

from meshcore_control.adapters.homeassistant import HomeAssistantStatus
from meshcore_control.app import BridgeService
from meshcore_control.auth.authorization import AuthorizedUser, Authorizer
from meshcore_control.auth.roles import Role
from meshcore_control.commands.router import CommandRouter
from meshcore_control.config import AppConfig, StatusEntityConfig
from meshcore_control.models import InboundMessage
from meshcore_control.plugins import build_registry
from meshcore_control.security.deduplication import Deduplicator
from meshcore_control.security.rate_limit import RateLimiter
from meshcore_control.storage.database import connect_database
from meshcore_control.storage.repositories import AuditRepository
from meshcore_control.transport.fake import FakeTransport


@dataclass
class FakeHA:
    status: HomeAssistantStatus
    states: dict[str, dict[str, object]] | None = None

    async def check_available(self) -> HomeAssistantStatus:
        return self.status

    async def get_config(self) -> dict[str, object]:
        return {"version": "2026.1.0", "location_name": "Home"}

    async def get_state(self, entity_or_alias: str) -> dict[str, object]:
        if self.states is None or entity_or_alias not in self.states:
            raise KeyError(entity_or_alias)
        return self.states[entity_or_alias]


def build_test_service(
    connection: sqlite3.Connection,
    *,
    authorized: bool = True,
    ha: FakeHA | None = None,
    channel_index: int = 1,
) -> tuple[BridgeService, FakeTransport, AuditRepository]:
    registry = build_registry()
    users = {}
    if authorized:
        users["sender-1"] = AuthorizedUser("sender-1", "tester", Role.admin)
    audit = AuditRepository(connection)
    services: dict[str, object] = {"registry": registry}
    services["config"] = AppConfig(
        status_entities={
            "temperature": StatusEntityConfig(
                entity_id="sensor.living_room_temperature",
                label="Temp",
            ),
            "missing": StatusEntityConfig(entity_id="sensor.missing", label="Missing"),
        }
    )
    if ha is not None:
        services["homeassistant"] = ha
    router = CommandRouter(
        registry=registry,
        authorizer=Authorizer(users),
        audit=audit,
        services=services,
        prefix="!",
    )
    transport = FakeTransport()
    service = BridgeService(
        transport=transport,
        router=router,
        deduplicator=Deduplicator(connection, window_seconds=300),
        rate_limiter=RateLimiter(max_commands=10, window_seconds=60),
        channel_index=channel_index,
    )
    return service, transport, audit


def message(
    text: str, *, message_id: str | None = "msg-1", channel_index: int = 1
) -> InboundMessage:
    return InboundMessage(
        transport="fake",
        message_id=message_id,
        sender_id="sender-1",
        channel_index=channel_index,
        text=text,
    )


def test_ping_replies_pong(tmp_path) -> None:
    service, transport, audit = build_test_service(connect_database(str(tmp_path / "audit.db")))

    outbound = asyncio.run(service.process_message(message("!ping")))

    assert outbound is not None
    assert outbound.text == "pong"
    assert transport.sent[0].text == "pong"
    assert audit.count_commands() == 1


def test_unauthorized_sender_does_not_execute_handler(tmp_path) -> None:
    service, transport, audit = build_test_service(
        connect_database(str(tmp_path / "audit.db")), authorized=False
    )

    outbound = asyncio.run(service.process_message(message("!ping")))

    assert outbound is not None
    assert outbound.text == "No autorizado."
    assert transport.sent[0].text == "No autorizado."
    assert audit.count_commands() == 1


def test_other_channels_are_ignored(tmp_path) -> None:
    service, transport, audit = build_test_service(connect_database(str(tmp_path / "audit.db")))

    outbound = asyncio.run(service.process_message(message("!ping", channel_index=0)))

    assert outbound is None
    assert transport.sent == []
    assert audit.count_commands() == 0


def test_duplicate_message_is_not_executed_twice(tmp_path) -> None:
    service, transport, audit = build_test_service(connect_database(str(tmp_path / "audit.db")))
    inbound = message("!ping", message_id="same-id")

    first = asyncio.run(service.process_message(inbound))
    second = asyncio.run(service.process_message(inbound))

    assert first is not None
    assert second is None
    assert [item.text for item in transport.sent] == ["pong"]
    assert audit.count_commands() == 1


def test_help_is_generated_from_registry(tmp_path) -> None:
    service, transport, _audit = build_test_service(connect_database(str(tmp_path / "audit.db")))

    outbound = asyncio.run(service.process_message(message("!help", message_id="help-1")))

    assert outbound is not None
    assert "MeshCore Bridge" in outbound.text
    assert "!ping" in outbound.text
    assert "!estado" in outbound.text
    assert transport.sent[-1].text == outbound.text


def test_estado_reports_home_assistant_ok(tmp_path) -> None:
    service, _transport, _audit = build_test_service(
        connect_database(str(tmp_path / "audit.db")),
        ha=FakeHA(
            HomeAssistantStatus(available=True, message="OK"),
            states={
                "sensor.living_room_temperature": {
                    "state": "23.1",
                    "attributes": {"unit_of_measurement": "C"},
                }
            },
        ),
    )

    outbound = asyncio.run(service.process_message(message("!estado", message_id="estado-ok")))

    assert outbound is not None
    assert "HA: OK" in outbound.text
    assert "Temp: 23.1 C" in outbound.text
    assert "Missing: N/D" in outbound.text
    assert "Internet: no requerido" in outbound.text


def test_estado_ha_reports_version(tmp_path) -> None:
    service, _transport, _audit = build_test_service(
        connect_database(str(tmp_path / "audit.db")),
        ha=FakeHA(HomeAssistantStatus(available=True, message="OK")),
    )

    outbound = asyncio.run(service.process_message(message("!estado ha", message_id="estado-ha")))

    assert outbound is not None
    assert "HA: OK" in outbound.text
    assert "Version: 2026.1.0" in outbound.text


def test_estado_reports_home_assistant_error_without_blocking(tmp_path) -> None:
    service, _transport, _audit = build_test_service(
        connect_database(str(tmp_path / "audit.db")),
        ha=FakeHA(HomeAssistantStatus(available=False, message="ConnectError")),
    )

    outbound = asyncio.run(service.process_message(message("!estado", message_id="estado-error")))

    assert outbound is not None
    assert "HA: ERROR ConnectError" in outbound.text


def test_audit_hashes_inbound_message_text(tmp_path) -> None:
    db_path = tmp_path / "audit.db"
    connection = connect_database(str(db_path))
    service, _transport, audit = build_test_service(connection)
    token_like_text = "!ping super-sensitive-token-value"

    outbound = asyncio.run(
        service.process_message(message(token_like_text, message_id="secret-message"))
    )

    assert outbound is not None
    rows = connection.execute("SELECT text_hash FROM inbound_messages").fetchall()
    assert len(rows) == 1
    assert token_like_text not in rows[0]["text_hash"]
    assert audit.count_commands() == 1


def test_rate_limit_blocks_without_executing(tmp_path) -> None:
    connection = connect_database(str(tmp_path / "audit.db"))
    registry = build_registry()
    users = {"sender-1": AuthorizedUser("sender-1", "tester", Role.admin)}
    audit = AuditRepository(connection)
    transport = FakeTransport()
    router = CommandRouter(
        registry=registry,
        authorizer=Authorizer(users),
        audit=audit,
        services={"registry": registry, "config": AppConfig()},
        prefix="!",
    )
    service = BridgeService(
        transport=transport,
        router=router,
        deduplicator=Deduplicator(connection, window_seconds=300),
        rate_limiter=RateLimiter(max_commands=1, window_seconds=60),
        channel_index=1,
    )

    first = asyncio.run(service.process_message(message("!ping", message_id="rl-1")))
    second = asyncio.run(service.process_message(message("!ping", message_id="rl-2")))

    assert first is not None
    assert second is not None
    assert second.text == "Rate limit."
    assert audit.count_commands() == 1
