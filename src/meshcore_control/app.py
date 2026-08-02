from __future__ import annotations

import logging

from meshcore_control.commands.router import CommandRouter
from meshcore_control.models import InboundMessage, OutboundMessage
from meshcore_control.security.deduplication import Deduplicator
from meshcore_control.security.rate_limit import RateLimiter
from meshcore_control.transport.base import Transport

logger = logging.getLogger(__name__)


class BridgeService:
    def __init__(
        self,
        *,
        transport: Transport,
        router: CommandRouter,
        deduplicator: Deduplicator,
        rate_limiter: RateLimiter | None = None,
        channel_index: int,
    ) -> None:
        self.transport = transport
        self.router = router
        self.deduplicator = deduplicator
        self.rate_limiter = rate_limiter or RateLimiter()
        self.channel_index = channel_index

    async def process_message(self, message: InboundMessage) -> OutboundMessage | None:
        if message.channel_index != self.channel_index:
            logger.info("Message ignored reason=wrong_channel channel=%s", message.channel_index)
            return None
        if not self.router.authorizer.allows_room(message):
            room_id = message.source_room.room_id if message.source_room is not None else "unknown"
            logger.info("Message ignored reason=room_not_allowed room=%s", room_id)
            return None
        if self.deduplicator.seen_or_store(message):
            logger.info("Message ignored reason=duplicate channel=%s", message.channel_index)
            return None
        if not self.rate_limiter.allow(message.sender_id):
            logger.warning("Message rejected reason=rate_limited channel=%s", message.channel_index)
            outbound = OutboundMessage(
                destination=message.sender_id,
                channel_index=self.channel_index,
                text="Rate limit.",
                reply_to=message.message_id,
                reply_target=message.reply_target,
            )
            await self.transport.send(outbound)
            return outbound
        response_text = await self.router.handle(message)
        if response_text is None:
            return None
        outbound = OutboundMessage(
            destination=message.sender_id,
            channel_index=self.channel_index,
            text=_trim_lora_response(response_text),
            reply_to=message.message_id,
            reply_target=message.reply_target,
        )
        await self.transport.send(outbound)
        logger.info("Response sent channel=%s", outbound.channel_index)
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
