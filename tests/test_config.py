from __future__ import annotations

import pytest

from meshcore_control.auth.roles import Role
from meshcore_control.config import load_config


def test_config_loads_authorized_users_and_local_ha_url(tmp_path) -> None:
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        """
meshcore:
  transport: usb
  channel_index: 1
  serial_port: /dev/serial/by-id/meshcore-companion
  reconnect_initial_seconds: 2
  reconnect_max_seconds: 20
homeassistant:
  base_url: http://homeassistant.local:8123
  token: ""
status:
  entities:
    temperature:
      entity_id: sensor.living_room_temperature
      label: Temp
security:
  rate_limit:
    commands: 3
    window_seconds: 30
users:
  sender-1:
    name: tester
    role: readonly
""",
        encoding="utf-8",
    )

    config = load_config(str(config_file))

    assert config.meshcore.channel_index == 1
    assert config.meshcore.transport == "usb"
    assert config.meshcore.serial_port == "/dev/serial/by-id/meshcore-companion"
    assert config.homeassistant.base_url == "http://homeassistant.local:8123"
    assert config.users["sender-1"].role is Role.readonly
    assert config.room_policies["meshcore-usb:channel:1"].minimum_role is Role.readonly
    assert config.status_entities["temperature"].label == "Temp"
    assert config.security.rate_limit.commands == 3


def test_public_channel_zero_is_rejected(tmp_path) -> None:
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        """
meshcore:
  channel_index: 0
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="must not be 0"):
        load_config(str(config_file))


def test_usb_transport_requires_serial_port(tmp_path) -> None:
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        """
meshcore:
  transport: usb
  channel_index: 1
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="SERIAL"):
        load_config(str(config_file))


def test_homeassistant_transport_requires_ha_token(tmp_path) -> None:
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        """
meshcore:
  transport: homeassistant
  channel_index: 1
homeassistant:
  base_url: http://homeassistant.local:8123
  token: ""
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="HA_TOKEN"):
        load_config(str(config_file))
