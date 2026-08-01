from __future__ import annotations

import time

from meshcore_control.protocol.constants import (
    CMD_APP_START,
    CMD_DEVICE_QUERY,
    CMD_GET_BATTERY,
    CMD_GET_CHANNEL,
    CMD_SEND_CHANNEL_MESSAGE,
    CMD_SYNC_NEXT_MESSAGE,
    MAX_CHANNEL_TEXT_BYTES,
)


def encode_app_start(app_name: str = "meshcore-control-bridge") -> bytes:
    return bytes([CMD_APP_START, 0, 0, 0, 0, 0, 0, 0]) + app_name.encode("utf-8")


def encode_device_query(*, app_target_version: int = 0x03) -> bytes:
    return bytes([CMD_DEVICE_QUERY, app_target_version])


def encode_get_channel(channel_index: int) -> bytes:
    _validate_channel_index(channel_index)
    return bytes([CMD_GET_CHANNEL, channel_index])


def encode_sync_next_message() -> bytes:
    return bytes([CMD_SYNC_NEXT_MESSAGE])


def encode_get_battery() -> bytes:
    return bytes([CMD_GET_BATTERY])


def encode_send_channel_message(
    *,
    channel_index: int,
    text: str,
    timestamp: int | None = None,
) -> bytes:
    _validate_channel_index(channel_index)
    encoded = text.encode("utf-8")
    if len(encoded) > MAX_CHANNEL_TEXT_BYTES:
        raise ValueError(f"channel text exceeds {MAX_CHANNEL_TEXT_BYTES} bytes")
    ts = int(time.time()) if timestamp is None else timestamp
    return (
        bytes([CMD_SEND_CHANNEL_MESSAGE, 0, channel_index])
        + ts.to_bytes(4, "little", signed=False)
        + encoded
    )


def _validate_channel_index(channel_index: int) -> None:
    if not 0 <= channel_index <= 7:
        raise ValueError("MeshCore channel index must be between 0 and 7")

