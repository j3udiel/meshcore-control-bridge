# MeshCore Control Bridge

Local Home Assistant App for running `meshcore-control-bridge` inside Home
Assistant OS or Supervised.

This App reuses the installed `meshcore-dev/meshcore-ha` integration. It listens
for `meshcore_message` events and replies with `meshcore.send_channel_message`.

The App is experimental. Do not use it for locks, alarms, fire safety, medical
systems, or critical infrastructure.

