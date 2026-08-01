from __future__ import annotations

import hashlib
import sqlite3
import time

from meshcore_control.models import InboundMessage


class Deduplicator:
    def __init__(self, connection: sqlite3.Connection, *, window_seconds: int) -> None:
        self.connection = connection
        self.window_seconds = window_seconds

    def seen_or_store(self, message: InboundMessage) -> bool:
        now = time.time()
        self.connection.execute("DELETE FROM deduplication_keys WHERE expires_at < ?", (now,))
        key = self._key(message)
        row = self.connection.execute(
            "SELECT dedup_key FROM deduplication_keys WHERE dedup_key = ?", (key,)
        ).fetchone()
        if row is not None:
            self.connection.commit()
            return True
        self.connection.execute(
            "INSERT INTO deduplication_keys (dedup_key, expires_at) VALUES (?, ?)",
            (key, now + self.window_seconds),
        )
        self.connection.commit()
        return False

    def _key(self, message: InboundMessage) -> str:
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
        return hashlib.sha256(material.encode("utf-8")).hexdigest()
