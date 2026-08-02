from __future__ import annotations

import shlex
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ParsedCommand:
    name: str
    args: list[str]


def parse_command(text: str, *, prefix: str) -> ParsedCommand | None:
    normalized = text.strip()
    if not normalized.startswith(prefix):
        return None
    command_text = normalized[len(prefix) :].strip()
    if not command_text:
        return None
    try:
        parts = shlex.split(command_text)
    except ValueError:
        return None
    if not parts:
        return None
    return ParsedCommand(name=parts[0].lower(), args=parts[1:])
