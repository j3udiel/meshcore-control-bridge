from __future__ import annotations

import argparse
import asyncio

from meshcore_control.adapters.homeassistant import HomeAssistantClient
from meshcore_control.app import BridgeService
from meshcore_control.auth.authorization import Authorizer
from meshcore_control.commands.router import CommandRouter
from meshcore_control.config import AppConfig, load_config
from meshcore_control.logging import configure_logging
from meshcore_control.plugins import build_registry
from meshcore_control.security.deduplication import Deduplicator
from meshcore_control.storage.database import connect_database
from meshcore_control.storage.repositories import AuditRepository
from meshcore_control.transport.meshcore import MeshCoreTransport


def build_service(config: AppConfig) -> BridgeService:
    connection = connect_database(config.database_path)
    registry = build_registry()
    services: dict[str, object] = {"registry": registry}
    if config.homeassistant.base_url and config.homeassistant.token:
        services["homeassistant"] = HomeAssistantClient(
            base_url=config.homeassistant.base_url,
            token=config.homeassistant.token,
            verify_tls=config.homeassistant.verify_tls,
            timeout_seconds=config.homeassistant.timeout_seconds,
            entity_aliases=config.entities.get("all", {}),
        )
    router = CommandRouter(
        registry=registry,
        authorizer=Authorizer(config.users),
        audit=AuditRepository(connection),
        services=services,
        prefix=config.command_prefix,
    )
    return BridgeService(
        transport=MeshCoreTransport(channel_index=config.meshcore.channel_index),
        router=router,
        deduplicator=Deduplicator(
            connection, window_seconds=config.deduplication_window_seconds
        ),
        channel_index=config.meshcore.channel_index,
    )


async def amain() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    configure_logging(args.log_level)
    config = load_config(args.config)
    service = build_service(config)
    await service.run_forever()


def main() -> None:
    asyncio.run(amain())


if __name__ == "__main__":
    main()
