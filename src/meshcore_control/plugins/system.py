from __future__ import annotations

from typing import Any, Protocol, cast

from meshcore_control.adapters.homeassistant import HomeAssistantStatus
from meshcore_control.auth.roles import Role
from meshcore_control.bridge_health import (
    BridgeHealthState,
    render_bridge_status_for_channel,
    render_last_activity,
)
from meshcore_control.commands.help import render_help
from meshcore_control.commands.registry import CommandContext, CommandDefinition, CommandRegistry
from meshcore_control.config import AppConfig
from meshcore_control.storage.audit_flow import AuditFlow
from meshcore_control.storage.normalized_audit import NormalizedAuditEventType

_EXTERIOR_RESPONSE_MAX_CHARS = 160
_BRIDGE_OVERRIDE_TARGETS = {
    "tg2mc": "telegram_to_meshcore",
    "mc2tg": "meshcore_to_telegram",
    "confirm": "confirmation",
}
_BRIDGE_OVERRIDE_LABELS = {
    "tg2mc": ("Telegram -> MeshCore", "T2M"),
    "mc2tg": ("MeshCore -> Telegram", "M2T"),
    "confirm": ("Confirmación Telegram", "CF"),
}


class HomeAssistantStatusClient(Protocol):
    async def check_available(self) -> HomeAssistantStatus:
        raise NotImplementedError

    async def get_config(self) -> dict[str, Any]:
        raise NotImplementedError

    async def get_state(self, entity_or_alias: str) -> dict[str, Any]:
        raise NotImplementedError


def register(registry: CommandRegistry) -> None:
    registry.register(
        CommandDefinition(
            name="ping",
            aliases=(),
            group="system",
            usage="!ping",
            help_text="Comprueba que el bridge responde.",
            minimum_role=Role.readonly,
            confirmation_required=False,
            handler=ping,
        )
    )
    registry.register(
        CommandDefinition(
            name="help",
            aliases=("h",),
            group="system",
            usage="!help [grupo]",
            help_text="Muestra ayuda segun permisos.",
            minimum_role=Role.readonly,
            confirmation_required=False,
            handler=help_command,
        )
    )
    registry.register(
        CommandDefinition(
            name="estado",
            aliases=("status",),
            group="system",
            usage="!estado\n!estado ha",
            help_text="Resumen corto de disponibilidad.",
            minimum_role=Role.readonly,
            confirmation_required=False,
            handler=estado,
        )
    )
    registry.register(
        CommandDefinition(
            name="exterior",
            aliases=("outdoor",),
            group="system",
            usage="!exterior",
            help_text="Muestra temperatura y humedad exterior configuradas.",
            minimum_role=Role.readonly,
            confirmation_required=False,
            handler=exterior,
        )
    )
    registry.register(
        CommandDefinition(
            name="bridge",
            aliases=(),
            group="system",
            usage=(
                "!bridge\n!bridge tg2mc on|off\n!bridge mc2tg on|off\n"
                "!bridge confirm on|off\n!bridge reset"
            ),
            help_text=(
                "Muestra estado interno seguro del bridge y permite overrides admin "
                "temporales."
            ),
            minimum_role=Role.readonly,
            confirmation_required=False,
            handler=bridge,
        )
    )
    registry.register(
        CommandDefinition(
            name="last",
            aliases=(),
            group="system",
            usage="!last",
            help_text="Muestra ultima actividad readonly del bridge.",
            minimum_role=Role.readonly,
            confirmation_required=False,
            handler=last,
        )
    )


async def ping(context: CommandContext, args: list[str]) -> str:
    return "pong"


async def help_command(context: CommandContext, args: list[str]) -> str:
    registry = context.services["registry"]
    if not isinstance(registry, CommandRegistry):
        raise TypeError("registry service is not a CommandRegistry")
    group = args[0].lower() if args else None
    return render_help(registry, context.user.role, group)


async def estado(context: CommandContext, args: list[str]) -> str:
    ha_client = context.services.get("homeassistant")
    config = context.services.get("config")
    if ha_client is None:
        ha_status = HomeAssistantStatus(available=False, message="sin configurar")
    else:
        ha_status = await cast(HomeAssistantStatusClient, ha_client).check_available()

    if args and args[0].lower() == "ha":
        return await _render_ha_status(ha_client, ha_status)

    companion_status = (
        "OK"
        if context.message.transport.startswith("meshcore")
        else context.message.transport.upper()
    )
    ha_text = "OK" if ha_status.available else f"ERROR {ha_status.message}"
    lines = ["CASA", f"HA: {ha_text}"]
    if isinstance(config, AppConfig) and ha_client is not None and ha_status.available:
        lines.extend(await _status_entity_lines(cast(HomeAssistantStatusClient, ha_client), config))
    lines.extend(
        [
            "Internet: no requerido",
            f"Companion: {companion_status}",
            "Servers: sin configurar",
        ]
    )
    return "\n".join(lines)


async def exterior(context: CommandContext, args: list[str]) -> str:
    config = context.services.get("config")
    weather = config.weather_status if isinstance(config, AppConfig) else None
    label = weather.label if weather is not None else "Exterior"
    if weather is None or not weather.temperature_entity:
        return f"{label}: no configurado"

    ha_client = context.services.get("homeassistant")
    if ha_client is None:
        return f"{label}: N/D"

    client = cast(HomeAssistantStatusClient, ha_client)
    temperature = await _safe_state_value(client, weather.temperature_entity)
    line = f"{label}: {temperature}"
    if weather.humidity_entity:
        humidity = await _safe_state_value(client, weather.humidity_entity)
        line = f"{line} · Humedad: {humidity}"
    return _fit_exterior_response(line, f"{label}: {temperature}", label)


async def bridge(context: CommandContext, args: list[str]) -> str:
    health = context.services.get("bridge_health")
    if not isinstance(health, BridgeHealthState):
        return "Bridge: N/D"
    if args:
        return _handle_bridge_admin(context, health, args)
    config = context.services.get("config")
    events_enabled = (
        config.health.home_assistant_events_enabled if isinstance(config, AppConfig) else None
    )
    heartbeat_seconds = config.health.heartbeat_seconds if isinstance(config, AppConfig) else None
    compact = context.message.transport != "telegram"
    return render_bridge_status_for_channel(
        health.snapshot(),
        channel_index=context.message.channel_index,
        compact=compact,
        health_events_enabled=events_enabled,
        heartbeat_seconds=heartbeat_seconds,
    )


def _handle_bridge_admin(
    context: CommandContext,
    health: BridgeHealthState,
    args: list[str],
) -> str:
    compact = context.message.transport != "telegram"
    operation = args[0].lower()
    if operation == "reset" and len(args) == 1:
        _audit_bridge_override(
            context,
            NormalizedAuditEventType.BRIDGE_RUNTIME_OVERRIDE_REQUESTED,
            {
                "operation": "reset",
                "target": "all",
                "requested_value": "configured",
                "transport": context.message.transport,
            },
        )
        if context.user.role < Role.admin:
            _audit_bridge_override(
                context,
                NormalizedAuditEventType.BRIDGE_RUNTIME_OVERRIDE_DENIED,
                {
                    "operation": "reset",
                    "target": "all",
                    "requested_value": "configured",
                    "transport": context.message.transport,
                    "reason": "insufficient_role",
                },
            )
            return "No autorizado."
        changes = health.reset_runtime_overrides()
        for change in changes:
            _audit_bridge_override(
                context,
                NormalizedAuditEventType.BRIDGE_RUNTIME_OVERRIDE_RESET,
                _change_metadata(
                    operation="reset",
                    target=change.target,
                    transport=context.message.transport,
                    previous_value=change.previous_value,
                    new_value=change.new_value,
                    override_value=change.override_value,
                ),
            )
        return "Overrides eliminados." if compact else (
            "Overrides temporales eliminados. Se usa la configuración de la App."
        )
    if len(args) != 2 or operation not in _BRIDGE_OVERRIDE_TARGETS:
        return _bridge_usage(compact)
    value_token = args[1].lower()
    if value_token not in {"on", "off"}:
        return _bridge_usage(compact)
    target = _BRIDGE_OVERRIDE_TARGETS[operation]
    requested = value_token == "on"
    _audit_bridge_override(
        context,
        NormalizedAuditEventType.BRIDGE_RUNTIME_OVERRIDE_REQUESTED,
        {
            "operation": "set",
            "target": target,
            "requested_value": requested,
            "transport": context.message.transport,
        },
    )
    if context.user.role < Role.admin:
        _audit_bridge_override(
            context,
            NormalizedAuditEventType.BRIDGE_RUNTIME_OVERRIDE_DENIED,
            {
                "operation": "set",
                "target": target,
                "requested_value": requested,
                "transport": context.message.transport,
                "reason": "insufficient_role",
            },
        )
        return "No autorizado."
    change = health.set_runtime_override(target, requested)
    _audit_bridge_override(
        context,
        NormalizedAuditEventType.BRIDGE_RUNTIME_OVERRIDE_APPLIED,
        _change_metadata(
            operation="set",
            target=change.target,
            transport=context.message.transport,
            previous_value=change.previous_value,
            new_value=change.new_value,
            override_value=change.override_value,
        ),
    )
    long_label, short_label = _BRIDGE_OVERRIDE_LABELS[operation]
    state = (
        ("activada" if requested else "desactivada")
        if operation == "confirm"
        else ("activado" if requested else "desactivado")
    )
    if compact:
        return f"{short_label}:{'on' if requested else 'off'}*"
    return f"{long_label}: {state} hasta reinicio."


def _bridge_usage(compact: bool) -> str:
    if compact:
        return "Uso: !bridge tg2mc|mc2tg|confirm on|off"
    return "Uso: !bridge tg2mc|mc2tg|confirm on|off o !bridge reset"


def _change_metadata(
    *,
    operation: str,
    target: str,
    transport: str,
    previous_value: bool,
    new_value: bool,
    override_value: bool | None,
) -> dict[str, object]:
    return {
        "operation": operation,
        "target": target,
        "previous_value": previous_value,
        "new_value": new_value,
        "override_value": override_value,
        "transport": transport,
        "result": "applied",
    }


def _audit_bridge_override(
    context: CommandContext,
    event_type: NormalizedAuditEventType,
    metadata: dict[str, object],
) -> None:
    audit_flow = context.services.get("audit_flow")
    if not isinstance(audit_flow, AuditFlow) or context.audit_trail is None:
        return
    try:
        context.audit_trail = audit_flow.runtime_override_event(
            context.audit_trail,
            event_type=event_type,
            metadata=metadata,
        )
    except Exception:
        health = context.services.get("bridge_health")
        if isinstance(health, BridgeHealthState):
            health.set_audit_db_health("degraded", reason="storage_error")


async def last(context: CommandContext, args: list[str]) -> str:
    health = context.services.get("bridge_health")
    if not isinstance(health, BridgeHealthState):
        return "Last: N/D"
    compact = context.message.transport != "telegram"
    try:
        return render_last_activity(health.snapshot(), compact=compact)
    except ValueError:
        return "Last: N/D" if compact else "Last activity\n\nN/D"


async def _render_ha_status(ha_client: object | None, status: HomeAssistantStatus) -> str:
    if ha_client is None:
        return "HA: sin configurar"
    if not status.available:
        return f"HA: ERROR {status.message}"
    try:
        ha_config = await cast(HomeAssistantStatusClient, ha_client).get_config()
    except Exception as exc:
        return f"HA: OK\nConfig: ERROR {exc.__class__.__name__}"
    version = ha_config.get("version", "N/D")
    location = ha_config.get("location_name", "N/D")
    return f"HA: OK\nVersion: {version}\nName: {location}"


async def _status_entity_lines(
    ha_client: HomeAssistantStatusClient,
    config: AppConfig,
) -> list[str]:
    lines: list[str] = []
    for entity in config.status_entities.values():
        try:
            state = await ha_client.get_state(entity.entity_id)
        except Exception:
            lines.append(f"{entity.label}: N/D")
            continue
        value = _format_state(state)
        lines.append(f"{entity.label}: {value}")
    return lines


def _format_state(state: dict[str, Any]) -> str:
    value = str(state.get("state", "N/D"))
    if value in {"unknown", "unavailable", ""}:
        return "N/D"
    attributes = state.get("attributes", {})
    unit = ""
    if isinstance(attributes, dict):
        unit_value = attributes.get("unit_of_measurement")
        unit = f" {unit_value}" if unit_value else ""
    return f"{value}{unit}"


async def _safe_state_value(client: HomeAssistantStatusClient, entity_id: str) -> str:
    try:
        return _format_state(await client.get_state(entity_id))
    except Exception:
        return "N/D"


def _fit_exterior_response(full_line: str, temperature_line: str, label: str) -> str:
    if len(full_line) <= _EXTERIOR_RESPONSE_MAX_CHARS:
        return full_line
    if len(temperature_line) <= _EXTERIOR_RESPONSE_MAX_CHARS:
        return temperature_line
    return f"{label}: N/D"
