from __future__ import annotations

from dataclasses import dataclass

from meshcore_control.auth.roles import Role


@dataclass(frozen=True, slots=True)
class AuthorizedUser:
    sender_id: str
    name: str
    role: Role


class Authorizer:
    def __init__(self, users: dict[str, AuthorizedUser]) -> None:
        self._users = users

    def get_user(self, sender_id: str) -> AuthorizedUser | None:
        return self._users.get(sender_id)

    def require(self, sender_id: str, minimum_role: Role) -> AuthorizedUser | None:
        user = self.get_user(sender_id)
        if user is None or user.role < minimum_role:
            return None
        return user
