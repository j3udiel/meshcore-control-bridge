from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from meshcore_control.app import BridgeService
from meshcore_control.auth.authorization import AuthorizedUser, Authorizer, RoomPolicy
from meshcore_control.auth.roles import Role
from meshcore_control.commands.router import CommandRouter
from meshcore_control.config import AppConfig, RateLimitConfig, TelegramConfig
from meshcore_control.models import InboundMessage, MessageIdentity, RoomRef, SenderIdentity
from meshcore_control.plugins import build_registry
from meshcore_control.security.deduplication import Deduplicator
from meshcore_control.security.rate_limit import RateLimiter
from meshcore_control.storage.audit_flow import AuditFlow
from meshcore_control.storage.database import connect_database
from meshcore_control.storage.normalized_audit import (
    AUDIT_KEY_MIN_BYTES,
    AuditKey,
    NormalizedAuditRepository,
    NormalizedAuditSettings,
)
from meshcore_control.storage.repositories import AuditRepository
from meshcore_control.telegram.client import TelegramConflictError, TelegramRateLimitError
from meshcore_control.telegram.service import (
    MESHCORE_TRANSPORT_NAME,
    MeshCoreToTelegramForwarder,
    render_telegram_forward_message,
)
from meshcore_control.telegram.store import TelegramStore
from meshcore_control.transport.fake import FakeTransport

MESHCORE_SENDER = "meshcore-pubkey-prefix:sender123"
MESHCORE_ROOM = "homeassistant-meshcore:channel:1"
PRIVATE_TEXT = "mensaje privado"
PRIVATE_TOKEN = "123456789:abcdefghijklmnopqrstuvwxyzABCDE"


@dataclass
class FakeTelegramClient:
    send_message_calls: list[dict[str, str]] = field(default_factory=list)
    send_error: Exception | None = None
    connection: sqlite3.Connection | None = None

    async def delete_webhook(self, *, drop_pending_updates: bool) -> None:
        raise NotImplementedError

    async def get_updates(
        self,
        *,
        offset: int | None,
        timeout: int,
        allowed_updates: tuple[str, ...] = ("message",),
    ) -> list[dict[str, Any]]:
        raise NotImplementedError

    async def send_message(self, *, chat_id: str, text: str) -> None:
        if self.connection is not None:
            assert not self.connection.in_transaction
        if self.send_error is not None:
            raise self.send_error
        self.send_message_calls.append({"chat_id": chat_id, "text": text})


@dataclass(slots=True)
class BuiltBridge:
    service: BridgeService
    telegram_client: FakeTelegramClient
    connection: sqlite3.Connection
    store: TelegramStore
    meshcore_transport: FakeTransport


def _build_bridge(
    tmp_path: Path,
    *,
    telegram_config: TelegramConfig | None = None,
    telegram_error: Exception | None = None,
    authorized: bool = True,
    connection: sqlite3.Connection | None = None,
) -> BuiltBridge:
    db = connection or connect_database(str(tmp_path / "audit.db"))
    registry = build_registry()
    users = {}
    if authorized:
        users[MESHCORE_SENDER] = AuthorizedUser(MESHCORE_SENDER, "meshcore-user", Role.readonly)
    legacy = AuditRepository(db)
    normalized = NormalizedAuditRepository(
        db,
        NormalizedAuditSettings(
            enabled=True,
            audit_key=AuditKey(key=b"m" * AUDIT_KEY_MIN_BYTES, key_id="mesh-tg-key"),
        ),
    )
    audit_flow = AuditFlow(connection=db, legacy=legacy, normalized=normalized)
    router = CommandRouter(
        registry=registry,
        authorizer=Authorizer(
            users,
            room_policies={
                MESHCORE_ROOM: RoomPolicy(
                    room_id=MESHCORE_ROOM,
                    enabled=True,
                    minimum_role=Role.readonly,
                    allow_commands=True,
                )
            },
        ),
        audit=legacy,
        audit_flow=audit_flow,
        services={"registry": registry, "config": AppConfig()},
        prefix="!",
    )
    client = FakeTelegramClient(send_error=telegram_error, connection=db)
    store = TelegramStore(db, audit_key=AuditKey(b"t" * AUDIT_KEY_MIN_BYTES, key_id="tg-key"))
    config = telegram_config or _telegram_config()
    forwarder = MeshCoreToTelegramForwarder(
        config=config,
        client=client,
        store=store,
        normalized_audit=normalized,
        backoff_max_seconds=1,
        sleep=_noop_sleep,
    )
    meshcore_transport = FakeTransport()
    service = BridgeService(
        transport=meshcore_transport,
        router=router,
        deduplicator=Deduplicator(db, window_seconds=300),
        audit_flow=audit_flow,
        rate_limiter=RateLimiter(max_commands=10, window_seconds=60),
        channel_index=1,
        normal_text_forwarder=forwarder,
    )
    return BuiltBridge(service, client, db, store, meshcore_transport)


def _telegram_config(**overrides: object) -> TelegramConfig:
    data = {
        "enabled": True,
        "allowed_private_chat_id": "1001",
        "allowed_user_id": "2002",
        "meshcore_channel_index": 1,
        "forward_meshcore_to_telegram": True,
        "meshcore_to_telegram_prefix": "MC: ",
        "max_telegram_message_length": 3900,
    }
    data.update(overrides)
    return TelegramConfig(**data)


async def _noop_sleep(delay: float) -> None:
    return None


def _message(
    text: str,
    *,
    message_id: str = "mesh-message-1",
    sender_id: str = MESHCORE_SENDER,
    channel_index: int = 1,
    metadata: dict[str, object] | None = None,
) -> InboundMessage:
    room = RoomRef.channel(transport=MESHCORE_TRANSPORT_NAME, channel_index=channel_index)
    message_identity = MessageIdentity.from_message_id(
        transport=MESHCORE_TRANSPORT_NAME,
        room_id=room.room_id,
        message_id=message_id,
    )
    return InboundMessage(
        transport=MESHCORE_TRANSPORT_NAME,
        message_id=message_id,
        sender_id=sender_id,
        channel_index=channel_index,
        text=text,
        metadata=metadata or {},
        source_room=room,
        reply_target=room,
        sender=SenderIdentity.from_sender_id(
            sender_id=sender_id,
            transport_scope=MESHCORE_TRANSPORT_NAME,
        ),
        message=message_identity,
    )


def _bridge_event_metadata(connection: sqlite3.Connection) -> list[str]:
    return [
        str(row["metadata_json"])
        for row in connection.execute(
            """
            SELECT metadata_json
            FROM normalized_audit_events
            WHERE event_type LIKE 'bridge.%'
            ORDER BY id
            """
        ).fetchall()
    ]


def _serialized_private_tables(connection: sqlite3.Connection) -> str:
    tables = (
        "telegram_bridge_pending",
        "telegram_audit_events",
        "normalized_audit_events",
        "audit_metadata",
    )
    values: list[str] = []
    for table in tables:
        values.extend(
            " ".join(str(value) for value in row)
            for row in connection.execute(f"SELECT * FROM {table}").fetchall()
        )
    return " ".join(values)


@pytest.mark.asyncio
async def test_meshcore_normal_text_forwards_to_telegram(tmp_path: Path) -> None:
    built = _build_bridge(tmp_path)

    outbound = await built.service.process_message(_message("Voy en 10 minutos"))

    assert outbound is None
    assert built.telegram_client.send_message_calls == [
        {"chat_id": "1001", "text": "MC: Voy en 10 minutos"}
    ]
    assert built.connection.execute("SELECT status FROM telegram_bridge_pending").fetchone()[0] == (
        "accepted_by_telegram"
    )


@pytest.mark.asyncio
async def test_pending_echo_lock_does_not_crash_or_echo_to_telegram(tmp_path: Path) -> None:
    database_path = tmp_path / "audit.db"
    locker = connect_database(str(database_path))
    connection = connect_database(str(database_path))
    connection.execute("PRAGMA busy_timeout=1")
    locker.execute("BEGIN IMMEDIATE")
    client = FakeTelegramClient()
    forwarder = MeshCoreToTelegramForwarder(
        config=_telegram_config(),
        client=client,
        store=TelegramStore(
            connection,
            audit_key=AuditKey(b"t" * AUDIT_KEY_MIN_BYTES, key_id="tg-key"),
        ),
        sleep=_noop_sleep,
    )

    handled = await forwarder.forward_normal_text(_message("TG: hello"))

    assert handled is True
    assert client.send_message_calls == []
    assert not connection.in_transaction
    locker.rollback()


@pytest.mark.asyncio
async def test_meshcore_to_telegram_prefix_can_be_disabled(tmp_path: Path) -> None:
    built = _build_bridge(
        tmp_path,
        telegram_config=_telegram_config(meshcore_to_telegram_prefix=""),
    )

    await built.service.process_message(_message("sin prefijo"))

    assert built.telegram_client.send_message_calls == [{"chat_id": "1001", "text": "sin prefijo"}]


@pytest.mark.asyncio
async def test_forward_meshcore_to_telegram_false_does_not_send(tmp_path: Path) -> None:
    built = _build_bridge(
        tmp_path,
        telegram_config=_telegram_config(forward_meshcore_to_telegram=False),
    )

    await built.service.process_message(_message("hello"))

    assert built.telegram_client.send_message_calls == []
    assert "forward_disabled" in " ".join(_bridge_event_metadata(built.connection))


@pytest.mark.asyncio
async def test_wrong_channel_is_not_forwarded(tmp_path: Path) -> None:
    built = _build_bridge(tmp_path)

    await built.service.process_message(_message("hello", channel_index=2))

    assert built.telegram_client.send_message_calls == []


@pytest.mark.asyncio
async def test_meshcore_commands_are_not_forwarded_to_telegram(tmp_path: Path) -> None:
    built = _build_bridge(tmp_path)

    outbound = await built.service.process_message(_message("!ping"))

    assert outbound is not None
    assert outbound.text == "pong"
    assert built.telegram_client.send_message_calls == []
    assert [sent.text for sent in built.meshcore_transport.sent] == ["pong"]


@pytest.mark.asyncio
async def test_self_sent_marker_is_ignored(tmp_path: Path) -> None:
    built = _build_bridge(tmp_path)

    await built.service.process_message(_message("self echo", metadata={"outgoing": True}))

    assert built.telegram_client.send_message_calls == []
    assert "loop_prevention" in " ".join(_bridge_event_metadata(built.connection))


@pytest.mark.asyncio
async def test_pending_bridge_record_echo_is_consumed_once(tmp_path: Path) -> None:
    built = _build_bridge(tmp_path)
    content = "TG: ok"
    built.store.create_bridge_record(
        correlation_id="corr:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        destination_transport=MESHCORE_TRANSPORT_NAME,
        destination_room_id=MESHCORE_ROOM,
        content=content,
        size_bytes=len(content.encode("utf-8")),
        status="accepted_by_meshcore_transport",
    )

    await built.service.process_message(_message(content, message_id="echo-1"))
    await built.service.process_message(_message(content, message_id="echo-2"))

    assert built.telegram_client.send_message_calls == [{"chat_id": "1001", "text": "MC: TG: ok"}]
    rows = built.connection.execute(
        "SELECT status FROM telegram_bridge_pending ORDER BY created_at"
    ).fetchall()
    assert [row["status"] for row in rows] == ["observed_echo", "accepted_by_telegram"]


@pytest.mark.asyncio
async def test_repeated_ok_messages_forward_when_no_pending_echo(tmp_path: Path) -> None:
    built = _build_bridge(tmp_path)

    await built.service.process_message(_message("ok", message_id="ok-1"))
    await built.service.process_message(_message("ok", message_id="ok-2"))

    assert [call["text"] for call in built.telegram_client.send_message_calls] == [
        "MC: ok",
        "MC: ok",
    ]


@pytest.mark.asyncio
async def test_same_text_from_other_sender_forwards(tmp_path: Path) -> None:
    built = _build_bridge(tmp_path)
    other_sender = "meshcore-pubkey-prefix:other123"
    built.service.router.authorizer._users[other_sender] = AuthorizedUser(  # noqa: SLF001
        other_sender,
        "other",
        Role.readonly,
    )

    await built.service.process_message(_message("ok", message_id="ok-1"))
    await built.service.process_message(
        _message("ok", message_id="ok-2", sender_id=other_sender)
    )

    assert len(built.telegram_client.send_message_calls) == 2


@pytest.mark.asyncio
async def test_sender_not_authorized_is_not_forwarded(tmp_path: Path) -> None:
    built = _build_bridge(tmp_path, authorized=False)

    await built.service.process_message(_message("private normal text"))

    assert built.telegram_client.send_message_calls == []
    assert "sender_not_registered" in " ".join(
        row["metadata_json"]
        for row in built.connection.execute("SELECT metadata_json FROM normalized_audit_events")
    )


@pytest.mark.asyncio
async def test_meshcore_to_telegram_rate_limit(tmp_path: Path) -> None:
    built = _build_bridge(
        tmp_path,
        telegram_config=_telegram_config(
            inbound_forwarding_rate_limit=RateLimitConfig(commands=1, window_seconds=60)
        ),
    )

    await built.service.process_message(_message("one", message_id="one"))
    await built.service.process_message(_message("two", message_id="two"))

    assert [call["text"] for call in built.telegram_client.send_message_calls] == ["MC: one"]
    assert "rate_limited" in " ".join(_bridge_event_metadata(built.connection))


@pytest.mark.asyncio
async def test_meshcore_to_telegram_send_failure_is_audited(tmp_path: Path) -> None:
    built = _build_bridge(tmp_path, telegram_error=TimeoutError("telegram timeout"))

    await built.service.process_message(_message("hello"))

    assert built.telegram_client.send_message_calls == []
    assert built.connection.execute("SELECT status FROM telegram_bridge_pending").fetchone()[0] == (
        "failed"
    )
    assert "transport_error" in " ".join(_bridge_event_metadata(built.connection))


@pytest.mark.asyncio
async def test_meshcore_to_telegram_429_is_bounded_failure(tmp_path: Path) -> None:
    built = _build_bridge(tmp_path, telegram_error=TelegramRateLimitError(100))

    await built.service.process_message(_message("hello"))

    assert built.connection.execute("SELECT status FROM telegram_bridge_pending").fetchone()[0] == (
        "failed"
    )
    assert "rate_limited" in " ".join(_bridge_event_metadata(built.connection))


@pytest.mark.asyncio
async def test_meshcore_to_telegram_409_is_bounded_failure(tmp_path: Path) -> None:
    built = _build_bridge(tmp_path, telegram_error=TelegramConflictError("conflict"))

    await built.service.process_message(_message("hello"))

    assert built.connection.execute("SELECT status FROM telegram_bridge_pending").fetchone()[0] == (
        "failed"
    )
    assert "consumer_conflict" in " ".join(_bridge_event_metadata(built.connection))


@pytest.mark.asyncio
async def test_meshcore_to_telegram_privacy_in_sqlite_and_logs(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    built = _build_bridge(tmp_path)
    caplog.set_level("INFO")

    await built.service.process_message(_message(PRIVATE_TEXT, message_id="mesh-private-id"))

    serialized = _serialized_private_tables(built.connection)
    logs = caplog.text
    for private_value in (PRIVATE_TEXT, MESHCORE_SENDER, "mesh-private-id", PRIVATE_TOKEN):
        assert private_value not in serialized
        assert private_value not in logs


def test_telegram_forward_render_truncates_utf8_safely() -> None:
    rendered = render_telegram_forward_message(text="áéíóú " * 1000, prefix="MC: ", max_bytes=40)

    assert rendered.text is not None
    assert rendered.truncated is True
    assert len(rendered.text.encode("utf-8")) <= 40
    assert rendered.text.endswith("... truncado")
