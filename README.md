# meshcore-control-bridge

`meshcore-control-bridge` is an experimental local daemon for receiving short
administrative commands through a private MeshCore channel and routing them to
explicitly registered command handlers.

The initial goal is out-of-band access to local Home Assistant status when
Internet access, public DNS, Telegram, or other cloud services are unavailable.

## Experimental Status Warning

This project is experimental and not production-ready.

- The USB MeshCore transport is experimental and has not been validated with
  real hardware yet.
- Do not use this for critical systems.
- LoRa packets can be delayed, duplicated, reordered, or lost.
- A private channel is confidentiality, not sufficient authentication.
- Do not use this as a remote shell.
- Do not use this as the only control path for locks, alarms, fire-safety
  systems, medical systems, or critical infrastructure.

## Problem

Home automation and self-hosted infrastructure often depend on Internet access
for remote administration. This project explores a smaller local control path:
receive a text command over a private MeshCore channel, authenticate the sender
when the transport exposes a stable MeshCore identifier, execute only registered
commands, and send a short response back through the same channel.

## Architecture

```text
MeshCore device
    |
    | private LoRa channel
    v
MeshCore Companion
    |
    | USB serial (experimental) / BLE (future) / TCP (unconfirmed)
    v
meshcore-control-bridge
    |
    +-- Home Assistant
    +-- MQTT (future)
    +-- Proxmox (future)
    +-- Docker (future)
    +-- local monitoring APIs (future)
```

The command engine is independent from MeshCore. MeshCore is only one transport
adapter behind a transport interface. The first experimental transport targets
USB serial framing. BLE is documented by MeshCore but is not implemented here.
TCP remains unconfirmed.

## Current Project Status

The command engine, authorization, deduplication, SQLite audit logging, a Home
Assistant availability client, and tests exist. The legacy `MeshCoreTransport`
placeholder still raises `NotImplementedError` by design. The new USB transport
is isolated as experimental code and must be validated with a real Companion
before it is trusted for administration.

This repository is suitable for development and review. It is not ready for
operating real critical controls.

## Implemented Features

- Transport interface.
- `FakeTransport` for tests and local command-engine development.
- Command registry and router.
- Command prefix parsing with `!`.
- Role model: `readonly`, `home`, `operator`, `admin`.
- Sender authorization using configured stable sender IDs.
- Private-channel filtering by configurable channel index.
- Message deduplication using `message_id` or a time-window content hash.
- SQLite audit tables for inbound messages and command executions.
- Home Assistant availability check using the local HTTP API.
- Optional configured Home Assistant status entities for `!estado`.
- Experimental MeshCore USB frame codec and USB transport session.
- Rate limiting per sender.
- Short LoRa-oriented responses.
- MeshCore diagnostic utility for discovering local support.
- Docker and systemd deployment examples.
- Ruff, mypy, pytest, and CI configuration.

## Not Implemented Yet

- Validated MeshCore Companion transport.
- BLE Companion transport.
- Authenticated sender extraction for channel text messages. The documented
  channel message frames do not include a full stable sender identity.
- Lights, scenes, climate, energy, server, MQTT, Proxmox, or Docker commands.
- Confirmation flow for critical actions.
- Telegram, REST API, or local CLI transports.
- Any write action against Home Assistant or infrastructure.

Future capabilities must remain explicit, allow-listed, tested, and role-gated.

## Security Model

The current model is intentionally conservative:

- Only messages from the configured private channel are routed.
- The public channel (`0`) is rejected for administration.
- The sender must match a configured stable MeshCore public key or node ID.
- Visible node names are not trusted for authentication.
- Commands must be registered in the command registry.
- Received text is never executed as shell, Python code, `eval`, or arbitrary
  subprocess input.
- Duplicate messages are ignored within a configured time window.
- Rate limiting applies per sender within a configurable time window.
- Audit logs store message hashes and command metadata, not Home Assistant
  tokens.
- Current MeshCore channel text frames do not expose a stable sender identity in
  the official documentation. Do not whitelist synthetic channel IDs for real
  administration unless you accept that limitation.

This project has not had a formal security audit. See [SECURITY.md](SECURITY.md)
and [docs/security-model.md](docs/security-model.md).

## Current Commands

Only these commands are currently implemented:

```text
!ping
!help
!help <group>
!estado
!estado ha
```

`!ping` returns:

```text
pong
```

`!help` is generated from the command registry and filtered by the sender role.

`!estado` returns a short status summary, including Home Assistant availability
and any read-only status entities configured in YAML.

## Requirements

- Python 3.12 or newer.
- Home Assistant Long-Lived Access Token for Home Assistant checks.
- SQLite, via Python standard library.
- `httpx` for Home Assistant HTTP calls.
- `PyYAML` for configuration files.
- `pyserial` for the current diagnostic utility.

No MeshCore hardware is required for tests.

## Development Installation

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"
```

Run checks:

```bash
python -m ruff check .
python -m mypy src
python -m pytest
bash scripts/check-secrets.sh
```

## Configuration

Use `config.example.yaml` and `.env.example` as templates. Do not commit real
`config.yaml`, `.env`, tokens, channel secrets, or device identifiers.

Minimal environment:

```env
MESHCORE_CHANNEL_INDEX=1
MESHCORE_TRANSPORT=placeholder
MESHCORE_SERIAL_PORT=
HA_BASE_URL=http://homeassistant.local:8123
HA_TOKEN=replace-with-home-assistant-long-lived-access-token
HA_VERIFY_TLS=true
HA_TIMEOUT_SECONDS=5
DATABASE_PATH=data/audit.db
```

Minimal YAML:

```yaml
meshcore:
  transport: placeholder
  channel_index: 1
  serial_port: null

homeassistant:
  base_url: http://homeassistant.local:8123
  token: ""

users:
  "meshcore-public-key-or-stable-node-id":
    name: "admin-device"
    role: "admin"

status:
  entities:
    temperature:
      entity_id: sensor.living_room_temperature
      label: Temp

security:
  rate_limit:
    commands: 5
    window_seconds: 60
```

Prefer storing `HA_TOKEN` in an environment variable instead of YAML.

## Local Home Assistant URL for Offline Use

Configure `HA_BASE_URL` with a local URL so the bridge can work when WAN access
or public DNS is down:

```env
HA_BASE_URL=http://homeassistant.local:8123
```

or:

```env
HA_BASE_URL=http://192.168.1.50:8123
```

The project should not require public DNS for its basic offline path.

## Home Assistant Long-Lived Access Token

In Home Assistant:

1. Open your user profile.
2. Find `Long-Lived Access Tokens`.
3. Create a token named `meshcore-control-bridge`.
4. Store it outside the repository, usually in `HA_TOKEN`.

The token is shown only once. Never paste it into issues, logs, examples, or
commits.

## Authorized MeshCore Users

Authorize stable identifiers, not display names:

```yaml
users:
  "meshcore-public-key-or-stable-node-id":
    name: "admin-device"
    role: "admin"
```

The channel secret protects confidentiality, but authorization still depends on
the configured sender identity.

## MeshCore Diagnostic Utility

List local serial ports:

```bash
meshcore-diagnose list
```

Inspect a candidate USB serial port:

```bash
meshcore-diagnose inspect \
  --port /dev/serial/by-id/meshcore-companion \
  --channel-index 1
```

Listen without showing message content:

```bash
meshcore-diagnose listen \
  --port /dev/serial/by-id/meshcore-companion \
  --channel-index 1 \
  --seconds 30
```

Send a test message only when explicitly requested:

```bash
meshcore-diagnose send-test \
  --port /dev/serial/by-id/meshcore-companion \
  --channel-index 1 \
  --text "!ping"
```

The utility:

- reports available Python support such as `pyserial`, `bleak`, `meshcore`, and
  `serial_asyncio`;
- lists serial ports;
- can query Companion info and channel slots over USB serial;
- redacts channel secrets and public identifiers;
- does not send text unless `send-test` is used explicitly.

USB support is based on the official Companion Protocol plus the MeshCore wiki
USB framing notes. It remains pending physical validation.

## Deployment Mode

| Mode | Use when | Status |
| --- | --- | --- |
| External Linux host or VM | First real test near Home Assistant and the Companion | Recommended |
| Home Assistant OS add-on | You want the bridge managed by Home Assistant Supervisor | Future skeleton only |

## Docker Deployment

```bash
cp .env.example .env
cp config.example.yaml config.yaml
docker compose up --build
```

Adjust any serial device mapping after `meshcore-diagnose` confirms the real
Companion path. The compose file does not mount the Docker socket and does not
use privileged mode.

## systemd Deployment

An example unit is available at
`deploy/meshcore-control-bridge.service`.

Expected paths:

- `/opt/meshcore-control-bridge`
- `/etc/meshcore-control-bridge.env`
- `/etc/meshcore-control-bridge.yaml`
- `/var/lib/meshcore-control-bridge`

Serial access may require adding the dedicated service user to a group such as
`dialout`, depending on the host.

## Testing and Quality Checks

```bash
python -m pytest
python -m ruff check .
python -m mypy src
bash scripts/check-secrets.sh
```

Tests use `FakeTransport` and do not require a real MeshCore Companion or Home
Assistant instance.

## Roadmap

- Confirm the real MeshCore Companion connection model: USB serial, BLE, or TCP.
- Validate USB serial with a real Companion.
- Confirm whether any channel-message variant exposes a stable sender identity.
- Add a Home Assistant OS add-on after the actual installation type is known.
- Add confirmation storage and `!confirm` / `!cancel`.
- Add read-only house, climate, network, and server status commands.
- Add allow-listed write actions only after role checks, confirmation, and tests.
- Add future transports such as local CLI, REST, or Telegram without coupling
  them to command handlers.

## Contributing

Contributions are welcome, especially protocol research, tests, documentation,
and small security improvements. Read [CONTRIBUTING.md](CONTRIBUTING.md) before
opening a pull request.

Do not include secrets, real channel keys, private logs, personal node IDs,
database files, or unredacted packet captures.

## Responsible Use

This tool is intended for carefully scoped local administration experiments. It
must not become a general remote shell. Commands that affect devices, alarms,
locks, servers, or Home Assistant services must be explicit, allow-listed,
role-gated, confirmed when sensitive, and tested.

## License

Apache-2.0. See [LICENSE](LICENSE).
