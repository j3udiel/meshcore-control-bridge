#!/usr/bin/env python3
from __future__ import annotations

import argparse
import getpass
import sys
import time
from typing import Any

import httpx


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Safely discover Telegram private chat_id and user_id for PR23 testing."
    )
    parser.add_argument("--timeout", type=int, default=60, help="seconds to wait")
    parser.add_argument(
        "--api-base-url",
        default="https://api.telegram.org",
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args()

    token = getpass.getpass("Telegram bot token: ").strip()
    if not token:
        print("token is required", file=sys.stderr)
        return 2

    client = httpx.Client(timeout=15.0, follow_redirects=False)
    try:
        if not _get_me(client, args.api_base_url, token):
            return 1
        print("Open the private chat with the bot, press Start, and send one text message.")
        offset = _initial_offset(client, args.api_base_url, token)
        deadline = time.monotonic() + max(args.timeout, 1)
        while time.monotonic() < deadline:
            updates = _get_updates(
                client,
                args.api_base_url,
                token,
                offset=offset,
                timeout=min(10, max(1, int(deadline - time.monotonic()))),
            )
            for update in updates:
                update_id = update.get("update_id")
                if isinstance(update_id, int):
                    offset = update_id + 1
                identity = _extract_private_human_identity(update)
                if identity is None:
                    continue
                chat_id, user_id = identity
                print(f'allowed_private_chat_id: "{chat_id}"')
                print(f'allowed_user_id: "{user_id}"')
                return 0
        print("timed out waiting for a private human text message", file=sys.stderr)
        return 1
    finally:
        client.close()


def _get_me(client: httpx.Client, base_url: str, token: str) -> bool:
    data = _post(client, base_url, token, "getMe", {})
    if data is None:
        return False
    if data.get("ok") is not True:
        print("Telegram getMe failed", file=sys.stderr)
        return False
    print("Telegram bot token accepted.")
    return True


def _initial_offset(client: httpx.Client, base_url: str, token: str) -> int | None:
    updates = _get_updates(client, base_url, token, offset=None, timeout=0)
    max_update_id = None
    for update in updates:
        update_id = update.get("update_id")
        if isinstance(update_id, int):
            max_update_id = update_id if max_update_id is None else max(max_update_id, update_id)
    return max_update_id + 1 if max_update_id is not None else None


def _get_updates(
    client: httpx.Client,
    base_url: str,
    token: str,
    *,
    offset: int | None,
    timeout: int,
) -> list[dict[str, Any]]:
    payload: dict[str, Any] = {"timeout": timeout, "allowed_updates": ["message"]}
    if offset is not None:
        payload["offset"] = offset
    data = _post(client, base_url, token, "getUpdates", payload)
    if data is None:
        return []
    result = data.get("result", [])
    return [item for item in result if isinstance(item, dict)] if isinstance(result, list) else []


def _post(
    client: httpx.Client,
    base_url: str,
    token: str,
    method: str,
    payload: dict[str, Any],
) -> dict[str, Any] | None:
    url = f"{base_url.rstrip('/')}/bot{token}/{method}"
    try:
        response = client.post(url, json=payload)
    except httpx.HTTPError:
        print(f"Telegram {method} request failed", file=sys.stderr)
        return None
    if response.status_code == 409:
        print(
            "Telegram returned HTTP 409. Another consumer or webhook is active for this bot.",
            file=sys.stderr,
        )
        return None
    if response.status_code >= 400:
        print(f"Telegram {method} returned HTTP {response.status_code}", file=sys.stderr)
        return None
    try:
        data = response.json()
    except ValueError:
        print(f"Telegram {method} returned invalid JSON", file=sys.stderr)
        return None
    return data if isinstance(data, dict) else None


def _extract_private_human_identity(update: dict[str, Any]) -> tuple[str, str] | None:
    message = update.get("message")
    if not isinstance(message, dict):
        return None
    if not isinstance(message.get("text"), str) or not message["text"].strip():
        return None
    chat = message.get("chat")
    sender = message.get("from")
    if not isinstance(chat, dict) or not isinstance(sender, dict):
        return None
    if chat.get("type") != "private":
        return None
    if sender.get("is_bot") is True:
        return None
    chat_id = chat.get("id")
    user_id = sender.get("id")
    if not isinstance(chat_id, int | str) or not isinstance(user_id, int | str):
        return None
    return str(chat_id), str(user_id)


if __name__ == "__main__":
    raise SystemExit(main())
