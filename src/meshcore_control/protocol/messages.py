from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Literal


@dataclass(frozen=True, slots=True)
class SelfInfo:
    public_key: str
    public_key_short: str
    name: str | None
    model_metadata: dict[str, Any]


@dataclass(frozen=True, slots=True)
class DeviceInfo:
    firmware_version: int
    max_contacts: int | None
    max_channels: int | None
    firmware_build: str | None
    model: str | None
    version: str | None


@dataclass(frozen=True, slots=True)
class ChannelInfo:
    channel_index: int
    name: str
    configured: bool
    secret_redacted: Literal["redacted"]


@dataclass(frozen=True, slots=True)
class ChannelTextMessage:
    channel_index: int
    text: str
    timestamp: int
    path_len: int
    text_type: int
    snr: float | None = None

    @property
    def synthetic_sender_id(self) -> str:
        return f"channel:{self.channel_index}:unknown"

    @property
    def synthetic_message_id(self) -> str:
        digest = hashlib.sha256(self.text.encode("utf-8")).hexdigest()[:16]
        return f"meshcore:{self.channel_index}:{self.timestamp}:{digest}"


@dataclass(frozen=True, slots=True)
class ContactTextMessage:
    sender_id: str
    text: str
    timestamp: int
    path_len: int
    text_type: int
    snr: float | None = None
