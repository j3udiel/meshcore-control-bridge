from __future__ import annotations


class RateLimiter:
    def allow(self, sender_id: str) -> bool:
        return bool(sender_id)
