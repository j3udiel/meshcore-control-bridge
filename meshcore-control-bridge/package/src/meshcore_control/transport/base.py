from __future__ import annotations

from typing import Protocol

from meshcore_control.models import InboundMessage, OutboundMessage


class Transport(Protocol):
    name: str

    async def receive(self) -> InboundMessage:
        raise NotImplementedError

    async def send(self, message: OutboundMessage) -> None:
        raise NotImplementedError

    async def close(self) -> None:
        raise NotImplementedError
