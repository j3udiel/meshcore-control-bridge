# Telegram to MeshCore Bridge Design

This document designs a future bidirectional bridge between Telegram and one
configured MeshCore room. It is design only. It does not implement a Telegram
transport, message forwarding, new runtime code, write commands, releases, or
USB changes.

The target behavior is:

- MeshCore channel messages appear in an authorized Telegram chat.
- Normal Telegram text can be forwarded to the configured MeshCore channel.
- Existing readonly commands such as `!ping`, `!estado`, and `!exterior` can run
  from Telegram through the same command engine.
- Commands are not forwarded to MeshCore by default.

## Configuration Model

Initial configuration:

```yaml
telegram:
  enabled: false
  bot_token_file: /data/telegram.bot_token
  allowed_chat_ids: []
  allowed_user_ids: []
  meshcore_channel_index: 1
  forward_meshcore_to_telegram: true
  forward_telegram_to_meshcore: true
  command_prefix: "!"
  max_meshcore_message_length: 180
  message_prefix: ""
```

`enabled` must default to `false`. A deployment must opt in explicitly.

`bot_token_file` points to a file outside public options and outside SQLite. The
token must never be stored in SQLite, command metadata, audit metadata, logs, or
Home Assistant public option text if a more private file mechanism is available.

`allowed_chat_ids` authorizes Telegram rooms. `allowed_user_ids` authorizes
human senders. Both lists contain configured raw IDs in protected
configuration, but audit records use HMAC references only.

`meshcore_channel_index` selects the MeshCore channel used for forwarded
Telegram text. Channel `0` must remain prohibited for administration.

`max_meshcore_message_length` is a conservative v1 limit measured in UTF-8
bytes. The default is `180` bytes until the active MeshCore transport exposes a
more precise capability. Implementations must validate the configured value
against transport capabilities before sending.

`message_prefix` is optional text prepended to Telegram-originated messages sent
to MeshCore, for example `TG: `. It must be length-limited and must reject
control characters.

## Identity And Authorization

Telegram has separate room identity and sender identity. A Telegram chat ID is a
room property, not a sender property.

Room identity:

```text
RoomRef(
  transport="telegram",
  room_id="telegram:chat:<chat-id-hmac-or-config-ref>",
  room_kind="private" | "group" | "supergroup" | "channel"
)
```

Sender identity:

```text
SenderIdentity(
  sender_id="telegram:user:<bot-instance>:<user-id-hmac-or-config-ref>",
  identity_kind="telegram_user",
  stable=true
)
```

If Telegram sender-chat messages are supported later, use a distinct namespace:

```text
telegram:sender-chat:<bot-instance>:<sender-chat-id-hmac-or-config-ref>
```

The raw Telegram `chat_id`, `user_id`, and optional `sender_chat_id` may appear
only in protected authorization configuration and in the in-memory update being
processed. They must not be written raw to normalized audit events.

### Chat Authorization

`allowed_chat_ids` is checked first. If the update comes from a chat that is not
configured, the bridge ignores it silently or logs a safe warning with an HMAC
chat reference. It must not reply to unknown group chats because replying proves
the bot is active.

Private chats:

- Require the private chat ID to be in `allowed_chat_ids`.
- Require the user ID to be in `allowed_user_ids`.
- The effective role comes from the configured Telegram user mapping.

Groups and supergroups:

- Require the group chat ID to be in `allowed_chat_ids`.
- Require the human sender's user ID to be in `allowed_user_ids`.
- Ignore anonymous admin or sender-chat messages in v1 unless explicitly
  supported with a separate sender-chat allowlist.
- Ignore messages from bots.

Channels:

- Ignore channel posts in v1 unless a future sender-chat policy is designed.

### Role Mapping

Telegram identities map to the existing role model:

- `readonly`
- `home`
- `operator`
- `admin`

The v1 milestone should use `readonly` only unless a later PR explicitly adds
write-capable commands and confirmation policy. Telegram command authorization
must call the same authorizer and `CommandRouter` path as MeshCore.

Unauthorized Telegram commands receive a short Telegram response:

```text
No autorizado.
```

Unauthorized normal Telegram text is not forwarded to MeshCore.

## Message Classification

Every Telegram update is classified before any command or bridge action:

| Input | v1 behavior |
| --- | --- |
| Text starting with `command_prefix` | Execute locally through `CommandRouter`. |
| Normal text | Forward to MeshCore only if `forward_telegram_to_meshcore=true`. |
| Unknown command | Reply in Telegram with the existing unknown-command response; do not forward. |
| Bot message | Ignore. |
| Edited message | Ignore in v1. |
| Document/image/sticker/audio/video | Ignore in v1, optionally reply with a safe short message. |
| Empty text | Ignore. |

Commands are never forwarded to MeshCore by default. A future explicit escape or
configuration option would need a separate security review.

MeshCore messages received from the configured channel are classified similarly:

- If they are commands, the existing command engine may execute them and reply
  through MeshCore.
- If they are normal text and `forward_meshcore_to_telegram=true`, forward to
  authorized Telegram rooms.
- Do not forward messages that are known to have originated from Telegram.

## Loop Prevention

The bridge must not rely on comparing text. It needs explicit bridge metadata
and short-lived deduplication.

Each inbound envelope has:

```text
correlation_id = "corr:<uuid>"
origin_transport = "telegram" | "meshcore-ha" | ...
origin_room_id
platform_message_id
```

Each forwarded bridge message has:

```text
bridge_message_id = "br:<uuid>"
correlation_id = original correlation_id
source_transport
source_room_id
destination_transport
destination_room_id
content_ref_hash
created_at
expires_at
```

When forwarding Telegram to MeshCore, store a pending bridge record keyed by:

```text
bridge_message_id
correlation_id
source_transport
source_room_id
destination_transport
destination_room_id
content_ref_hash
```

When a MeshCore message later appears, the bridge checks:

- whether the transport marks it as outgoing or self-sent;
- whether metadata contains a known `bridge_message_id` or reply correlation;
- whether a recent bridge record has the same destination room and content HMAC;
- whether the observed sender identity is the bridge's own MeshCore identity.

If any loop rule matches, audit `bridge.message.ignored` with
`reason=loop_prevention` and do not forward back to Telegram.

The deduplication window should be short, for example 10 minutes, and based on
expiration timestamps, not fixed time buckets. It must not deduplicate across
different transports, rooms, or senders unless a bridge record explicitly links
the messages.

## Length And Formatting

Telegram supports much longer text than MeshCore. MeshCore forwarding must use
the configured `max_meshcore_message_length` in UTF-8 bytes.

Rules:

- Measure encoded UTF-8 bytes, not Python characters.
- Never split inside a UTF-8 code point.
- Prefer truncating at a word boundary.
- Add a short marker such as `...` only if it fits.
- Do not invent multipart delivery in v1.
- Do not split command responses into multiple MeshCore messages unless the
  transport capability explicitly supports safe fragmentation.

Optional prefixes:

```text
TG: hello
```

Prefixes count toward the MeshCore byte limit.

Replies:

- Telegram replies can be represented in metadata as a HMAC reference to the
  replied-to platform message.
- v1 does not attempt threaded reply rendering on MeshCore.
- Telegram command responses should use the original Telegram chat as
  `reply_target`.

LoRa-compatible formatting should stay short:

```text
TG: bring backup link online?
```

## Normalized Audit

Add event types:

- `bridge.message.received`
- `bridge.message.forwarded`
- `bridge.message.ignored`
- `bridge.message.failed`

All bridge events use the existing normalized audit conventions:

- `schema_version`
- `event_id`
- `correlation_id`
- `causation_event_id`
- UTC timestamps
- HMAC references using the configured audit key
- allow-listed metadata only

Do not store:

- raw Telegram text;
- raw MeshCore text;
- bot token;
- raw chat ID;
- raw user ID;
- chat title;
- username;
- display names;
- full update payloads;
- full service error bodies.

Allowed metadata examples:

```json
{
  "direction": "telegram_to_meshcore",
  "source_transport": "telegram",
  "destination_transport": "meshcore-ha",
  "result": "forwarded",
  "size_bytes": 42,
  "reason": "allowed"
}
```

Allowed `reason` values should include:

- `allowed`
- `unauthorized_chat`
- `unauthorized_user`
- `bot_message`
- `edited_message`
- `unsupported_message_type`
- `command_not_forwarded`
- `unknown_command`
- `loop_prevention`
- `duplicate`
- `too_long`
- `rate_limited`
- `telegram_send_failed`
- `meshcore_send_failed`
- `shutdown`

`source_room_ref_hash`, `destination_room_ref_hash`,
`sender_ref_hash`, `platform_message_ref_hash`, and `bridge_message_ref_hash`
must use HMAC-SHA256 with domain separation. Raw IDs stay only in protected
configuration and memory.

## Error Model

Telegram offline:

- Long polling retries with bounded exponential backoff.
- Do not drop MeshCore command handling while Telegram is unavailable.
- Audit `bridge.message.failed` for forwarding failures.

MeshCore offline:

- Telegram commands still execute locally if they do not require MeshCore.
- Normal Telegram text cannot be forwarded; reply with a short failure.

Timeouts:

- Treat send and receive timeouts as transport failures.
- Use short, bounded retries for transient Telegram errors.

Telegram rate limits:

- Respect Telegram retry hints when available.
- Use an internal queue and backoff.
- Avoid retry storms during outages.

Internal rate limits:

- Apply existing per-sender command rate limits to Telegram commands.
- Add separate forwarding rate limits per Telegram room.

Duplicate messages:

- Use Telegram `update_id` and message ID when available.
- Use normalized room, sender, message ID, and text HMAC fallback when needed.

Shutdown:

- Stop long polling.
- Stop consuming bridge queues.
- Flush in-flight audit writes with short transactions.
- Do not start new forwards after shutdown begins.

## Concurrency

v1 should use Telegram long polling, not webhooks.

Suggested tasks:

- Telegram polling task.
- MeshCore receive task.
- Command processing task or bounded worker pool.
- Telegram outbound queue.
- MeshCore outbound queue.
- Health task.

Queues must be bounded. If a queue is full:

- Prefer dropping bridge forwards over dropping command responses.
- Audit `bridge.message.ignored` with `reason=rate_limited` or
  `reason=too_long` as appropriate.
- Apply backpressure without unbounded memory growth.

Ordering:

- Preserve order per source room where feasible.
- Do not globally serialize all transports.

SQLite:

- Do not keep transactions open across Telegram API calls, MeshCore sends,
  Home Assistant calls, or any `await`.
- Record each completed stage in a short transaction.

## Security

Token handling:

- Read the bot token from `bot_token_file`.
- File must be regular, owned by the expected runtime user where portable,
  mode `0600`, and not a symlink.
- Never log token contents or token file contents.
- Rotation is done by replacing the token file and restarting the bridge in v1.

Startup:

- Do not process old Telegram updates on startup. Use long polling offset
  handling to start after the latest update unless the operator explicitly opts
  into replay.
- Webhooks are out of scope for v1.

Authorization:

- Reject unknown chats.
- Reject unknown users.
- Ignore bot messages.
- Do not trust usernames, first names, last names, or chat titles.

Execution:

- Do not execute arbitrary text.
- Do not add write commands.
- Do not route unknown Telegram commands to MeshCore.

Logging:

- Log transport, direction, command name, result, and safe reason codes.
- Do not log raw message text, raw IDs, usernames, chat titles, or token values.

## UX Examples

MeshCore to Telegram:

```text
[MeshCore] Battery is low near the relay.
```

Telegram to MeshCore:

```text
TG: I am checking the backup link.
```

Telegram command:

```text
User: !ping
Bridge: pong
```

Unknown Telegram command:

```text
User: !doesnotexist
Bridge: Comando desconocido. Usa !help
```

Unauthorized chat:

```text
No response in groups; safe warning in logs only.
```

Unauthorized private user:

```text
No autorizado.
```

Message too long:

```text
No enviado: mensaje demasiado largo para MeshCore.
```

MeshCore send failure:

```text
No enviado: MeshCore no disponible.
```

Unsupported message type:

```text
No compatible en esta version.
```

## Planned Tests

- Authorized chat.
- Unauthorized chat.
- Authorized user.
- Unauthorized user.
- Private chat command.
- Group command.
- Bot message ignored.
- Edited message ignored.
- Text command executes locally.
- Normal text forwards to MeshCore.
- Unknown command replies in Telegram and does not forward.
- Unsupported media ignored or receives a safe response.
- Telegram to MeshCore loop prevention.
- MeshCore to Telegram loop prevention.
- Duplicate Telegram update ignored.
- Duplicate MeshCore message ignored.
- Long UTF-8 text truncates without splitting code points.
- Message prefix counts toward MeshCore byte limit.
- Telegram send failure.
- MeshCore send failure.
- Restart does not process old updates.
- Token file mode validation.
- Token privacy in logs and SQLite.
- Audit privacy for raw chat IDs, user IDs, usernames, titles, message IDs, and
  text.
- No SQLite transaction remains open across an awaited network call.
- Graceful shutdown drains or safely drops queued bridge messages.

## Out Of Scope

- Multimedia forwarding.
- Multiple Telegram bots.
- Multiple MeshCore channels.
- Telegram webhooks.
- Write commands.
- Edited or deleted message synchronization.
- Historical synchronization.
- USB transport work.
- Release publishing.
