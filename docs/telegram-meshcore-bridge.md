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

## Telegram V1 Scope

Telegram v1 is intentionally narrow:

- One Telegram bot.
- One authorized private Telegram chat.
- One authorized Telegram user.
- One MeshCore channel.
- Plain text only.
- Existing readonly commands only.
- Long polling only.

Groups, supergroups, channels, multiple bots, multiple MeshCore channels,
webhooks, media forwarding, and write commands are later phases.

Telegram platform constraints:

- A user must open the bot chat and press Start or send the first message.
- A bot cannot initiate a conversation with a user that has not started it.
- Group support requires a separate review of BotFather Privacy Mode, bot admin
  permissions, mention handling, and group-specific authorization.

## Configuration Model

Initial v1 configuration:

```yaml
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
```

`enabled` must default to `false`. A deployment must opt in explicitly.

`bot_token_import` is a one-time protected import field used by the Home
Assistant App UI. When non-empty, startup validates the token shape, writes it
atomically to `bot_token_file`, sets mode `0600`, and then avoids re-exposing the
value. If the Home Assistant Supervisor options API cannot clear the import
field automatically, the App must log a safe warning telling the operator to
clear the field after successful import. The token must never appear in logs,
SQLite, normalized audit metadata, exceptions, health output, documentation with
real values, or diagnostics.

`bot_token_file` points to the persisted token file. It is created from
`bot_token_import` or reused after restart. It must be a regular file, not a
symlink, mode `0600`, and owned by the expected runtime user where portable.
Rotation is explicit: set a new `bot_token_import`, restart, verify import, and
clear the import field if it remains visible in the UI.

`allowed_private_chat_id` authorizes exactly one Telegram private chat.
`allowed_user_id` authorizes exactly one human Telegram user. Both raw IDs exist
only in protected configuration and memory. Audit records use HMAC references
only. Empty values keep Telegram disabled or fail closed when
`telegram.enabled=true`.

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
  room_kind="private"
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

### V1 Authorization

`allowed_private_chat_id` is checked first. If an update is not from the
configured private chat, the bridge ignores it and logs only a safe reason with
an HMAC chat reference.

Private chat authorization:

- Require the private chat ID to equal `allowed_private_chat_id`.
- Require the user ID to equal `allowed_user_id`.
- The effective role comes from the configured Telegram user mapping.

Groups, supergroups, and channels:

- Out of scope for v1.
- Ignore without reply.
- A later design must revisit Privacy Mode, admin permissions, anonymous admins,
  sender-chat updates, mentions, and group-specific roles.

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

Every inbound message uses this shared pipeline:

```text
message received
-> channel/room authorization
-> deduplication
-> rate limit
-> classification
   -> command: command engine, response to origin transport
   -> normal text: bridge policy
   -> unsupported/empty: ignore
```

Classification happens after security filters. Normal MeshCore text must be
able to reach Telegram before the command router returns `not_a_command`.
Normal text does not enter the `CommandRouter`.

Responses produced by the bridge are never bridged again. MeshCore commands are
not shown as normal Telegram chat by default. Telegram command responses return
only to the Telegram chat. MeshCore command responses return only to MeshCore.

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

## Telegram Long Polling

v1 uses long polling and must not use webhooks.

Startup sequence:

1. Load and validate the token file.
2. Call `deleteWebhook` with `drop_pending_updates=true` when Telegram bridge is
   enabled for the first time or after an explicit operator reset. This prevents
   old updates from being processed at activation.
3. Fail closed if a webhook remains configured or another consumer appears to be
   using the same bot.
4. Call `getUpdates` with `allowed_updates=["message"]`.
5. Use a bounded long-poll timeout, for example 50 seconds, plus a shorter HTTP
   client timeout around connection setup.
6. Track `last_update_id` and request `offset=last_update_id+1`.

Update confirmation policy:

- Persist `last_update_id` only after the update has been classified,
  deduplicated, audited, and either processed or intentionally ignored.
- If the process exits before persisting the offset, Telegram may deliver the
  update again. Normalized deduplication and command idempotency must handle the
  repeat.
- If the process exits after persisting the offset but before a best-effort
  forward completes, Telegram will not redeliver the update. The bridge records
  the failed or dropped state in audit.
- Do not process pending history on first activation.

`allowed_updates=["message"]` intentionally excludes edited messages, channel
posts, callback queries, inline queries, and other update types in v1.

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

1. Whether the transport marks it as outgoing or self-sent.
2. Whether metadata contains a known `bridge_message_id` or reply correlation.
3. Whether the observed sender identity is the bridge's own MeshCore identity.
4. Whether a pending bridge record links this source/destination pair.
5. Whether a content HMAC fallback matches.

If any loop rule matches, audit `bridge.message.ignored` with
`reason=loop_prevention` and do not forward back to Telegram.

The fallback content HMAC is the last resort and is intentionally narrow. It may
match only when all of these are true:

- The direction is the reverse of a pending bridge send.
- The destination room equals the room that originally received the bridge send.
- The byte size matches the pending record.
- The record is inside a short temporal window.
- A pending send record exists.
- The pending record is consumed once and then removed.

This fallback must not block all identical text globally. Legitimate repeated
messages such as `ok` twice from Telegram, `ok` twice from MeshCore, the same
text from two users, or the same text after restart must be allowed unless they
match a concrete pending bridge echo.

The bridge deduplication window should be short and based on expiration
timestamps, not fixed time buckets. It must not deduplicate across different
transports, rooms, or senders unless a bridge record explicitly links the
messages.

## Delivery States

Forwarding records use explicit delivery states:

- `accepted_by_telegram`: Telegram accepted the API call.
- `accepted_by_meshcore_transport`: Home Assistant or the MeshCore transport
  accepted the send request.
- `observed_echo`: the bridge observed a probable echo of its own forwarded
  message and consumed the pending record.
- `failed`: the destination transport returned an error or timed out.
- `dropped`: the bridge intentionally skipped forwarding due to policy,
  deduplication, shutdown, or backpressure.

`accepted_by_meshcore_transport` does not prove LoRa delivery to the final
recipient. It only means the local MeshCore integration or transport accepted
the send request.

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

- Import the bot token from the protected one-time App option
  `bot_token_import`.
- Write the token to `bot_token_file` with an atomic create/replace sequence.
- File must be regular, owned by the expected runtime user where portable,
  mode `0600`, and not a symlink.
- Never log token contents or token file contents.
- Rotation is done by setting a new `bot_token_import`, restarting, verifying
  the new token was written, and clearing the import field if the Supervisor UI
  still shows it.

Startup:

- Do not process old Telegram updates on startup. Use long polling offset
  handling to start after the latest update unless the operator explicitly opts
  into replay.
- Webhooks are out of scope for v1.
- If `deleteWebhook` fails or a webhook remains active, fail closed to avoid two
  consumers processing the same bot.

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
No response; safe warning in logs only.
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

## Installation And Enrollment UX

Operator flow:

1. Create a Telegram bot with BotFather.
2. Open a private chat with the bot and press Start or send a first message.
3. Obtain `chat_id` and `user_id` using a safe enrollment mode or diagnostic.
4. Configure `allowed_private_chat_id` and `allowed_user_id`.
5. Paste the token into `bot_token_import`.
6. Enable Telegram.
7. Restart the App.
8. Verify that `bot_token_file` exists and the import field is cleared or
   manually clear it in the UI.
9. Send normal Telegram text and confirm it appears on MeshCore.
10. Send normal MeshCore text and confirm it appears in Telegram.
11. Send `!ping` in Telegram and confirm the response is only in Telegram.

Safe enrollment mode:

- Disabled by default.
- Requires Telegram to be enabled and a token already imported.
- Listens only to private chat messages.
- Prints or displays only redacted diagnostics by default.
- Shows `chat_id` and `user_id` only behind an explicit diagnostic command or UI
  action that warns not to share the output.
- Does not print the bot token, raw update payloads, usernames, names, chat
  titles, or message text.
- Does not authorize the discovered IDs automatically.

CLI/App diagnostic alternative:

```text
telegram-diagnose enroll --seconds 60
```

Expected output shape:

```text
Telegram enrollment candidate
chat_id: <numeric-id>
user_id: <numeric-id>
chat_type: private
message_text: redacted
```

The diagnostic must avoid dumping full Telegram updates. It should fail closed
if the token file permissions are unsafe.

## Planned Tests

- Authorized private chat.
- Unauthorized private chat.
- Authorized user.
- Unauthorized user.
- Group ignored in v1.
- Supergroup ignored in v1.
- Channel ignored in v1.
- Bot message ignored.
- Edited message ignored.
- Text command executes locally.
- Normal text forwards to MeshCore.
- Unknown command replies in Telegram and does not forward.
- Unsupported media ignored or receives a safe response.
- Telegram to MeshCore loop prevention.
- MeshCore to Telegram loop prevention.
- Legitimate `ok` twice from Telegram is not blocked by loop prevention.
- Legitimate `ok` twice from MeshCore is not blocked by loop prevention.
- Real bridge echo is blocked and consumes one pending bridge record.
- Same text from two different users is not blocked.
- Same text after restart is not blocked unless a durable pending record
  explicitly matches.
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
- First activation drops pending updates and does not process old history.
- Offset persists only after an update is processed or intentionally ignored.
- Crash before offset persistence is made safe by deduplication.
- Crash after offset persistence records failed or dropped forwarding state.
- Token import writes a regular `0600` file and does not expose the token.
- Token rotation replaces the file only through an explicit import.

## Relationship To Configurable Commands

The design may mention `!exterior` as an example of a future or optional
readonly command. Telegram bridge v1 must not depend on that command existing.
The bridge executes whatever readonly commands are registered in the command
registry at runtime, currently including the commands available on `main`.

## Out Of Scope

- Multimedia forwarding.
- Multiple Telegram bots.
- Multiple MeshCore channels.
- Telegram webhooks.
- Telegram groups, supergroups, and channels.
- Write commands.
- Edited or deleted message synchronization.
- Historical synchronization.
- USB transport work.
- Release publishing.
