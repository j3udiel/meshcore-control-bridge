# MeshCore Transport

The real MeshCore transport is not implemented yet.

## Known State

- A transport interface exists.
- `FakeTransport` works and is used by tests.
- `MeshCoreTransport` is a placeholder that raises `NotImplementedError`.
- USB serial, BLE, or TCP access to the Companion has not been confirmed.
- No MeshCore API is assumed or invented.

## Required Inbound Data

A real transport must produce:

- `sender_id`;
- `channel_index`;
- `message_id`, when available;
- `text`;
- `metadata`;
- receive timestamp.

## Required Outbound Data

A real transport must be able to send:

- destination or reply target;
- channel index;
- text;
- reply correlation, when supported;
- metadata required by the Companion.

## Implementation Tasks

1. Identify whether the Companion exposes USB serial, BLE, TCP, or another API.
2. Find official protocol documentation or a maintained compatible library.
3. Confirm how to enumerate channels without exposing channel secrets.
4. Confirm how received messages expose sender identity and channel index.
5. Confirm whether a stable message ID exists.
6. Confirm how to send a reply on a private channel.
7. Update `meshcore-diagnose` to validate the real connection.
8. Implement `MeshCoreTransport` behind the existing interface.
9. Add tests with recorded, sanitized fixtures.
10. Keep the public channel disabled for administrative commands.
