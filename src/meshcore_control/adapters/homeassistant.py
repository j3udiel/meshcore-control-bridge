from __future__ import annotations

from dataclasses import dataclass
from typing import Any

_BEARER_SCHEME = "Bearer"
_MAX_JSON_BYTES = 1_000_000


@dataclass(frozen=True, slots=True)
class HomeAssistantStatus:
    available: bool
    message: str


class HomeAssistantClient:
    def __init__(
        self,
        *,
        base_url: str,
        token: str,
        verify_tls: bool,
        timeout_seconds: float,
        entity_aliases: dict[str, str] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._token = token
        self.verify_tls = verify_tls
        self.timeout_seconds = timeout_seconds
        self.entity_aliases = entity_aliases or {}

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"{_BEARER_SCHEME} {self._token}",
            "Content-Type": "application/json",
        }

    async def check_available(self) -> HomeAssistantStatus:
        try:
            import httpx
        except ImportError as exc:
            raise RuntimeError("httpx is required for Home Assistant API calls") from exc

        try:
            async with httpx.AsyncClient(
                base_url=self.base_url,
                headers=self._headers,
                verify=self.verify_tls,
                timeout=self.timeout_seconds,
            ) as client:
                response = await client.get("/api/")
                response.raise_for_status()
                return HomeAssistantStatus(available=True, message="OK")
        except Exception as exc:
            return HomeAssistantStatus(available=False, message=exc.__class__.__name__)

    async def get_config(self) -> dict[str, Any]:
        response = await self._get("/api/config")
        return dict(response)

    async def get_states(self) -> list[dict[str, Any]]:
        response = await self._get("/api/states")
        if not isinstance(response, list):
            raise ValueError("Home Assistant states response was not a list")
        return [dict(item) for item in response if isinstance(item, dict)]

    async def get_state(self, entity_or_alias: str) -> dict[str, Any]:
        entity_id = self.resolve_entity(entity_or_alias)
        response = await self._get(f"/api/states/{entity_id}")
        return dict(response)

    async def call_service(
        self, domain: str, service: str, payload: dict[str, Any]
    ) -> list[dict[str, Any]]:
        try:
            import httpx
        except ImportError as exc:
            raise RuntimeError("httpx is required for Home Assistant API calls") from exc

        async with httpx.AsyncClient(
            base_url=self.base_url,
            headers=self._headers,
            verify=self.verify_tls,
            timeout=self.timeout_seconds,
        ) as client:
            response = await client.post(f"/api/services/{domain}/{service}", json=payload)
            response.raise_for_status()
            _validate_response_size(response)
            return list(response.json())

    async def _get(self, path: str) -> Any:
        try:
            import httpx
        except ImportError as exc:
            raise RuntimeError("httpx is required for Home Assistant API calls") from exc

        async with httpx.AsyncClient(
            base_url=self.base_url,
            headers=self._headers,
            verify=self.verify_tls,
            timeout=self.timeout_seconds,
        ) as client:
            response = await client.get(path)
            response.raise_for_status()
            _validate_response_size(response)
            return response.json()

    def resolve_entity(self, entity_or_alias: str) -> str:
        return self.entity_aliases.get(entity_or_alias, entity_or_alias)


def _validate_response_size(response: Any) -> None:
    content = getattr(response, "content", b"")
    if isinstance(content, bytes) and len(content) > _MAX_JSON_BYTES:
        raise ValueError("Home Assistant response too large")
