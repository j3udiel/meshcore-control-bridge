from __future__ import annotations

import logging
import os
import sqlite3
import stat
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone

import pytest

from meshcore_control.models import InboundMessage, RoomRef, SenderIdentity
from meshcore_control.storage import normalized_audit
from meshcore_control.storage.database import connect_database
from meshcore_control.storage.normalized_audit import (
    AUDIT_KEY_MIN_BYTES,
    AuditKey,
    AuditKeyError,
    NormalizedAuditEvent,
    NormalizedAuditEventType,
    NormalizedAuditRepository,
    NormalizedAuditSettings,
    canonical_metadata_json,
    load_audit_key_file,
    load_or_create_app_audit_key,
    new_correlation_id,
    new_event_id,
    utc_rfc3339,
)

PRIVATE_SENDER = "meshcore-pubkey-prefix:private-sender-value"
PRIVATE_MESSAGE_ID = "private-platform-message-id"
PRIVATE_TEXT = "!ping private text"


def inbound() -> InboundMessage:
    room = RoomRef.channel(transport="homeassistant-meshcore", channel_index=1)
    return InboundMessage(
        transport="homeassistant-meshcore",
        message_id=PRIVATE_MESSAGE_ID,
        sender_id=PRIVATE_SENDER,
        channel_index=1,
        text=PRIVATE_TEXT,
        received_at=datetime(2026, 8, 3, 13, 49, 15, 526000, tzinfo=UTC),
        source_room=room,
        reply_target=room,
    )


def repository_for(tmp_path, key: AuditKey | None = None) -> NormalizedAuditRepository:
    audit_key = key or AuditKey(key=b"b" * AUDIT_KEY_MIN_BYTES, key_id="test-key")
    return NormalizedAuditRepository(
        connect_database(str(tmp_path / "audit.db")),
        NormalizedAuditSettings(enabled=True, audit_key=audit_key),
    )


def test_existing_sqlite_database_receives_additive_normalized_schema(tmp_path) -> None:
    db_path = tmp_path / "audit.db"
    legacy = sqlite3.connect(db_path)
    legacy.execute(
        """
        CREATE TABLE inbound_messages (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          transport TEXT NOT NULL,
          message_id TEXT,
          sender_id TEXT NOT NULL,
          channel_index INTEGER NOT NULL,
          text_hash TEXT NOT NULL,
          received_at TEXT NOT NULL,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    legacy.commit()
    legacy.close()

    connection = connect_database(str(db_path))
    connect_database(str(db_path)).close()

    tables = {
        row["name"]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert "inbound_messages" in tables
    assert "normalized_audit_events" in tables
    assert "audit_metadata" in tables


def test_normalized_audit_schema_has_expected_checks_and_indexes(tmp_path) -> None:
    connection = connect_database(str(tmp_path / "audit.db"))
    sql = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='normalized_audit_events'"
    ).fetchone()["sql"]
    indexes = {
        row["name"]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        ).fetchall()
    }

    assert "event_id TEXT NOT NULL UNIQUE" in sql
    assert "CHECK (schema_version >= 1)" in sql
    assert "CHECK (sender_stable IN (0, 1))" in sql
    assert "CHECK (duration_ms IS NULL OR duration_ms >= 0)" in sql
    assert "CHECK (length(metadata_json) <= 4096)" in sql
    assert "idx_normalized_audit_correlation_time" in indexes
    assert "idx_normalized_audit_causation" in indexes
    assert "idx_normalized_audit_type_time" in indexes
    assert "idx_normalized_audit_room_time" in indexes
    assert "idx_normalized_audit_sender_time" in indexes
    assert "idx_normalized_audit_event_id" not in indexes


def test_event_and_correlation_ids_have_documented_formats() -> None:
    event_id = new_event_id()
    correlation_id = new_correlation_id()

    assert event_id.startswith("evt:")
    assert len(event_id) == 36
    assert correlation_id.startswith("corr:")
    assert len(correlation_id) == 37


def test_hmac_references_use_domain_separation_and_key_id() -> None:
    key = AuditKey(key=b"a" * AUDIT_KEY_MIN_BYTES, key_id="test-key")

    sender_ref = key.sender_ref_hash(PRIVATE_SENDER)
    message_ref = key.message_ref_hash(
        transport="homeassistant-meshcore",
        room_id="homeassistant-meshcore:channel:1",
        message_id=PRIVATE_MESSAGE_ID,
    )

    assert sender_ref.startswith("hmac-sha256:v1:test-key:")
    assert message_ref.startswith("hmac-sha256:v1:test-key:")
    assert sender_ref != message_ref
    assert PRIVATE_SENDER not in sender_ref
    assert PRIVATE_MESSAGE_ID not in message_ref


def test_from_inbound_uses_normalized_sender_identity_not_legacy_sender_id() -> None:
    key = AuditKey(key=b"a" * AUDIT_KEY_MIN_BYTES, key_id="test-key")
    normalized_sender = "meshcore-pubkey-prefix:normalized-sender"
    room = RoomRef.channel(transport="homeassistant-meshcore", channel_index=1)
    message = InboundMessage(
        transport="homeassistant-meshcore",
        message_id=PRIVATE_MESSAGE_ID,
        sender_id=PRIVATE_SENDER,
        channel_index=1,
        text=PRIVATE_TEXT,
        received_at=datetime(2026, 8, 3, 13, 49, 15, 526000, tzinfo=UTC),
        source_room=room,
        reply_target=room,
        sender=SenderIdentity(
            sender_id=normalized_sender,
            transport_scope="homeassistant-meshcore",
            identity_kind="meshcore_pubkey_prefix",
            stable=True,
        ),
    )

    event = NormalizedAuditEvent.from_inbound(
        event_type=NormalizedAuditEventType.MESSAGE_RECEIVED,
        message=message,
        audit_key=key,
    )

    assert event.sender_ref_hash == key.sender_ref_hash(normalized_sender)
    assert event.sender_ref_hash != key.sender_ref_hash(PRIVATE_SENDER)


def test_from_inbound_without_sender_records_no_sender_reference() -> None:
    key = AuditKey(key=b"a" * AUDIT_KEY_MIN_BYTES, key_id="test-key")
    message = inbound()
    object.__setattr__(message, "sender", None)

    event = NormalizedAuditEvent.from_inbound(
        event_type=NormalizedAuditEventType.MESSAGE_RECEIVED,
        message=message,
        audit_key=key,
    )

    assert event.sender_ref_hash is None
    assert event.sender_identity_kind is None
    assert event.sender_stable is False


def test_from_inbound_requires_message_identity_or_explicit_correlation_id() -> None:
    key = AuditKey(key=b"a" * AUDIT_KEY_MIN_BYTES, key_id="test-key")
    message = inbound()
    object.__setattr__(message, "message", None)

    with pytest.raises(ValueError, match="MessageIdentity"):
        NormalizedAuditEvent.from_inbound(
            event_type=NormalizedAuditEventType.MESSAGE_RECEIVED,
            message=message,
            audit_key=key,
        )

    correlation_id = new_correlation_id()
    first = NormalizedAuditEvent.from_inbound(
        event_type=NormalizedAuditEventType.MESSAGE_RECEIVED,
        message=message,
        audit_key=key,
        correlation_id=correlation_id,
    )
    second = NormalizedAuditEvent.from_inbound(
        event_type=NormalizedAuditEventType.MESSAGE_IGNORED,
        message=message,
        audit_key=key,
        correlation_id=correlation_id,
        metadata={"ignore_reason": "wrong_channel"},
    )
    assert first.correlation_id == second.correlation_id == correlation_id


def test_repository_writes_normalized_event_without_private_values(tmp_path) -> None:
    connection = connect_database(str(tmp_path / "audit.db"))
    key = AuditKey(key=b"b" * AUDIT_KEY_MIN_BYTES, key_id="test-key")
    repository = NormalizedAuditRepository(
        connection,
        NormalizedAuditSettings(enabled=True, audit_key=key),
    )
    message = inbound()
    event = NormalizedAuditEvent.from_inbound(
        event_type=NormalizedAuditEventType.MESSAGE_RECEIVED,
        message=message,
        audit_key=key,
        metadata={
            "channel_index": 1,
            "message_id_present": True,
            "identity_kind": "meshcore_pubkey_prefix",
            "identity_stable": True,
        },
    )

    assert repository.record(event) is True

    row = connection.execute("SELECT * FROM normalized_audit_events").fetchone()
    serialized_row = " ".join(str(value) for value in row)
    metadata_rows = {
        row["key"]: row["value"]
        for row in connection.execute("SELECT key, value FROM audit_metadata").fetchall()
    }
    assert row["event_id"].startswith("evt:")
    assert row["correlation_id"].startswith("corr:")
    assert row["sender_ref_hash"].startswith("hmac-sha256:v1:test-key:")
    assert row["message_ref_hash"].startswith("hmac-sha256:v1:test-key:")
    assert row["occurred_at"] == "2026-08-03T13:49:15.526Z"
    assert PRIVATE_SENDER not in serialized_row
    assert PRIVATE_MESSAGE_ID not in serialized_row
    assert PRIVATE_TEXT not in serialized_row
    assert key.key.hex() not in serialized_row
    assert metadata_rows["audit_key_id"] == "test-key"
    assert metadata_rows["normalized_audit_schema_version"] == "1"
    assert key.key.hex() not in " ".join(metadata_rows.values())

    sqlite_values = []
    for table in ("normalized_audit_events", "audit_metadata"):
        rows = connection.execute(f"SELECT * FROM {table}").fetchall()
        sqlite_values.extend(" ".join(str(value) for value in row) for row in rows)
    serialized_database = " ".join(sqlite_values)
    assert PRIVATE_SENDER not in serialized_database
    assert PRIVATE_MESSAGE_ID not in serialized_database
    assert PRIVATE_TEXT not in serialized_database
    assert key.key.hex() not in serialized_database


def test_validation_errors_and_logs_do_not_include_private_values(tmp_path, caplog) -> None:
    repository = repository_for(tmp_path)
    event = NormalizedAuditEvent(
        event_type=NormalizedAuditEventType.MESSAGE_RECEIVED,
        correlation_id=new_correlation_id(),
        transport="fake",
        source_room_id="fake:channel:1",
        source_room_kind="meshcore_channel",
        sender_stable=False,
        metadata={"raw_sender_id": PRIVATE_SENDER},
    )

    with caplog.at_level(logging.WARNING), pytest.raises(ValueError) as exc_info:
        repository.record(event)

    message = str(exc_info.value)
    assert PRIVATE_SENDER not in message
    assert PRIVATE_MESSAGE_ID not in message
    assert PRIVATE_TEXT not in message
    assert repository.settings.audit_key is not None
    assert repository.settings.audit_key.key.hex() not in message
    assert PRIVATE_SENDER not in caplog.text
    assert PRIVATE_MESSAGE_ID not in caplog.text
    assert PRIVATE_TEXT not in caplog.text
    assert repository.settings.audit_key.key.hex() not in caplog.text


@pytest.mark.parametrize(
    ("event", "match"),
    [
        (
            NormalizedAuditEvent(
                event_type=NormalizedAuditEventType.MESSAGE_RECEIVED,
                event_id="bad",
                correlation_id=new_correlation_id(),
                transport="fake",
                source_room_id="fake:channel:1",
                source_room_kind="meshcore_channel",
                sender_stable=False,
            ),
            "event_id",
        ),
        (
            NormalizedAuditEvent(
                event_type=NormalizedAuditEventType.MESSAGE_RECEIVED,
                correlation_id="bad",
                transport="fake",
                source_room_id="fake:channel:1",
                source_room_kind="meshcore_channel",
                sender_stable=False,
            ),
            "correlation_id",
        ),
        (
            NormalizedAuditEvent(
                event_type=NormalizedAuditEventType.MESSAGE_RECEIVED,
                correlation_id=new_correlation_id(),
                causation_event_id="bad",
                transport="fake",
                source_room_id="fake:channel:1",
                source_room_kind="meshcore_channel",
                sender_stable=False,
            ),
            "causation_event_id",
        ),
        (
            NormalizedAuditEvent(
                event_type=NormalizedAuditEventType.MESSAGE_RECEIVED,
                correlation_id=new_correlation_id(),
                transport="",
                source_room_id="fake:channel:1",
                source_room_kind="meshcore_channel",
                sender_stable=False,
            ),
            "transport",
        ),
        (
            NormalizedAuditEvent(
                event_type=NormalizedAuditEventType.MESSAGE_RECEIVED,
                correlation_id=new_correlation_id(),
                transport="fake",
                source_room_id="fake:channel:1",
                source_room_kind="meshcore_channel",
                sender_stable=False,
                duration_ms=-1,
            ),
            "duration_ms",
        ),
        (
            NormalizedAuditEvent(
                event_type=NormalizedAuditEventType.MESSAGE_RECEIVED,
                correlation_id=new_correlation_id(),
                transport="fake",
                source_room_id="fake:channel:1",
                source_room_kind="meshcore_channel",
                sender_stable=False,
                schema_version=2,
            ),
            "schema_version",
        ),
    ],
)
def test_repository_rejects_invalid_events_before_sql(tmp_path, event, match) -> None:
    repository = repository_for(tmp_path)

    with pytest.raises(ValueError, match=match):
        repository.record(event)

    count = repository.connection.execute(
        "SELECT COUNT(*) AS count FROM normalized_audit_events"
    ).fetchone()["count"]
    assert count == 0


def test_repository_rejects_reference_hashes_from_another_audit_key(tmp_path) -> None:
    repository_key = AuditKey(key=b"b" * AUDIT_KEY_MIN_BYTES, key_id="repo-key")
    event_key = AuditKey(key=b"c" * AUDIT_KEY_MIN_BYTES, key_id="other-key")
    repository = repository_for(tmp_path, repository_key)
    event = NormalizedAuditEvent.from_inbound(
        event_type=NormalizedAuditEventType.MESSAGE_RECEIVED,
        message=inbound(),
        audit_key=event_key,
    )

    with pytest.raises(ValueError, match="key_id"):
        repository.record(event)


def test_repository_rejects_unknown_reference_hash_format(tmp_path) -> None:
    repository = repository_for(tmp_path)
    assert repository.settings.audit_key is not None
    event = replace(
        NormalizedAuditEvent.from_inbound(
            event_type=NormalizedAuditEventType.MESSAGE_RECEIVED,
            message=inbound(),
            audit_key=repository.settings.audit_key,
        ),
        sender_ref_hash="sha256:legacy",
    )

    with pytest.raises(ValueError, match="sender_ref_hash"):
        repository.record(event)


def test_repository_event_factory_binds_events_to_repository_key(tmp_path) -> None:
    repository = repository_for(tmp_path)
    assert repository.settings.audit_key is not None

    event = repository.event_from_inbound(
        event_type=NormalizedAuditEventType.MESSAGE_RECEIVED,
        message=inbound(),
    )

    assert event.sender_ref_hash is not None
    assert event.sender_ref_hash.startswith(
        f"hmac-sha256:v1:{repository.settings.audit_key.key_id}:"
    )


def test_disabled_standalone_legacy_does_not_write_normalized_rows(tmp_path, caplog) -> None:
    connection = connect_database(str(tmp_path / "audit.db"))
    repository = NormalizedAuditRepository(
        connection,
        NormalizedAuditSettings.legacy_disabled(),
    )
    event = NormalizedAuditEvent(
        event_type=NormalizedAuditEventType.MESSAGE_IGNORED,
        correlation_id=new_correlation_id(),
        transport="fake",
        source_room_id="fake:channel:1",
        source_room_kind="meshcore_channel",
        sender_stable=False,
        metadata={"ignore_reason": "wrong_channel"},
    )

    with caplog.at_level(logging.WARNING):
        assert repository.record(event) is False

    assert "Normalized audit disabled" in caplog.text
    assert PRIVATE_SENDER not in caplog.text
    assert PRIVATE_MESSAGE_ID not in caplog.text
    assert PRIVATE_TEXT not in caplog.text
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        assert repository.record(event) is False
    assert caplog.text == ""

    count = connection.execute(
        "SELECT COUNT(*) AS count FROM normalized_audit_events"
    ).fetchone()["count"]
    assert count == 0


def test_standalone_explicit_key_enables_normalized_audit(monkeypatch) -> None:
    monkeypatch.setenv("AUDIT_KEY", (b"c" * AUDIT_KEY_MIN_BYTES).hex())

    settings = NormalizedAuditSettings.from_environment()

    assert settings.enabled is True
    assert settings.audit_key is not None


def test_standalone_rejects_ambiguous_key_configuration(monkeypatch, tmp_path) -> None:
    key_path = tmp_path / "audit.key"
    key_path.write_bytes(b"c" * AUDIT_KEY_MIN_BYTES)
    key_path.chmod(0o600)
    monkeypatch.setenv("AUDIT_KEY", (b"c" * AUDIT_KEY_MIN_BYTES).hex())
    monkeypatch.setenv("AUDIT_KEY_FILE", str(key_path))

    with pytest.raises(AuditKeyError, match="mutually exclusive"):
        NormalizedAuditSettings.from_environment()


def test_standalone_explicit_invalid_key_fails_closed(monkeypatch) -> None:
    monkeypatch.setenv("AUDIT_KEY", "abcd")

    with pytest.raises(AuditKeyError):
        NormalizedAuditSettings.from_environment()


def test_standalone_explicit_activation_without_key_fails_closed(monkeypatch) -> None:
    monkeypatch.delenv("AUDIT_KEY", raising=False)
    monkeypatch.delenv("AUDIT_KEY_FILE", raising=False)
    monkeypatch.setenv("NORMALIZED_AUDIT_REQUIRED", "true")

    with pytest.raises(AuditKeyError):
        NormalizedAuditSettings.from_environment()


def test_standalone_legacy_without_key_is_disabled(monkeypatch) -> None:
    monkeypatch.delenv("AUDIT_KEY", raising=False)
    monkeypatch.delenv("AUDIT_KEY_FILE", raising=False)
    monkeypatch.delenv("NORMALIZED_AUDIT_REQUIRED", raising=False)

    settings = NormalizedAuditSettings.from_environment()

    assert settings.enabled is False
    assert settings.audit_key is None


def test_homeassistant_app_key_created_securely_and_reused(tmp_path) -> None:
    key_path = tmp_path / "audit.key"

    first = load_or_create_app_audit_key(str(key_path))
    second = load_or_create_app_audit_key(str(key_path))

    mode = stat.S_IMODE(key_path.stat().st_mode)
    assert mode == 0o600
    assert len(key_path.read_bytes()) >= AUDIT_KEY_MIN_BYTES
    assert first.key == second.key
    assert first.key_id == second.key_id


@pytest.mark.parametrize("mode", [0o644, 0o666])
def test_audit_key_loader_rejects_broad_file_permissions(tmp_path, mode) -> None:
    key_path = tmp_path / "audit.key"
    key_path.write_bytes(b"d" * AUDIT_KEY_MIN_BYTES)
    key_path.chmod(mode)

    with pytest.raises(AuditKeyError, match="permissions"):
        load_audit_key_file(str(key_path))


def test_audit_key_loader_accepts_valid_0600_file(tmp_path) -> None:
    key_path = tmp_path / "audit.key"
    key_path.write_bytes(b"d" * AUDIT_KEY_MIN_BYTES)
    key_path.chmod(0o600)

    key = load_audit_key_file(str(key_path))

    assert key.key == b"d" * AUDIT_KEY_MIN_BYTES


def test_existing_invalid_app_key_is_not_overwritten(tmp_path) -> None:
    key_path = tmp_path / "audit.key"
    key_path.write_bytes(b"short")
    before = key_path.read_bytes()

    with pytest.raises(AuditKeyError):
        load_or_create_app_audit_key(str(key_path))

    assert key_path.read_bytes() == before


def test_audit_key_loader_rejects_symlink(tmp_path) -> None:
    target = tmp_path / "target.key"
    target.write_bytes(b"d" * AUDIT_KEY_MIN_BYTES)
    link = tmp_path / "audit.key"
    link.symlink_to(target)

    with pytest.raises(AuditKeyError):
        load_audit_key_file(str(link))


def test_audit_key_loader_rejects_directory(tmp_path) -> None:
    with pytest.raises(AuditKeyError):
        load_audit_key_file(str(tmp_path))


def test_app_key_creation_writes_all_bytes(monkeypatch, tmp_path) -> None:
    original_write = normalized_audit.os.write
    write_calls = 0

    def partial_write(fd: int, data: bytes) -> int:
        nonlocal write_calls
        write_calls += 1
        return original_write(fd, data[:5])

    monkeypatch.setattr(normalized_audit.os, "write", partial_write)

    key_path = tmp_path / "audit.key"
    key = load_or_create_app_audit_key(str(key_path))

    assert write_calls > 1
    assert key_path.read_bytes() == key.key
    assert len(key_path.read_bytes()) == AUDIT_KEY_MIN_BYTES


def test_app_key_creation_handles_concurrent_publication(monkeypatch, tmp_path) -> None:
    published_key = b"e" * AUDIT_KEY_MIN_BYTES
    original_link = normalized_audit.os.link
    link_calls = 0

    def racing_link(src: os.PathLike[str] | str, dst: os.PathLike[str] | str) -> None:
        nonlocal link_calls
        link_calls += 1
        dst_path = os.fspath(dst)
        fd = os.open(dst_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(fd, published_key)
            os.fsync(fd)
        finally:
            os.close(fd)
        raise FileExistsError(dst_path)

    monkeypatch.setattr(normalized_audit.os, "link", racing_link)

    key_path = tmp_path / "audit.key"
    key = load_or_create_app_audit_key(str(key_path))

    assert link_calls == 1
    assert key.key == published_key
    assert key_path.read_bytes() == published_key
    assert not list(tmp_path.glob(".audit.key.*.tmp"))
    monkeypatch.setattr(normalized_audit.os, "link", original_link)


def test_metadata_is_canonical_and_empty_object_when_empty() -> None:
    assert canonical_metadata_json(NormalizedAuditEventType.RESPONSE_SENT, {}) == "{}"
    payload = canonical_metadata_json(
        NormalizedAuditEventType.COMMAND_AUTHORIZATION,
        {
            "authorization_reason": "sender_not_registered",
            "authorization_result": "denied",
        },
    )

    assert payload == (
        '{"authorization_reason":"sender_not_registered",'
        '"authorization_result":"denied"}'
    )


def test_metadata_rejects_unknown_keys() -> None:
    with pytest.raises(ValueError, match="unknown keys"):
        canonical_metadata_json(
            NormalizedAuditEventType.MESSAGE_RECEIVED,
            {"raw_sender_id": PRIVATE_SENDER},
        )


def test_metadata_rejects_oversized_strings() -> None:
    with pytest.raises(ValueError, match="string exceeds"):
        canonical_metadata_json(
            NormalizedAuditEventType.RESPONSE_SENT,
            {"transport_service": "x" * 300},
        )


def test_metadata_rejects_unsupported_objects_without_str_fallback() -> None:
    with pytest.raises(ValueError, match="unsupported"):
        canonical_metadata_json(
            NormalizedAuditEventType.MESSAGE_IGNORED,
            {"ignore_reason": object()},
        )


def test_command_parsed_rejects_invalid_parse_result() -> None:
    with pytest.raises(ValueError, match="parse_result"):
        canonical_metadata_json(
            NormalizedAuditEventType.COMMAND_PARSED,
            {"parse_result": "authorized"},
        )


def test_utc_rfc3339_formats_utc_with_milliseconds() -> None:
    value = datetime(2026, 8, 3, 13, 49, 15, 526000, tzinfo=UTC)

    assert utc_rfc3339(value) == "2026-08-03T13:49:15.526Z"


def test_utc_rfc3339_converts_offset_to_utc() -> None:
    value = datetime(
        2026,
        8,
        3,
        15,
        49,
        15,
        526000,
        tzinfo=timezone(timedelta(hours=2)),
    )

    assert utc_rfc3339(value) == "2026-08-03T13:49:15.526Z"


def test_utc_rfc3339_rejects_naive_datetime() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        utc_rfc3339(datetime(2026, 8, 3, 13, 49, 15, 526000))
