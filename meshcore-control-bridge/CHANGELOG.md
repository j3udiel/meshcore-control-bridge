# Changelog

## Unreleased

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
