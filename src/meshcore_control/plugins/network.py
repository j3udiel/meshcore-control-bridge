from __future__ import annotations

from meshcore_control.adapters.homeassistant_state import EntityState
from meshcore_control.auth.roles import Role
from meshcore_control.commands.registry import CommandContext, CommandDefinition, CommandRegistry
from meshcore_control.plugins.home_status import (
    available_count,
    binary_on_off,
    failure_reason,
    fit_lora,
    health_relative,
    read_configured,
    services_from_context,
    set_audit_counts,
)


def register(registry: CommandRegistry) -> None:
    registry.register(
        CommandDefinition(
            name="red",
            aliases=(),
            group="red",
            usage="!red",
            help_text="Estado readonly de conectividad configurada y transportes.",
            minimum_role=Role.readonly,
            confirmation_required=False,
            handler=red,
        )
    )


async def red(context: CommandContext, args: list[str]) -> str:
    services = services_from_context(context)
    network = services.config.home_status.network
    ids = [
        network.internet_entity,
        network.router_entity,
        network.dns_entity,
        network.home_assistant_entity,
        *network.additional_entities,
    ]
    ids = [entity_id for entity_id in ids if entity_id]
    states = await read_configured(services.reader, ids)
    set_audit_counts(
        context,
        queried=len(ids),
        available=available_count(states.values()),
        failure_reason=failure_reason(states.values(), configured=bool(ids)),
    )
    snapshot = services.health.snapshot() if services.health is not None else None
    internet = states.get(network.internet_entity)
    router = states.get(network.router_entity)
    dns = states.get(network.dns_entity)
    ha_entity = states.get(network.home_assistant_entity)

    ha_state = (
        _connected(snapshot.ha_websocket_state if snapshot is not None else "disconnected")
        if ha_entity is None
        else binary_on_off(ha_entity)
    )
    mc_state = _connected(
        snapshot.meshcore_transport_state if snapshot is not None else "disconnected"
    )
    tg_state = _telegram(snapshot.telegram_polling_state if snapshot is not None else "disabled")
    t2m = health_relative(
        snapshot.last_tg_to_mc if snapshot is not None else None,
        compact=services.compact,
    )
    m2t = health_relative(
        snapshot.last_mc_to_tg if snapshot is not None else None,
        compact=services.compact,
    )

    if services.compact:
        lines = [
            "Net:"
            f"{binary_on_off(internet)} RTR:{binary_on_off(router)} "
            f"DNS:{_dns(dns, compact=True)}",
            f"HA:{ha_state} MC:{mc_state} TG:{tg_state}",
            f"T2M:{t2m} M2T:{m2t}",
        ]
        return fit_lora("\n".join(lines), max_bytes=services.meshcore_max_bytes)

    lines = ["Red"]
    if internet is not None:
        lines.append(f"Internet: {_online(internet)}")
    if router is not None:
        lines.append(f"Router: {_online(router)}")
    if dns is not None:
        lines.append(f"DNS: {_dns(dns)}")
    lines.extend(
        [
            f"Home Assistant: {_connected_text(ha_state)}",
            f"MeshCore: {_connected_text(mc_state)}",
            f"Telegram: {_telegram_text(tg_state)}",
            f"Último TG->MC: {t2m}",
            f"Último MC->TG: {m2t}",
        ]
    )
    return "\n".join(lines)


def _connected(value: str) -> str:
    return "on" if value == "connected" else "off"


def _telegram(value: str) -> str:
    if value == "connected":
        return "on"
    if value == "disabled":
        return "off"
    return "deg"


def _online(state: EntityState | None) -> str:
    rendered = binary_on_off(state)
    if rendered == "on":
        return "online"
    if rendered == "off":
        return "offline"
    return "N/D"


def _dns(state: EntityState | None, *, compact: bool = False) -> str:
    rendered = binary_on_off(state)
    if rendered == "on":
        return "ok" if compact else "OK"
    if rendered == "off":
        return "fail"
    return "N/D"


def _connected_text(value: str) -> str:
    if value == "on":
        return "conectado"
    if value == "off":
        return "desconectado"
    return "degradado"


def _telegram_text(value: str) -> str:
    if value == "on":
        return "conectado"
    if value == "off":
        return "desactivado"
    return "degradado"
