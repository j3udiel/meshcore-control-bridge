from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import httpx

from meshcore_control.telegram.token import TelegramToken


class TelegramApiError(RuntimeError):
    pass


class TelegramRateLimitError(TelegramApiError):
    def __init__(self, retry_after: float) -> None:
        super().__init__("Telegram rate limit")
        self.retry_after = retry_after


class TelegramConflictError(TelegramApiError):
    pass


@dataclass(frozen=True, slots=True)
class TelegramBotApiClient:
    token: TelegramToken
    base_url: str = "https://api.telegram.org"
    timeout_seconds: float = 60.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "base_url", self.base_url.rstrip("/"))

    async def delete_webhook(self, *, drop_pending_updates: bool) -> None:
        await self._post(
            "deleteWebhook",
            {"drop_pending_updates": drop_pending_updates},
            timeout=10.0,
        )

    async def get_updates(
        self,
        *,
        offset: int | None,
        timeout: int,
        allowed_updates: Sequence[str] = ("message",),
    ) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {
            "timeout": timeout,
            "allowed_updates": list(allowed_updates),
        }
        if offset is not None:
            payload["offset"] = offset
        data = await self._post("getUpdates", payload, timeout=timeout + 10.0)
        result = data.get("result", [])
        if not isinstance(result, list):
            raise TelegramApiError("Telegram getUpdates result is invalid")
        updates: list[dict[str, Any]] = []
        for item in result:
            if isinstance(item, dict):
                updates.append(item)
        return updates

    async def send_message(
        self,
        *,
        chat_id: str,
        text: str,
    ) -> None:
        await self._post(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": text,
                "disable_web_page_preview": True,
            },
            timeout=15.0,
        )

    async def _post(
        self,
        method: str,
        payload: dict[str, Any],
        *,
        timeout: float,
    ) -> dict[str, Any]:
        url = f"{self.base_url}/bot{self.token.value}/{method}"
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            response = await client.post(url, json=payload)
        if response.status_code == 429:
            raise TelegramRateLimitError(_retry_after(response))
        if response.status_code == 409:
            raise TelegramConflictError("Telegram bot has another active update consumer")
        if response.status_code >= 400:
            raise TelegramApiError(f"Telegram API request failed with HTTP {response.status_code}")
        data = response.json()
        if not isinstance(data, dict) or data.get("ok") is not True:
            raise TelegramApiError("Telegram API response was not ok")
        return data


def _retry_after(response: httpx.Response) -> float:
    try:
        data = response.json()
    except ValueError:
        return 30.0
    if isinstance(data, dict):
        parameters = data.get("parameters")
        if isinstance(parameters, dict):
            retry_after = parameters.get("retry_after")
            if isinstance(retry_after, int | float) and retry_after > 0:
                return float(retry_after)
    return 30.0
