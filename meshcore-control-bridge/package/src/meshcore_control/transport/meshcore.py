from __future__ import annotations

from meshcore_control.models import InboundMessage, OutboundMessage


class MeshCoreTransport:
    name = "meshcore"

    def __init__(self, *, channel_index: int) -> None:
        self.channel_index = channel_index

    async def receive(self) -> InboundMessage:
        raise NotImplementedError(
            "MeshCore transport is intentionally not implemented until the local "
            "Companion connection model and protocol are confirmed. Use "
            "`meshcore-diagnose` first."
        )

    async def send(self, message: OutboundMessage) -> None:
        raise NotImplementedError(
            "MeshCore transport is intentionally not implemented until the Companion "
            "API is confirmed."
        )

    async def close(self) -> None:
        return None
