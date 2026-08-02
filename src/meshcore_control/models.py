from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any

NORMALIZED_MESSAGE_SCHEMA_VERSION = 1
MAX_METADATA_BYTES = 8192
ROOM_KINDS = frozenset({"meshcore_channel", "telegram_chat", "direct", "local"})
IDENTITY_KINDS = frozenset(
    {
        "meshcore_pubkey_prefix",
        "meshcore_node_id",
        "telegram_user_id",
        "telegram_sender_chat_id",
        "synthetic_test",
        "unknown",
    }
)


@dataclass(frozen=True, slots=True)
class RoomRef:
    transport: str
    room_id: str
    room_kind: str
    display_name: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _validate_schema_text(self.transport, "room transport")
        _validate_schema_text(self.room_id, "room_id")
        _validate_kind(self.room_kind, ROOM_KINDS, "room_kind")
        object.__setattr__(self, "metadata", _copy_metadata(self.metadata))

    @classmethod
    def channel(cls, *, transport: str, channel_index: int) -> RoomRef:
        return cls(
            transport=transport,
            room_id=f"{transport}:channel:{channel_index}",
            room_kind="meshcore_channel",
            metadata={"channel_index": channel_index},
        )


@dataclass(frozen=True, slots=True)
class SenderIdentity:
    sender_id: str
    transport_scope: str
    identity_kind: str
    stable: bool
    display_name: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _validate_schema_text(self.sender_id, "sender_id")
        _validate_schema_text(self.transport_scope, "sender transport_scope")
        _validate_kind(self.identity_kind, IDENTITY_KINDS, "sender identity_kind")
        object.__setattr__(self, "metadata", _copy_metadata(self.metadata))

    @classmethod
    def from_sender_id(cls, *, sender_id: str, transport_scope: str) -> SenderIdentity:
        identity_kind = "unknown"
        stable = bool(sender_id and not sender_id.endswith(":unknown"))
        if sender_id.startswith("meshcore-pubkey-prefix:"):
            identity_kind = "meshcore_pubkey_prefix"
        elif sender_id.startswith("telegram-user:"):
            identity_kind = "telegram_user_id"
        elif sender_id.startswith("test:"):
            identity_kind = "synthetic_test"
            stable = False
        return cls(
            sender_id=sender_id,
            transport_scope=transport_scope,
            identity_kind=identity_kind,
            stable=stable,
        )


@dataclass(frozen=True, slots=True)
class MessageOrigin:
    transport: str
    room_id: str
    message_id: str | None
    bridge_instance_id: str | None = None

    def __post_init__(self) -> None:
        _validate_schema_text(self.transport, "origin transport")
        _validate_schema_text(self.room_id, "origin room_id")


@dataclass(frozen=True, slots=True)
class MessageIdentity:
    message_id: str | None
    id_kind: str
    correlation_id: str
    origin: MessageOrigin

    def __post_init__(self) -> None:
        _validate_schema_text(self.id_kind, "message id_kind")
        _validate_schema_text(self.correlation_id, "correlation_id")
        if self.id_kind not in {"platform", "derived", "missing"}:
            raise ValueError("message id_kind must be one of: platform, derived, missing")

    @classmethod
    def from_message_id(
        cls,
        *,
        transport: str,
        room_id: str,
        message_id: str | None,
        id_kind: str | None = None,
    ) -> MessageIdentity:
        resolved_id_kind = id_kind or ("platform" if message_id else "missing")
        return cls(
            message_id=message_id,
            id_kind=resolved_id_kind,
            correlation_id=f"corr:{uuid.uuid4().hex}",
            origin=MessageOrigin(
                transport=transport,
                room_id=room_id,
                message_id=message_id,
            ),
        )


@dataclass(frozen=True, slots=True)
class InboundMessage:
    transport: str
    message_id: str | None
    sender_id: str
    channel_index: int
    text: str
    received_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    metadata: Mapping[str, Any] = field(default_factory=dict)
    schema_version: int = NORMALIZED_MESSAGE_SCHEMA_VERSION
    source_room: RoomRef | None = None
    reply_target: RoomRef | None = None
    sender: SenderIdentity | None = None
    message: MessageIdentity | None = None

    def __post_init__(self) -> None:
        _validate_schema_version(self.schema_version)
        _validate_schema_text(self.transport, "transport")
        _validate_schema_text(self.sender_id, "sender_id")
        object.__setattr__(self, "metadata", _copy_metadata(self.metadata))
        source_room = self.source_room or RoomRef.channel(
            transport=self.transport,
            channel_index=self.channel_index,
        )
        reply_target = self.reply_target or source_room
        sender = self.sender or SenderIdentity.from_sender_id(
            sender_id=self.sender_id,
            transport_scope=self.transport,
        )
        message = self.message or MessageIdentity.from_message_id(
            transport=source_room.transport,
            room_id=source_room.room_id,
            message_id=self.message_id,
        )
        _validate_transport_consistency(
            envelope_transport=self.transport,
            source_room=source_room,
            reply_target=reply_target,
            sender=sender,
        )
        object.__setattr__(self, "source_room", source_room)
        object.__setattr__(self, "reply_target", reply_target)
        object.__setattr__(self, "sender", sender)
        object.__setattr__(self, "message", message)


@dataclass(frozen=True, slots=True)
class OutboundMessage:
    destination: str
    channel_index: int
    text: str
    reply_to: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    schema_version: int = NORMALIZED_MESSAGE_SCHEMA_VERSION
    reply_target: RoomRef | None = None

    def __post_init__(self) -> None:
        _validate_schema_version(self.schema_version)
        _validate_schema_text(self.destination, "destination")
        object.__setattr__(self, "metadata", _copy_metadata(self.metadata))


def _validate_transport_consistency(
    *,
    envelope_transport: str,
    source_room: RoomRef,
    reply_target: RoomRef,
    sender: SenderIdentity,
) -> None:
    if source_room.transport != envelope_transport:
        raise ValueError("source_room.transport must match message transport")
    if reply_target.transport != source_room.transport:
        raise ValueError("reply_target.transport must match source_room.transport")
    if sender.transport_scope != source_room.transport:
        raise ValueError("sender.transport_scope must match source_room.transport")


def _validate_schema_version(schema_version: int) -> None:
    if schema_version != NORMALIZED_MESSAGE_SCHEMA_VERSION:
        raise ValueError(f"unsupported message schema_version: {schema_version}")


def _validate_schema_text(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")


def _validate_kind(value: str, allowed: frozenset[str], name: str) -> None:
    _validate_schema_text(value, name)
    if value in allowed or value.startswith("custom:"):
        return
    raise ValueError(f"{name} is not supported: {value}")


def _copy_metadata(metadata: Mapping[str, Any]) -> Mapping[str, Any]:
    if not isinstance(metadata, Mapping):
        raise ValueError("metadata must be a mapping")
    try:
        encoded = json.dumps(metadata, ensure_ascii=True, sort_keys=True).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("metadata must be JSON-serializable") from exc
    if len(encoded) > MAX_METADATA_BYTES:
        raise ValueError("metadata exceeds maximum size")
    copied = json.loads(encoded.decode("utf-8"))
    if not isinstance(copied, dict):
        raise ValueError("metadata must be a mapping")
    return MappingProxyType(copied)
