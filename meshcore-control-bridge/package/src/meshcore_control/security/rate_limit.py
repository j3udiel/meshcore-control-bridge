from __future__ import annotations

import time
from collections import defaultdict, deque


class RateLimiter:
    def __init__(self, *, max_commands: int = 5, window_seconds: int = 60) -> None:
        self.max_commands = max_commands
        self.window_seconds = window_seconds
        self._events: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, sender_id: str) -> bool:
        if not sender_id:
            return False
        now = time.monotonic()
        events = self._events[sender_id]
        while events and now - events[0] > self.window_seconds:
            events.popleft()
        if len(events) >= self.max_commands:
            return False
        events.append(now)
        return True
