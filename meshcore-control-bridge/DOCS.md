# MeshCore Control Bridge App

## Installation For Local Testing

1. Install Samba or Terminal & SSH in Home Assistant.
2. Copy this folder to `/addons/meshcore-control-bridge`.
3. Go to Settings -> Apps -> App Store.
4. Select the menu and reload local Apps, or choose Check for updates.
5. Open Local apps.
6. Install MeshCore Control Bridge.
7. Configure `channel_index`, `meshcore_entry_id`, `authorized_senders`, and
   `status_entities`.
8. Start the App.
9. Review the App logs.
10. Send `!ping` on the configured private MeshCore channel.
11. Send `!estado ha`.
12. Send `!estado`.

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

