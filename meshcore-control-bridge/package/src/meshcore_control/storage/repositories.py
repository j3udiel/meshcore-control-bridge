from __future__ import annotations

import hashlib
import json
import sqlite3

from meshcore_control.models import InboundMessage


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class AuditRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def record_inbound_message(self, message: InboundMessage) -> None:
        self.connection.execute(
            """
            INSERT INTO inbound_messages (
              transport, message_id, sender_id, channel_index, text_hash, received_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                message.transport,
                message.message_id,
                message.sender_id,
                message.channel_index,
                _text_hash(message.text),
                message.received_at.isoformat(),
            ),
        )
        self.connection.commit()

    def record_command(
        self,
        *,
        message: InboundMessage,
        command: str,
        args: list[str],
        result: str,
        duration_ms: int,
        error: str | None,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO command_executions (
              message_id, sender_id, command, args_json, result, duration_ms, error
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                message.message_id,
                message.sender_id,
                command,
                json.dumps(args, ensure_ascii=True),
                result,
                duration_ms,
                error,
            ),
        )
        self.connection.commit()

    def count_commands(self) -> int:
        row = self.connection.execute("SELECT COUNT(*) AS count FROM command_executions").fetchone()
        return int(row["count"])
