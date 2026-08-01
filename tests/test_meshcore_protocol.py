from __future__ import annotations

import pytest

from meshcore_control.models import OutboundMessage
from meshcore_control.protocol.constants import USB_APP_TO_RADIO_PREFIX, USB_RADIO_TO_APP_PREFIX
from meshcore_control.protocol.decoder import (
    decode_channel_info,
    decode_channel_text_message,
    decode_contact_text_message,
    decode_device_info,
    decode_self_info,
)
from meshcore_control.protocol.encoder import encode_send_channel_message
from meshcore_control.protocol.framing import FrameLengthError, UsbFrameParser, encode_usb_frame
from meshcore_control.transport.meshcore_usb import MeshCoreUsbSession, MeshCoreUsbSettings


def test_usb_frame_encode_uses_app_to_radio_prefix() -> None:
    frame = encode_usb_frame(b"\x0a")

    assert frame == bytes([USB_APP_TO_RADIO_PREFIX, 1, 0, 0x0A])


def test_usb_frame_parser_handles_fragmented_frame() -> None:
    parser = UsbFrameParser()
    frame = bytes([USB_RADIO_TO_APP_PREFIX, 3, 0, 1, 2, 3])

    assert parser.feed(frame[:2]) == []
    assert parser.feed(frame[2:]) == [b"\x01\x02\x03"]


def test_usb_frame_parser_handles_concatenated_frames() -> None:
    parser = UsbFrameParser()
    stream = encode_usb_frame(b"a", radio_to_app=True) + encode_usb_frame(b"bc", radio_to_app=True)

    assert parser.feed(stream) == [b"a", b"bc"]


def test_usb_frame_parser_recovers_after_garbage() -> None:
    parser = UsbFrameParser()
    stream = b"garbage" + encode_usb_frame(b"ok", radio_to_app=True)

    assert parser.feed(stream) == [b"ok"]


def test_usb_frame_parser_rejects_invalid_length() -> None:
    parser = UsbFrameParser(max_payload_size=4)
    with pytest.raises(FrameLengthError):
        parser.feed(bytes([USB_RADIO_TO_APP_PREFIX, 5, 0]))


def test_decode_channel_message_v3() -> None:
    payload = b"\x11\x08\x00\x00\x01\xff\x00" + (123).to_bytes(4, "little") + b"!ping"

    message = decode_channel_text_message(payload)

    assert message.channel_index == 1
    assert message.snr == 2.0
    assert message.text == "!ping"
    assert message.synthetic_sender_id == "channel:1:unknown"


def test_decode_contact_message_exposes_prefix_sender() -> None:
    payload = (
        b"\x07"
        + bytes.fromhex("001122334455")
        + b"\xff\x00"
        + (123).to_bytes(4, "little")
        + b"!ping"
    )

    message = decode_contact_text_message(payload)

    assert message.sender_id == "001122334455"
    assert message.text == "!ping"


def test_decode_channel_info_redacts_secret() -> None:
    payload = b"\x12\x01" + b"private-control-channel".ljust(32, b"\x00") + (b"s" * 16)

    channel = decode_channel_info(payload)

    assert channel.channel_index == 1
    assert channel.configured is True
    assert channel.secret_redacted == "redacted"


def test_decode_device_info() -> None:
    payload = (
        b"\x0d\x03\x10\x08"
        + (123456).to_bytes(4, "little")
        + b"2026-01-01\x00\x00"
        + b"Companion".ljust(40, b"\x00")
        + b"1.12.0".ljust(20, b"\x00")
    )

    info = decode_device_info(payload)

    assert info.firmware_version == 3
    assert info.max_contacts == 32
    assert info.max_channels == 8
    assert info.version == "1.12.0"


def test_decode_self_info_redacts_to_short_key() -> None:
    public_key = bytes(range(32))
    payload = (
        b"\x05\x00\x01\x02"
        + public_key
        + bytes(22)
        + b"admin-device"
    )

    info = decode_self_info(payload)

    assert info.public_key == public_key.hex()
    assert info.public_key_short.startswith("00010203")
    assert info.name == "admin-device"


def test_send_channel_message_limit() -> None:
    with pytest.raises(ValueError, match="exceeds"):
        encode_send_channel_message(channel_index=1, text="x" * 134)


class FakeSerial:
    def __init__(self) -> None:
        self.writes: list[bytes] = []
        self.reads: list[bytes] = []
        self.closed = False

    def read(self, size: int) -> bytes:
        if self.reads:
            return self.reads.pop(0)
        return b""

    def write(self, data: bytes) -> int:
        self.writes.append(data)
        return len(data)

    def close(self) -> None:
        self.closed = True


def test_usb_session_sends_response_frame() -> None:
    serial = FakeSerial()
    serial.reads.append(encode_usb_frame(b"\x06\x01abcd\x00\x00\x00\x00", radio_to_app=True))
    session = MeshCoreUsbSession(
        MeshCoreUsbSettings(port="/dev/ttyUSB-test", channel_index=1),
        serial_factory=lambda **_kwargs: serial,
    )
    session._serial = serial

    import asyncio

    asyncio.run(session.send_channel_message(OutboundMessage("sender", 1, "pong")))

    assert serial.writes
    assert serial.writes[0][0] == USB_APP_TO_RADIO_PREFIX
