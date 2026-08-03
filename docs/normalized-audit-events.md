# Normalized Audit Events Design

This document is a design proposal only. It does not enable a migration, change
runtime writes, add Telegram, add bridging, or enable write commands.

## Goals

- Move new audit writes to the normalized room and sender model.
- Preserve existing SQLite tables and existing queries.
- Avoid storing real sender IDs, platform message IDs, full message text,
  tokens, or transport secrets in new audit records.
- Correlate receive, filtering, command execution, deduplication decisions, and
  outbound responses without using private identifiers as audit keys.

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
```

`audit_metadata` stores non-secret audit state such as:

- `normalized_audit_schema_version`
- `audit_key_id`
- `audit_key_created_at`

It must not store `audit_key`.

`occurred_at`, `created_at`, and `audit_metadata.updated_at` must be UTC RFC3339
timestamps with a `Z` suffix and consistent precision across the application.
Use one format, for example millisecond precision:

```text
2026-08-03T13:49:15.526Z
```

## Event Identifiers

Every normalized audit event has its own stable event identifier:

```text
event_id = "evt:<uuid>"
```

Every audited command flow has a correlation identifier:

```text
correlation_id = "corr:<uuid>"
```

`causation_event_id` references another normalized audit `event_id`. It must
never reference the local SQLite `id` integer. The local integer is an internal
storage detail only.

Every event in `normalized_audit_events` must have a `correlation_id`. The
transport adapter or the first normalization boundary must create the
`correlation_id` before emitting the first normalized audit event. Failures that
occur before an inbound envelope can be normalized are outside this table or
must use a separate operational logging mechanism.

## Events To Record

Initial normalized event types:

- `message.received`: after minimal transport normalization, before channel and
  room filters. This makes `message.ignored` with `reason=wrong_channel`
  correlatable.
- `message.ignored`: wrong channel, disabled room, unsupported room, duplicate,
  rate-limited, unsupported message type, or missing required identity.
- `command.parsed`: command parsing result.
- `command.authorization`: allowed or denied, with a safe denial reason.
- `command.execution`: command handler completed or failed.
- `response.sent`: outbound response accepted by the transport or service call.
- `response.failed`: outbound response failed.

Critical write-operation, confirmation, Telegram, and bridging events are out of
scope for the first implementation.

## Command Parse Event

`command.parsed` metadata must include:

```text
parse_result = recognized | unknown | malformed | not_a_command
```

Rules:

- `command_name` may be stored only when it belongs to the registered command
  registry.
- Unknown command text must not be copied into `command_name`.
- Full arguments must never be stored.
- Arbitrary tokens, entity IDs from user input, or private message fragments
  must never be stored.
- If argument telemetry is needed later, store only bounded structural facts
  such as `argument_count` or allowlisted option names.

## Legacy Compatibility

The existing tables stay readable and writable during the transition. The first
implementation can dual-write:

- Keep current `record_inbound_message()` and `record_command()` behavior so
  current tests, operational queries, and existing SQLite files continue to
  work.
- Add normalized audit writes beside the legacy writes.
- Do not backfill or reinterpret legacy rows. In particular, do not invent
  `source_room`, `reply_target`, `sender_ref_hash`, `message_ref_hash`, or
  `correlation_id` for old records.

Dual-write must be transactional:

- Legacy and normalized rows for the same step are written in one SQLite
  transaction.
- If the normalized write fails, the legacy write for that step must roll back
  too.
- If the legacy write fails, the normalized write for that step must roll back
  too.
- Partial writes must not be silently accepted.
- The caller should receive or log a safe audit failure that does not include
  private message text, raw sender IDs, tokens, or `audit_key`.

A later migration can add read APIs that union legacy and normalized records,
but old rows should be clearly labeled as legacy and partial.

## Audit Key Management

New private references use an HMAC key named `audit_key`.

Requirements:

- Minimum length: 32 random bytes.
- Generate with a CSPRNG only.
- Never fall back to SHA-256 without a key.
- Never use an ephemeral runtime key.
- Never store `audit_key` in SQLite.
- Never write `audit_key` to logs.
- Never commit `audit_key` to the repository.

Standalone deployments:

- Load from an environment variable such as `AUDIT_KEY`, or from a secrets file
  outside the repository.
- Fail closed if the configured key is missing, too short, unreadable, or
  malformed.

Home Assistant App deployments:

- Use `/data/audit.key`.
- If the file does not exist, create it with mode `0600` from the beginning.
- Generate the key with a CSPRNG.
- Write to a temporary file in `/data`, fsync the file, fsync the directory, and
  rename atomically to `/data/audit.key`.
- Do not follow symlinks when opening or creating the key file.
- If an existing key file is invalid, do not regenerate over it. Fail closed
  with a safe error so the operator can inspect the local state.

Key rotation is out of scope for the first implementation. Until rotation is
implemented, changing the key intentionally breaks continuity of private
reference hashes across old and new audit rows.

## Activation Policy

Normalized audit requires a valid `audit_key`, but existing standalone
installations must not fail just because they have not opted into normalized
audit yet.

Home Assistant App:

- Normalized audit is activated by the Home Assistant App runtime.
- The App loads `/data/audit.key` if it already exists.
- If `/data/audit.key` does not exist, the App creates it using the safe key
  creation rules above.
- If `/data/audit.key` exists but is invalid, unreadable, too short, or a
  symlink, startup fails closed.
- The App must never regenerate over an invalid existing key file.
- The same key file must be reused across App restarts.

Standalone with an explicit key:

- If `AUDIT_KEY` or an explicit key-file configuration is present, normalized
  audit is activated.
- The key is validated before any normalized audit writes happen.
- Missing, invalid, too-short, unreadable, or malformed explicit keys are fatal.

Standalone legacy without a key and without explicit activation:

- The process continues to start.
- Existing legacy audit writes remain active.
- No normalized audit rows are written.
- The process emits one safe warning that normalized audit is disabled because
  no audit key is configured.
- The process must not generate an ephemeral key.
- The process must not fall back to plain SHA-256 references.

Explicit normalized-audit activation:

- A future flag or configuration value may explicitly require normalized audit
  for standalone deployments.
- When explicitly enabled, absence or invalidity of the audit key is fatal.
- This design PR does not add a new public configuration option; it only defines
  the future behavior.

Activation decision:

- Home Assistant App: enabled by runtime.
- Standalone: enabled by presence of a valid key or by a future explicit
  enablement flag/configuration.
- Standalone legacy: disabled when no key and no explicit enablement are
  present.

## Private Reference Formats

Each key has a non-secret `key_id`, stored in `audit_metadata`. `key_id` is not
the key and must not be enough to derive the key.

Sender reference:

```text
sender_ref_hash = "hmac-sha256:v1:<key_id>:<hex>"
```

HMAC input:

```text
sender-ref:v1\0<normalized_sender_id>
```

Message reference:

```text
message_ref_hash = "hmac-sha256:v1:<key_id>:<hex>"
```

HMAC input:

```text
message-ref:v1\0<transport>\0<room_id>\0<message_id>
```

The raw sender ID and raw platform message ID must only live in protected
configuration or runtime memory. Do not store `platform_message_id` with plain
SHA-256 because platform IDs can be low-entropy or linkable.

## Fields Never Stored

Normalized audit rows must never store:

- raw `sender_id`;
- raw platform `message_id`;
- full message text;
- raw command argument text when it can contain private user input;
- `SUPERVISOR_TOKEN`;
- Home Assistant Long-Lived Access Tokens;
- `Authorization` headers;
- MeshCore channel secrets;
- private keys;
- `audit_key`;
- full MeshCore public keys unless they are explicitly redacted or HMACed;
- raw packet captures or RX logs.

Message text can be represented only as:

- `text_ref_hash`, if useful, using HMAC with an explicit domain separation
  string;
- `text_length`;
- `command_name`, after parsing a known command.

Do not use plain SHA-256 for private identifiers or message text unless the
input is guaranteed public and high-entropy.

## Metadata Policy

`metadata_json` must be:

- JSON-serializable;
- canonicalized before writing, for example sorted keys and compact separators;
- `{}` when empty;
- bounded to no more than 4096 bytes per event;
- validated against an allowlist for the specific `event_type`;
- rejected when it contains unknown keys;
- bounded by maximum nesting depth;
- bounded by maximum string length;
- bounded by maximum number of object keys and array elements;
- free of secrets, full private message text, raw sender IDs, raw platform
  message IDs, and raw packet captures.

The serializer must not convert arbitrary objects with `str()`. Unsupported
types must fail validation.

Allowed example keys, subject to per-event allowlists:

- `channel_index`
- `message_id_present`
- `identity_kind`
- `identity_stable`
- `ignore_reason`
- `deduplication_result`
- `authorization_result`
- `authorization_reason`
- `parse_result`
- `argument_count`
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
- `message.received` has no `causation_event_id`.
- Later events set `causation_event_id` to a prior normalized audit `event_id`
  when a causal parent exists.
- `response.sent` uses the same `correlation_id` as the inbound command and
  records `reply_target_transport`, `reply_target_room_id`, and
  `reply_target_room_kind`, not linked rooms.
- Bridging correlation across multiple rooms is explicitly future work.

`correlation_id` is for traceability. It must not be used for deduplication or
authorization.

## Implementation Acceptance Criteria

The first implementation PR after this design should satisfy:

- Existing SQLite databases open without manual migration.
- Existing legacy audit tests still pass.
- New normalized audit tables are created additively.
- New command flow dual-writes normalized audit events transactionally.
- Partial legacy/normalized writes cannot succeed silently.
- New audit records include `event_id` and `schema_version`.
- New audit records include `correlation_id`, `source_room`, and
  `reply_target` where available.
- New audit records include `sender_ref_hash` instead of raw `sender_id`.
- New audit records include `message_ref_hash` instead of raw platform
  `message_id`.
- Tests prove raw sender IDs, raw message IDs, and full private message text are
  absent from new audit tables.
- Tests prove `audit_key` is absent from logs and SQLite.
- Tests prove legacy rows are not backfilled with invented normalized fields.
- Tests prove `message.received` can be correlated with
  `message.ignored reason=wrong_channel`.
- Tests prove metadata rejects unknown keys and unsupported object types.
- Tests prove a legacy standalone installation without `AUDIT_KEY` still starts.
- Tests prove no normalized rows are created when normalized audit is inactive.
- Tests prove explicit normalized-audit activation without a key fails closed.
- Tests prove the Home Assistant App reuses the same `/data/audit.key` across
  restarts.
- Home Assistant App startup remains unchanged.
- No public configuration change is required for existing users.
