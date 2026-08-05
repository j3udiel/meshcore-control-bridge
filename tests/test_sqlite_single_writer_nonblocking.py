from __future__ import annotations

import asyncio
import statistics
import time
from pathlib import Path

import pytest

from meshcore_control.config import AppConfig, TelegramConfig
from meshcore_control.main import _build_telegram_services
from meshcore_control.storage.audit_flow import AuditFlow
from meshcore_control.storage.database import (
    connect_database,
    connection_info,
    telegram_database_path,
    write_transaction,
)
from meshcore_control.storage.normalized_audit import (
    AUDIT_KEY_MIN_BYTES,
    AuditKey,
    NormalizedAuditRepository,
    NormalizedAuditSettings,
)
from meshcore_control.storage.repositories import AuditRepository
from meshcore_control.telegram.database import migrate_telegram_tables
from meshcore_control.telegram.store import TelegramStore

VALID_TOKEN = "123456789:abcdefghijklmnopqrstuvwxyzABCDE"


def test_telegram_database_path_is_sibling_of_audit_db(tmp_path: Path) -> None:
    assert telegram_database_path(str(tmp_path / "audit.db")) == str(tmp_path / "telegram.db")


def test_telegram_tables_migrate_from_legacy_audit_db(tmp_path: Path) -> None:
    audit = connect_database(str(tmp_path / "audit.db"), connection_name="audit")
    telegram = connect_database(str(tmp_path / "telegram.db"), connection_name="telegram")
    store = TelegramStore(audit, audit_key=AuditKey(b"a" * AUDIT_KEY_MIN_BYTES))
    store.mark_activated()
    store.persist_last_update_id(42)
    store.create_bridge_record(
        correlation_id="corr:test",
        destination_transport="homeassistant-meshcore",
        destination_room_id="homeassistant-meshcore:channel:1",
        content="pending",
        size_bytes=7,
        status="accepted_by_meshcore_transport",
    )

    migrate_telegram_tables(source_connection=audit, target_connection=telegram)
    migrate_telegram_tables(source_connection=audit, target_connection=telegram)

    migrated = TelegramStore(telegram, audit_key=AuditKey(b"a" * AUDIT_KEY_MIN_BYTES))
    assert migrated.is_activated()
    assert migrated.last_update_id() == 42
    assert (
        telegram.execute("SELECT COUNT(*) FROM telegram_bridge_pending").fetchone()[0] == 1
    )
    assert not audit.in_transaction
    assert not telegram.in_transaction


def test_build_telegram_services_uses_separate_telegram_database(tmp_path: Path) -> None:
    token_file = tmp_path / "telegram.bot_token"
    token_file.write_text(VALID_TOKEN, encoding="utf-8")
    token_file.chmod(0o600)
    audit_path = tmp_path / "audit.db"
    audit = connect_database(str(audit_path), connection_name="audit")
    TelegramStore(audit, audit_key=AuditKey(b"a" * AUDIT_KEY_MIN_BYTES)).persist_last_update_id(
        99
    )
    settings = NormalizedAuditSettings(
        enabled=True,
        audit_key=AuditKey(key=b"t" * AUDIT_KEY_MIN_BYTES, key_id="audit-key"),
    )
    repository = NormalizedAuditRepository(audit, settings)
    telegram_service, forwarder = _build_telegram_services(
        AppConfig(
            database_path=str(audit_path),
            telegram=TelegramConfig(
                enabled=True,
                bot_token_file=str(token_file),
                allowed_private_chat_id="1001",
                allowed_user_id="2002",
            ),
        ),
        settings,
        router=None,  # type: ignore[arg-type]
        audit_flow=AuditFlow(
            connection=audit,
            legacy=AuditRepository(audit),
            normalized=repository,
        ),
        meshcore_transport=None,  # type: ignore[arg-type]
        normalized_audit=repository,
    )

    assert telegram_service is not None
    assert forwarder is not None
    assert connection_info(telegram_service.store.connection).path == str(tmp_path / "telegram.db")
    assert telegram_service.store.last_update_id() == 99


def test_audit_lock_does_not_block_telegram_pending_record(tmp_path: Path) -> None:
    audit = connect_database(str(tmp_path / "audit.db"), connection_name="audit")
    telegram = connect_database(str(tmp_path / "telegram.db"), connection_name="telegram")
    audit.execute("BEGIN IMMEDIATE")
    store = TelegramStore(telegram, audit_key=AuditKey(b"a" * AUDIT_KEY_MIN_BYTES))

    started = time.perf_counter()
    record = store.create_bridge_record(
        correlation_id="corr:test",
        destination_transport="homeassistant-meshcore",
        destination_room_id="homeassistant-meshcore:channel:1",
        content="hello",
        size_bytes=5,
        status="accepted_by_meshcore_transport",
    )
    elapsed = time.perf_counter() - started

    assert record.status == "accepted_by_meshcore_transport"
    assert elapsed < 0.2
    assert not telegram.in_transaction
    audit.rollback()


@pytest.mark.asyncio
async def test_event_loop_remains_responsive_while_telegram_state_is_written(
    tmp_path: Path,
) -> None:
    telegram = connect_database(str(tmp_path / "telegram.db"), connection_name="telegram")
    store = TelegramStore(telegram, audit_key=AuditKey(b"a" * AUDIT_KEY_MIN_BYTES))
    ticks = 0

    async def ticker() -> None:
        nonlocal ticks
        deadline = time.perf_counter() + 0.15
        while time.perf_counter() < deadline:
            await asyncio.sleep(0.005)
            ticks += 1

    async def writes() -> None:
        for index in range(40):
            store.seen_or_store_update(index)
            await asyncio.sleep(0)

    await asyncio.gather(ticker(), writes())

    assert ticks >= 10
    assert not telegram.in_transaction


def test_stress_interleaved_audit_and_telegram_writes(tmp_path: Path) -> None:
    audit = connect_database(str(tmp_path / "audit.db"), connection_name="audit")
    telegram = connect_database(str(tmp_path / "telegram.db"), connection_name="telegram")
    store = TelegramStore(telegram, audit_key=AuditKey(b"a" * AUDIT_KEY_MIN_BYTES))
    latencies: list[float] = []

    for index in range(1000):
        started = time.perf_counter()
        if index % 3 == 0:
            write_transaction(
                audit,
                lambda index=index: audit.execute(
                    "INSERT INTO audit_events (event_type, data_json) VALUES (?, ?)",
                    ("stress", f'{{"index":{index}}}'),
                ),
                operation_name="stress.audit",
            )
        elif index % 3 == 1:
            store.seen_or_store_update(index)
        else:
            store.create_bridge_record(
                correlation_id="corr:test",
                destination_transport="homeassistant-meshcore",
                destination_room_id="homeassistant-meshcore:channel:1",
                content=f"hello {index}",
                size_bytes=7,
                status="accepted_by_meshcore_transport",
            )
        latencies.append(time.perf_counter() - started)

    p95 = statistics.quantiles(latencies, n=20)[18]
    assert p95 < 0.05
    assert not audit.in_transaction
    assert not telegram.in_transaction
