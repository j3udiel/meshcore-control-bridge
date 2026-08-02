# Changelog

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
