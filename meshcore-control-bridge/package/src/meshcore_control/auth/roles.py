from __future__ import annotations

from enum import IntEnum


class Role(IntEnum):
    readonly = 10
    home = 20
    operator = 30
    admin = 40


def parse_role(value: str) -> Role:
    try:
        return Role[value.strip().lower()]
    except KeyError as exc:
        valid = ", ".join(role.name for role in Role)
        raise ValueError(f"invalid role {value!r}; expected one of: {valid}") from exc
