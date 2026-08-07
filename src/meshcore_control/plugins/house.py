from __future__ import annotations

from meshcore_control.adapters.homeassistant_state import EntityState
from meshcore_control.auth.roles import Role
from meshcore_control.commands.registry import CommandContext, CommandDefinition, CommandRegistry
from meshcore_control.config import AppConfig
from meshcore_control.plugins.home_status import (
    alarm_label,
    available_count,
    binary_on_off,
    count_on,
    failure_reason,
    fit_lora,
    format_measurement,
    format_percent,
    last_changed,
    motion_detected,
    open_names,
    read_configured,
    services_from_context,
    set_audit_counts,
)


def register(registry: CommandRegistry) -> None:
    registry.register(
        CommandDefinition(
            name="alarma",
            aliases=(),
            group="alarma",
            usage="!alarma",
            help_text="Estado readonly de la alarma y sensores asociados.",
            minimum_role=Role.readonly,
            confirmation_required=False,
            handler=alarma,
        )
    )
    registry.register(
        CommandDefinition(
            name="casa",
            aliases=(),
            group="casa",
            usage="!casa",
            help_text="Resumen readonly de casa, presencia, puertas, ambiente y red.",
            minimum_role=Role.readonly,
            confirmation_required=False,
            handler=casa,
        )
    )


async def alarma(context: CommandContext, args: list[str]) -> str:
    services = services_from_context(context)
    config = services.config.home_status.alarm
    entity_ids = (
        [config.entity_id]
        + list(config.door_entities)
        + list(config.motion_entities)
    )
    states = await read_configured(services.reader, entity_ids)
    queried = len([entity_id for entity_id in entity_ids if entity_id])
    set_audit_counts(
        context,
        queried=queried,
        available=available_count(states.values()),
        failure_reason=failure_reason(states.values(), configured=queried > 0),
    )
    if queried == 0:
        return "Alarm:N/D" if services.compact else "Alarma: no configurada."
    alarm_state = states.get(config.entity_id)
    doors = [states[entity_id] for entity_id in config.door_entities if entity_id in states]
    motions = [states[entity_id] for entity_id in config.motion_entities if entity_id in states]
    opened = open_names(doors)
    active_motion = motion_detected(motions)
    if services.compact:
        if alarm_state is not None and alarm_state.state == "triggered":
            return fit_lora(
                "\n".join(
                    [
                        "ALARM:TRIGGERED",
                        f"Doors:{','.join(opened) if opened else 'none'}",
                        f"Motion:{','.join(active_motion) if active_motion else 'none'}",
                    ]
                ),
                max_bytes=services.meshcore_max_bytes,
            )
        ago = last_changed(alarm_state, compact=True)
        lines = [
            f"Alarm:{alarm_label(alarm_state, compact=True)}",
            f"Doors:{len(opened)} open",
            f"Motion:{'yes' if active_motion else 'none'}",
        ]
        if ago:
            lines.append(f"Ago:{ago}")
        return fit_lora("\n".join(lines), max_bytes=services.meshcore_max_bytes)

    lines = [f"Alarma: {alarm_label(alarm_state)}"]
    if opened:
        lines.append(f"Puertas abiertas: {', '.join(opened)}")
    elif doors:
        lines.append(f"Puertas: {len(doors)} cerradas, 0 abiertas")
    if active_motion:
        lines.append(f"Movimiento: {', '.join(active_motion)}")
    elif motions:
        lines.append("Movimiento: sin detectar")
    ago = last_changed(alarm_state)
    if ago:
        lines.append(f"Último cambio: {ago}")
    return "\n".join(lines)


async def casa(context: CommandContext, args: list[str]) -> str:
    services = services_from_context(context)
    config = services.config.home_status
    ids = _home_entity_ids(services.config)
    states = await read_configured(services.reader, ids)
    queried = len(ids)
    set_audit_counts(
        context,
        queried=queried,
        available=available_count(states.values()),
        failure_reason=failure_reason(states.values(), configured=queried > 0),
    )
    if queried == 0:
        return "Casa:N/D" if services.compact else "Estado de casa no configurado."

    alarm_state = states.get(config.alarm.entity_id)
    persons = [states[e] for e in config.home.person_entities if e in states]
    presence = [states[e] for e in config.home.presence_entities if e in states]
    doors = [states[e] for e in config.home.door_entities if e in states]
    lights = [states[e] for e in config.home.light_entities if e in states]
    server_states = [
        states[e.availability_entity]
        for e in config.servers.entries
        if e.availability_entity and e.availability_entity in states
    ]
    internet = states.get(config.network.internet_entity)

    if not states or available_count(states.values()) == 0:
        return "Casa:N/D" if services.compact else "Estado de casa no disponible."

    people_home = sum(1 for state in persons if state.available and state.state == "home")
    presence_on = count_on(presence)
    open_doors = len(open_names(doors))
    lights_on = count_on(lights)
    servers_online = sum(1 for state in server_states if binary_on_off(state) == "on")
    servers_total = len(server_states)
    temperature = states.get(config.home.temperature_entity)
    humidity = states.get(config.home.humidity_entity)
    ups = states.get(config.home.ups_battery_entity)

    if services.compact:
        lines = [
            "Casa "
            f"A:{alarm_label(alarm_state, compact=True)} "
            f"P:{_compact_presence(people_home, presence_on)}",
            f"D:{open_doors} L:{lights_on} T:{format_measurement(temperature, compact_unit=True)}",
        ]
        tail = []
        if internet is not None:
            tail.append(f"Net:{binary_on_off(internet)}")
        if servers_total:
            tail.append(f"S:{servers_online}/{servers_total}")
        if ups is not None:
            tail.append(f"UPS:{format_percent(ups, compact=True)}")
        if tail:
            lines.append(" ".join(tail))
        return fit_lora("\n".join(lines), max_bytes=services.meshcore_max_bytes)

    lines = ["Casa"]
    if alarm_state is not None:
        lines.append(f"Alarma: {alarm_label(alarm_state)}")
    if persons or presence:
        lines.append(f"Presencia: {_presence_text(people_home, presence_on)}")
    if doors:
        lines.append("Puertas: cerradas" if open_doors == 0 else f"Puertas: {open_doors} abiertas")
    if lights:
        lines.append(f"Luces: {lights_on} encendidas")
    if temperature is not None:
        lines.append(f"Temperatura: {format_measurement(temperature)}")
    if humidity is not None:
        lines.append(f"Humedad: {format_percent(humidity)}")
    if internet is not None:
        lines.append(f"Internet: {_online_text(internet)}")
    if servers_total:
        lines.append(f"Servidores: {servers_online}/{servers_total} online")
    if ups is not None:
        lines.append(f"UPS: {format_percent(ups)}")
    return "\n".join(lines) if len(lines) > 1 else "Estado de casa no disponible."


def _home_entity_ids(config: AppConfig) -> list[str]:
    home_status = config.home_status
    ids: list[str] = [
        home_status.alarm.entity_id,
        *home_status.home.person_entities,
        *home_status.home.presence_entities,
        *home_status.home.door_entities,
        *home_status.home.light_entities,
        home_status.home.temperature_entity,
        home_status.home.humidity_entity,
        home_status.home.ups_battery_entity,
        home_status.network.internet_entity,
    ]
    ids.extend(
        entry.availability_entity
        for entry in home_status.servers.entries
        if entry.availability_entity
    )
    return [entity_id for entity_id in ids if entity_id]


def _presence_text(people_home: int, presence_on: int) -> str:
    if people_home == 0 and presence_on == 0:
        return "nadie"
    total = people_home + presence_on
    return "presencia" if total == 1 else f"{total} presentes"


def _compact_presence(people_home: int, presence_on: int) -> str:
    return "none" if people_home == 0 and presence_on == 0 else "yes"


def _online_text(state: EntityState | None) -> str:
    rendered = binary_on_off(state)
    return "online" if rendered == "on" else "offline" if rendered == "off" else "N/D"
