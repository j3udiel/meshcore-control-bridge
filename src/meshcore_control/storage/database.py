from __future__ import annotations

import sqlite3
from pathlib import Path

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
"""


def connect_database(path: str) -> sqlite3.Connection:
    db_path = Path(path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    connection.executescript(SCHEMA)
    connection.commit()
    return connection
