from __future__ import annotations

import logging
import sqlite3

from meshcore_control.storage.database import write_transaction

logger = logging.getLogger(__name__)

TELEGRAM_TABLE_COLUMNS: dict[str, tuple[str, ...]] = {
    "telegram_state": ("key", "value", "updated_at"),
    "telegram_update_deduplication": ("update_ref_hash", "expires_at", "created_at"),
    "telegram_audit_events": (
        "id",
        "event_type",
        "update_ref_hash",
        "chat_ref_hash",
        "user_ref_hash",
        "chat_type",
        "message_type",
        "reason",
        "created_at",
    ),
    "telegram_bridge_pending": (
        "bridge_message_id",
        "correlation_id",
        "destination_transport",
        "destination_room_id",
        "content_ref_hash",
        "size_bytes",
        "status",
        "created_at",
        "expires_at",
    ),
}


def migrate_telegram_tables(
    *,
    source_connection: sqlite3.Connection,
    target_connection: sqlite3.Connection,
) -> None:
    """Copy Telegram operational state out of legacy audit.db storage."""

    def copy_tables() -> None:
        for table, columns in TELEGRAM_TABLE_COLUMNS.items():
            quoted_columns = ", ".join(columns)
            placeholders = ", ".join("?" for _ in columns)
            rows = source_connection.execute(
                f"SELECT {quoted_columns} FROM {table}",  # noqa: S608 - table names are fixed.
            ).fetchall()
            if not rows:
                continue
            target_connection.executemany(
                f"INSERT OR IGNORE INTO {table} ({quoted_columns}) VALUES ({placeholders})",
                [tuple(row[column] for column in columns) for row in rows],
            )

    write_transaction(
        target_connection,
        copy_tables,
        operation_name="telegram.migrate_tables",
    )
    logger.info("Telegram SQLite state migration checked")
