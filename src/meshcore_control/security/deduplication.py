from __future__ import annotations

import hashlib
import sqlite3
import time
from collections.abc import Callable

from meshcore_control.models import InboundMessage
from meshcore_control.storage.database import write_transaction


class Deduplicator:
    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        window_seconds: int,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.connection = connection
        self.window_seconds = window_seconds
        self.clock = clock or time.time

    def seen_or_store(self, message: InboundMessage) -> bool:
        now = self.clock()
        key = self._key(message)
        keys = (key, *self._legacy_keys(message))

        def store_key() -> bool:
            self.connection.execute("DELETE FROM deduplication_keys WHERE expires_at < ?", (now,))
            for candidate in keys:
                row = self.connection.execute(
                    "SELECT dedup_key FROM deduplication_keys WHERE dedup_key = ?",
                    (candidate,),
                ).fetchone()
                if row is not None:
                    return True
            self.connection.execute(
                "INSERT INTO deduplication_keys (dedup_key, expires_at) VALUES (?, ?)",
                (key, now + self.window_seconds),
            )
            return False

        return write_transaction(self.connection, store_key)

    def _key(self, message: InboundMessage) -> str:
        source_room = message.source_room
        sender = message.sender
        message_identity = message.message
        source_transport = source_room.transport if source_room is not None else message.transport
        room_id = (
            source_room.room_id
            if source_room is not None
            else f"{message.transport}:channel:{message.channel_index}"
        )
        sender_id = sender.sender_id if sender is not None else message.sender_id
        platform_message_id = (
            message_identity.message_id if message_identity is not None else message.message_id
        )
        if platform_message_id:
            material = (
                f"id:v2:{source_transport}:{room_id}:"
                f"{_hash_private(sender_id)}:{platform_message_id}"
            )
        else:
            material = (
                f"hash:v2:{source_transport}:{room_id}:"
                f"{_hash_private(sender_id)}:{_hash_private(_normalize_text(message.text))}"
            )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    def _legacy_keys(self, message: InboundMessage) -> tuple[str, ...]:
        if message.message_id:
            material = (
                f"id:{message.transport}:{message.sender_id}:"
                f"{message.channel_index}:{message.message_id}"
            )
        else:
            bucket = int(message.received_at.timestamp() // self.window_seconds)
            material = (
                f"hash:{message.transport}:{message.sender_id}:{message.channel_index}:"
                f"{bucket}:{message.text.strip()}"
            )
        return (hashlib.sha256(material.encode("utf-8")).hexdigest(),)


def _normalize_text(text: str) -> str:
    return " ".join(text.strip().split())


def _hash_private(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
