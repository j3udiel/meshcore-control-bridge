from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class InboundMessage:
    transport: str
    message_id: str | None
    sender_id: str
    channel_index: int
    text: str
    received_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class OutboundMessage:
    destination: str
    channel_index: int
    text: str
    reply_to: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
