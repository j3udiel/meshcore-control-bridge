# MeshCore Control Bridge App

## Installation From The App Store Repository

Home Assistant discovers Apps from the default branch of a GitHub repository.
The repository must contain `repository.yaml` at its root and this App must be
directly under `meshcore-control-bridge/config.yaml`.

Once the Home Assistant App pull request has been merged into the default branch
and an image has been published, install it like any other Home Assistant App:

1. Open Home Assistant.
2. Go to Settings -> Apps -> Repositories.
3. Select Add.
4. Enter:

   ```text
   https://github.com/j3udiel/meshcore-control-bridge
   ```

5. Save the repository.
6. Open the App Store.
7. Find `MeshCore Control Bridge`.
8. Install the App.
9. Configure the options.
10. Start the App.
11. Review the App logs.
12. Send `!ping` on the configured private MeshCore channel.
13. Send `!estado ha`.
14. Send `!estado`.

During draft testing, this URL only works after the App files are present on the
repository default branch. Home Assistant does not install Apps from an arbitrary
pull request branch through the normal repository UI.

## Initial Test Configuration

Use this starting point for the first readonly test:

```yaml
channel_index: 1
meshcore_entry_id: ""
command_prefix: "!"
authorized_senders: []
status_entities: []
weather_status:
  temperature_entity: ""
  humidity_entity: ""
  label: "Exterior"
telegram:
  enabled: false
  bot_token_import: ""
  bot_token_file: /data/telegram.bot_token
  allowed_private_chat_id: ""
  allowed_user_id: ""
  meshcore_channel_index: 1
  forward_meshcore_to_telegram: true
  forward_telegram_to_meshcore: true
  command_prefix: "!"
  max_meshcore_message_length: 180
  max_telegram_message_length: 3900
  message_prefix: "TG: "
  meshcore_to_telegram_prefix: "MC: "
  send_forward_confirmation: false
  forwarding_rate_limit:
    messages: 5
    window_seconds: 60
  inbound_forwarding_rate_limit:
    messages: 20
    window_seconds: 60
rate_limit:
  commands: 5
  window_seconds: 60
allow_unidentified_readonly_testing: true
log_level: debug
```

`channel_index` must not be `0`; the public channel is rejected.

`allow_unidentified_readonly_testing` is only for the first diagnostic pass. It
allows readonly commands from channel messages that do not expose a stable
`pubkey_prefix`. Disable it after you obtain and configure the sender's
`pubkey_prefix`.

`weather_status.temperature_entity` enables `!exterior`. Leave it empty until
you choose the read-only Home Assistant temperature entity to expose. The
humidity entity is optional. The command preserves the units reported by Home
Assistant and returns `N/D` when an entity is unavailable or cannot be read.

You do not configure `HA_TOKEN` for this App. Home Assistant provides
`SUPERVISOR_TOKEN` automatically, and the App uses that token only for the
internal Home Assistant API proxy.

## Image Distribution

The App configuration references:

```text
ghcr.io/j3udiel/meshcore-control-bridge
```

The App version in `config.yaml` selects the image tag. For version `0.1.18`,
Supervisor pulls:

```text
ghcr.io/j3udiel/meshcore-control-bridge:0.1.18
```

If the image has not been published yet, installation from the public repository
will not complete. The repository keeps a Dockerfile for local development and a
manual/tag-driven GitHub Actions workflow to publish the multi-architecture GHCR
image when ready.

## Development Alternative

For App development only, you can still use Home Assistant local App testing
methods. Do not use that as the normal installation path for users.

To test a pull request branch before publishing a new GHCR image, clone the
branch into a temporary local App directory on the Home Assistant host:

```sh
cd /addons
git clone --branch feat/configurable-outdoor-status \
  https://github.com/j3udiel/meshcore-control-bridge.git \
  meshcore-control-bridge-pr20
```

Then edit the temporary copy at:

```text
/addons/meshcore-control-bridge-pr20/meshcore-control-bridge/config.yaml
```

Comment the `image:` line before installing from Local Apps:

```yaml
# image: "ghcr.io/j3udiel/meshcore-control-bridge"
```

This forces Supervisor to build the local Dockerfile from the checked-out pull
request branch instead of downloading the published stable GHCR image for the
same App version. Do not commit this local edit, and do not use this path for
normal user installation.

## Required Values

Use generic, redacted values in issues and logs.

- `pubkey_prefix`: use the `pubkey_prefix` shown by `meshcore-ha` events or
  contact data. It must identify the sender stably enough for your mesh.
- `meshcore_entry_id`: only required when more than one MeshCore config entry
  exists. Find it with `meshcore-diagnose ha-inspect` or in Developer Tools.
- `entity_id`: choose read-only Home Assistant entities to include in `!estado`.

## Supervisor Authentication

The App uses `SUPERVISOR_TOKEN` provided by Home Assistant. Do not configure a
Long-Lived Access Token for the App. The token is not printed, stored, or copied
to `/data`.

REST calls use `http://supervisor/core/api/`.
WebSocket calls use `ws://supervisor/core/websocket`.

## Security Notes

Channel `0` is rejected. `authorized_senders` is empty by default, so the App
will not execute commands until you configure at least one sender.

`allow_unidentified_readonly_testing` is for diagnostics only. When enabled,
messages without `pubkey_prefix` can be processed as readonly from the configured
channel. Leave it disabled for normal use.

This experimental App intentionally does not ship a custom `apparmor.txt`.
Home Assistant Supervisor applies its default AppArmor profile. A custom profile
can be added later after validating the full S6 startup path used by
`ghcr.io/home-assistant/base`.

Supervisor `watchdog` is intentionally not declared until the App exposes an
HTTP or TCP endpoint compatible with Supervisor watchdog checks. The Docker
`HEALTHCHECK` remains internal container health metadata.

## Audit Data

Version `0.1.9` includes the normalized audit events introduced in `0.1.7` for
the existing readonly command flow. The App creates `/data/audit.key` on first
start, stores it with restricted file permissions, and reuses the same key after
restarts. Sender and platform message references are stored as HMAC-SHA256
values; raw sender IDs, raw message IDs, command arguments, message text,
tokens, and the audit key are not stored in normalized audit rows.

The existing legacy SQLite tables remain in place for compatibility. This
release does not change commands, authorization, deduplication, or response
texts.

## Outdoor Status

Version `0.1.8` added the readonly `!exterior` command. Configure
`weather_status.temperature_entity` with a Home Assistant temperature entity to
enable useful output. `weather_status.humidity_entity` is optional, and
`weather_status.label` controls the short response label. The command does not
hardcode entity IDs, does not accept entity IDs from messages, and does not
store sensor values or configured entity IDs in normalized audit metadata.

## Telegram Foundation

The Telegram foundation is disabled by default. When enabled, it supports only
one bot, one authorized private chat, one authorized Telegram user, plain text,
and long polling. It imports the bot token once from `telegram.bot_token_import`
and stores it in `telegram.bot_token_file`, normally
`/data/telegram.bot_token`, with restricted file permissions.

Version `0.1.9` allows the authorized private Telegram chat to execute the
existing readonly commands `!ping`, `!help`, `!estado`, `!estado ha`, and
`!exterior` through the same command router used by MeshCore. Replies are sent
back to Telegram with plain-text `sendMessage`.

Version `0.1.10` can forward authorized normal Telegram text to the configured
MeshCore channel through the existing Home Assistant MeshCore transport.
Telegram commands and command responses are not forwarded to MeshCore. This
release does not include MeshCore to Telegram forwarding.

Version `0.1.11` can also forward normal text from the configured MeshCore
channel to the authorized Telegram private chat. Commands remain local to their
originating transport. The bridge still does not support groups, media,
webhooks, write commands, or USB release work.

Version `0.1.13` is a hotfix for nested SQLite transaction failures, persistent
writer contention, and shutdown ordering. It uses explicit SQLite autocommit
mode, SAVEPOINTs for nested writes, bounded rollback-before-retry behavior, and
degraded audit handling so audit database failures do not stop MeshCore or
Telegram forwarding. It also distinguishes missing, null, and empty
`authorized_senders` configuration while keeping authorization fail-closed.

Version `0.1.14` separates Telegram operational state into `telegram.db` so
Telegram offset, update deduplication, local Telegram audit rows, and pending
bridge records no longer contend with `audit.db` audit and command writes.
Existing Telegram rows are copied idempotently from `audit.db` with
`INSERT OR IGNORE`; `audit.db` is preserved unchanged.

Version `0.1.15` adds `telegram.send_forward_confirmation`. It defaults to
`false`, so successful Telegram to MeshCore forwards are no longer confirmed in
Telegram unless the operator enables the option. Errors, rate limits,
authorization failures, oversized-message responses, commands, and MeshCore to
Telegram forwarding are unchanged.

The `!bridge` command reports readonly operational state on the originating
transport only. It summarizes the App version, MeshCore and Telegram state,
forwarding flags, confirmation setting, audit database health, Telegram database
health, and the last safe failure reason. It never includes tokens, raw Telegram
chat IDs or user IDs, MeshCore sender IDs, pubkeys, message IDs, message text,
entity IDs, or filesystem paths.

The `!last` command reports readonly last-activity counters from the same
in-memory `BridgeHealthState`. Telegram receives a detailed response; MeshCore
receives a compact LoRa-friendly response. It reports relative last Telegram to
MeshCore activity, relative last MeshCore to Telegram activity, success and
failure counters, processed command count, uptime, and the last sanitized failure
reason. These counters and timestamps reset when the App restarts. This release
does not add SQLite persistence for `!last`; `/data/health.json` and
`meshcore_control_bridge_health` events remain the external observable state.

The App writes `/data/health.json` atomically for the Docker healthcheck and
local diagnostics. The file may report `status: degraded` while the process
stays healthy; degraded bridge state is visible without forcing a Supervisor
restart.

### Home Assistant Health Integration

The App can also publish the redacted health snapshot as a Home Assistant event:

```yaml
health:
  home_assistant_events_enabled: true
  heartbeat_seconds: 60
```

Native Home Assistant entities are not created directly in this phase. A Home
Assistant App does not have a supported public API for registering native
entities, and the bridge must not use Home Assistant internals, write directly
to the Home Assistant database, create stale entities without cleanup, require
MQTT infrastructure, or add a separate HACS integration here. The supported
surface for this release is the Home Assistant WebSocket event bus plus
trigger-based template sensors that you control in Home Assistant.

The event type is `meshcore_control_bridge_health`. It is emitted after startup,
after relevant state changes, and on the configured heartbeat. Rapid changes are
coalesced, identical functional snapshots are skipped, and heartbeat publication
refreshes state at the configured interval. The event payload contains only safe
status fields:

```json
{
  "schema_version": 1,
  "status": "ok",
  "version": "0.1.18",
  "uptime_seconds": 123,
  "meshcore": "connected",
  "telegram": "connected",
  "channel": 1,
  "forwarding": {
    "telegram_to_meshcore": true,
    "meshcore_to_telegram": true,
    "confirmation": false
  },
  "database": {
    "audit": "ok",
    "telegram": "ok"
  },
  "counters": {
    "tg_to_mc_success": 0,
    "tg_to_mc_failed": 0,
    "mc_to_tg_success": 0,
    "mc_to_tg_failed": 0,
    "commands_processed": 0
  },
  "last_activity": {
    "telegram_to_meshcore": null,
    "meshcore_to_telegram": null
  },
  "last_error": {
    "timestamp": null,
    "reason": "none"
  }
}
```

It never includes tokens, chat IDs, user IDs, sender IDs, pubkeys, message IDs,
message text, correlation IDs, paths, configured entity IDs, command contents,
or database contents.

To expose entities, add trigger-based template sensors to your own Home
Assistant configuration:

Trigger-based template sensors do not have state until Home Assistant receives
the first `meshcore_control_bridge_health` event after they are loaded. After
adding this YAML, restart Home Assistant or reload Template entities from
Developer Tools, then wait for the next bridge heartbeat or restart the App to
publish an initial event.

```yaml
template:
  - trigger:
      - platform: event
        event_type: meshcore_control_bridge_health
    sensor:
      - name: MeshCore Control Bridge Status
        unique_id: meshcore_control_bridge_status
        state: "{{ trigger.event.data.status }}"
        attributes:
          schema_version: "{{ trigger.event.data.schema_version }}"
          version: "{{ trigger.event.data.version }}"
          meshcore: "{{ trigger.event.data.meshcore }}"
          telegram: "{{ trigger.event.data.telegram }}"
          channel: "{{ trigger.event.data.channel }}"
          tg_to_mc: "{{ trigger.event.data.forwarding.telegram_to_meshcore }}"
          mc_to_tg: "{{ trigger.event.data.forwarding.meshcore_to_telegram }}"
          confirmation: "{{ trigger.event.data.forwarding.confirmation }}"
          audit_db: "{{ trigger.event.data.database.audit }}"
          telegram_db: "{{ trigger.event.data.database.telegram }}"
          uptime_seconds: "{{ trigger.event.data.uptime_seconds }}"
          last_tg_to_mc: "{{ trigger.event.data.last_activity.telegram_to_meshcore }}"
          last_mc_to_tg: "{{ trigger.event.data.last_activity.meshcore_to_telegram }}"
          last_error_at: "{{ trigger.event.data.last_error.timestamp }}"
          last_error_reason: "{{ trigger.event.data.last_error.reason }}"
          tg_to_mc_success: "{{ trigger.event.data.counters.tg_to_mc_success }}"
          tg_to_mc_failed: "{{ trigger.event.data.counters.tg_to_mc_failed }}"
          mc_to_tg_success: "{{ trigger.event.data.counters.mc_to_tg_success }}"
          mc_to_tg_failed: "{{ trigger.event.data.counters.mc_to_tg_failed }}"
          commands_processed: "{{ trigger.event.data.counters.commands_processed }}"
      - name: MeshCore Control Bridge Version
        unique_id: meshcore_control_bridge_version
        state: "{{ trigger.event.data.version }}"
      - name: MeshCore Control Bridge Uptime
        unique_id: meshcore_control_bridge_uptime
        state: "{{ trigger.event.data.uptime_seconds }}"
        unit_of_measurement: s
      - name: MeshCore Control Bridge MeshCore
        unique_id: meshcore_control_bridge_meshcore
        state: "{{ trigger.event.data.meshcore }}"
      - name: MeshCore Control Bridge Telegram
        unique_id: meshcore_control_bridge_telegram
        state: "{{ trigger.event.data.telegram }}"
      - name: MeshCore Control Bridge Last TG to MC
        unique_id: meshcore_control_bridge_last_tg_to_mc
        state: "{{ trigger.event.data.last_activity.telegram_to_meshcore or 'none' }}"
      - name: MeshCore Control Bridge Last MC to TG
        unique_id: meshcore_control_bridge_last_mc_to_tg
        state: "{{ trigger.event.data.last_activity.meshcore_to_telegram or 'none' }}"
      - name: MeshCore Control Bridge Last Error
        unique_id: meshcore_control_bridge_last_error
        state: "{{ trigger.event.data.last_error.reason }}"
      - name: MeshCore Control Bridge TG to MC Success
        unique_id: meshcore_control_bridge_tg_to_mc_success
        state: "{{ trigger.event.data.counters.tg_to_mc_success }}"
      - name: MeshCore Control Bridge TG to MC Failed
        unique_id: meshcore_control_bridge_tg_to_mc_failed
        state: "{{ trigger.event.data.counters.tg_to_mc_failed }}"
      - name: MeshCore Control Bridge MC to TG Success
        unique_id: meshcore_control_bridge_mc_to_tg_success
        state: "{{ trigger.event.data.counters.mc_to_tg_success }}"
      - name: MeshCore Control Bridge MC to TG Failed
        unique_id: meshcore_control_bridge_mc_to_tg_failed
        state: "{{ trigger.event.data.counters.mc_to_tg_failed }}"
      - name: MeshCore Control Bridge Commands Processed
        unique_id: meshcore_control_bridge_commands_processed
        state: "{{ trigger.event.data.counters.commands_processed }}"
    binary_sensor:
      - name: MeshCore Control Bridge Healthy
        unique_id: meshcore_control_bridge_healthy
        state: "{{ trigger.event.data.status == 'ok' }}"
      - name: MeshCore Control Bridge Audit DB
        unique_id: meshcore_control_bridge_audit_db
        state: "{{ trigger.event.data.database.audit == 'ok' }}"
      - name: MeshCore Control Bridge Telegram DB
        unique_id: meshcore_control_bridge_telegram_db
        state: "{{ trigger.event.data.database.telegram == 'ok' }}"
```

Recommended automations:

```yaml
automation:
  - alias: MeshCore Control Bridge degraded
    trigger:
      - platform: state
        entity_id: sensor.meshcore_control_bridge_status
        to: degraded
        for: "00:05:00"
    action:
      - service: notify.notify
        data:
          message: MeshCore Control Bridge has been degraded for five minutes.

  - alias: MeshCore Control Bridge Telegram disconnected
    trigger:
      - platform: state
        entity_id: sensor.meshcore_control_bridge_status
    condition:
      - condition: template
        value_template: "{{ state_attr('sensor.meshcore_control_bridge_status', 'telegram') == 'disconnected' }}"
    action:
      - service: notify.notify
        data:
          message: MeshCore Control Bridge Telegram polling is disconnected.

  - alias: MeshCore Control Bridge MeshCore disconnected
    trigger:
      - platform: state
        entity_id: sensor.meshcore_control_bridge_status
    condition:
      - condition: template
        value_template: "{{ state_attr('sensor.meshcore_control_bridge_status', 'meshcore') == 'disconnected' }}"
    action:
      - service: notify.notify
        data:
          message: MeshCore Control Bridge MeshCore transport is disconnected.

  - alias: MeshCore Control Bridge forwarding failures increased
    trigger:
      - platform: state
        entity_id:
          - sensor.meshcore_control_bridge_tg_to_mc_failed
          - sensor.meshcore_control_bridge_mc_to_tg_failed
    condition:
      - condition: template
        value_template: "{{ trigger.from_state is not none and trigger.to_state.state | int(0) > trigger.from_state.state | int(0) }}"
    action:
      - service: notify.notify
        data:
          message: MeshCore Control Bridge forwarding failure counter increased.
```

Simple dashboard card:

```yaml
type: entities
title: MeshCore Control Bridge
entities:
  - entity: binary_sensor.meshcore_control_bridge_healthy
  - entity: binary_sensor.meshcore_control_bridge_audit_db
  - entity: binary_sensor.meshcore_control_bridge_telegram_db
  - entity: sensor.meshcore_control_bridge_status
  - entity: sensor.meshcore_control_bridge_version
  - entity: sensor.meshcore_control_bridge_uptime
  - entity: sensor.meshcore_control_bridge_meshcore
  - entity: sensor.meshcore_control_bridge_telegram
  - entity: sensor.meshcore_control_bridge_last_error
  - entity: sensor.meshcore_control_bridge_last_tg_to_mc
  - entity: sensor.meshcore_control_bridge_last_mc_to_tg
  - entity: sensor.meshcore_control_bridge_tg_to_mc_success
  - entity: sensor.meshcore_control_bridge_tg_to_mc_failed
  - entity: sensor.meshcore_control_bridge_mc_to_tg_success
  - entity: sensor.meshcore_control_bridge_mc_to_tg_failed
  - entity: sensor.meshcore_control_bridge_commands_processed
```

The Telegram runtime validates configuration, manages the token file, clears
pending updates on first activation, polls Telegram with
`allowed_updates=["message"]`, filters unsupported or unauthorized updates,
persists `last_update_id`, deduplicates repeated updates, and records safe audit
events without raw message text, raw chat IDs, raw user IDs, or token values.

## Telegram Foundation Local Smoke Test

This flow tests a Telegram development branch locally without publishing a
release and without replacing the stable App image.

1. Create a temporary bot with BotFather.
2. Do not paste the token into shell commands, shell history, logs, issues, or
   screenshots.
3. From Home Assistant Terminal & SSH, or another trusted shell with `bash` and
   `curl`, download the enrollment helper from the PR branch and run it:

   ```sh
     curl -fsSLo /tmp/telegram-enroll.sh \
     https://raw.githubusercontent.com/j3udiel/meshcore-control-bridge/feat/telegram-to-meshcore-forwarding/scripts/telegram-enroll.sh
   bash /tmp/telegram-enroll.sh --timeout 60
   ```

4. Paste the bot token only at the hidden prompt. The helper does not require
   Python, `jq`, `yq`, Perl, Ruby, or Node.js.
5. Open the private chat with the bot, press Start, and send one text message.
6. Copy only the resulting values:

   ```yaml
   allowed_private_chat_id: "<id>"
   allowed_user_id: "<id>"
   ```

   The enrollment tool must not print usernames, names, message text, payloads,
   or the bot token.

7. On the Home Assistant host, from Terminal & SSH with access to `/addons`,
   download the local App preparation helper and run it:

   ```sh
   curl -fsSLo /tmp/prepare-local-telegram-pr23.sh \
     https://raw.githubusercontent.com/j3udiel/meshcore-control-bridge/feat/telegram-to-meshcore-forwarding/scripts/prepare-local-telegram-pr23.sh
   bash /tmp/prepare-local-telegram-pr23.sh
   ```

   The helper uses only shell tools available in core-ssh. It keeps the Git
   checkout in `/addons/.meshcore-control-bridge-telegram-forwarding-source`
   and generates a self-contained local App at
   `/addons/meshcore-control-bridge-telegram-forwarding`.

   The generated App root contains `config.yaml`, `Dockerfile`, `run.sh`,
   `pyproject.toml`, `README.md`, `src/`, translations, and the App
   documentation. The helper changes the local App name and slug, comments the
   `image:` line, and rewrites the local Dockerfile so Supervisor can build from
   that App directory as the Docker build context.

   By default it verifies the current remote HEAD of the PR branch. If you want
   to pin a specific commit from the PR, pass it explicitly:

   ```sh
   bash /tmp/prepare-local-telegram-pr23.sh <pr-head-sha>
   ```

8. In Home Assistant, reload Local apps.
9. Install `MeshCore Control Bridge Telegram Forwarding`. It uses the separate
   slug `meshcore_control_bridge_telegram_forwarding`.
10. Configure only the test App:

    ```yaml
    telegram:
      enabled: true
      bot_token_import: "<token>"
      bot_token_file: /data/telegram.bot_token
      allowed_private_chat_id: "<id>"
      allowed_user_id: "<id>"
      meshcore_channel_index: 1
      forward_meshcore_to_telegram: true
      forward_telegram_to_meshcore: true
      command_prefix: "!"
      max_meshcore_message_length: 180
      max_telegram_message_length: 3900
      message_prefix: "TG: "
      meshcore_to_telegram_prefix: "MC: "
      send_forward_confirmation: false
      forwarding_rate_limit:
        messages: 5
        window_seconds: 60
      inbound_forwarding_rate_limit:
        messages: 20
        window_seconds: 60
    ```

11. Start the test App and inspect logs. Expected safe signals include Telegram
    first activation, pending update discard, polling startup, and bridge ready.
12. Clear `telegram.bot_token_import` in the App UI after the token file has
    been imported.
13. Restart the test App.
14. Send another private text message to the bot.
15. Confirm that polling continues and pending updates are not discarded again
    on normal restart.

### MeshCore to Telegram forwarding

When Telegram is enabled, normal text received on the configured MeshCore channel
can be forwarded to the authorized private Telegram chat:

```yaml
telegram:
  forward_meshcore_to_telegram: true
  meshcore_to_telegram_prefix: "MC: "
  max_telegram_message_length: 3900
  inbound_forwarding_rate_limit:
    messages: 20
    window_seconds: 60
```

Commands are not bridged. A MeshCore `!ping` still executes locally and replies
only on MeshCore. A Telegram `!ping` still replies only in Telegram. Normal
Telegram text forwarded to MeshCore is recorded as pending; if the same transport
message is later observed back from MeshCore, the App consumes that pending
record and does not echo the text back to Telegram.

For PRs that include Telegram readonly commands, send these messages in the
authorized private Telegram chat:

```text
!ping
!estado
!exterior
```

Expected responses are the same short command responses used by MeshCore, sent
only to the Telegram chat. Then send normal text such as:

```text
Voy en 10 minutos
```

Expected behavior for the forwarding branch:

- Telegram receives `Enviado a MeshCore.`
- MeshCore receives `TG: Voy en 10 minutos` on the configured channel.
- Telegram commands such as `!ping` are not sent to MeshCore.
- Normal MeshCore text appears in Telegram with the configured `MC: ` prefix.
- MeshCore commands such as `!ping` are not sent to Telegram.

After testing:

1. Stop and uninstall the test App from Home Assistant.
2. Run:

   ```sh
   curl -fsSLo /tmp/remove-local-telegram-pr23.sh \
     https://raw.githubusercontent.com/j3udiel/meshcore-control-bridge/feat/telegram-to-meshcore-forwarding/scripts/remove-local-telegram-pr23.sh
   bash /tmp/remove-local-telegram-pr23.sh
   ```

3. Revoke the temporary bot in BotFather if it will not be reused.
