# MeshCore Control Bridge

Home Assistant App for running `meshcore-control-bridge` inside Home Assistant
OS or Supervised.

This App reuses the installed `meshcore-dev/meshcore-ha` integration. It listens
for `meshcore_message` events and replies with `meshcore.send_channel_message`.
Telegram support is disabled by default. When enabled, the current development
branch can run readonly commands from one authorized private chat and forward
normal Telegram text to the configured MeshCore channel. MeshCore to Telegram
forwarding is not implemented yet.

The App is experimental. Do not use it for locks, alarms, fire safety, medical
systems, or critical infrastructure.

Install it by adding the repository URL in Home Assistant:

```text
https://github.com/j3udiel/meshcore-control-bridge
```

Home Assistant uses the repository default branch, so the App appears in the
store only after these App files are merged there. See `DOCS.md` for the test
configuration and current limitations.
