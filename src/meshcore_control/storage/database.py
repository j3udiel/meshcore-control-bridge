from __future__ import annotations

import logging
import sqlite3
import time
from collections.abc import Callable
from pathlib import Path

logger = logging.getLogger(__name__)

SQLITE_BUSY_TIMEOUT_MS = 5000
SQLITE_CONNECTION_TIMEOUT_SECONDS = 5.0
SQLITE_WRITE_RETRIES = 3
SQLITE_WRITE_RETRY_INITIAL_SECONDS = 0.05

SCHEMA = """
CREATE TABLE IF NOT EXISTS inbound_messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  transport TEXT NOT NULL,
  message_id TEXT,
  sender_id TEXT NOT NULL,
  channel_index INTEGER NOT NULL,
  text_hash TEXT NOT NULL,
  received_at TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS command_executions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  message_id TEXT,
  sender_id TEXT NOT NULL,
  command TEXT NOT NULL,
  args_json TEXT NOT NULL,
  result TEXT NOT NULL,
  duration_ms INTEGER NOT NULL,
  error TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS confirmations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  confirmation_id TEXT NOT NULL,
  sender_id TEXT NOT NULL,
  command TEXT NOT NULL,
  args_json TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS authorized_users (
  sender_id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  role TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS audit_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  event_type TEXT NOT NULL,
  data_json TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS audit_metadata (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS normalized_audit_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  event_id TEXT NOT NULL UNIQUE,
  schema_version INTEGER NOT NULL CHECK (schema_version >= 1),
  event_type TEXT NOT NULL,
  correlation_id TEXT NOT NULL,
  causation_event_id TEXT,
  transport TEXT NOT NULL,
  source_room_id TEXT NOT NULL,
  source_room_kind TEXT NOT NULL,
  reply_target_transport TEXT,
  reply_target_room_id TEXT,
  reply_target_room_kind TEXT,
  sender_ref_hash TEXT,
  sender_identity_kind TEXT,
  sender_stable INTEGER NOT NULL CHECK (sender_stable IN (0, 1)),
  message_ref_hash TEXT,
  command_name TEXT,
  command_result TEXT,
  duration_ms INTEGER CHECK (duration_ms IS NULL OR duration_ms >= 0),
  metadata_json TEXT NOT NULL CHECK (length(metadata_json) <= 4096),
  occurred_at TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_normalized_audit_correlation_time
  ON normalized_audit_events (correlation_id, occurred_at);

CREATE INDEX IF NOT EXISTS idx_normalized_audit_causation
  ON normalized_audit_events (causation_event_id);

CREATE INDEX IF NOT EXISTS idx_normalized_audit_type_time
  ON normalized_audit_events (event_type, occurred_at);

CREATE INDEX IF NOT EXISTS idx_normalized_audit_room_time
  ON normalized_audit_events (transport, source_room_id, occurred_at);

CREATE INDEX IF NOT EXISTS idx_normalized_audit_sender_time
  ON normalized_audit_events (sender_ref_hash, occurred_at);

CREATE TABLE IF NOT EXISTS deduplication_keys (
  dedup_key TEXT PRIMARY KEY,
  expires_at REAL NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS telegram_state (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS telegram_update_deduplication (
  update_ref_hash TEXT PRIMARY KEY,
  expires_at REAL NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS telegram_audit_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  event_type TEXT NOT NULL,
  update_ref_hash TEXT,
  chat_ref_hash TEXT,
  user_ref_hash TEXT,
  chat_type TEXT,
  message_type TEXT,
  reason TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS telegram_bridge_pending (
  bridge_message_id TEXT PRIMARY KEY,
  correlation_id TEXT NOT NULL,
  destination_transport TEXT NOT NULL,
  destination_room_id TEXT NOT NULL,
  content_ref_hash TEXT NOT NULL,
  size_bytes INTEGER NOT NULL,
  status TEXT NOT NULL,
  created_at REAL NOT NULL,
  expires_at REAL NOT NULL
);
"""


def connect_database(path: str) -> sqlite3.Connection:
    db_path = Path(path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path, timeout=SQLITE_CONNECTION_TIMEOUT_SECONDS)
    connection.row_factory = sqlite3.Row
    configure_connection(connection)
    connection.executescript(SCHEMA)
    connection.commit()
    return connection


def configure_connection(connection: sqlite3.Connection) -> None:
    """Apply settings required by every writer connected to the same SQLite file."""
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA synchronous=NORMAL")


def write_transaction[T](
    connection: sqlite3.Connection,
    operation: Callable[[], T],
    *,
    retries: int = SQLITE_WRITE_RETRIES,
    initial_backoff_seconds: float = SQLITE_WRITE_RETRY_INITIAL_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    """Run a short write transaction with bounded recovery for transient SQLite locks."""
    attempt = 0
    backoff = initial_backoff_seconds
    while True:
        try:
            connection.execute("BEGIN IMMEDIATE")
            result = operation()
            connection.commit()
            return result
        except Exception as exc:
            _rollback_quietly(connection)
            if not is_sqlite_locked(exc) or attempt >= retries:
                raise
            attempt += 1
            logger.warning(
                "SQLite write retry scheduled reason=database_locked attempt=%s",
                attempt,
            )
            sleep(backoff)
            backoff *= 2


def is_sqlite_locked(exc: BaseException) -> bool:
    if not isinstance(exc, sqlite3.OperationalError):
        return False
    message = str(exc).lower()
    return "database is locked" in message or "database table is locked" in message


def _rollback_quietly(connection: sqlite3.Connection) -> None:
    try:
        connection.rollback()
    except sqlite3.Error:
        logger.debug("SQLite rollback failed after write error", exc_info=True)
