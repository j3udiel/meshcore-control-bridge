from __future__ import annotations

import asyncio
import sys
import types
from typing import Any

from meshcore_control.adapters.homeassistant import HomeAssistantClient


class FakeResponse:
    def __init__(self, payload: Any, *, status_error: Exception | None = None) -> None:
        self.payload = payload
        self.status_error = status_error
        self.content = b"{}"

    def raise_for_status(self) -> None:
        if self.status_error is not None:
            raise self.status_error

    def json(self) -> Any:
        return self.payload


class FakeAsyncClient:
    requests: list[tuple[str, dict[str, str]]] = []

    def __init__(self, **kwargs: Any) -> None:
        self.headers = dict(kwargs["headers"])

    async def __aenter__(self) -> FakeAsyncClient:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def get(self, path: str) -> FakeResponse:
        self.requests.append((path, self.headers))
        if path == "/api/":
            return FakeResponse({"message": "API running."})
        if path == "/api/config":
            return FakeResponse({"version": "2026.1.0"})
        if path == "/api/states/sensor.test":
            return FakeResponse({"state": "23", "attributes": {"unit_of_measurement": "C"}})
        raise AssertionError(f"unexpected path {path}")


def test_homeassistant_client_read_methods(monkeypatch, caplog) -> None:
    fake_httpx = types.SimpleNamespace(AsyncClient=FakeAsyncClient)
    monkeypatch.setitem(sys.modules, "httpx", fake_httpx)
    client = HomeAssistantClient(
        base_url="http://homeassistant.local:8123",
        token="test-token-not-real",
        verify_tls=True,
        timeout_seconds=5,
    )

    status = asyncio.run(client.check_available())
    config = asyncio.run(client.get_config())
    state = asyncio.run(client.get_state("sensor.test"))

    assert status.available is True
    assert config["version"] == "2026.1.0"
    assert state["state"] == "23"
    assert FakeAsyncClient.requests[0][1]["Authorization"] == "Bearer test-token-not-real"
    assert "test-token-not-real" not in caplog.text
