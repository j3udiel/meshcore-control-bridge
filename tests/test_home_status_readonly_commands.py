from __future__ import annotations

import asyncio
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from itertools import count
from typing import Any

import pytest

from meshcore_control.adapters.homeassistant_state import HomeAssistantStateReader
from meshcore_control.app import BridgeService
from meshcore_control.auth.authorization import AuthorizedUser, Authorizer, RoomPolicy
from meshcore_control.auth.roles import Role
from meshcore_control.bridge_health import BridgeHealthState
from meshcore_control.commands.router import CommandRouter
from meshcore_control.config import (
    AppConfig,
    HomeStatusAlarmConfig,
    HomeStatusConfig,
    HomeStatusHomeConfig,
    HomeStatusNetworkConfig,
    HomeStatusServerEntryConfig,
    HomeStatusServersConfig,
    load_config,
)
from meshcore_control.homeassistant_app import HomeAssistantAppOptions
from meshcore_control.models import (
    InboundMessage,
    MessageIdentity,
    OutboundMessage,
    RoomRef,
    SenderIdentity,
)
from meshcore_control.plugins import build_registry
from meshcore_control.security.deduplication import Deduplicator
from meshcore_control.security.rate_limit import RateLimiter
from meshcore_control.storage.audit_flow import AuditFlow
from meshcore_control.storage.database import connect_database
from meshcore_control.storage.normalized_audit import (
    AUDIT_KEY_MIN_BYTES,
    AuditKey,
    NormalizedAuditRepository,
    NormalizedAuditSettings,
)
from meshcore_control.storage.repositories import AuditRepository
from meshcore_control.telegram.identity import TELEGRAM_ROOM_ID, TELEGRAM_SENDER_ID, telegram_room
from meshcore_control.transport.fake import FakeTransport

SENDER_ID = "meshcore-pubkey-prefix:home-status-sender"
_MESSAGE_COUNTER = count(1)


@dataclass(slots=True)
class FakeHA:
    states: dict[str, dict[str, Any]]
    errors: set[str] | None = None
    delay: float = 0
    call_service_called: bool = False
    get_state_calls: int = 0
    get_states_calls: int = 0

    async def get_state(self, entity_or_alias: str) -> dict[str, Any]:
        self.get_state_calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.errors and entity_or_alias in self.errors:
            raise TimeoutError
        if entity_or_alias not in self.states:
            raise KeyError(entity_or_alias)
        return self.states[entity_or_alias]

    async def get_states(self) -> list[dict[str, Any]]:
        self.get_states_calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        return [
            {"entity_id": entity_id, **payload}
            for entity_id, payload in self.states.items()
            if not self.errors or entity_id not in self.errors
        ]

    async def call_service(self, *args: object, **kwargs: object) -> None:
        self.call_service_called = True
        raise AssertionError("readonly commands must not call services")


class FailingTransport(FakeTransport):
    def __init__(self, failures: list[BaseException]) -> None:
        super().__init__()
        self.failures = list(failures)

    async def send(self, message: OutboundMessage) -> None:
        if self.failures:
            raise self.failures.pop(0)
        await super().send(message)


def state(value: str, *, unit: str | None = None, name: str | None = None) -> dict[str, Any]:
    attributes: dict[str, object] = {}
    if unit is not None:
        attributes["unit_of_measurement"] = unit
    if name is not None:
        attributes["friendly_name"] = name
    return {
        "state": value,
        "attributes": attributes,
        "last_changed": (datetime.now(UTC) - timedelta(minutes=12)).isoformat(),
    }


def _home_status_config() -> HomeStatusConfig:
    return HomeStatusConfig(
        alarm=HomeStatusAlarmConfig(
            entity_id="alarm_control_panel.configured",
            door_entities=("binary_sensor.front_door",),
            motion_entities=("binary_sensor.hall_motion",),
        ),
        home=HomeStatusHomeConfig(
            person_entities=("person.primary",),
            presence_entities=("binary_sensor.presence",),
            door_entities=("binary_sensor.front_door",),
            light_entities=("light.kitchen", "light.office"),
            temperature_entity="sensor.home_temperature",
            humidity_entity="sensor.home_humidity",
            ups_battery_entity="sensor.ups_battery",
        ),
        servers=HomeStatusServersConfig(
            entries=(
                HomeStatusServerEntryConfig(
                    alias="principal",
                    name="Servidor principal",
                    availability_entity="binary_sensor.server_online",
                    cpu_entity="sensor.server_cpu",
                    memory_entity="sensor.server_memory",
                    disk_entity="sensor.server_disk",
                    temperature_entity="sensor.server_temperature",
                ),
                HomeStatusServerEntryConfig(
                    alias="nas",
                    name="NAS",
                    availability_entity="binary_sensor.nas_online",
                ),
            )
        ),
        network=HomeStatusNetworkConfig(
            internet_entity="binary_sensor.internet",
            router_entity="binary_sensor.router",
            dns_entity="binary_sensor.dns",
        ),
    )


def _states() -> dict[str, dict[str, Any]]:
    return {
        "alarm_control_panel.configured": state("armed_away"),
        "binary_sensor.front_door": state("off", name="Entrada"),
        "binary_sensor.hall_motion": state("off", name="Pasillo"),
        "person.primary": state("not_home"),
        "binary_sensor.presence": state("off"),
        "light.kitchen": state("on"),
        "light.office": state("on"),
        "sensor.home_temperature": state("23.4", unit="°C"),
        "sensor.home_humidity": state("48", unit="%"),
        "sensor.ups_battery": state("94", unit="%"),
        "binary_sensor.server_online": state("on"),
        "sensor.server_cpu": state("12", unit="%"),
        "sensor.server_memory": state("38", unit="%"),
        "sensor.server_disk": state("71", unit="%"),
        "sensor.server_temperature": state("42", unit="°C"),
        "binary_sensor.nas_online": state("off"),
        "binary_sensor.internet": state("on"),
        "binary_sensor.router": state("on"),
        "binary_sensor.dns": state("on"),
    }


def _service(
    tmp_path,
    *,
    home_status: HomeStatusConfig | None = None,
    ha: FakeHA | None = None,
    transport_name: str = "homeassistant-meshcore",
    role: Role = Role.readonly,
    normalized_audit: bool = False,
    health: BridgeHealthState | None = None,
    response_max_bytes: int | None = None,
    transport: FakeTransport | None = None,
) -> tuple[BridgeService, FakeTransport, sqlite3.Connection]:
    connection = connect_database(str(tmp_path / f"{transport_name}.db"))
    registry = build_registry()
    legacy = AuditRepository(connection)
    audit_flow = None
    if normalized_audit:
        audit_flow = AuditFlow(
            connection=connection,
            legacy=legacy,
            normalized=NormalizedAuditRepository(
                connection,
                NormalizedAuditSettings(
                    enabled=True,
                    audit_key=AuditKey(key=b"h" * AUDIT_KEY_MIN_BYTES, key_id="home-key"),
                ),
            ),
        )
    config = AppConfig(home_status=home_status or _home_status_config())
    services: dict[str, object] = {"registry": registry, "config": config}
    if ha is not None:
        services["home_status_reader"] = HomeAssistantStateReader(
            ha,
            per_entity_timeout_seconds=0.05,
            total_timeout_seconds=0.15,
            concurrency=4,
        )
    if health is not None:
        services["bridge_health"] = health
    router = CommandRouter(
        registry=registry,
        authorizer=Authorizer(
            {
                SENDER_ID: AuthorizedUser(SENDER_ID, "tester", role),
                TELEGRAM_SENDER_ID: AuthorizedUser(TELEGRAM_SENDER_ID, "telegram tester", role),
            },
            room_policies={
                "homeassistant-meshcore:channel:1": RoomPolicy(
                    room_id="homeassistant-meshcore:channel:1",
                    enabled=True,
                    minimum_role=Role.readonly,
                    allow_commands=True,
                ),
                TELEGRAM_ROOM_ID: RoomPolicy(
                    room_id=TELEGRAM_ROOM_ID,
                    enabled=True,
                    minimum_role=Role.readonly,
                    allow_commands=True,
                )
            },
        ),
        audit=legacy,
        audit_flow=audit_flow,
        services=services,
        prefix="!",
    )
    transport = transport or FakeTransport()
    return (
        BridgeService(
            transport=transport,
            router=router,
            deduplicator=Deduplicator(connection, window_seconds=300),
            audit_flow=audit_flow,
            rate_limiter=RateLimiter(max_commands=20, window_seconds=60),
            channel_index=1,
            bridge_health=health,
            meshcore_response_max_bytes=response_max_bytes
            if response_max_bytes is not None
            else 3900
            if transport_name == "telegram"
            else 180,
        ),
        transport,
        connection,
    )


def _message(
    text: str,
    *,
    transport: str = "homeassistant-meshcore",
    sender: str | None = None,
    message_id: str = "msg-1",
) -> InboundMessage:
    sender_id = sender or (TELEGRAM_SENDER_ID if transport == "telegram" else SENDER_ID)
    room = telegram_room() if transport == "telegram" else RoomRef.channel(
        transport=transport,
        channel_index=1,
    )
    identity = MessageIdentity.from_message_id(
        transport=transport,
        room_id=room.room_id,
        message_id=message_id,
    )
    return InboundMessage(
        transport=transport,
        message_id=identity.message_id,
        sender_id=sender_id,
        channel_index=1,
        text=text,
        source_room=room,
        reply_target=room,
        sender=SenderIdentity.from_sender_id(sender_id=sender_id, transport_scope=transport),
        message=identity,
    )


def _run(service: BridgeService, text: str, *, transport: str = "homeassistant-meshcore") -> str:
    outbound = asyncio.run(
        service.process_message(
            _message(text, transport=transport, message_id=f"msg-{next(_MESSAGE_COUNTER)}")
        )
    )
    assert outbound is not None
    return outbound.text


def test_alarma_armed_away_and_compact(tmp_path) -> None:
    service, _, _ = _service(tmp_path, ha=FakeHA(_states()))
    text = _run(service, "!alarma")

    assert "Alarm:away" in text
    assert "Doors:0 open" in text
    assert "Motion:none" in text
    assert len(text.encode("utf-8")) <= 180


@pytest.mark.parametrize(
    "command",
    ["!alarma", "!casa", "!servers", "!servers principal", "!red"],
)
def test_home_status_meshcore_responses_fit_default_utf8_budget(
    tmp_path,
    command: str,
) -> None:
    service, _, _ = _service(tmp_path, ha=FakeHA(_states()))

    text = _run(service, command)

    assert len(text.encode("utf-8")) <= 180
    assert text.encode("utf-8").decode("utf-8") == text


def test_home_status_meshcore_unicode_truncates_on_utf8_boundary(tmp_path) -> None:
    states = _states()
    states["alarm_control_panel.configured"] = state("triggered")
    states["binary_sensor.front_door"] = state("on", name="Entrada áéíóú" * 12)
    service, _, _ = _service(tmp_path, ha=FakeHA(states), response_max_bytes=80)

    text = _run(service, "!alarma")

    assert len(text.encode("utf-8")) <= 80
    assert text.encode("utf-8").decode("utf-8") == text
    assert text.endswith("...") or "ALARM" in text


@pytest.mark.parametrize(
    ("alarm_state", "expected"),
    [
        ("disarmed", "Alarma: desarmada"),
        ("triggered", "Alarma: ALERTA ACTIVADA"),
        ("unavailable", "Alarma: no disponible"),
    ],
)
def test_alarma_states_from_telegram(tmp_path, alarm_state: str, expected: str) -> None:
    states = _states()
    states["alarm_control_panel.configured"] = state(alarm_state)
    service, _, _ = _service(tmp_path, ha=FakeHA(states), transport_name="telegram")

    text = _run(service, "!alarma", transport="telegram")

    assert expected in text


def test_alarma_open_door_and_motion_names_are_sanitized(tmp_path) -> None:
    states = _states()
    states["binary_sensor.front_door"] = state("on", name="Entrada")
    states["binary_sensor.hall_motion"] = state("on", name="Pasillo")
    service, _, _ = _service(tmp_path, ha=FakeHA(states), transport_name="telegram")

    text = _run(service, "!alarma", transport="telegram")

    assert "Puertas abiertas: Entrada" in text
    assert "Movimiento: Pasillo" in text
    assert "binary_sensor.front_door" not in text


def test_alarma_not_configured(tmp_path) -> None:
    service, _, _ = _service(
        tmp_path,
        home_status=HomeStatusConfig(),
        ha=FakeHA({}),
        transport_name="telegram",
    )

    assert _run(service, "!alarma", transport="telegram") == "Alarma: no configurada."


def test_casa_complete_summary(tmp_path) -> None:
    service, _, _ = _service(tmp_path, ha=FakeHA(_states()), transport_name="telegram")

    text = _run(service, "!casa", transport="telegram")

    assert "Casa" in text
    assert "Alarma: armada fuera" in text
    assert "Presencia: nadie" in text
    assert "Puertas: cerradas" in text
    assert "Luces: 2 encendidas" in text
    assert "Temperatura: 23.4 °C" in text
    assert "Humedad: 48 %" in text
    assert "Internet: online" in text
    assert "Servidores: 1/2 online" in text
    assert "UPS: 94 %" in text


def test_casa_uses_single_bulk_homeassistant_state_request(tmp_path) -> None:
    ha = FakeHA(_states())
    service, _, _ = _service(tmp_path, ha=ha, transport_name="telegram")

    text = _run(service, "!casa", transport="telegram")

    assert "Casa" in text
    assert ha.get_states_calls == 1
    assert ha.get_state_calls == 0


def test_casa_does_not_create_connection_explosion_for_typical_config(tmp_path) -> None:
    entries = tuple(
        HomeStatusServerEntryConfig(
            alias=f"srv{i}",
            name=f"Servidor {i}",
            availability_entity=f"binary_sensor.srv{i}",
        )
        for i in range(5)
    )
    home_status = _home_status_config()
    config = HomeStatusConfig(
        alarm=HomeStatusAlarmConfig(
            entity_id=home_status.alarm.entity_id,
            door_entities=tuple(f"binary_sensor.door{i}" for i in range(5)),
            motion_entities=home_status.alarm.motion_entities,
        ),
        home=HomeStatusHomeConfig(
            person_entities=("person.one", "person.two"),
            presence_entities=home_status.home.presence_entities,
            door_entities=tuple(f"binary_sensor.door{i}" for i in range(5)),
            light_entities=tuple(f"light.light{i}" for i in range(5)),
            temperature_entity=home_status.home.temperature_entity,
            humidity_entity=home_status.home.humidity_entity,
            ups_battery_entity=home_status.home.ups_battery_entity,
        ),
        servers=HomeStatusServersConfig(entries=entries),
        network=home_status.network,
    )
    states = _states()
    states.update({f"binary_sensor.door{i}": state("off") for i in range(5)})
    states.update({f"light.light{i}": state("on" if i < 2 else "off") for i in range(5)})
    states.update({f"binary_sensor.srv{i}": state("on") for i in range(5)})
    states["person.one"] = state("not_home")
    states["person.two"] = state("home")
    ha = FakeHA(states)
    service, _, _ = _service(
        tmp_path,
        home_status=config,
        ha=ha,
        transport_name="telegram",
    )

    text = _run(service, "!casa", transport="telegram")

    assert "Casa" in text
    assert ha.get_states_calls == 1
    assert ha.get_state_calls == 0


def test_casa_partial_failures_keep_available_data(tmp_path) -> None:
    service, _, _ = _service(
        tmp_path,
        ha=FakeHA(_states(), errors={"sensor.home_temperature", "binary_sensor.internet"}),
        transport_name="telegram",
    )

    text = _run(service, "!casa", transport="telegram")

    assert "Luces: 2 encendidas" in text
    assert "Temperatura: N/D" in text
    assert "Internet: N/D" in text


def test_casa_all_entities_failed_reports_unavailable(tmp_path) -> None:
    states = _states()
    service, _, _ = _service(
        tmp_path,
        ha=FakeHA(states, errors=set(states)),
        transport_name="telegram",
    )

    text = _run(service, "!casa", transport="telegram")

    assert text == "Estado de casa no disponible."


def test_casa_does_not_expose_sensitive_presence_details(tmp_path) -> None:
    states = _states()
    states["person.primary"] = {
        "state": "home",
        "attributes": {"latitude": 1.2, "longitude": 3.4, "gps_accuracy": 5},
    }
    service, _, _ = _service(tmp_path, ha=FakeHA(states), transport_name="telegram")

    text = _run(service, "!casa", transport="telegram")

    assert "Presencia: presencia" in text
    assert "latitude" not in text
    assert "longitude" not in text
    assert "1.2" not in text


def test_servers_summary_and_detail(tmp_path) -> None:
    service, _, _ = _service(tmp_path, ha=FakeHA(_states()), transport_name="telegram")

    summary = _run(service, "!servers", transport="telegram")
    detail = _run(service, "!servers principal", transport="telegram")

    assert "Servidor principal: online" in summary
    assert "NAS: offline" in summary
    assert "Total: 1/2 online" in summary
    assert "Servidor principal" in detail
    assert "CPU: 12 %" in detail
    assert "RAM: 38 %" in detail
    assert "Disco: 71 %" in detail
    assert "Temperatura: 42 °C" in detail


def test_servers_unknown_alias_and_unavailable_not_offline(tmp_path) -> None:
    states = _states()
    states["binary_sensor.nas_online"] = state("unavailable")
    service, _, _ = _service(tmp_path, ha=FakeHA(states), transport_name="telegram")

    assert _run(service, "!servers missing", transport="telegram") == "Servidor no configurado."
    summary = _run(service, "!servers", transport="telegram")
    assert "NAS: unavailable" in summary
    assert "Total: 1/2 online" in summary


def test_servers_meshcore_truncates_entries(tmp_path) -> None:
    entries = tuple(
        HomeStatusServerEntryConfig(
            alias=f"srv{i}",
            name=f"Servidor {i}",
            availability_entity=f"binary_sensor.srv{i}",
        )
        for i in range(12)
    )
    states = {f"binary_sensor.srv{i}": state("on") for i in range(12)}
    service, _, _ = _service(
        tmp_path,
        home_status=HomeStatusConfig(servers=HomeStatusServersConfig(entries=entries)),
        ha=FakeHA(states),
    )

    text = _run(service, "!servers")

    assert "Srv 12/12" in text
    assert "+4 mas" in text
    assert len(text) < 180


def test_red_complete_and_transport_states(tmp_path) -> None:
    health = BridgeHealthState(started_at=datetime.now(UTC) - timedelta(minutes=5))
    health.configure(
        telegram_enabled=True,
        forward_telegram_to_meshcore=True,
        forward_meshcore_to_telegram=True,
        forward_confirmation_enabled=False,
    )
    health.set_meshcore_connected(True)
    health.set_telegram_polling("connected")
    with health._lock:
        health._last_tg_to_mc = datetime.now(UTC) - timedelta(minutes=2)
        health._last_mc_to_tg = datetime.now(UTC) - timedelta(minutes=5)
    service, _, _ = _service(
        tmp_path,
        ha=FakeHA(_states()),
        transport_name="telegram",
        health=health,
    )

    text = _run(service, "!red", transport="telegram")

    assert "Internet: online" in text
    assert "Router: online" in text
    assert "DNS: OK" in text
    assert "Home Assistant: conectado" in text
    assert "MeshCore: conectado" in text
    assert "Telegram: conectado" in text
    assert "Último TG->MC: hace 2m" in text


def test_red_internet_offline_and_telegram_disabled(tmp_path) -> None:
    states = _states()
    states["binary_sensor.internet"] = state("off")
    health = BridgeHealthState()
    health.configure(
        telegram_enabled=False,
        forward_telegram_to_meshcore=False,
        forward_meshcore_to_telegram=False,
        forward_confirmation_enabled=False,
    )
    service, _, _ = _service(tmp_path, ha=FakeHA(states), transport_name="telegram", health=health)

    text = _run(service, "!red", transport="telegram")

    assert "Internet: offline" in text
    assert "Telegram: desactivado" in text


def test_commands_work_from_telegram_and_do_not_cross_transports(tmp_path) -> None:
    service, transport, _ = _service(tmp_path, ha=FakeHA(_states()), transport_name="telegram")

    text = _run(service, "!casa", transport="telegram")

    assert text.startswith("Casa")
    assert transport.sent[-1].destination == TELEGRAM_SENDER_ID


def test_telegram_home_status_response_is_not_limited_by_lora_budget(tmp_path) -> None:
    entries = tuple(
        HomeStatusServerEntryConfig(
            alias=f"srv{i}",
            name=f"Servidor de laboratorio {i}",
            availability_entity=f"binary_sensor.srv{i}",
        )
        for i in range(12)
    )
    states = {f"binary_sensor.srv{i}": state("on") for i in range(12)}
    service, _, _ = _service(
        tmp_path,
        home_status=HomeStatusConfig(servers=HomeStatusServersConfig(entries=entries)),
        ha=FakeHA(states),
        transport_name="telegram",
    )

    text = _run(service, "!servers", transport="telegram")

    assert len(text.encode("utf-8")) > 180
    assert "Servidor de laboratorio 11: online" in text


@pytest.mark.parametrize("command", ["!alarma", "!casa", "!servers", "!servers principal", "!red"])
def test_home_status_meshcore_response_timeout_does_not_stop_next_command(
    tmp_path,
    command: str,
) -> None:
    health = BridgeHealthState()
    transport = FailingTransport([TimeoutError()])
    service, transport, _ = _service(
        tmp_path,
        ha=FakeHA(_states()),
        health=health,
        transport=transport,
    )

    first = asyncio.run(
        service.process_message(_message(command, message_id=f"timeout-{command}"))
    )
    second = asyncio.run(service.process_message(_message("!ping", message_id=f"next-{command}")))

    assert first is not None
    assert second is not None
    assert second.text == "pong"
    assert [message.text for message in transport.sent] == ["pong"]
    assert health.snapshot().last_failure_reason == "transport_timeout"


def test_telegram_command_still_works_after_meshcore_response_timeout(tmp_path) -> None:
    health = BridgeHealthState()
    transport = FailingTransport([TimeoutError()])
    service, _, _ = _service(
        tmp_path,
        ha=FakeHA(_states()),
        health=health,
        transport=transport,
    )

    asyncio.run(service.process_message(_message("!red", message_id="timeout-red")))
    telegram_text = _run(service, "!casa", transport="telegram")

    assert telegram_text.startswith("Casa")
    assert health.snapshot().last_failure_reason == "transport_timeout"


@pytest.mark.parametrize("role", [Role.readonly, Role.home, Role.operator, Role.admin])
def test_home_status_roles_are_readonly_allowed(tmp_path, role: Role) -> None:
    service, _, _ = _service(tmp_path, ha=FakeHA(_states()), role=role)

    assert _run(service, "!red")


def test_unauthorized_user_denied(tmp_path) -> None:
    service, _, _ = _service(tmp_path, ha=FakeHA(_states()))

    outbound = asyncio.run(
        service.process_message(
            _message("!casa", sender="meshcore-pubkey-prefix:not-authorized")
        )
    )

    assert outbound is not None
    assert outbound.text == "No autorizado."


def test_rate_limit_still_applies(tmp_path) -> None:
    service, _, _ = _service(tmp_path, ha=FakeHA(_states()))
    service.rate_limiter = RateLimiter(max_commands=3, window_seconds=60)

    for index in range(3):
        outbound = asyncio.run(
            service.process_message(_message("!red", message_id=f"rate-{index}"))
        )
        assert outbound is not None
    outbound = asyncio.run(
        service.process_message(_message("!red", message_id="rate-limited"))
    )

    assert outbound is not None
    assert outbound.text == "Rate limit."


def test_audit_metadata_is_redacted(tmp_path) -> None:
    service, _, connection = _service(
        tmp_path,
        ha=FakeHA(_states()),
        normalized_audit=True,
        transport_name="telegram",
    )

    _run(service, "!servers principal", transport="telegram")

    rows = connection.execute("SELECT metadata_json FROM normalized_audit_events").fetchall()
    combined = "\n".join(row[0] for row in rows)
    assert "entities_queried" in combined
    assert "entities_available" in combined
    assert "binary_sensor.server_online" not in combined
    assert "Servidor principal" not in combined
    assert "telegram" not in combined.lower() or "telegram" in combined


def test_no_entity_ids_tokens_or_coordinates_in_responses(tmp_path) -> None:
    service, _, _ = _service(tmp_path, ha=FakeHA(_states()), transport_name="telegram")

    combined = "\n".join(
        _run(service, command, transport="telegram")
        for command in ("!alarma", "!casa", "!servers", "!red")
    )

    assert "binary_sensor." not in combined
    assert "sensor." not in combined
    assert "alarm_control_panel." not in combined
    assert "token" not in combined.lower()
    assert "latitude" not in combined


def test_help_includes_home_status_commands(tmp_path) -> None:
    service, _, _ = _service(tmp_path, ha=FakeHA(_states()), transport_name="telegram")

    assert "!alarma" in _run(service, "!help alarma", transport="telegram")
    assert "!casa" in _run(service, "!help casa", transport="telegram")
    assert "!servers" in _run(service, "!help servers", transport="telegram")
    assert "!red" in _run(service, "!help red", transport="telegram")


def test_forwarding_and_bridge_admin_commands_still_registered(tmp_path) -> None:
    health = BridgeHealthState()
    health.configure(
        telegram_enabled=True,
        forward_telegram_to_meshcore=True,
        forward_meshcore_to_telegram=True,
        forward_confirmation_enabled=False,
    )
    service, _, _ = _service(tmp_path, ha=FakeHA(_states()), role=Role.admin, health=health)

    assert _run(service, "!bridge tg2mc off") == "T2M:off*"
    assert _run(service, "!ping") == "pong"


def test_old_app_options_load_without_home_status() -> None:
    options = HomeAssistantAppOptions.from_mapping(
        {
            "authorized_senders": [
                {"pubkey_prefix": "abcdef", "name": "Tester", "role": "readonly"}
            ]
        }
    )

    assert options.home_status == HomeAssistantAppOptions().home_status


def test_invalid_schema_rejects_duplicates_and_bad_alias(tmp_path) -> None:
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        """
home_status:
  servers:
    entries:
      - alias: bad alias
        name: Bad
      - alias: bad alias
        name: Duplicate
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="alias"):
        load_config(str(config_file))


def test_duplicate_server_alias_rejected(tmp_path) -> None:
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        """
home_status:
  servers:
    entries:
      - alias: nas
        name: NAS 1
      - alias: nas
        name: NAS 2
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="unique"):
        load_config(str(config_file))


def test_timeout_of_one_entity_does_not_cancel_rest(tmp_path) -> None:
    service, _, _ = _service(
        tmp_path,
        ha=FakeHA(_states(), errors={"sensor.home_temperature"}),
        transport_name="telegram",
    )

    text = _run(service, "!casa", transport="telegram")

    assert "Temperatura: N/D" in text
    assert "Luces: 2 encendidas" in text
