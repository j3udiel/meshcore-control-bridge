from __future__ import annotations

from dataclasses import dataclass

from meshcore_control.auth.roles import Role
from meshcore_control.models import InboundMessage


@dataclass(frozen=True, slots=True)
class AuthorizedUser:
    sender_id: str
    name: str
    role: Role


@dataclass(frozen=True, slots=True)
class RoomPolicy:
    room_id: str
    enabled: bool = True
    minimum_role: Role = Role.readonly
    allow_commands: bool = True


class Authorizer:
    def __init__(
        self,
        users: dict[str, AuthorizedUser],
        *,
        room_policies: dict[str, RoomPolicy] | None = None,
    ) -> None:
        self._users = users
        self._room_policies = room_policies or {}

    def get_user(self, sender_id: str) -> AuthorizedUser | None:
        return self._users.get(sender_id)

    def require(self, sender_id: str, minimum_role: Role) -> AuthorizedUser | None:
        user = self.get_user(sender_id)
        if user is None or user.role < minimum_role:
            return None
        return user

    def room_policy(self, message: InboundMessage) -> RoomPolicy | None:
        if not self._room_policies:
            return None
        if message.source_room is None:
            return None
        return self._room_policies.get(message.source_room.room_id)

    def allows_room(self, message: InboundMessage) -> bool:
        policy = self.room_policy(message)
        if policy is None:
            return not self._room_policies
        return policy.enabled and policy.allow_commands

    def require_message(
        self,
        message: InboundMessage,
        minimum_role: Role,
    ) -> AuthorizedUser | None:
        if not self.allows_room(message):
            return None
        sender = message.sender.sender_id if message.sender is not None else message.sender_id
        user = self.get_user(sender)
        if user is None:
            return None
        policy = self.room_policy(message)
        required_role = minimum_role
        if policy is not None and policy.minimum_role > required_role:
            required_role = policy.minimum_role
        if user.role < required_role:
            return None
        return user
