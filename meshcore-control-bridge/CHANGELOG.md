# Changelog

## Unreleased

### Added

- Add readonly `!alarma`, `!casa`, `!servers`, and `!red` commands backed by
  allowlisted Home Assistant status entities.
- Add optional `home_status` App configuration for alarm, home, server, and
  network summaries.

### Security / Behavior

- New status commands never accept arbitrary entity IDs, hosts, IPs, services, or
  actions from message text.
- No alarm, entity, server, shell, DNS, ping, or HTTP write/probe behavior is
  added.
- Responses and audit stay redacted and omit configured entity IDs and raw
  identifiers.
- Forwarding, echo prevention, bridge admin controls, and authorization remain
  unchanged.

## 0.1.21

- Prevent MeshCore response timeouts from stopping the App.
- Keep `BridgeService` alive after expected transport/service errors.
- Improve Home Assistant WebSocket command/result correlation.
- Use one reader task per WebSocket connection.
- Clean pending command waiters on timeout, cancellation, and disconnect.
- Prevent MeshCore event loss during WebSocket queue pressure.
- Close command WebSocket connections deterministically.
- Enforce MeshCore response size in UTF-8 bytes.
- Keep `!help` within the configured MeshCore transport budget.
- Keep Telegram and the bridge running after a failed MeshCore command
  response.
- Continue processing the next MeshCore message after a response failure.
- Report service timeouts through safe health reasons and best-effort audit.
- Do not automatically retry ambiguous send timeouts.
- Does not change authorization, forwarding, echo prevention, write commands,
  or USB release work.

## 0.1.20

- Add temporary admin-only `!bridge` runtime controls for Telegram to MeshCore
  forwarding, MeshCore to Telegram forwarding, and Telegram success
  confirmations.
- Add `!bridge reset` to clear runtime overrides and return to App
  configuration without restarting.
- Add explicit `telegram.authorized_user_role`, defaulting to `readonly`, so
  Telegram admin access must be configured intentionally.
- Keep runtime overrides in memory only; they reset on App restart.
- Keep `!bridge` readonly without arguments.
- Report effective forwarding state and redacted `runtime_overrides` in health
  snapshots and Home Assistant health events.
- Require the `admin` role for override subcommands.
- Store override audit events with allow-listed structured metadata only.
- Do not expose tokens, raw IDs, pubkeys, message text, command arguments,
  paths, or correlation IDs.
- Does not add persistent remote configuration, Home Assistant admin service
  calls, write commands, or USB release work.

## 0.1.19

- Add schema version 1 to Home Assistant bridge health events.
- Document the stable `meshcore_control_bridge_health` event contract.
- Add ready-to-use trigger-based template sensors and binary sensors.
- Add dashboard and automation examples for bridge health.
- Expose readonly bridge health through the Home Assistant event bus.
- Publish startup, state-change, and heartbeat health snapshots.
- Coalesce rapid changes and avoid duplicate functional snapshots.
- Keep `/data/health.json` as the canonical local health surface.
- Document bridge status, version, uptime, MeshCore and Telegram transport
  state, last activity, last safe error, success and failure counters, command
  counter, and audit and Telegram database health.
- Keep health data redacted without tokens, chat IDs, user IDs, sender IDs,
  pubkeys, message IDs, message text, entity IDs, paths, or correlation IDs.
- Do not use native Home Assistant internals or direct Home Assistant database
  writes.
- Does not change forwarding, authorization, command behavior, MQTT discovery,
  HACS packaging, administrative commands, or USB release work.

## 0.1.18

- Add readonly `!last` command for Telegram and MeshCore.
- Show recent forwarding activity, safe counters, uptime, and sanitized
  last-error state.
- Add deterministic UTC-based relative-time formatting.
- Add `!last` to the authorized readonly help output.
- Keep `!last` local to the originating transport.
- Send detailed activity summary to Telegram.
- Send compact LoRa-friendly summary to MeshCore.
- Read data from the in-memory `BridgeHealthState`.
- Avoid SQLite queries for the command.
- Reset activity counters and timestamps when the App restarts.
- Exclude tokens, raw IDs, pubkeys, message IDs, message text, command
  contents, entity IDs, paths, and correlation IDs.
- Keep last-error output restricted to sanitized safe reasons.
- Keep existing forwarding, echo prevention, authorization, `/data/health.json`,
  and Home Assistant health events unchanged.
- Does not add persistent counters, administrative bridge controls, MQTT, HACS
  integration, write commands, or USB release work.

## 0.1.17

- Publish redacted bridge health snapshots as Home Assistant events.
- Add `health.home_assistant_events_enabled`.
- Add configurable health heartbeat interval.
- Add a single async health publisher with coalescing and deduplication.
- Add Home Assistant template sensor, binary sensor, alert, and dashboard
  examples.
- Extend `!bridge` with Home Assistant event and heartbeat status.
- Publish `meshcore_control_bridge_health` events after startup, state changes,
  and heartbeat intervals.
- Coalesce rapid changes.
- Skip functional duplicates except for heartbeat publication.
- Publish the latest snapshot after reconnection.
- Keep publication failures from blocking forwarding, commands, or Docker
  healthcheck behavior.
- Exclude tokens, raw IDs, pubkeys, message IDs, message text, command
  contents, entity IDs, paths, and correlation IDs from event payloads.
- Keep last-error reasons sanitized.
- Does not create native entities automatically or modify `configuration.yaml`.
- Documents trigger-based template sensors.
- Does not add MQTT, HTTP endpoints, HACS integration, administrative bridge
  controls, persistent runtime configuration changes, write commands, command
  bridging, native HA integration, or USB release work.

## 0.1.16

- Add readonly `!bridge` status command for Telegram and MeshCore.
- Add concurrency-safe in-memory bridge health state.
- Add redacted `/data/health.json` for local diagnostics.
- Add safe forwarding counters, transport state, and database health reporting.
- Add Docker healthcheck support for healthy and degraded operational states.
- Keep `!bridge` local to the originating transport.
- Send detailed `!bridge` status to Telegram.
- Send compact LoRa-friendly `!bridge` status to MeshCore.
- Keep degraded bridge state visible without forcing a container restart.
- Keep forwarding, commands, and authorization behavior unchanged.
- Exclude tokens, raw chat IDs, user IDs, sender IDs, pubkeys, message IDs,
  message text, entity IDs, and sensitive paths from health output.
- Restrict last-error reasons to a safe allowlist.
- Does not add command bridging, write commands, native Home Assistant entities,
  an HTTP endpoint, MQTT discovery, administrative bridge controls, persistent
  runtime configuration changes, or USB release work.

## 0.1.15

- Add `telegram.send_forward_confirmation`.
- Allow successful Telegram to MeshCore confirmations to be disabled.
- Default successful forwarding confirmations to disabled.
- When `false`, normal Telegram messages are forwarded to MeshCore without
  replying `Enviado a MeshCore.`
- When `true`, the existing success confirmation is preserved.
- Keep errors, authorization failures, rate limits, and oversized-message
  responses visible.
- Keep Telegram commands responding normally.
- Keep MeshCore to Telegram forwarding unchanged.
- Preserve existing Telegram credentials, offset, and authorized senders.
- Accept only real boolean values.
- Does not add commands, transports, forwarding or echo-prevention changes, or
  USB release work.

## 0.1.14

- Separate Telegram operational state from the audit database.
- Prevent Telegram bridge writes from contending with audit and command writes.
- Fix transaction boundaries that previously protected the wrong SQLite
  connection.
- Reduce SQLite busy waits that blocked the asyncio event loop.
- Preserve Telegram offset, deduplication, and pending bridge records during
  migration.
- Improve bridge responsiveness under concurrent Telegram and MeshCore traffic.
- Copy existing Telegram operational rows idempotently from `audit.db` to
  `telegram.db`.
- Preserve `audit.db` unchanged.
- Retain existing Telegram activation state, offset, and pending records.
- Use `INSERT OR IGNORE` migration that is safe to run repeatedly.
- Keep echo prevention fail-closed.
- Keep commands and authorization unchanged.
- Do not add raw message text, IDs, or tokens to logs.
- Keep `allow_unidentified_readonly_testing` diagnostic-only.
- Does not add commands, transports, groups, media, webhooks, write commands, or
  USB release work.

## 0.1.13

- Fix nested SQLite transaction failures during audit and command processing.
- Use explicit SQLite autocommit mode and SAVEPOINTs for nested writes.
- Prevent audit database failures from stopping MeshCore or Telegram forwarding.
- Improve bounded retry and rollback behavior for concurrent SQLite writers.
- Fix service shutdown ordering and asynchronous generator cleanup.
- Distinguish missing, null, and empty `authorized_senders` configuration.
- Keep authorization fail-closed and `allow_unidentified_readonly_testing`
  diagnostic-only.
- Keep Telegram authorization separate from MeshCore `authorized_senders`.
- Do not add raw message text, IDs, or tokens to logs.
- Keep commands and forwarding behavior unchanged.
- Does not add commands, transports, groups, media, webhooks, write commands, or
  USB release work.

## 0.1.12

- Fix concurrent SQLite writer failures during Telegram bridge forwarding.
- Prevent `database is locked` errors from crashing the Home Assistant App.
- Make pending bridge record creation and echo consumption resilient.
- Apply consistent WAL, busy timeout, and short transaction handling.
- Improve service shutdown after task failures.
- Keep forwarding failures fail-closed without logging raw message text,
  identifiers, or audit secrets.
- Keep Telegram and MeshCore commands unchanged.
- Does not add commands, transports, groups, multimedia, webhooks, write
  commands, or USB release work.

## 0.1.11

- Forward normal text from the configured MeshCore channel to the authorized
  Telegram private chat.
- Add bidirectional normal-text forwarding between Telegram and MeshCore.
- Add configurable MeshCore-to-Telegram prefix and Telegram response size limit.
- Add a dedicated MeshCore-to-Telegram forwarding rate limit.
- Consume pending bridge records so Telegram-originated MeshCore echoes are not
  reflected back to Telegram.
- Mark consumed pending echo records as `observed_echo`.
- Add normalized bridge audit events for MeshCore-to-Telegram forwarding
  decisions.
- Keep MeshCore and Telegram commands local to their originating transport.
- Keep raw MeshCore text, sender IDs, Telegram chat IDs, Telegram user IDs, and
  bot tokens out of normalized bridge audit.
- Does not bridge commands, add groups, multimedia, webhooks, replies, multiple
  chats, multiple MeshCore channels, write commands, or USB release work.

## 0.1.10

- Forward normal text from the authorized Telegram private chat to MeshCore.
- Add configurable Telegram message prefix.
- Add UTF-8 byte-aware MeshCore message limiting.
- Add a dedicated Telegram forwarding rate limit.
- Confirm accepted, failed, and rate-limited forwards in Telegram.
- Store pending bridge records for future loop prevention.
- Add normalized bridge audit events.
- Keep Telegram commands and command responses local to Telegram.
- Use the existing Home Assistant MeshCore transport for forwarding.
- Keep channel 0 prohibited.
- Do not store raw Telegram text, token, chat ID, or user ID in normalized
  audit.
- Treat `accepted_by_meshcore_transport` as transport acceptance, not final LoRa
  delivery.
- Does not add MeshCore to Telegram forwarding, bidirectional bridging, runtime
  loop prevention, groups, media, webhooks, write commands, or USB release
  work.

## 0.1.9

- Add disabled-by-default Telegram v1 foundation for one bot, one private chat,
  one authorized user, and long polling.
- Add secure Telegram bot token import and persistence to a protected token
  file.
- Add Telegram update offset persistence, update deduplication, bounded backoff,
  and safe ignored-update auditing.
- Add readonly Telegram command execution for `!ping`, `!help`, `!estado`,
  `!estado ha`, and `!exterior` through the existing command router.
- Add plain-text Telegram `sendMessage` responses to the authorized private
  chat.
- Keep Telegram disabled by default.
- Map Telegram v1 to the readonly role only.
- Keep raw Telegram bot tokens, message text, chat IDs, and user IDs out of
  normalized audit rows.
- Do not send Telegram commands or responses to MeshCore.
- Do not forward normal Telegram text yet.
- Does not add Telegram to MeshCore forwarding, MeshCore to Telegram forwarding,
  bidirectional bridging, groups, media, webhooks, write commands, or USB
  release work.

## 0.1.8

- Add configurable readonly `!exterior` command for Home Assistant outdoor
  temperature and optional humidity entities.
- Use operator-selected Home Assistant temperature and optional humidity
  entities for `!exterior`.
- Add a configurable label for the `!exterior` response.
- Return safe `N/D` values for unavailable, unknown, or missing entities.
- Document the future Telegram to MeshCore bridge design without adding a
  Telegram runtime implementation.
- Do not add write commands, Telegram bridging, USB release work, or hardcoded
  entity IDs.
- Keep normalized audit from storing sensor values or configured entity IDs.

## 0.1.7

- Add normalized audit events with correlated IDs for the current message and
  command flow.
- Add private sender and platform-message references using HMAC-SHA256.
- Create and reuse persistent `/data/audit.key` in the Home Assistant App.
- Record message receipt, filters, parsing, authorization, command execution,
  and response outcomes in normalized audit events.
- Preserve legacy audit tables and existing queries.
- Keep command behavior, authorization, deduplication, and response text
  unchanged.
- Does not include Telegram, bridging, write commands, or USB transport release.

## 0.1.6

- Hotfix the GitHub Actions App publishing workflow so it builds
  `meshcore-control-bridge/Dockerfile` instead of the standalone root
  `Dockerfile`.
- Strengthen the built-image check so published App artifacts must keep the
  Home Assistant base `/init` entrypoint, run `/run.sh`, load `/data/options.json`,
  and avoid the standalone `/config/config.yaml` startup path.

## 0.1.5

- Remove the duplicated vendored Python package from the App directory.
- Build the App image from the repository root `pyproject.toml` and canonical
  `src/` tree.
- Add artifact checks so the built image must contain the current package
  version, unidentified readonly sender, authorization logs, WebSocket idle
  heartbeat hook, and healthcheck code.
- Add an image-level unidentified readonly authorization smoke test.

## 0.1.4

- Make `allow_unidentified_readonly_testing` grant a temporary readonly sender
  with the reserved ID `test:unidentified:channel:<channel>`.
- Stop deriving unidentified authorization from visible MeshCore sender names.
- Allow `!ping`, `!help`, `!estado`, and `!estado ha` in unidentified readonly
  testing mode.
- Keep unidentified testing scoped to the configured channel and readonly
  commands only.
- Refresh the App healthcheck during idle WebSocket periods so it does not
  expire only because no MeshCore messages arrive.
- Add safe command-flow logs for receive, authorization, duplicate, rate limit,
  and response service-call outcomes.

## 0.1.3

- Keep Home Assistant WebSocket event subscriptions open during idle periods.
- Reconnect only after a real WebSocket close or transport error.
- Prevent protocol libraries from logging authentication frames at debug level.
- Add defensive log redaction for access tokens, bearer headers, and
  `SUPERVISOR_TOKEN` values.
- Add safe bridge lifecycle logs for App runtime, authentication, subscription,
  channel listening, entry selection, and readiness.

## 0.1.2

- Remove the custom `apparmor.txt` profile for the experimental App.
- Let Home Assistant Supervisor apply its default AppArmor profile so the
  `ghcr.io/home-assistant/base` `/init` and S6 startup path can run.
- Keep `init: false` because the base image provides its own init system.

## 0.1.1

- Remove Supervisor `apparmor` configuration from `config.yaml`; the custom
  profile is supplied by `apparmor.txt`.
- Keep Supervisor `watchdog` undeclared until the App exposes a compatible HTTP
  or TCP endpoint.
- Tighten App repository validation for discovery-sensitive metadata.

## 0.1.0

- Initial experimental Home Assistant App.
- Uses `SUPERVISOR_TOKEN` and the internal Home Assistant API proxy.
- Listens to `meshcore_message` events.
- Replies with `meshcore.send_channel_message`.
- Persists audit data in `/data/audit.db`.
- Declares the public GHCR image reference for repository-based installation.
