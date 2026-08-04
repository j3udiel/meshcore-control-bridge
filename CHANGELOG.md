# Changelog

All notable changes to this project will be documented in this file.

The format is based on Keep a Changelog, and this project adheres to semantic
versioning once releases begin.

## [Unreleased]

## [0.1.11] - 2026-08-05

### Added

- Forward normal text from the configured MeshCore channel to the authorized
  Telegram private chat.
- Bidirectional normal-text forwarding between Telegram and MeshCore.
- Configurable MeshCore-to-Telegram prefix and Telegram response size limit.
- Dedicated MeshCore-to-Telegram forwarding rate limit.
- Pending bridge record consumption for Telegram-originated MeshCore echoes.
- Observed-echo handling for pending bridge records.
- Normalized bridge audit events for MeshCore-to-Telegram forwarding decisions.

### Security / Behavior

- MeshCore and Telegram commands remain local to their originating transport.
- Telegram-originated text observed back from MeshCore is not reflected to
  Telegram.
- Raw MeshCore text, sender IDs, Telegram chat IDs, Telegram user IDs, and bot
  tokens are not stored in normalized bridge audit.
- `accepted_by_telegram` means Telegram Bot API acceptance, not user-read
  confirmation.
- `accepted_by_meshcore_transport` does not mean final LoRa delivery.

### Not Included

- Bridging commands between platforms.
- Telegram groups, multimedia, webhooks, replies, or multiple chats.
- Multiple MeshCore channels.
- Write commands.
- USB release work.

## [0.1.10] - 2026-08-04

### Added

- Forward normal text from the authorized Telegram private chat to MeshCore.
- Configurable Telegram message prefix.
- UTF-8 byte-aware MeshCore message limiting.
- Dedicated Telegram forwarding rate limit.
- Telegram confirmation for accepted, failed, and rate-limited forwards.
- Pending bridge records for future loop prevention.
- Normalized bridge audit events.

### Security / Behavior

- Telegram commands and command responses remain local to Telegram.
- Forwarding uses the existing Home Assistant MeshCore transport.
- Channel 0 remains prohibited.
- Raw Telegram text, token, chat ID and user ID are not stored in normalized
  audit.
- `accepted_by_meshcore_transport` does not mean final LoRa delivery.

### Not Included

- MeshCore to Telegram forwarding.
- Bidirectional bridging.
- Runtime loop prevention.
- Groups, multimedia or webhooks.
- Write commands.
- USB release work.

## [0.1.9] - 2026-08-04

### Added

- Disabled-by-default Telegram v1 foundation for one bot, one private chat, one
  authorized user, and long polling.
- Secure Telegram bot token import and persistence to a protected token file.
- Telegram update offset persistence, update deduplication, bounded backoff, and
  safe ignored-update auditing.
- Readonly Telegram command execution for `!ping`, `!help`, `!estado`,
  `!estado ha`, and `!exterior` through the existing command router.
- Plain-text Telegram `sendMessage` responses to the authorized private chat.

### Security / Behavior

- Telegram is disabled by default.
- Telegram v1 maps the authorized private chat and user to the readonly role.
- Raw Telegram bot tokens, message text, chat IDs, and user IDs are not stored
  in normalized audit rows.
- Telegram commands and responses are not sent to MeshCore.
- Normal Telegram text is not forwarded yet.

### Not Included

- Telegram to MeshCore forwarding.
- MeshCore to Telegram forwarding.
- Bidirectional bridging or loop-prevention runtime.
- Telegram groups, supergroups, channels, media, or webhooks.
- Write commands.
- USB transport release work.

## [0.1.8] - 2026-08-03

### Added

- Configurable readonly `!exterior` command for Home Assistant outdoor
  temperature and optional humidity entities.
- Operator-selected Home Assistant temperature entity for `!exterior`.
- Optional Home Assistant humidity entity for `!exterior`.
- Configurable `!exterior` response label.
- Safe `N/D` handling for unavailable, unknown, or missing Home Assistant
  entities.
- Telegram to MeshCore bridge design document.

### Security / Behavior

- `!exterior` uses only configured Home Assistant entity IDs; no entity IDs are
  hardcoded or accepted from messages.
- No write commands are added.
- No Telegram runtime implementation is added.
- Normalized audit does not store sensor values or configured entity IDs.

### Not Included

- Telegram transport.
- Telegram to MeshCore bridging.
- Write actions.
- USB transport release work.

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
