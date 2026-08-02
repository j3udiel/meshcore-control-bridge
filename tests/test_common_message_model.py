from __future__ import annotations

import pytest

from meshcore_control.models import (
    NORMALIZED_MESSAGE_SCHEMA_VERSION,
    InboundMessage,
    MessageIdentity,
    MessageOrigin,
    OutboundMessage,
    RoomRef,
    SenderIdentity,
)


def test_legacy_inbound_message_gets_normalized_fields() -> None:
    message = InboundMessage(
        transport="fake",
        message_id="msg-1",
        sender_id="sender-1",
        channel_index=1,
        text="!ping",
    )

    assert message.schema_version == NORMALIZED_MESSAGE_SCHEMA_VERSION
    assert message.channel_index == 1
    assert message.source_room is not None
    assert message.source_room.transport == "fake"
    assert message.source_room.room_id == "fake:channel:1"
    assert message.source_room.metadata["channel_index"] == 1
    assert message.reply_target == message.source_room
    assert message.sender is not None
    assert message.sender.sender_id == "sender-1"
    assert message.sender.transport_scope == "fake"
    assert message.message is not None
    assert message.message.message_id == "msg-1"
    assert message.message.origin.room_id == "fake:channel:1"


def test_inbound_message_accepts_explicit_source_room_and_reply_target() -> None:
    source_room = RoomRef(
        transport="telegram",
        room_id="telegram:chat:123",
        room_kind="telegram_chat",
    )
    reply_target = RoomRef(
        transport="telegram",
        room_id="telegram:chat:123:thread:456",
        room_kind="telegram_chat",
    )
    sender = SenderIdentity(
        sender_id="telegram-user:primary-bot:789",
        transport_scope="telegram",
        identity_kind="telegram_user_id",
        stable=True,
    )
    identity = MessageIdentity(
        message_id="42",
        id_kind="platform",
        correlation_id="telegram:42",
        origin=MessageOrigin(
            transport="telegram",
            room_id="telegram:chat:123",
            message_id="42",
        ),
    )

    message = InboundMessage(
        transport="telegram",
        message_id="42",
        sender_id="telegram-user:primary-bot:789",
        channel_index=1,
        text="!help",
        source_room=source_room,
        reply_target=reply_target,
        sender=sender,
        message=identity,
    )

    assert message.source_room == source_room
    assert message.reply_target == reply_target
    assert message.sender == sender
    assert message.message == identity


def test_inbound_message_rejects_unsupported_schema_version() -> None:
    with pytest.raises(ValueError, match="unsupported message schema_version"):
        InboundMessage(
            transport="fake",
            message_id="msg-1",
            sender_id="sender-1",
            channel_index=1,
            text="!ping",
            schema_version=999,
        )


def test_inbound_message_rejects_source_room_transport_mismatch() -> None:
    with pytest.raises(ValueError, match="source_room.transport"):
        InboundMessage(
            transport="fake",
            message_id="msg-1",
            sender_id="sender-1",
            channel_index=1,
            text="!ping",
            source_room=RoomRef(
                transport="telegram",
                room_id="telegram:chat:123",
                room_kind="telegram_chat",
            ),
        )


def test_inbound_message_rejects_reply_target_transport_mismatch() -> None:
    with pytest.raises(ValueError, match="reply_target.transport"):
        InboundMessage(
            transport="fake",
            message_id="msg-1",
            sender_id="sender-1",
            channel_index=1,
            text="!ping",
            reply_target=RoomRef(
                transport="telegram",
                room_id="telegram:chat:123",
                room_kind="telegram_chat",
            ),
        )


def test_inbound_message_rejects_sender_transport_scope_mismatch() -> None:
    with pytest.raises(ValueError, match="sender.transport_scope"):
        InboundMessage(
            transport="fake",
            message_id="msg-1",
            sender_id="sender-1",
            channel_index=1,
            text="!ping",
            sender=SenderIdentity(
                sender_id="sender-1",
                transport_scope="telegram",
                identity_kind="telegram_user_id",
                stable=True,
            ),
        )


def test_outbound_message_gets_reply_target_without_breaking_legacy_fields() -> None:
    outbound = OutboundMessage(
        destination="sender-1",
        channel_index=1,
        text="pong",
        metadata={"transport": "fake"},
    )

    assert outbound.destination == "sender-1"
    assert outbound.channel_index == 1
    assert outbound.reply_target is not None
    assert outbound.reply_target.transport == "fake"
    assert outbound.reply_target.room_id == "fake:channel:1"


def test_metadata_must_be_json_serializable() -> None:
    with pytest.raises(ValueError, match="JSON-serializable"):
        InboundMessage(
            transport="fake",
            message_id="msg-1",
            sender_id="sender-1",
            channel_index=1,
            text="!ping",
            metadata={"bad": object()},
        )
