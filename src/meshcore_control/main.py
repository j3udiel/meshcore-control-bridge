from __future__ import annotations

import argparse
import asyncio
import logging

from meshcore_control.adapters.homeassistant import HomeAssistantClient
from meshcore_control.app import BridgeService
from meshcore_control.auth.authorization import Authorizer
from meshcore_control.commands.router import CommandRouter
from meshcore_control.config import AppConfig, load_config
from meshcore_control.homeassistant_app import load_homeassistant_app_config
from meshcore_control.logging import configure_logging
from meshcore_control.plugins import build_registry
from meshcore_control.security.deduplication import Deduplicator
from meshcore_control.security.rate_limit import RateLimiter
from meshcore_control.storage.database import connect_database
from meshcore_control.storage.repositories import AuditRepository
from meshcore_control.transport.base import Transport
from meshcore_control.transport.homeassistant_meshcore import (
    HomeAssistantMeshCoreSettings,
    HomeAssistantMeshCoreTransport,
)
from meshcore_control.transport.meshcore import MeshCoreTransport

logger = logging.getLogger(__name__)


def build_service(config: AppConfig) -> BridgeService:
    connection = connect_database(config.database_path)
    registry = build_registry()
    services: dict[str, object] = {"registry": registry, "config": config}
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
        authorizer=Authorizer(config.users, room_policies=config.room_policies),
        audit=AuditRepository(connection),
        services=services,
        prefix=config.command_prefix,
    )
    transport = _build_transport(config)
    return BridgeService(
        transport=transport,
        router=router,
        deduplicator=Deduplicator(
            connection, window_seconds=config.deduplication_window_seconds
        ),
        rate_limiter=RateLimiter(
            max_commands=config.security.rate_limit.commands,
            window_seconds=config.security.rate_limit.window_seconds,
        ),
        channel_index=config.meshcore.channel_index,
    )


def _build_transport(config: AppConfig) -> Transport:
    if config.meshcore.transport == "homeassistant":
        return HomeAssistantMeshCoreTransport(
            settings=HomeAssistantMeshCoreSettings(
                channel_index=config.meshcore.channel_index,
                ha_base_url=config.homeassistant.base_url,
                ha_token=config.homeassistant.token,
                ha_verify_tls=config.homeassistant.verify_tls,
                ha_timeout_seconds=config.homeassistant.timeout_seconds,
                ha_websocket_url=config.homeassistant.websocket_url,
                ha_entry_id=config.meshcore.ha_entry_id,
                event_types=config.meshcore.event_types,
                require_stable_sender=config.meshcore.require_stable_sender,
                allow_channel_without_sender=config.meshcore.allow_channel_without_sender,
                healthcheck_path=config.meshcore.healthcheck_path,
            )
        )
    if config.meshcore.transport == "usb":
        raise NotImplementedError(
            "USB transport is not available in this branch. "
            "Use meshcore.transport=homeassistant or the experimental USB PR."
        )
    return MeshCoreTransport(channel_index=config.meshcore.channel_index)


async def amain() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--log-level", default="INFO")
    parser.add_argument("--home-assistant-app", action="store_true")
    args = parser.parse_args()

    if args.home_assistant_app:
        config, options = load_homeassistant_app_config()
        configure_logging(options.log_level.upper())
        logger.info("Home Assistant App runtime detected")
        if options.allow_unidentified_readonly_testing:
            logger.warning("Unidentified readonly testing enabled")
    else:
        configure_logging(args.log_level)
        config = load_config(args.config)
    service = build_service(config)
    if args.home_assistant_app:
        logger.info("Bridge ready")
    await service.run_forever()


def main() -> None:
    asyncio.run(amain())


if __name__ == "__main__":
    main()
