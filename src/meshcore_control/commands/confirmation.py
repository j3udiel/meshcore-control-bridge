from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from secrets import token_hex


@dataclass(frozen=True, slots=True)
class PendingConfirmation:
    confirmation_id: str
    sender_id: str
    command: str
    args: tuple[str, ...]
    expires_at: datetime


class ConfirmationStore:
    def __init__(self, ttl_seconds: int = 60) -> None:
        self.ttl_seconds = ttl_seconds
        self._pending: dict[str, PendingConfirmation] = {}

    def create(self, sender_id: str, command: str, args: list[str]) -> PendingConfirmation:
        confirmation = PendingConfirmation(
            confirmation_id=token_hex(2).upper(),
            sender_id=sender_id,
            command=command,
            args=tuple(args),
            expires_at=datetime.now(UTC) + timedelta(seconds=self.ttl_seconds),
        )
        self._pending[confirmation.confirmation_id] = confirmation
        return confirmation
