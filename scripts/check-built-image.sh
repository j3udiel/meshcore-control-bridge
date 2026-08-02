#!/usr/bin/env bash
set -euo pipefail

image="${1:-meshcore-control-bridge-ha-app:test}"

docker run --rm --entrypoint /bin/sh "${image}" -lc 'python3 - <<'"'"'PY'"'"'
import asyncio
import inspect
import sqlite3

import meshcore_control
from meshcore_control.adapters.homeassistant import HomeAssistantStatus
from meshcore_control.app import BridgeService
from meshcore_control.auth.authorization import AuthorizedUser, Authorizer
from meshcore_control.auth.roles import Role
from meshcore_control.commands.router import CommandRouter
from meshcore_control.homeassistant_app import unidentified_testing_sender_id
from meshcore_control.models import InboundMessage, OutboundMessage
from meshcore_control.plugins import build_registry
from meshcore_control.security.deduplication import Deduplicator
from meshcore_control.security.rate_limit import RateLimiter
from meshcore_control.storage.database import connect_database
from meshcore_control.storage.repositories import AuditRepository
import meshcore_control.adapters.homeassistant_ws as ws
import meshcore_control.commands.router as router
import meshcore_control.homeassistant_app_health as health

assert meshcore_control.__version__ == "0.1.5"
assert unidentified_testing_sender_id(1) == "test:unidentified:channel:1"
assert "authorization=" in inspect.getsource(router)
assert "on_idle" in inspect.getsource(ws)
assert "healthcheck is stale" in inspect.getsource(health)


class Transport:
    def __init__(self) -> None:
        self.sent = []

    async def receive(self):
        raise NotImplementedError

    async def send(self, message: OutboundMessage) -> None:
        self.sent.append(message)


class HA:
    async def check_available(self) -> HomeAssistantStatus:
        return HomeAssistantStatus(available=True, message="OK")

    async def get_config(self):
        return {"version": "2026.8.0", "location_name": "Home"}


async def main() -> None:
    sender = unidentified_testing_sender_id(1)
    connection = connect_database(":memory:")
    registry = build_registry()
    transport = Transport()
    router_instance = CommandRouter(
        registry=registry,
        authorizer=Authorizer({sender: AuthorizedUser(sender, "test", Role.readonly)}),
        audit=AuditRepository(connection),
        services={"registry": registry, "homeassistant": HA()},
        prefix="!",
    )
    service = BridgeService(
        transport=transport,
        router=router_instance,
        deduplicator=Deduplicator(connection, window_seconds=300),
        rate_limiter=RateLimiter(max_commands=5, window_seconds=60),
        channel_index=1,
    )
    outbound = await service.process_message(
        InboundMessage(
            transport="homeassistant-meshcore",
            message_id="artifact-1",
            sender_id=sender,
            channel_index=1,
            text="!ping",
        )
    )
    assert outbound is not None
    assert outbound.text == "pong"
    assert transport.sent[-1].channel_index == 1


asyncio.run(main())
print("built-image-ok")
PY'
