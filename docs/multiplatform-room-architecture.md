# Multiplatform Room Architecture

This document describes the target architecture for making
`meshcore-control-bridge` a transport-neutral room command system. Home
Assistant remains an important adapter and deployment runtime, but it must not
be the center of the command engine.

The next milestone is limited to Telegram read-only commands:

- Telegram receives messages.
- Telegram sender identity maps to the common identity model.
- `!ping` and `!help` work from Telegram.
- MeshCore through Home Assistant and Telegram use the same `CommandRouter`.
- The core has no dependency on Home Assistant runtime objects.
- No write commands are enabled.

Automatic bridging between platforms is intentionally out of scope until message
IDs, origins, correlation IDs, deduplication, and loop prevention rules are
specified and tested.

## Current State

The current code already has useful separation:

- `InboundMessage` and `OutboundMessage` dataclasses.
- A `Transport` protocol.
- `BridgeService` for channel filtering, deduplication, rate limiting, routing,
  and responding.
- `CommandRouter` independent from MeshCore protocol details.
- `HomeAssistantMeshCoreTransport` translating Home Assistant `meshcore_message`
  events to command messages.
- `HomeAssistantClient` as an adapter used by read-only status commands.

The main limitation is that the common model still uses MeshCore-specific
language:

- `channel_index` assumes numeric MeshCore channels.
- `destination` assumes a sender-oriented reply target.
- `sender_id` is a string without typed identity kind or trust level.
- Deduplication keys do not include a room abstraction or direction.
- Audit rows store transport and channel data, but not a normalized room,
  origin, correlation ID, or transport capability snapshot.
- Home Assistant App runtime code constructs configuration for both the
  Home Assistant MeshCore transport and the Home Assistant REST adapter, which
  can make Home Assistant look like the hub rather than one adapter.

## Target Shape

```mermaid
flowchart TD
    MT[MeshCore via Home Assistant transport] --> NI[Normalized inbound envelope]
    TT[Telegram transport] --> NI
    FT[Fake transport] --> NI

    NI --> LP[Loop prevention]
    LP --> DD[Deduplication]
    DD --> RL[Rate limiting]
    RL --> AU[Authorization]
    AU --> CR[Command router]
    CR --> PL[Command plugins]

    PL --> HA[Home Assistant adapter]
    PL --> FUT[Future adapters]

    CR --> OR[Outbound response envelope]
    OR --> MT
    OR --> TT

    NI --> AD[Audit]
    CR --> AD
    OR --> AD
```

The command router should only depend on normalized envelopes and service
adapters. A transport may be backed by Home Assistant, Telegram, USB serial, CLI,
or tests, but those details should stop at the transport boundary.

## Common Model

### Transport Name

A stable lowercase identifier for the adapter implementation.

Examples:

- `meshcore-ha`
- `meshcore-usb`
- `telegram`
- `fake`

Transport names are not security identities. They only scope message IDs,
capabilities, room IDs, and audit records.

### Room

A room is the transport-neutral place where a message was observed and where a
reply should normally be sent.

Proposed model:

```python
@dataclass(frozen=True, slots=True)
class RoomRef:
    transport: str
    room_id: str
    room_kind: Literal["meshcore_channel", "telegram_chat", "direct", "local"]
    display_name: str | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)
```

Examples:

- MeshCore channel 1 through Home Assistant:
  `RoomRef("meshcore-ha", "meshcore-ha:channel:1", "meshcore_channel")`
- Telegram chat:
  `RoomRef("telegram", "telegram:chat:-1001234567890", "telegram_chat")`
- Future CLI:
  `RoomRef("local-cli", "local-cli:session", "local")`

MeshCore channel `0` remains forbidden for administration by policy. That policy
should live in room authorization or transport configuration, not in command
handlers.

### Sender Identity

Sender identity should be typed and should record whether it is stable enough
for authorization.

Proposed model:

```python
@dataclass(frozen=True, slots=True)
class SenderIdentity:
    sender_id: str
    transport: str
    identity_kind: Literal[
        "meshcore_pubkey_prefix",
        "meshcore_node_id",
        "telegram_user_id",
        "telegram_chat_id",
        "synthetic_test",
        "unknown",
    ]
    stable: bool
    display_name: str | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)
```

Rules:

- Authorization must use `sender_id`, not display names.
- Display names are for diagnostics only.
- Synthetic identities are allowed only when an explicit test mode grants a
  temporary read-only role.
- Telegram should use numeric Telegram user IDs for user identity, not usernames.
- Telegram usernames and chat titles are not authentication material.

Examples:

- `meshcore-pubkey-prefix:abcdef123456`
- `telegram-user:123456789`
- `test:unidentified:meshcore-ha:channel:1`

### Message Identity

Inbound message IDs must be scoped by transport and room. A platform-provided
message ID is preferred. If no stable ID exists, use a hash fallback with a
short replay window.

Proposed model:

```python
@dataclass(frozen=True, slots=True)
class MessageIdentity:
    message_id: str | None
    id_kind: Literal["platform", "derived", "missing"]
    correlation_id: str
    origin: MessageOrigin
```

`correlation_id` links an inbound command, command execution, and outbound
response. It is not used as authentication.

### Origin

Origin is required before any cross-platform bridging exists.

Proposed model:

```python
@dataclass(frozen=True, slots=True)
class MessageOrigin:
    transport: str
    room_id: str
    message_id: str | None
    bridge_instance_id: str | None = None
```

Loop prevention should mark messages generated by this bridge and ignore them
when they return through a transport as events.

### Inbound Envelope

Proposed future replacement for the current `InboundMessage` shape:

```python
@dataclass(frozen=True, slots=True)
class InboundEnvelope:
    transport: str
    room: RoomRef
    sender: SenderIdentity
    message: MessageIdentity
    text: str
    received_at: datetime
    metadata: Mapping[str, object] = field(default_factory=dict)
```

Compatibility can be introduced gradually by adding fields to the existing
`InboundMessage` first, then migrating storage and tests.

### Outbound Response

Outbound responses should target a room, not a MeshCore channel.

```python
@dataclass(frozen=True, slots=True)
class OutboundResponse:
    transport: str
    room: RoomRef
    text: str
    reply_to: str | None
    correlation_id: str
    metadata: Mapping[str, object] = field(default_factory=dict)
```

The transport decides how to convert that response:

- MeshCore through Home Assistant calls `meshcore.send_channel_message`.
- Telegram calls `sendMessage`.
- FakeTransport appends to an in-memory queue.

## Transport Capabilities

Each transport should expose capabilities so the core does not assume every
platform behaves like LoRa.

```python
@dataclass(frozen=True, slots=True)
class TransportCapabilities:
    supports_replies: bool
    supports_message_ids: bool
    supports_stable_sender_ids: bool
    supports_room_ids: bool
    supports_direct_messages: bool
    max_outbound_chars: int
    delivery: Literal["best_effort", "acknowledged", "unknown"]
```

Initial capabilities:

| Transport | Stable sender | Message ID | Room ID | Max response | Delivery |
| --- | --- | --- | --- | --- | --- |
| MeshCore via Home Assistant | yes when `pubkey_prefix` is present | Home Assistant context ID or derived | channel index | LoRa-short | best effort |
| Telegram | numeric user ID | Telegram message ID | chat ID | larger than LoRa | acknowledged by API |
| Fake | configurable | configurable | configurable | test-defined | unknown |

Handlers should not directly inspect capabilities. The response formatter may
use them to trim or paginate output.

## Authorization

Authorization should become transport-neutral:

```yaml
users:
  "meshcore-pubkey-prefix:abcdef123456":
    name: admin-device
    role: admin

  "telegram-user:123456789":
    name: admin-telegram
    role: readonly
```

Room policy should be separate from user identity:

```yaml
rooms:
  "meshcore-ha:channel:1":
    enabled: true
    minimum_role: readonly
    allow_commands: true

  "telegram:chat:123456789":
    enabled: true
    minimum_role: readonly
    allow_commands: true
```

The effective permission is the stricter result of:

- sender role;
- room policy;
- command minimum role;
- optional transport capability restrictions;
- optional test-mode restrictions.

Unidentified testing must remain an explicit read-only mode and should produce a
synthetic sender only for the configured room.

## Command Context

`CommandContext` should be transport-neutral and should not expose Home
Assistant runtime details.

Proposed model:

```python
@dataclass(slots=True)
class CommandContext:
    inbound: InboundEnvelope
    user: AuthorizedUser
    services: ServiceRegistry
    response_profile: ResponseProfile
```

`ResponseProfile` can describe formatting constraints:

- `short_text`
- `max_chars`
- `line_budget`
- `supports_markdown`

MeshCore responses stay brief. Telegram can use a larger profile, but commands
should still return concise text by default.

## Deduplication

Deduplication should use a normalized key:

```text
transport | room_id | sender_id | message_id
```

When the platform lacks `message_id`, fallback to:

```text
transport | room_id | sender_id | time_bucket | hash(normalized_text)
```

Do not deduplicate across transports unless a future bridge explicitly sets a
shared `correlation_id`. A Telegram `!ping` and MeshCore `!ping` are different
commands.

## Loop Prevention

Loop prevention is required before automatic bridging.

Current command replies are not bridging. They should still carry metadata that
prevents self-processing if a transport emits outgoing messages as inbound
events.

Initial rules:

- Ignore transport events marked as outgoing.
- Attach a bridge-generated `correlation_id` to audit records.
- Store outbound responses with `origin.transport`, `origin.room_id`, and
  `reply_to`.
- Do not forward messages from one transport to another in the Telegram
  milestone.

Future bridge rules must define:

- which rooms are linked;
- which command prefixes are bridged or suppressed;
- how TTL is represented;
- how edits/deletes are handled;
- how loops are detected across restarts;
- whether bridged messages are auditable without storing private text.

## Audit Events

Audit should record normalized metadata without storing full private message
text.

Suggested event fields:

- `audit_id`
- `correlation_id`
- `event_type`
- `transport`
- `room_id`
- `room_kind`
- `sender_id`
- `sender_identity_kind`
- `sender_stable`
- `message_id`
- `command`
- `result`
- `reason`
- `duration_ms`
- `created_at`
- `metadata_json`

Private message text should remain hashed, not stored verbatim.

## Home Assistant's Role

Home Assistant should have two separate responsibilities:

1. Runtime for the Home Assistant App deployment.
2. Adapter for Home Assistant state queries and MeshCore events from
   `meshcore-ha`.

Neither responsibility should leak into the command core.

The Home Assistant App may construct a config object from `/data/options.json`
and `SUPERVISOR_TOKEN`, but after startup it should provide the same transport
and adapter interfaces as any other deployment.

## Telegram Milestone Design

The Telegram adapter should be added as another transport, not as a special
router path.

Initial scope:

- Long polling or webhook decision documented before implementation.
- Receive text messages.
- Map Telegram user ID to `telegram-user:<id>`.
- Map Telegram chat ID to `telegram:chat:<id>`.
- Use the existing command prefix.
- Support only `!ping` and `!help` initially.
- Reuse the same `CommandRouter`.
- Use the same `Authorizer`, `Deduplicator`, `RateLimiter`, and audit storage.
- Do not bridge Telegram messages to MeshCore.
- Do not enable Home Assistant writes.

Security rules:

- Do not authorize by Telegram username.
- Do not log full message text by default.
- Store Telegram bot token only in environment or external secrets.
- Ignore non-text messages for the first milestone.
- Treat group chats and private chats as different rooms.

## PR Plan

### PR 1: Normalize Core Message Model

Goal: introduce transport-neutral types while preserving existing behavior.

Changes:

- Add `RoomRef`, `SenderIdentity`, `MessageIdentity`, `MessageOrigin`,
  `InboundEnvelope`, `OutboundResponse`, and `TransportCapabilities`.
- Add compatibility constructors from current `InboundMessage`.
- Keep the existing router behavior unchanged.
- Add tests for MeshCore through Home Assistant and FakeTransport compatibility.

Risk: low if the existing dataclasses remain available during migration.

### PR 2: Move Filtering and Authorization to Normalized Context

Goal: remove MeshCore-specific channel assumptions from core services.

Changes:

- Replace `channel_index` checks in `BridgeService` with room policy checks.
- Keep `channel_index` as MeshCore metadata only.
- Add room allowlist configuration.
- Update deduplication to include `room_id`.
- Update audit records with room and correlation fields.

Risk: medium because it touches command ingress and audit.

### PR 3: Transport Capabilities and Response Profiles

Goal: make output formatting transport-aware without changing command handlers.

Changes:

- Add `TransportCapabilities`.
- Add `ResponseProfile`.
- Keep LoRa-short formatting for MeshCore.
- Allow Telegram to use a larger default response size later.

Risk: low.

### PR 4: Telegram Transport Skeleton

Goal: add Telegram as a transport behind the same interface.

Changes:

- Add Telegram config with token from environment only.
- Implement receive/send using one selected mode, preferably long polling for
  the first local milestone unless webhook deployment is explicitly chosen.
- Map Telegram user and chat IDs to common identity and room models.
- Add tests with mocked Telegram API.
- No bridging and no write commands.

Risk: medium due to API polling, token handling, and offset persistence.

### PR 5: Telegram Read-Only Commands

Goal: make `!ping` and `!help` work from Telegram using the same router.

Changes:

- Wire Telegram transport into app startup for non-Home Assistant deployments.
- Add config examples for Telegram users and rooms.
- Add audit tests for Telegram command execution.
- Verify no Home Assistant runtime dependency in core.

Risk: low to medium.

### PR 6: Bridging Design Only

Goal: document cross-platform message bridging before implementation.

Changes:

- Specify bridge rules, room mappings, TTL, loop prevention, correlation IDs,
  and audit semantics.
- Add tests for rule evaluation without sending real messages.
- Do not forward messages yet.

Risk: low.

## Non-Goals for This Milestone

- MeshCore USB changes.
- Home Assistant entity expansion.
- Lights, scenes, locks, alarms, or server actions.
- Telegram-to-MeshCore or MeshCore-to-Telegram automatic forwarding.
- Critical commands or confirmation flows.
- Publishing a new Home Assistant App version.
