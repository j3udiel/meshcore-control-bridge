# Changelog

All notable changes to this project will be documented in this file.

The format is based on Keep a Changelog, and this project adheres to semantic
versioning once releases begin.

## [Unreleased]

## [0.1.7] - 2026-08-03

### Added

- Normalized audit events with correlated event and command-flow identifiers.
- Private sender and platform-message references using HMAC-SHA256.
- Persistent `/data/audit.key` handling for the Home Assistant App.
- Audit coverage for message receipt, filters, parsing, authorization, command
  execution, and response outcomes.

### Changed

- Legacy audit tables and existing command queries are preserved.
- Command behavior, authorization, deduplication, and response text are
  unchanged.

### Not Included

- Telegram transport.
- Cross-platform bridging.
- Write commands.
- USB transport release.

## [0.1.6] - 2026-08-03

### Added

- Command registry.
- Sender authorization and roles.
- Private-channel filtering.
- Message deduplication.
- SQLite audit storage.
- Home Assistant availability client.
- Read-only Home Assistant config and state calls.
- `FakeTransport` for tests.
- `!ping`.
- `!help`.
- `!estado`.
- Experimental MeshCore USB frame codec and USB transport.
- Experimental Home Assistant MeshCore transport over WebSocket events and
  `meshcore.send_channel_message`.
- Home Assistant App layout using `SUPERVISOR_TOKEN`, `/data/audit.db`, and the
  internal Supervisor Home Assistant API proxy.
- Expanded MeshCore diagnostic utility.
- Per-sender rate limiting.
- Docker and systemd examples.
- Tests.

### Notes

- The USB MeshCore transport is experimental and not hardware-validated yet.
- Documented channel text frames do not expose a stable sender identity.
