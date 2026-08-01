# MeshCore Transport

The original `MeshCoreTransport` placeholder still raises `NotImplementedError`.
An experimental USB serial transport now exists as `MeshCoreUSBTransport`.

## Known State

- A transport interface exists.
- `FakeTransport` works and is used by tests.
- `MeshCoreTransport` is a placeholder that raises `NotImplementedError`.
- `MeshCoreUSBTransport` implements the documented Companion command frames and
  the USB framing described in the MeshCore wiki.
- The USB path has not been validated against a real Companion yet.
- BLE is documented by MeshCore but not implemented here.
- TCP access to the Companion has not been confirmed.

## Sources Used

- Official MeshCore Companion Protocol: https://docs.meshcore.io/companion_protocol/
- MeshCore wiki USB framing note: https://github.com/meshcore-dev/MeshCore/wiki/Companion-Radio-Protocol

The current official Companion Protocol page says it is still in development and
focuses on BLE. The USB framing note lives in a wiki page marked as moved and
potentially outdated, so hardware validation is mandatory.

## Confirmed USB Framing

From the MeshCore wiki:

- radio to app frames start with ASCII `>` (`0x3e`);
- app to radio frames start with ASCII `<` (`0x3c`);
- both prefixes are followed by a 16-bit little-endian frame length;
- the payload is one Companion Protocol frame.

The parser supports fragmented frames, concatenated frames, invalid-length
rejection, and recovery after unrelated bytes.

## Confirmed Commands Used

- `CMD_APP_START` (`0x01`) returns `PACKET_SELF_INFO` (`0x05`).
- `CMD_DEVICE_QUERY` (`0x16`) returns `PACKET_DEVICE_INFO` (`0x0d`).
- `CMD_GET_CHANNEL` (`0x1f`) returns `PACKET_CHANNEL_INFO` (`0x12`).
- `CMD_SYNC_NEXT_MESSAGE` (`0x0a`) returns queued message packets or
  `PACKET_NO_MORE_MSGS` (`0x0a`).
- `CMD_SEND_CHANNEL_MESSAGE` (`0x03`) returns `PACKET_MSG_SENT` (`0x06`).

## Sender Identity Limitation

The documented channel text frames (`0x08` and `0x11`) contain channel index,
path length, text type, timestamp, and text. They do not contain a full stable
sender public key or node ID.

The transport therefore marks these messages with metadata
`sender_id_available=false` and uses a synthetic sender ID such as
`channel:1:unknown`. This is not sufficient for per-user authorization. Do not
use that synthetic ID as a real security boundary.

## Required Inbound Data

An ideal real transport must produce:

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

1. Test USB serial with a real Companion.
2. Confirm the actual device path and permissions.
3. Confirm whether the device firmware emits sender identity for channel
   messages through another frame or setting.
4. Confirm whether a stable message ID exists beyond timestamp/content.
5. Validate channel enumeration without logging secrets.
6. Validate sending a reply on channel 1.
7. Record sanitized binary fixtures.
8. Keep the public channel disabled for administrative commands.
