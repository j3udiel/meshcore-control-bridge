from __future__ import annotations

import asyncio
import hashlib
import logging
import sqlite3
from datetime import UTC, datetime

from meshcore_control.app import BridgeService
from meshcore_control.auth.authorization import AuthorizedUser, Authorizer
from meshcore_control.auth.roles import Role
from meshcore_control.commands.router import CommandRouter
from meshcore_control.config import AppConfig
from meshcore_control.models import InboundMessage, RoomRef
from meshcore_control.plugins import build_registry
from meshcore_control.security.deduplication import Deduplicator
from meshcore_control.security.rate_limit import RateLimiter
from meshcore_control.storage.database import connect_database
from meshcore_control.storage.repositories import AuditRepository
from meshcore_control.transport.fake import FakeTransport

SENDER_ID = "meshcore-pubkey-prefix:abcdef123456"
OTHER_SENDER_ID = "meshcore-pubkey-prefix:123456abcdef"


def inbound(
    *,
    text: str = "!ping",
    message_id: str | None = "platform-message-1",
    sender_id: str = SENDER_ID,
    room_id: str = "homeassistant-meshcore:channel:1",
    transport: str = "homeassistant-meshcore",
    channel_index: int = 1,
    received_at: datetime | None = None,
) -> InboundMessage:
    room = RoomRef(
        transport=transport,
        room_id=room_id,
        room_kind="meshcore_channel",
        metadata={"channel_index": channel_index},
    )
    return InboundMessage(
        transport=transport,
        message_id=message_id,
        sender_id=sender_id,
        channel_index=channel_index,
        text=text,
        received_at=received_at or datetime(2026, 8, 2, tzinfo=UTC),
        source_room=room,
        reply_target=room,
    )


def deduplicator(connection: sqlite3.Connection, *, window_seconds: int = 300) -> Deduplicator:
    return Deduplicator(connection, window_seconds=window_seconds)


def test_same_platform_message_id_room_and_sender_is_duplicate(tmp_path) -> None:
    dedup = deduplicator(connect_database(str(tmp_path / "audit.db")))
    message = inbound()

    assert dedup.seen_or_store(message) is False
    assert dedup.seen_or_store(message) is True


def test_same_platform_message_id_in_another_room_is_not_duplicate(tmp_path) -> None:
    dedup = deduplicator(connect_database(str(tmp_path / "audit.db")))

    assert dedup.seen_or_store(inbound(room_id="homeassistant-meshcore:channel:1")) is False
    assert dedup.seen_or_store(inbound(room_id="homeassistant-meshcore:channel:2")) is False


def test_same_platform_message_id_from_another_sender_is_not_duplicate(tmp_path) -> None:
    dedup = deduplicator(connect_database(str(tmp_path / "audit.db")))

    assert dedup.seen_or_store(inbound(sender_id=SENDER_ID)) is False
    assert dedup.seen_or_store(inbound(sender_id=OTHER_SENDER_ID)) is False


def test_same_text_without_message_id_inside_window_is_duplicate(tmp_path) -> None:
    dedup = deduplicator(connect_database(str(tmp_path / "audit.db")), window_seconds=300)
    first = inbound(message_id=None, text="  !ping   private-text  ")
    second = inbound(message_id=None, text="!ping private-text")

    assert dedup.seen_or_store(first) is False
    assert dedup.seen_or_store(second) is True


def test_same_text_without_message_id_outside_window_is_not_duplicate(tmp_path) -> None:
    dedup = deduplicator(connect_database(str(tmp_path / "audit.db")), window_seconds=60)
    first = inbound(
        message_id=None,
        text="!ping private-text",
        received_at=datetime(2026, 8, 2, 10, 0, tzinfo=UTC),
    )
    second = inbound(
        message_id=None,
        text="!ping private-text",
        received_at=datetime(2026, 8, 2, 10, 2, tzinfo=UTC),
    )

    assert dedup.seen_or_store(first) is False
    assert dedup.seen_or_store(second) is False


def test_correlation_id_does_not_affect_duplicate_detection(tmp_path) -> None:
    dedup = deduplicator(connect_database(str(tmp_path / "audit.db")))
    first = inbound(message_id="same-platform-id")
    second = inbound(message_id="same-platform-id")

    assert first.message is not None
    assert second.message is not None
    assert first.message.correlation_id != second.message.correlation_id
    assert dedup.seen_or_store(first) is False
    assert dedup.seen_or_store(second) is True


def test_legacy_sqlite_deduplication_key_is_still_honored(tmp_path) -> None:
    connection = connect_database(str(tmp_path / "audit.db"))
    message = inbound(
        transport="fake",
        room_id="fake:channel:1",
        sender_id="sender-1",
        message_id="legacy-message-id",
    )
    legacy_material = "id:fake:sender-1:1:legacy-message-id"
    legacy_key = hashlib.sha256(legacy_material.encode("utf-8")).hexdigest()
    connection.execute(
        "INSERT INTO deduplication_keys (dedup_key, expires_at) VALUES (?, ?)",
        (legacy_key, datetime.now(UTC).timestamp() + 300),
    )
    connection.commit()

    assert deduplicator(connection).seen_or_store(message) is True


def test_duplicate_log_does_not_include_sender_or_private_text(tmp_path, caplog) -> None:
    connection = connect_database(str(tmp_path / "audit.db"))
    registry = build_registry()
    transport = FakeTransport()
    audit = AuditRepository(connection)
    router = CommandRouter(
        registry=registry,
        authorizer=Authorizer({SENDER_ID: AuthorizedUser(SENDER_ID, "tester", Role.admin)}),
        audit=audit,
        services={"registry": registry, "config": AppConfig()},
        prefix="!",
    )
    service = BridgeService(
        transport=transport,
        router=router,
        deduplicator=Deduplicator(connection, window_seconds=300),
        rate_limiter=RateLimiter(max_commands=10, window_seconds=60),
        channel_index=1,
    )
    message = inbound(text="!ping private-text-token", message_id="same-id")

    with caplog.at_level(logging.INFO):
        first = asyncio.run(service.process_message(message))
        second = asyncio.run(service.process_message(message))

    assert first is not None
    assert second is None
    assert SENDER_ID not in caplog.text
    assert "private-text-token" not in caplog.text
