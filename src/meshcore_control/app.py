from __future__ import annotations

from meshcore_control.commands.router import CommandRouter
from meshcore_control.models import InboundMessage, OutboundMessage
from meshcore_control.security.deduplication import Deduplicator
from meshcore_control.transport.base import Transport


class BridgeService:
    def __init__(
        self,
        *,
        transport: Transport,
        router: CommandRouter,
        deduplicator: Deduplicator,
        channel_index: int,
    ) -> None:
        self.transport = transport
        self.router = router
        self.deduplicator = deduplicator
        self.channel_index = channel_index

    async def process_message(self, message: InboundMessage) -> OutboundMessage | None:
        if message.channel_index != self.channel_index:
            return None
        if self.deduplicator.seen_or_store(message):
            return None
        response_text = await self.router.handle(message)
        if response_text is None:
            return None
        outbound = OutboundMessage(
            destination=message.sender_id,
            channel_index=self.channel_index,
            text=_trim_lora_response(response_text),
            reply_to=message.message_id,
        )
        await self.transport.send(outbound)
        return outbound

    async def run_forever(self) -> None:
        while True:
            message = await self.transport.receive()
            await self.process_message(message)


def _trim_lora_response(text: str, max_chars: int = 480) -> str:
    normalized = "\n".join(line.rstrip() for line in text.strip().splitlines())
    if len(normalized) <= max_chars:
        return normalized
    return normalized[: max_chars - 18].rstrip() + "\n... pide detalle"
