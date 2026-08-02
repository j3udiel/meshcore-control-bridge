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

You do not configure `HA_TOKEN` for this App. Home Assistant provides
`SUPERVISOR_TOKEN` automatically, and the App uses that token only for the
internal Home Assistant API proxy.

## Image Distribution

The App configuration references:

```text
ghcr.io/j3udiel/meshcore-control-bridge
```

The App version in `config.yaml` selects the image tag. For version `0.1.3`,
Supervisor pulls:

```text
ghcr.io/j3udiel/meshcore-control-bridge:0.1.3
```

If the image has not been published yet, installation from the public repository
will not complete. The repository keeps a Dockerfile for local development and a
manual/tag-driven GitHub Actions workflow to publish the multi-architecture GHCR
image when ready.

## Development Alternative

For App development only, you can still use Home Assistant local App testing
methods. Do not use that as the normal installation path for users.

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
