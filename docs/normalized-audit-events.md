# Normalized Audit Events Design

This document is a design proposal only. It does not enable a migration, change
runtime writes, add Telegram, add bridging, or enable write commands.

## Goals

- Move new audit writes to the normalized room and sender model.
- Preserve existing SQLite tables and existing queries.
- Avoid storing real sender IDs, full message text, tokens, or transport
  secrets in new audit records.
- Correlate receive, command execution, deduplication decisions, and outbound
  responses without using private identifiers as audit keys.

## Additive SQLite Schema

The migration should be additive. Existing tables remain unchanged:

- `inbound_messages`
- `command_executions`
- `confirmations`
- `authorized_users`
- `audit_events`
- `deduplication_keys`

New tables:

```sql
CREATE TABLE IF NOT EXISTS audit_metadata (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS normalized_audit_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  schema_version INTEGER NOT NULL,
  event_type TEXT NOT NULL,
  correlation_id TEXT NOT NULL,
  causation_id TEXT,
  transport TEXT NOT NULL,
  source_room_id TEXT NOT NULL,
  source_room_kind TEXT NOT NULL,
  reply_target_room_id TEXT,
  reply_target_room_kind TEXT,
  sender_ref_hash TEXT,
  sender_identity_kind TEXT,
  sender_stable INTEGER NOT NULL,
  platform_message_id_hash TEXT,
  command_name TEXT,
  command_result TEXT,
  duration_ms INTEGER,
  metadata_json TEXT NOT NULL,
  occurred_at TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_normalized_audit_correlation
  ON normalized_audit_events (correlation_id);

CREATE INDEX IF NOT EXISTS idx_normalized_audit_room_time
  ON normalized_audit_events (transport, source_room_id, occurred_at);

CREATE INDEX IF NOT EXISTS idx_normalized_audit_sender_time
  ON normalized_audit_events (sender_ref_hash, occurred_at);
```

`audit_metadata` stores non-secret audit state such as:

- `normalized_audit_schema_version`
- `audit_key_id`
- `audit_key_created_at`

It must not store `audit_key`.

## Events To Record

Initial normalized event types:

- `message.received`: after transport normalization and channel/room filtering.
- `message.ignored`: wrong channel, disabled room, duplicate, rate-limited, or
  unsupported message type.
- `command.parsed`: command name was recognized or rejected as unknown.
- `command.authorization`: allowed or denied, with a safe denial reason.
- `command.execution`: command handler completed or failed.
- `response.sent`: outbound response accepted by the transport or service call.
- `response.failed`: outbound response failed.

Critical write-operation, confirmation, Telegram, and bridging events are out of
scope for the first implementation.

## Legacy Compatibility

The existing tables stay readable and writable during the transition. The first
implementation can dual-write:

- Keep current `record_inbound_message()` and `record_command()` behavior so
  current tests, operational queries, and existing SQLite files continue to
  work.
- Add normalized audit writes beside the legacy writes.
- Do not backfill or reinterpret legacy rows. In particular, do not invent
  `source_room`, `reply_target`, or `sender_ref_hash` for old records.

A later migration can add read APIs that union legacy and normalized records,
but old rows should be clearly labeled as legacy and partial.

## Audit Key Management

New sender references use an HMAC key named `audit_key`.

Storage rules:

- `audit_key` must never be stored in the repository.
- `audit_key` must never be written to SQLite.
- For standalone deployments, load from an environment variable such as
  `AUDIT_KEY`, or from a secrets file outside the repository.
- For Home Assistant App deployments, generate `/data/audit.key` on first boot
  with mode `0600` if no key exists.
- `/data/audit.key` remains local App state and is not copied to logs or audit
  tables.

Key rotation is out of scope for the first implementation. Until rotation is
implemented, changing the key intentionally breaks continuity of
`sender_ref_hash` across old and new audit rows.

## Sender Reference Format

Use HMAC-SHA256 over the normalized sender ID:

```text
sender_ref_hash = "hmac-sha256:v1:" + hex(HMAC-SHA256(audit_key, sender_id))
```

The HMAC input is the exact normalized `message.sender.sender_id`, for example:

- `meshcore-pubkey-prefix:<instance>:<prefix>`
- `test:unidentified:channel:<channel>`

The raw sender ID must only live in protected authorization configuration or in
runtime memory.

## Fields Never Stored

Normalized audit rows must never store:

- raw `sender_id`;
- full message text;
- raw command argument text when it can contain private user input;
- `SUPERVISOR_TOKEN`;
- Home Assistant Long-Lived Access Tokens;
- `Authorization` headers;
- MeshCore channel secrets;
- private keys;
- `audit_key`;
- full MeshCore public keys unless they are explicitly redacted or hashed;
- raw packet captures or RX logs.

Message text can be represented only as:

- `text_hash`, if useful, using SHA-256 over normalized text;
- `text_length`;
- `command_name`, after parsing known commands.

## Metadata Policy

`metadata_json` must be:

- JSON-serializable;
- bounded in size, initially no more than 4096 bytes per event;
- composed only of documented keys for each event type;
- free of secrets, full private message text, raw sender IDs, and raw packet
  captures.

Allowed examples:

- `channel_index`
- `message_id_present`
- `identity_kind`
- `identity_stable`
- `deduplication_result`
- `authorization_result`
- `authorization_reason`
- `rate_limit_result`
- `transport_service`
- `response_length`
- redacted transport identifiers, if needed for debugging

Transport-specific metadata must be documented before it is emitted.

## Correlation Model

The normalized message model already provides:

- `message.message.correlation_id`
- `message.message.message_id`
- `message.source_room`
- `message.reply_target`

Correlation rules:

- All events caused by one inbound message use the same `correlation_id`.
- `message.received` has no `causation_id`.
- Later events can set `causation_id` to the prior normalized audit event ID or
  to a generated event-local ID.
- `response.sent` uses the same `correlation_id` as the inbound command and
  records `reply_target_room_id`, not linked rooms.
- Bridging correlation across multiple rooms is explicitly future work.

`correlation_id` is for traceability. It must not be used for deduplication or
authorization.

## Implementation Acceptance Criteria

The first implementation PR after this design should satisfy:

- Existing SQLite databases open without manual migration.
- Existing legacy audit tests still pass.
- New normalized audit table is created additively.
- New command flow dual-writes normalized audit events.
- New audit records include `schema_version`.
- New audit records include `correlation_id`, `source_room`, and
  `reply_target` where available.
- New audit records include `sender_ref_hash` instead of raw `sender_id`.
- Tests prove raw sender IDs and full private message text are absent from new
  audit tables.
- Tests prove `audit_key` is absent from logs and SQLite.
- Tests prove legacy rows are not backfilled with invented normalized fields.
- Home Assistant App startup remains unchanged.
- No public configuration change is required for existing users.
