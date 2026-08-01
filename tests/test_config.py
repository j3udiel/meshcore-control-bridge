from __future__ import annotations

import pytest

from meshcore_control.auth.roles import Role
from meshcore_control.config import load_config


def test_config_loads_authorized_users_and_local_ha_url(tmp_path) -> None:
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        """
meshcore:
  channel_index: 1
homeassistant:
  base_url: http://homeassistant.local:8123
  token: ""
users:
  sender-1:
    name: tester
    role: readonly
""",
        encoding="utf-8",
    )

    config = load_config(str(config_file))

    assert config.meshcore.channel_index == 1
    assert config.homeassistant.base_url == "http://homeassistant.local:8123"
    assert config.users["sender-1"].role is Role.readonly


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
