from __future__ import annotations

from typing import Any

from meshcore_control.protocol.constants import (
    PACKET_CHANNEL_INFO,
    PACKET_CHANNEL_MSG_RECV,
    PACKET_CHANNEL_MSG_RECV_V3,
    PACKET_CONTACT_MSG_RECV,
    PACKET_CONTACT_MSG_RECV_V3,
    PACKET_DEVICE_INFO,
    PACKET_SELF_INFO,
)
from meshcore_control.protocol.messages import (
    ChannelInfo,
    ChannelTextMessage,
    ContactTextMessage,
    DeviceInfo,
    SelfInfo,
)


class DecodeError(ValueError):
    """Raised when a Companion Protocol packet cannot be decoded safely."""


def packet_type(payload: bytes) -> int:
    if not payload:
        raise DecodeError("empty Companion packet")
    return payload[0]


def decode_packet(payload: bytes) -> object:
    match packet_type(payload):
        case value if value == PACKET_SELF_INFO:
            return decode_self_info(payload)
        case value if value == PACKET_DEVICE_INFO:
            return decode_device_info(payload)
        case value if value == PACKET_CHANNEL_INFO:
            return decode_channel_info(payload)
        case value if value in {PACKET_CHANNEL_MSG_RECV, PACKET_CHANNEL_MSG_RECV_V3}:
            return decode_channel_text_message(payload)
        case value if value in {PACKET_CONTACT_MSG_RECV, PACKET_CONTACT_MSG_RECV_V3}:
            return decode_contact_text_message(payload)
        case _:
            return {"packet_type": payload[0], "raw_length": len(payload)}


def decode_self_info(payload: bytes) -> SelfInfo:
    if len(payload) < 36:
        raise DecodeError("SELF_INFO packet too short")
    public_key = payload[4:36].hex()
    metadata: dict[str, Any] = {}
    name: str | None = None
    if len(payload) >= 58:
        metadata = {
            "adv_type": payload[1],
            "tx_power": payload[2],
            "max_tx_power": payload[3],
            "radio_freq": int.from_bytes(payload[48:52], "little") / 1000.0,
            "radio_bw": int.from_bytes(payload[52:56], "little") / 1000.0,
            "radio_sf": payload[56],
            "radio_cr": payload[57],
        }
        name = _decode_text(payload[58:])
    return SelfInfo(
        public_key=public_key,
        public_key_short=_short_id(public_key),
        name=name,
        model_metadata=metadata,
    )


def decode_device_info(payload: bytes) -> DeviceInfo:
    if len(payload) < 2:
        raise DecodeError("DEVICE_INFO packet too short")
    firmware_version = payload[1]
    if firmware_version >= 3 and len(payload) >= 80:
        return DeviceInfo(
            firmware_version=firmware_version,
            max_contacts=payload[2] * 2,
            max_channels=payload[3],
            firmware_build=_decode_text(payload[8:20]),
            model=_decode_text(payload[20:60]),
            version=_decode_text(payload[60:80]),
        )
    return DeviceInfo(
        firmware_version=firmware_version,
        max_contacts=None,
        max_channels=None,
        firmware_build=None,
        model=None,
        version=None,
    )


def decode_channel_info(payload: bytes) -> ChannelInfo:
    if len(payload) < 50:
        raise DecodeError("CHANNEL_INFO packet too short")
    name = _decode_text(payload[2:34])
    secret = payload[34:50]
    return ChannelInfo(
        channel_index=payload[1],
        name=name,
        configured=bool(name) or any(secret),
        secret_redacted="redacted",
    )


def decode_channel_text_message(payload: bytes) -> ChannelTextMessage:
    kind = packet_type(payload)
    offset = 1
    snr: float | None = None
    if kind == PACKET_CHANNEL_MSG_RECV_V3:
        if len(payload) < 11:
            raise DecodeError("CHANNEL_MSG_RECV_V3 packet too short")
        snr = _snr(payload[offset])
        offset += 3
    elif len(payload) < 8:
        raise DecodeError("CHANNEL_MSG_RECV packet too short")

    channel_index = payload[offset]
    path_len = payload[offset + 1]
    text_type = payload[offset + 2]
    timestamp = int.from_bytes(payload[offset + 3 : offset + 7], "little")
    text = _decode_text(payload[offset + 7 :])
    return ChannelTextMessage(
        channel_index=channel_index,
        text=text,
        timestamp=timestamp,
        path_len=path_len,
        text_type=text_type,
        snr=snr,
    )


def decode_contact_text_message(payload: bytes) -> ContactTextMessage:
    kind = packet_type(payload)
    offset = 1
    snr: float | None = None
    if kind == PACKET_CONTACT_MSG_RECV_V3:
        if len(payload) < 20:
            raise DecodeError("CONTACT_MSG_RECV_V3 packet too short")
        snr = _snr(payload[offset])
        offset += 3
    elif len(payload) < 13:
        raise DecodeError("CONTACT_MSG_RECV packet too short")

    sender_id = payload[offset : offset + 6].hex()
    offset += 6
    path_len = payload[offset]
    text_type = payload[offset + 1]
    offset += 2
    timestamp = int.from_bytes(payload[offset : offset + 4], "little")
    offset += 4
    if text_type == 2:
        offset += 4
    text = _decode_text(payload[offset:])
    return ContactTextMessage(
        sender_id=sender_id,
        text=text,
        timestamp=timestamp,
        path_len=path_len,
        text_type=text_type,
        snr=snr,
    )


def _decode_text(payload: bytes) -> str:
    return payload.decode("utf-8", errors="replace").rstrip("\x00").strip()


def _snr(raw: int) -> float:
    signed = raw if raw < 128 else raw - 256
    return signed / 4.0


def _short_id(value: str) -> str:
    return f"{value[:8]}...{value[-4:]}" if len(value) > 12 else value
