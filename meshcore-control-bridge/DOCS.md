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
  message_prefix: ""
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

The App version in `config.yaml` selects the image tag. For version `0.1.9`,
Supervisor pulls:

```text
ghcr.io/j3udiel/meshcore-control-bridge:0.1.9
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

Normal Telegram text is still `foundation_only` and is not forwarded to
MeshCore. This release does not include Telegram to MeshCore forwarding,
MeshCore to Telegram forwarding, bidirectional bridging, groups, media,
webhooks, write commands, or USB release work.

The Telegram runtime validates configuration, manages the token file, clears
pending updates on first activation, polls Telegram with
`allowed_updates=["message"]`, filters unsupported or unauthorized updates,
persists `last_update_id`, deduplicates repeated updates, and records safe audit
events without raw message text, raw chat IDs, raw user IDs, or token values.

## Telegram Foundation Local Smoke Test

This flow tests PR23 locally without publishing a release and without replacing
the stable App image.

1. Create a temporary bot with BotFather.
2. Do not paste the token into shell commands, shell history, logs, issues, or
   screenshots.
3. From Home Assistant Terminal & SSH, or another trusted shell with `bash` and
   `curl`, download the enrollment helper from the PR branch and run it:

   ```sh
   curl -fsSLo /tmp/telegram-enroll.sh \
     https://raw.githubusercontent.com/j3udiel/meshcore-control-bridge/feat/telegram-readonly-commands/scripts/telegram-enroll.sh
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
     https://raw.githubusercontent.com/j3udiel/meshcore-control-bridge/feat/telegram-readonly-commands/scripts/prepare-local-telegram-pr23.sh
   bash /tmp/prepare-local-telegram-pr23.sh
   ```

   The helper uses only shell tools available in core-ssh. It keeps the Git
   checkout in `/addons/.meshcore-control-bridge-pr23-source` and generates a
   self-contained local App at `/addons/meshcore-control-bridge-pr23`.

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
9. Install `MeshCore Control Bridge PR23`. It uses the separate slug
   `meshcore_control_bridge_pr23`.
10. Configure only the PR23 App:

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
      message_prefix: ""
    ```

11. Start the PR23 App and inspect logs. Expected safe signals include Telegram
    first activation, pending update discard, polling startup, and bridge ready.
12. Clear `telegram.bot_token_import` in the App UI after the token file has
    been imported.
13. Restart the PR23 App.
14. Send another private text message to the bot.
15. Confirm that polling continues and pending updates are not discarded again
    on normal restart.

For PRs that include Telegram readonly commands, send these messages in the
authorized private Telegram chat:

```text
!ping
!estado
!exterior
```

Expected responses are the same short command responses used by MeshCore, sent
only to the Telegram chat. Normal Telegram text still stays local to the
Telegram foundation and is not forwarded to MeshCore.

After testing:

1. Stop and uninstall the PR23 App from Home Assistant.
2. Run:

   ```sh
   curl -fsSLo /tmp/remove-local-telegram-pr23.sh \
     https://raw.githubusercontent.com/j3udiel/meshcore-control-bridge/feat/telegram-foundation/scripts/remove-local-telegram-pr23.sh
   bash /tmp/remove-local-telegram-pr23.sh
   ```

3. Revoke the temporary bot in BotFather if it will not be reused.
