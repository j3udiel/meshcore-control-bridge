from __future__ import annotations

import asyncio

from meshcore_control.models import InboundMessage, OutboundMessage


class FakeTransport:
    name = "fake"

    def __init__(self) -> None:
        self.inbound: asyncio.Queue[InboundMessage] = asyncio.Queue()
        self.sent: list[OutboundMessage] = []

    async def receive(self) -> InboundMessage:
        return await self.inbound.get()

    async def send(self, message: OutboundMessage) -> None:
        self.sent.append(message)

    async def close(self) -> None:
        return None

    async def inject(self, message: InboundMessage) -> None:
        await self.inbound.put(message)
