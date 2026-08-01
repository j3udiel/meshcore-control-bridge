from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ServerStatus:
    alias: str
    available: bool
    summary: str


class ServerProvider:
    async def status(self, alias: str) -> ServerStatus:
        raise NotImplementedError
