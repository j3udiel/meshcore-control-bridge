from __future__ import annotations

import argparse
import asyncio
import logging
from contextlib import suppress

from meshcore_control.adapters.homeassistant import HomeAssistantClient
from meshcore_control.adapters.homeassistant_state import HomeAssistantStateReader
from meshcore_control.adapters.homeassistant_ws import HomeAssistantWebSocketClient
from meshcore_control.app import BridgeService
from meshcore_control.auth.authorization import AuthorizedUser, Authorizer, RoomPolicy
from meshcore_control.auth.roles import Role
from meshcore_control.bridge_health import BridgeHealthState
from meshcore_control.commands.router import CommandRouter
from meshcore_control.config import AppConfig, load_config
from meshcore_control.homeassistant_app import load_homeassistant_app_config
from meshcore_control.homeassistant_health_events import HomeAssistantHealthEventPublisher
from meshcore_control.logging import configure_logging, register_redaction_secret
from meshcore_control.plugins import build_registry
from meshcore_control.security.deduplication import Deduplicator
from meshcore_control.security.rate_limit import RateLimiter
from meshcore_control.storage.audit_flow import AuditFlow
from meshcore_control.storage.database import connect_database, telegram_database_path
from meshcore_control.storage.normalized_audit import (
    NormalizedAuditRepository,
    NormalizedAuditSettings,
)
from meshcore_control.storage.repositories import AuditRepository
from meshcore_control.telegram.client import TelegramBotApiClient
from meshcore_control.telegram.database import migrate_telegram_tables
from meshcore_control.telegram.identity import TELEGRAM_ROOM_ID, TELEGRAM_SENDER_ID
from meshcore_control.telegram.service import (
    MeshCoreToTelegramForwarder,
    TelegramFoundationService,
)
from meshcore_control.telegram.store import TelegramStore
from meshcore_control.telegram.token import load_or_import_token
from meshcore_control.transport.base import Transport
from meshcore_control.transport.homeassistant_meshcore import (
    HomeAssistantMeshCoreSettings,
    HomeAssistantMeshCoreTransport,
)
from meshcore_control.transport.meshcore import MeshCoreTransport

logger = logging.getLogger(__name__)


def build_service(
    config: AppConfig,
    *,
    normalized_audit_settings: NormalizedAuditSettings | None = None,
    bridge_health: BridgeHealthState | None = None,
) -> BridgeService:
    connection = connect_database(config.database_path, connection_name="audit")
    registry = build_registry()
    services: dict[str, object] = {"registry": registry, "config": config}
    if bridge_health is not None:
        services["bridge_health"] = bridge_health
    if config.homeassistant.base_url and config.homeassistant.token:
        homeassistant_client = HomeAssistantClient(
            base_url=config.homeassistant.base_url,
            token=config.homeassistant.token,
            verify_tls=config.homeassistant.verify_tls,
            timeout_seconds=config.homeassistant.timeout_seconds,
            entity_aliases=config.entities.get("all", {}),
        )
        services["homeassistant"] = homeassistant_client
        services["home_status_reader"] = HomeAssistantStateReader(homeassistant_client)
    legacy_audit = AuditRepository(connection)
    normalized_settings = normalized_audit_settings or NormalizedAuditSettings.from_environment()
    normalized_repository = NormalizedAuditRepository(connection, normalized_settings)
    audit_flow = AuditFlow(
        connection=connection,
        legacy=legacy_audit,
        normalized=normalized_repository,
    )
    services["audit_flow"] = audit_flow
    router = CommandRouter(
        registry=registry,
        authorizer=Authorizer(
            _authorized_users(config),
            room_policies=_room_policies(config),
        ),
        audit=legacy_audit,
        audit_flow=audit_flow,
        services=services,
        prefix=config.command_prefix,
    )
    transport = _build_transport(config, bridge_health=bridge_health)
    return BridgeService(
        transport=transport,
        router=router,
        deduplicator=Deduplicator(
            connection, window_seconds=config.deduplication_window_seconds
        ),
        audit_flow=audit_flow,
        rate_limiter=RateLimiter(
            max_commands=config.security.rate_limit.commands,
            window_seconds=config.security.rate_limit.window_seconds,
        ),
        channel_index=config.meshcore.channel_index,
        bridge_health=bridge_health,
        meshcore_response_max_bytes=config.telegram.max_meshcore_message_length,
    )


def _build_transport(
    config: AppConfig,
    *,
    bridge_health: BridgeHealthState | None = None,
) -> Transport:
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
            ),
            bridge_health=bridge_health,
        )
    if config.meshcore.transport == "usb":
        raise NotImplementedError(
            "USB transport is not available in this branch. "
            "Use meshcore.transport=homeassistant or the experimental USB PR."
        )
    return MeshCoreTransport(channel_index=config.meshcore.channel_index)


def _authorized_users(config: AppConfig) -> dict[str, AuthorizedUser]:
    users = dict(config.users)
    if config.telegram.enabled:
        users[TELEGRAM_SENDER_ID] = AuthorizedUser(
            sender_id=TELEGRAM_SENDER_ID,
            name="telegram-authorized-user",
            role=config.telegram.authorized_user_role,
        )
    return users


def _room_policies(config: AppConfig) -> dict[str, RoomPolicy]:
    policies = dict(config.room_policies)
    if config.telegram.enabled:
        policies[TELEGRAM_ROOM_ID] = RoomPolicy(
            room_id=TELEGRAM_ROOM_ID,
            enabled=True,
            minimum_role=Role.readonly,
            allow_commands=True,
        )
    return policies


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
    normalized_audit_settings = (
        NormalizedAuditSettings.homeassistant_app()
        if args.home_assistant_app
        else NormalizedAuditSettings.from_environment()
    )
    bridge_health = BridgeHealthState(healthcheck_path=config.meshcore.healthcheck_path)
    bridge_health.configure(
        telegram_enabled=config.telegram.enabled,
        forward_telegram_to_meshcore=config.telegram.forward_telegram_to_meshcore,
        forward_meshcore_to_telegram=config.telegram.forward_meshcore_to_telegram,
        forward_confirmation_enabled=config.telegram.send_forward_confirmation,
    )
    service = build_service(
        config,
        normalized_audit_settings=normalized_audit_settings,
        bridge_health=bridge_health,
    )
    telegram_service, meshcore_to_telegram_forwarder = _build_telegram_services(
        config,
        normalized_audit_settings,
        router=service.router,
        audit_flow=service.audit_flow,
        meshcore_transport=service.transport,
        normalized_audit=service.audit_flow.normalized if service.audit_flow else None,
        bridge_health=bridge_health,
    )
    service.normal_text_forwarder = meshcore_to_telegram_forwarder
    health_event_publisher = _build_health_event_publisher(config, bridge_health)
    if args.home_assistant_app:
        logger.info("Bridge ready")
    await _run_services(service, telegram_service, health_event_publisher)


def _build_health_event_publisher(
    config: AppConfig,
    bridge_health: BridgeHealthState,
) -> HomeAssistantHealthEventPublisher | None:
    if not config.health.home_assistant_events_enabled:
        return None
    if not config.homeassistant.base_url or not config.homeassistant.token:
        return None
    client = HomeAssistantWebSocketClient(
        base_url=config.homeassistant.base_url,
        token=config.homeassistant.token,
        verify_tls=config.homeassistant.verify_tls,
        timeout_seconds=config.homeassistant.timeout_seconds,
        websocket_url_override=config.homeassistant.websocket_url,
    )
    return HomeAssistantHealthEventPublisher(
        health=bridge_health,
        client=client,
        channel_index=config.meshcore.channel_index,
        heartbeat_seconds=config.health.heartbeat_seconds,
        enabled=config.health.home_assistant_events_enabled,
    )


def _build_telegram_services(
    config: AppConfig,
    normalized_audit_settings: NormalizedAuditSettings,
    *,
    router: CommandRouter,
    audit_flow: AuditFlow | None,
    meshcore_transport: Transport,
    normalized_audit: NormalizedAuditRepository | None,
    bridge_health: BridgeHealthState | None = None,
) -> tuple[TelegramFoundationService | None, MeshCoreToTelegramForwarder | None]:
    if not config.telegram.enabled:
        return None, None
    if normalized_audit_settings.audit_key is None:
        raise RuntimeError("Telegram foundation requires normalized audit key")
    token = load_or_import_token(
        token_import=config.telegram.bot_token_import,
        token_file=config.telegram.bot_token_file,
    )
    register_redaction_secret(token.value)
    telegram_db_path = telegram_database_path(config.database_path)
    connection = connect_database(telegram_db_path, connection_name="telegram")
    if telegram_db_path != config.database_path:
        migration_source = connect_database(
            config.database_path,
            connection_name="telegram-migration-source",
        )
        try:
            migrate_telegram_tables(
                source_connection=migration_source,
                target_connection=connection,
            )
        finally:
            migration_source.close()
    client = TelegramBotApiClient(token=token)
    store = TelegramStore(connection, audit_key=normalized_audit_settings.audit_key)
    telegram_service = TelegramFoundationService(
        config=config.telegram,
        client=client,
        store=store,
        router=router,
        audit_flow=audit_flow,
        meshcore_transport=meshcore_transport,
        normalized_audit=normalized_audit,
        bridge_health=bridge_health,
    )
    meshcore_to_telegram_forwarder = MeshCoreToTelegramForwarder(
        config=config.telegram,
        client=client,
        store=store,
        normalized_audit=normalized_audit,
        bridge_health=bridge_health,
    )
    return telegram_service, meshcore_to_telegram_forwarder


async def _run_services(
    bridge_service: BridgeService,
    telegram_service: TelegramFoundationService | None,
    health_event_publisher: HomeAssistantHealthEventPublisher | None = None,
) -> None:
    if telegram_service is None and health_event_publisher is None:
        await bridge_service.run_forever()
        return
    if health_event_publisher is not None:
        health_event_publisher.start()
    bridge_task = asyncio.create_task(bridge_service.run_forever(), name="bridge-service")
    tasks = {bridge_task}
    if telegram_service is not None:
        telegram_task = asyncio.create_task(telegram_service.run(), name="telegram-foundation")
        tasks.add(telegram_task)
    first_exception: BaseException | None = None
    try:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
        first_exception = _first_task_exception(done)
        if first_exception is not None:
            logger.error("Service task stopped unexpectedly")
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
    finally:
        if telegram_service is not None:
            telegram_service.stop()
        if health_event_publisher is not None:
            with suppress(Exception):
                await health_event_publisher.stop()
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        with suppress(Exception):
            await bridge_service.close()
    if first_exception is not None:
        raise first_exception


def _first_task_exception(tasks: set[asyncio.Task[None]]) -> BaseException | None:
    for task in tasks:
        if task.cancelled():
            continue
        exception = task.exception()
        if exception is not None:
            return exception
    return None


def main() -> None:
    asyncio.run(amain())


if __name__ == "__main__":
    main()
