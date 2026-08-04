from __future__ import annotations

import asyncio
import concurrent.futures
import sqlite3
import time
from pathlib import Path

import pytest

from meshcore_control.main import _run_services
from meshcore_control.storage.database import connect_database, write_transaction
from meshcore_control.storage.normalized_audit import AuditKey
from meshcore_control.telegram.store import TelegramStore


def test_connect_database_enables_wal_busy_timeout_and_foreign_keys(tmp_path: Path) -> None:
    connection = connect_database(str(tmp_path / "audit.db"))

    assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert connection.execute("PRAGMA busy_timeout").fetchone()[0] >= 5000
    assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert connection.execute("PRAGMA synchronous").fetchone()[0] == 1


def test_telegram_bridge_mutations_commit_immediately(tmp_path: Path) -> None:
    store = TelegramStore(
        connect_database(str(tmp_path / "audit.db")),
        audit_key=AuditKey(b"a" * 32),
    )

    store.create_bridge_record(
        correlation_id="corr:test",
        destination_transport="homeassistant-meshcore",
        destination_room_id="homeassistant-meshcore:channel:1",
        content="hello",
        size_bytes=5,
        status="accepted_by_meshcore_transport",
    )

    assert not store.connection.in_transaction
    consumed = store.consume_pending_echo(
        destination_transport="homeassistant-meshcore",
        destination_room_id="homeassistant-meshcore:channel:1",
        content="hello",
        size_bytes=5,
    )
    assert consumed is not None
    assert consumed.status == "observed_echo"
    assert not store.connection.in_transaction


def test_telegram_store_waits_for_short_lived_writer_lock(tmp_path: Path) -> None:
    database_path = tmp_path / "audit.db"
    locker = connect_database(str(database_path))
    locker.execute("BEGIN IMMEDIATE")

    def create_record() -> str:
        connection = connect_database(str(database_path))
        store = TelegramStore(connection, audit_key=AuditKey(b"a" * 32))
        record = store.create_bridge_record(
            correlation_id="corr:test",
            destination_transport="homeassistant-meshcore",
            destination_room_id="homeassistant-meshcore:channel:1",
            content="hello",
            size_bytes=5,
            status="accepted_by_meshcore_transport",
        )
        return record.status

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(create_record)
        time.sleep(0.1)
        assert not future.done()
        locker.commit()
        assert future.result(timeout=3) == "accepted_by_meshcore_transport"


def test_telegram_store_persistent_lock_rolls_back_and_leaves_no_transaction(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "audit.db"
    locker = connect_database(str(database_path))
    contender = connect_database(str(database_path))
    contender.execute("PRAGMA busy_timeout=1")
    locker.execute("BEGIN IMMEDIATE")
    store = TelegramStore(contender, audit_key=AuditKey(b"a" * 32))

    with pytest.raises(sqlite3.OperationalError):
        store.create_bridge_record(
            correlation_id="corr:test",
            destination_transport="homeassistant-meshcore",
            destination_room_id="homeassistant-meshcore:channel:1",
            content="hello",
            size_bytes=5,
            status="accepted_by_meshcore_transport",
        )

    assert not contender.in_transaction
    locker.rollback()


def test_write_transaction_rolls_back_partial_write(tmp_path: Path) -> None:
    connection = connect_database(str(tmp_path / "audit.db"))

    def failing_write() -> None:
        connection.execute(
            """
            INSERT INTO telegram_state (key, value)
            VALUES (?, ?)
            """,
            ("temporary", "value"),
        )
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        write_transaction(connection, failing_write)

    assert connection.execute("SELECT COUNT(*) FROM telegram_state").fetchone()[0] == 0
    assert not connection.in_transaction


@pytest.mark.asyncio
async def test_run_services_cancels_sibling_and_closes_without_secondary_aclose_error() -> None:
    class CrashingBridge:
        closed = False

        async def run_forever(self) -> None:
            raise RuntimeError("primary failure")

        async def close(self) -> None:
            self.closed = True

    class WaitingTelegram:
        stopped = False
        cancelled = False

        async def run(self) -> None:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled = True
                raise

        def stop(self) -> None:
            self.stopped = True

    bridge = CrashingBridge()
    telegram = WaitingTelegram()

    with pytest.raises(RuntimeError, match="primary failure"):
        await _run_services(bridge, telegram)  # type: ignore[arg-type]

    assert bridge.closed
    assert telegram.stopped
    assert telegram.cancelled
