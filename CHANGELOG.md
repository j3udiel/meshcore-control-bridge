# Changelog

All notable changes to this project will be documented in this file.

The format is based on Keep a Changelog, and this project adheres to semantic
versioning once releases begin.

## [Unreleased]

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
