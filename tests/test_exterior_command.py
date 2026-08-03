from __future__ import annotations

import asyncio
import sqlite3
from dataclasses import dataclass
from typing import Any

import pytest

from meshcore_control.adapters.homeassistant import HomeAssistantStatus
from meshcore_control.app import BridgeService
from meshcore_control.auth.authorization import AuthorizedUser, Authorizer
from meshcore_control.auth.roles import Role
from meshcore_control.commands.router import CommandRouter
from meshcore_control.config import AppConfig, WeatherStatusConfig, load_config
from meshcore_control.homeassistant_app import HomeAssistantAppOptions
from meshcore_control.models import InboundMessage
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
from meshcore_control.transport.fake import FakeTransport

SENDER_ID = "meshcore-pubkey-prefix:configured-test-sender"


def entity_id(name: str) -> str:
    return ".".join(("sensor", name))


TEMPERATURE_ENTITY = entity_id("configured_temperature")
HUMIDITY_ENTITY = entity_id("configured_humidity")


@dataclass(slots=True)
class FakeHA:
    states: dict[str, dict[str, Any]]
    error: Exception | None = None

    async def check_available(self) -> HomeAssistantStatus:
        return HomeAssistantStatus(available=True, message="OK")

    async def get_config(self) -> dict[str, object]:
        return {"version": "2026.8.0", "location_name": "Home"}

    async def get_state(self, entity_or_alias: str) -> dict[str, Any]:
        if self.error is not None:
            raise self.error
        if entity_or_alias not in self.states:
            raise KeyError(entity_or_alias)
        return self.states[entity_or_alias]


def state(value: str, unit: str) -> dict[str, Any]:
    return {"state": value, "attributes": {"unit_of_measurement": unit}}


def build_service(
    connection: sqlite3.Connection,
    *,
    weather_status: WeatherStatusConfig,
    ha: FakeHA | None = None,
    normalized_audit: bool = False,
) -> tuple[BridgeService, FakeTransport]:
    registry = build_registry()
    legacy = AuditRepository(connection)
    audit_flow: AuditFlow | None = None
    if normalized_audit:
        audit_flow = AuditFlow(
            connection=connection,
            legacy=legacy,
            normalized=NormalizedAuditRepository(
                connection,
                NormalizedAuditSettings(
                    enabled=True,
                    audit_key=AuditKey(key=b"w" * AUDIT_KEY_MIN_BYTES, key_id="weather-key"),
                ),
            ),
        )
    services: dict[str, object] = {
        "registry": registry,
        "config": AppConfig(weather_status=weather_status),
    }
    if ha is not None:
        services["homeassistant"] = ha
    router = CommandRouter(
        registry=registry,
        authorizer=Authorizer({SENDER_ID: AuthorizedUser(SENDER_ID, "tester", Role.readonly)}),
        audit=legacy,
        audit_flow=audit_flow,
        services=services,
        prefix="!",
    )
    transport = FakeTransport()
    return (
        BridgeService(
            transport=transport,
            router=router,
            deduplicator=Deduplicator(connection, window_seconds=300),
            audit_flow=audit_flow,
            rate_limiter=RateLimiter(max_commands=10, window_seconds=60),
            channel_index=1,
        ),
        transport,
    )


def message(text: str, *, message_id: str = "weather-message") -> InboundMessage:
    return InboundMessage(
        transport="fake",
        message_id=message_id,
        sender_id=SENDER_ID,
        channel_index=1,
        text=text,
    )


def run_exterior(
    tmp_path,
    *,
    weather_status: WeatherStatusConfig,
    ha: FakeHA | None,
    message_id: str = "weather-message",
) -> str:
    service, _transport = build_service(
        connect_database(str(tmp_path / f"{message_id}.db")),
        weather_status=weather_status,
        ha=ha,
    )
    outbound = asyncio.run(service.process_message(message("!exterior", message_id=message_id)))
    assert outbound is not None
    return outbound.text


def test_exterior_reports_temperature_and_humidity(tmp_path) -> None:
    text = run_exterior(
        tmp_path,
        weather_status=WeatherStatusConfig(
            temperature_entity=TEMPERATURE_ENTITY,
            humidity_entity=HUMIDITY_ENTITY,
            label="Exterior",
        ),
        ha=FakeHA(
            {
                TEMPERATURE_ENTITY: state("24.6", "°C"),
                HUMIDITY_ENTITY: state("61", "%"),
            }
        ),
    )

    assert text == "Exterior: 24.6 °C · Humedad: 61 %"


def test_exterior_reports_only_configured_temperature(tmp_path) -> None:
    text = run_exterior(
        tmp_path,
        weather_status=WeatherStatusConfig(temperature_entity=TEMPERATURE_ENTITY),
        ha=FakeHA({TEMPERATURE_ENTITY: state("75.0", "°F")}),
    )

    assert text == "Exterior: 75.0 °F"


def test_exterior_temperature_unavailable(tmp_path) -> None:
    text = run_exterior(
        tmp_path,
        weather_status=WeatherStatusConfig(
            temperature_entity=TEMPERATURE_ENTITY,
            humidity_entity=HUMIDITY_ENTITY,
        ),
        ha=FakeHA(
            {
                TEMPERATURE_ENTITY: state("unavailable", "°C"),
                HUMIDITY_ENTITY: state("61", "%"),
            }
        ),
    )

    assert text == "Exterior: N/D · Humedad: 61 %"


def test_exterior_humidity_unavailable(tmp_path) -> None:
    text = run_exterior(
        tmp_path,
        weather_status=WeatherStatusConfig(
            temperature_entity=TEMPERATURE_ENTITY,
            humidity_entity=HUMIDITY_ENTITY,
        ),
        ha=FakeHA(
            {
                TEMPERATURE_ENTITY: state("24.6", "°C"),
                HUMIDITY_ENTITY: state("unknown", "%"),
            }
        ),
    )

    assert text == "Exterior: 24.6 °C · Humedad: N/D"


def test_exterior_missing_entity(tmp_path) -> None:
    text = run_exterior(
        tmp_path,
        weather_status=WeatherStatusConfig(temperature_entity=TEMPERATURE_ENTITY),
        ha=FakeHA({}),
    )

    assert text == "Exterior: N/D"


def test_exterior_home_assistant_timeout(tmp_path) -> None:
    text = run_exterior(
        tmp_path,
        weather_status=WeatherStatusConfig(temperature_entity=TEMPERATURE_ENTITY),
        ha=FakeHA({}, error=TimeoutError()),
    )

    assert text == "Exterior: N/D"


def test_exterior_uses_configurable_label_and_short_response(tmp_path) -> None:
    text = run_exterior(
        tmp_path,
        weather_status=WeatherStatusConfig(
            temperature_entity=TEMPERATURE_ENTITY,
            humidity_entity=HUMIDITY_ENTITY,
            label="Patio",
        ),
        ha=FakeHA(
            {
                TEMPERATURE_ENTITY: state("24.6", "°C"),
                HUMIDITY_ENTITY: state("61", "%"),
            }
        ),
    )

    assert text == "Patio: 24.6 °C · Humedad: 61 %"
    assert len(text) < 80


def test_exterior_accepts_32_character_label(tmp_path) -> None:
    label = "X" * 32
    text = run_exterior(
        tmp_path,
        weather_status=WeatherStatusConfig(temperature_entity=TEMPERATURE_ENTITY, label=label),
        ha=FakeHA({TEMPERATURE_ENTITY: state("24.6", "°C")}),
    )

    assert text == f"{label}: 24.6 °C"


def test_yaml_weather_status_rejects_too_long_label(tmp_path) -> None:
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        """
meshcore:
  transport: fake
  channel_index: 1
weather_status:
  temperature_entity: ""
  label: "XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX"
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="32 characters"):
        load_config(str(config_file))


def test_yaml_weather_status_rejects_newline_label(tmp_path) -> None:
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        """
meshcore:
  transport: fake
  channel_index: 1
weather_status:
  temperature_entity: ""
  label: "Exterior\\nPatio"
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="newlines"):
        load_config(str(config_file))


def test_app_weather_status_rejects_control_character_label() -> None:
    with pytest.raises(ValueError, match="control characters"):
        HomeAssistantAppOptions.from_mapping(
            {
                "channel_index": 1,
                "allow_unidentified_readonly_testing": True,
                "weather_status": {"label": "Ex\tterior"},
            }
        )


def test_weather_status_empty_label_defaults_to_exterior(tmp_path) -> None:
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        """
meshcore:
  transport: fake
  channel_index: 1
weather_status:
  temperature_entity: ""
  label: "   "
""",
        encoding="utf-8",
    )

    yaml_config = load_config(str(config_file))
    app_options = HomeAssistantAppOptions.from_mapping(
        {
            "channel_index": 1,
            "allow_unidentified_readonly_testing": True,
            "weather_status": {"label": ""},
        }
    )

    assert yaml_config.weather_status.label == "Exterior"
    assert app_options.weather_status.label == "Exterior"


def test_weather_status_label_validation_is_equivalent_for_app_and_yaml(tmp_path) -> None:
    label = "  Patio  "
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        f"""
meshcore:
  transport: fake
  channel_index: 1
weather_status:
  temperature_entity: ""
  label: "{label}"
""",
        encoding="utf-8",
    )

    yaml_config = load_config(str(config_file))
    app_options = HomeAssistantAppOptions.from_mapping(
        {
            "channel_index": 1,
            "allow_unidentified_readonly_testing": True,
            "weather_status": {"label": label},
        }
    )

    assert yaml_config.weather_status.label == "Patio"
    assert app_options.weather_status.label == "Patio"


def test_exterior_long_unit_drops_humidity_without_cutting_unit(tmp_path) -> None:
    long_unit = "unit-" + ("x" * 180)
    text = run_exterior(
        tmp_path,
        weather_status=WeatherStatusConfig(
            temperature_entity=TEMPERATURE_ENTITY,
            humidity_entity=HUMIDITY_ENTITY,
        ),
        ha=FakeHA(
            {
                TEMPERATURE_ENTITY: state("24.6", "°C"),
                HUMIDITY_ENTITY: state("61", long_unit),
            }
        ),
    )

    assert text == "Exterior: 24.6 °C"


def test_exterior_without_temperature_entity_is_safe(tmp_path) -> None:
    text = run_exterior(
        tmp_path,
        weather_status=WeatherStatusConfig(label="Exterior"),
        ha=FakeHA({}),
    )

    assert text == "Exterior: no configurado"


def test_exterior_command_name_is_normalized_audited(tmp_path) -> None:
    connection = connect_database(str(tmp_path / "audit.db"))
    service, _transport = build_service(
        connection,
        weather_status=WeatherStatusConfig(temperature_entity=TEMPERATURE_ENTITY),
        ha=FakeHA({TEMPERATURE_ENTITY: state("24.6", "°C")}),
        normalized_audit=True,
    )

    outbound = asyncio.run(service.process_message(message("!exterior")))

    assert outbound is not None
    rows = connection.execute(
        """
        SELECT event_type, command_name, metadata_json
        FROM normalized_audit_events
        ORDER BY id
        """
    ).fetchall()
    assert any(
        row["event_type"] == "command.execution" and row["command_name"] == "exterior"
        for row in rows
    )
    serialized = " ".join(" ".join(str(value) for value in row) for row in rows)
    assert TEMPERATURE_ENTITY not in serialized
    assert HUMIDITY_ENTITY not in serialized
    assert "24.6" not in serialized


def test_existing_ping_and_estado_behaviour_still_work(tmp_path) -> None:
    service, transport = build_service(
        connect_database(str(tmp_path / "audit.db")),
        weather_status=WeatherStatusConfig(temperature_entity=TEMPERATURE_ENTITY),
        ha=FakeHA({}),
    )

    ping = asyncio.run(service.process_message(message("!ping", message_id="ping")))
    estado = asyncio.run(service.process_message(message("!estado", message_id="estado")))

    assert ping is not None
    assert ping.text == "pong"
    assert estado is not None
    assert "HA: OK" in estado.text
    assert [item.text for item in transport.sent][:1] == ["pong"]
