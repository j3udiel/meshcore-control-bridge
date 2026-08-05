from __future__ import annotations

import asyncio
import logging
import sqlite3
from dataclasses import dataclass

import pytest

from meshcore_control.app import BridgeService
from meshcore_control.auth.authorization import AuthorizedUser, Authorizer, RoomPolicy
from meshcore_control.auth.roles import Role
from meshcore_control.commands.registry import CommandDefinition
from meshcore_control.commands.router import CommandRouter
from meshcore_control.config import AppConfig
from meshcore_control.models import InboundMessage, OutboundMessage
from meshcore_control.plugins import build_registry
from meshcore_control.security.deduplication import Deduplicator
from meshcore_control.security.rate_limit import RateLimiter
from meshcore_control.storage.audit_flow import AuditFlow
from meshcore_control.storage.database import connect_database
from meshcore_control.storage.normalized_audit import (
    AUDIT_KEY_MIN_BYTES,
    AuditKey,
    AuditKeyError,
    NormalizedAuditRepository,
    NormalizedAuditSettings,
)
from meshcore_control.storage.repositories import AuditRepository
from meshcore_control.transport.fake import FakeTransport

PRIVATE_SENDER = "meshcore-pubkey-prefix:private-flow-sender"
PRIVATE_MESSAGE_ID = "private-flow-message-id"
PRIVATE_TEXT = "!ping private-argument"
PRIVATE_TOKEN = "supervisor-token-private-value"


@dataclass(slots=True)
class BuiltService:
    service: BridgeService
    transport: FakeTransport
    connection: sqlite3.Connection
    audit_flow: AuditFlow | None


class FailingTransport(FakeTransport):
    async def send(self, message: OutboundMessage) -> None:
        raise RuntimeError(f"send failed {PRIVATE_TOKEN}")


def build_service(
    connection: sqlite3.Connection,
    *,
    authorized: bool = True,
    normalized: bool = True,
    channel_index: int = 1,
    max_commands: int = 10,
    transport: FakeTransport | None = None,
    room_policies: dict[str, RoomPolicy] | None = None,
) -> BuiltService:
    registry = build_registry()
    users = {}
    if authorized:
        users[PRIVATE_SENDER] = AuthorizedUser(PRIVATE_SENDER, "tester", Role.admin)
    legacy = AuditRepository(connection)
    settings = (
        NormalizedAuditSettings(
            enabled=True,
            audit_key=AuditKey(key=b"f" * AUDIT_KEY_MIN_BYTES, key_id="flow-key"),
        )
        if normalized
        else NormalizedAuditSettings.legacy_disabled()
    )
    normalized_repository = NormalizedAuditRepository(connection, settings)
    audit_flow = AuditFlow(
        connection=connection,
        legacy=legacy,
        normalized=normalized_repository,
    )
    router = CommandRouter(
        registry=registry,
        authorizer=Authorizer(users, room_policies=room_policies),
        audit=legacy,
        audit_flow=audit_flow,
        services={"registry": registry, "config": AppConfig()},
        prefix="!",
    )
    fake_transport = transport or FakeTransport()
    service = BridgeService(
        transport=fake_transport,
        router=router,
        deduplicator=Deduplicator(connection, window_seconds=300),
        audit_flow=audit_flow,
        rate_limiter=RateLimiter(max_commands=max_commands, window_seconds=60),
        channel_index=channel_index,
    )
    return BuiltService(service, fake_transport, connection, audit_flow)


def message(
    text: str = "!ping",
    *,
    message_id: str | None = PRIVATE_MESSAGE_ID,
    channel_index: int = 1,
    sender_id: str = PRIVATE_SENDER,
) -> InboundMessage:
    return InboundMessage(
        transport="fake",
        message_id=message_id,
        sender_id=sender_id,
        channel_index=channel_index,
        text=text,
    )


def audit_events(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    return connection.execute(
        "SELECT * FROM normalized_audit_events ORDER BY id"
    ).fetchall()


def event_types(connection: sqlite3.Connection) -> list[str]:
    return [row["event_type"] for row in audit_events(connection)]


def serialized_normalized_sqlite(connection: sqlite3.Connection) -> str:
    parts: list[str] = []
    for table in (
        "audit_metadata",
        "normalized_audit_events",
    ):
        parts.extend(
            " ".join(str(value) for value in row)
            for row in connection.execute(f"SELECT * FROM {table}").fetchall()
        )
    return " ".join(parts)


def legacy_snapshot(connection: sqlite3.Connection) -> dict[str, list[tuple[object, ...]]]:
    return {
        "inbound_messages": [
            tuple(row)
            for row in connection.execute(
                """
                SELECT transport, message_id, sender_id, channel_index, text_hash
                FROM inbound_messages
                ORDER BY id
                """
            ).fetchall()
        ],
        "command_executions": [
            tuple(row)
            for row in connection.execute(
                """
                SELECT message_id, sender_id, command, args_json, result, error
                FROM command_executions
                ORDER BY id
                """
            ).fetchall()
        ],
    }


def run_case(tmp_path, name: str, *, normalized: bool) -> dict[str, list[tuple[object, ...]]]:
    connection = connect_database(str(tmp_path / f"{name}-{normalized}.db"))
    if name == "room_not_allowed":
        built = build_service(
            connection,
            normalized=normalized,
            room_policies={
                "fake:channel:2": RoomPolicy(
                    room_id="fake:channel:2",
                    enabled=True,
                    minimum_role=Role.readonly,
                    allow_commands=True,
                )
            },
        )
        asyncio.run(built.service.process_message(message("!ping", message_id=f"{name}-msg")))
        return legacy_snapshot(connection)
    if name == "wrong_channel":
        built = build_service(connection, normalized=normalized)
        asyncio.run(built.service.process_message(message("!ping", channel_index=2)))
        return legacy_snapshot(connection)
    if name == "duplicate":
        built = build_service(connection, normalized=normalized)
        inbound = message("!ping", message_id="duplicate-legacy")
        asyncio.run(built.service.process_message(inbound))
        asyncio.run(built.service.process_message(inbound))
        return legacy_snapshot(connection)
    if name == "rate_limited":
        built = build_service(connection, normalized=normalized, max_commands=1)
        asyncio.run(built.service.process_message(message("!ping", message_id="rl-1")))
        asyncio.run(built.service.process_message(message("!ping", message_id="rl-2")))
        return legacy_snapshot(connection)
    if name == "not_a_command":
        built = build_service(connection, normalized=normalized)
        asyncio.run(built.service.process_message(message("hello", message_id="not-command")))
        return legacy_snapshot(connection)
    if name == "unknown":
        built = build_service(connection, normalized=normalized)
        asyncio.run(built.service.process_message(message("!unknown arg", message_id="unknown")))
        return legacy_snapshot(connection)
    if name == "unauthorized":
        built = build_service(connection, normalized=normalized, authorized=False)
        asyncio.run(built.service.process_message(message("!ping", message_id="unauthorized")))
        return legacy_snapshot(connection)
    if name == "ping":
        built = build_service(connection, normalized=normalized)
        asyncio.run(built.service.process_message(message("!ping", message_id="ping")))
        return legacy_snapshot(connection)
    if name == "failed":
        async def fail_handler(_context, _args):  # type: ignore[no-untyped-def]
            raise RuntimeError("boom")

        built = build_service(connection, normalized=normalized)
        built.service.router.registry.register(
            CommandDefinition(
                name="fail-legacy",
                aliases=(),
                group="system",
                usage="!fail-legacy",
                help_text="fail",
                minimum_role=Role.readonly,
                confirmation_required=False,
                handler=fail_handler,
            )
        )
        asyncio.run(built.service.process_message(message("!fail-legacy x", message_id="failed")))
        return legacy_snapshot(connection)
    raise AssertionError(f"unknown case {name}")


def assert_private_values_not_in_normalized(connection: sqlite3.Connection) -> None:
    serialized = " ".join(
        " ".join(str(value) for value in row)
        for row in connection.execute("SELECT * FROM normalized_audit_events").fetchall()
    )
    metadata = " ".join(
        " ".join(str(value) for value in row)
        for row in connection.execute("SELECT * FROM audit_metadata").fetchall()
    )
    assert PRIVATE_SENDER not in serialized
    assert PRIVATE_MESSAGE_ID not in serialized
    assert PRIVATE_TEXT not in serialized
    assert PRIVATE_TOKEN not in serialized
    assert (b"f" * AUDIT_KEY_MIN_BYTES).hex() not in serialized
    assert (b"f" * AUDIT_KEY_MIN_BYTES).hex() not in metadata


@pytest.mark.parametrize(
    "case_name",
    [
        "wrong_channel",
        "room_not_allowed",
        "duplicate",
        "rate_limited",
        "not_a_command",
        "unknown",
        "unauthorized",
        "ping",
        "failed",
    ],
)
def test_legacy_tables_have_same_semantics_with_normalized_on_or_off(
    tmp_path,
    case_name: str,
) -> None:
    normalized_off = run_case(tmp_path, case_name, normalized=False)
    normalized_on = run_case(tmp_path, case_name, normalized=True)

    assert normalized_on == normalized_off


def test_allowed_ping_records_full_causal_sequence(tmp_path) -> None:
    built = build_service(connect_database(str(tmp_path / "audit.db")))

    outbound = asyncio.run(built.service.process_message(message()))

    assert outbound is not None
    assert outbound.text == "pong"
    rows = audit_events(built.connection)
    assert [row["event_type"] for row in rows] == [
        "message.received",
        "command.parsed",
        "command.authorization",
        "command.execution",
        "response.sent",
    ]
    correlation_ids = {row["correlation_id"] for row in rows}
    assert len(correlation_ids) == 1
    assert rows[0]["causation_event_id"] is None
    for previous, current in zip(rows[:-1], rows[1:], strict=True):
        assert current["causation_event_id"] == previous["event_id"]
    assert rows[1]["command_name"] == "ping"
    assert rows[3]["command_name"] == "ping"
    assert rows[3]["command_result"] == "succeeded"
    assert_private_values_not_in_normalized(built.connection)


def test_unknown_command_does_not_store_unregistered_command_name(tmp_path) -> None:
    built = build_service(connect_database(str(tmp_path / "audit.db")))

    outbound = asyncio.run(built.service.process_message(message("!doesnotexist secret")))

    assert outbound is not None
    assert outbound.text == "Comando desconocido. Usa !help"
    rows = audit_events(built.connection)
    assert event_types(built.connection) == [
        "message.received",
        "command.parsed",
        "command.execution",
        "response.sent",
    ]
    assert rows[1]["command_name"] is None
    assert rows[2]["command_name"] is None
    assert "doesnotexist" not in " ".join(str(value) for row in rows for value in row)


def test_authorization_denied_is_audited_without_changing_response(tmp_path) -> None:
    built = build_service(connect_database(str(tmp_path / "audit.db")), authorized=False)

    outbound = asyncio.run(built.service.process_message(message()))

    assert outbound is not None
    assert outbound.text == "No autorizado."
    assert event_types(built.connection) == [
        "message.received",
        "command.parsed",
        "command.authorization",
        "command.execution",
        "response.sent",
    ]
    auth_row = audit_events(built.connection)[2]
    assert '"authorization_result":"denied"' in auth_row["metadata_json"]
    assert '"authorization_reason":"sender_not_registered"' in auth_row["metadata_json"]


def test_duplicate_message_records_ignore_event(tmp_path) -> None:
    built = build_service(connect_database(str(tmp_path / "audit.db")))
    inbound = message(message_id="duplicate-id")

    first = asyncio.run(built.service.process_message(inbound))
    second = asyncio.run(built.service.process_message(inbound))

    assert first is not None
    assert second is None
    rows = audit_events(built.connection)
    assert rows[-1]["event_type"] == "message.ignored"
    assert '"ignore_reason":"duplicate"' in rows[-1]["metadata_json"]
    assert rows[-1]["causation_event_id"] == rows[-2]["event_id"]


def test_rate_limit_records_ignore_and_response(tmp_path) -> None:
    built = build_service(
        connect_database(str(tmp_path / "audit.db")),
        max_commands=1,
    )

    first = asyncio.run(built.service.process_message(message("!ping", message_id="rl-1")))
    second = asyncio.run(built.service.process_message(message("!ping", message_id="rl-2")))

    assert first is not None
    assert second is not None
    assert second.text == "Rate limit."
    rows = audit_events(built.connection)
    assert rows[-2]["event_type"] == "message.ignored"
    assert '"ignore_reason":"rate_limited"' in rows[-2]["metadata_json"]
    assert rows[-1]["event_type"] == "response.sent"


def test_wrong_channel_records_ignore_event(tmp_path) -> None:
    built = build_service(connect_database(str(tmp_path / "audit.db")))

    outbound = asyncio.run(built.service.process_message(message(channel_index=2)))

    assert outbound is None
    rows = audit_events(built.connection)
    assert [row["event_type"] for row in rows] == ["message.received", "message.ignored"]
    assert rows[1]["causation_event_id"] == rows[0]["event_id"]
    assert '"ignore_reason":"wrong_channel"' in rows[1]["metadata_json"]


def test_command_execution_failure_is_audited(tmp_path) -> None:
    async def fail_handler(_context, _args):  # type: ignore[no-untyped-def]
        raise RuntimeError(f"boom {PRIVATE_TOKEN}")

    built = build_service(connect_database(str(tmp_path / "audit.db")))
    registry = built.service.router.registry
    registry.register(
        CommandDefinition(
            name="fail",
            aliases=(),
            group="system",
            usage="!fail",
            help_text="fail",
            minimum_role=Role.readonly,
            confirmation_required=False,
            handler=fail_handler,
        )
    )

    outbound = asyncio.run(built.service.process_message(message("!fail hidden-token")))

    assert outbound is not None
    assert outbound.text.startswith("ERROR")
    rows = audit_events(built.connection)
    assert rows[-2]["event_type"] == "command.execution"
    assert rows[-2]["command_result"] == "failed"
    assert PRIVATE_TOKEN not in " ".join(str(value) for row in rows for value in row)


def test_response_failure_is_audited(tmp_path) -> None:
    built = build_service(
        connect_database(str(tmp_path / "audit.db")),
        transport=FailingTransport(),
    )

    with pytest.raises(RuntimeError):
        asyncio.run(built.service.process_message(message()))

    rows = audit_events(built.connection)
    assert rows[-1]["event_type"] == "response.failed"
    assert PRIVATE_TOKEN not in " ".join(str(value) for row in rows for value in row)


def test_rollback_when_legacy_command_write_fails(tmp_path, monkeypatch) -> None:
    built = build_service(connect_database(str(tmp_path / "audit.db")))

    def fail_insert_command(**_kwargs):  # type: ignore[no-untyped-def]
        raise sqlite3.OperationalError("legacy write failed")

    monkeypatch.setattr(built.service.router.audit, "insert_command", fail_insert_command)

    outbound = asyncio.run(built.service.process_message(message()))

    assert outbound is not None
    assert outbound.text == "pong"
    assert "command.execution" not in event_types(built.connection)
    assert built.service.router.audit.count_commands() == 0
    assert legacy_snapshot(built.connection)["inbound_messages"] == []
    assert not built.connection.in_transaction


def test_rollback_when_legacy_inbound_write_fails(tmp_path, monkeypatch) -> None:
    built = build_service(connect_database(str(tmp_path / "audit.db")))

    def fail_insert_inbound(_message):  # type: ignore[no-untyped-def]
        raise sqlite3.OperationalError("legacy inbound failed")

    monkeypatch.setattr(built.service.router.audit, "insert_inbound_message", fail_insert_inbound)

    outbound = asyncio.run(built.service.process_message(message()))

    assert outbound is not None
    assert outbound.text == "pong"
    assert built.service.router.audit.count_commands() == 0
    assert "command.execution" not in event_types(built.connection)
    assert not built.connection.in_transaction


def test_rollback_when_normalized_command_write_fails(tmp_path, monkeypatch) -> None:
    built = build_service(connect_database(str(tmp_path / "audit.db")))
    assert built.audit_flow is not None
    original_insert = built.audit_flow.normalized.insert_event

    def fail_command_execution(event, **kwargs):  # type: ignore[no-untyped-def]
        if event.event_type.value == "command.execution":
            raise sqlite3.OperationalError("normalized write failed")
        return original_insert(event, **kwargs)

    monkeypatch.setattr(built.audit_flow.normalized, "insert_event", fail_command_execution)

    outbound = asyncio.run(built.service.process_message(message()))

    assert outbound is not None
    assert outbound.text == "pong"
    assert built.service.router.audit.count_commands() == 0
    assert "command.execution" not in event_types(built.connection)
    assert legacy_snapshot(built.connection)["inbound_messages"] == []
    assert not built.connection.in_transaction


def test_standalone_legacy_without_key_keeps_legacy_audit_only(tmp_path, caplog) -> None:
    built = build_service(
        connect_database(str(tmp_path / "audit.db")),
        normalized=False,
    )

    with caplog.at_level(logging.WARNING):
        outbound = asyncio.run(built.service.process_message(message()))

    assert outbound is not None
    assert outbound.text == "pong"
    assert built.service.router.audit.count_commands() == 1
    assert event_types(built.connection) == []
    assert caplog.text.count("Normalized audit disabled") == 1


def test_homeassistant_app_audit_key_is_persistent(tmp_path) -> None:
    key_path = tmp_path / "audit.key"

    first = NormalizedAuditSettings.homeassistant_app(key_path=str(key_path))
    second = NormalizedAuditSettings.homeassistant_app(key_path=str(key_path))

    assert first.enabled is True
    assert second.enabled is True
    assert first.audit_key is not None
    assert second.audit_key is not None
    assert first.audit_key.key == second.audit_key.key


def test_invalid_homeassistant_app_key_fails_before_processing(tmp_path) -> None:
    key_path = tmp_path / "audit.key"
    key_path.write_bytes(b"short")
    key_path.chmod(0o600)

    with pytest.raises(AuditKeyError):
        NormalizedAuditSettings.homeassistant_app(key_path=str(key_path))


def test_private_values_absent_from_sqlite_and_logs(tmp_path, caplog) -> None:
    built = build_service(connect_database(str(tmp_path / "audit.db")))

    with caplog.at_level(logging.INFO):
        outbound = asyncio.run(built.service.process_message(message(PRIVATE_TEXT)))

    assert outbound is not None
    serialized = serialized_normalized_sqlite(built.connection)
    assert PRIVATE_SENDER not in serialized
    assert PRIVATE_MESSAGE_ID not in serialized
    assert PRIVATE_TEXT not in serialized
    assert PRIVATE_TOKEN not in serialized
    assert (b"f" * AUDIT_KEY_MIN_BYTES).hex() not in serialized
    assert PRIVATE_SENDER not in caplog.text
    assert PRIVATE_MESSAGE_ID not in caplog.text
    assert PRIVATE_TEXT not in caplog.text
    assert PRIVATE_TOKEN not in caplog.text
