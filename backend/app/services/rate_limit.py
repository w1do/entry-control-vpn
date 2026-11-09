from __future__ import annotations

import time
from collections import defaultdict, deque


class LoginRateLimiter:
    """In-memory login brute-force limiter (single worker / single admin)."""

    def __init__(
        self,
        *,
        max_attempts: int = 5,
        window_seconds: int = 300,
        block_seconds: int = 900,
    ) -> None:
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self.block_seconds = block_seconds
        self._attempts: dict[str, deque[float]] = defaultdict(deque)
        self._blocked_until: dict[str, float] = {}

    def retry_after(self, key: str) -> int | None:
        now = time.monotonic()
        blocked_until = self._blocked_until.get(key)
        if blocked_until is not None:
            if blocked_until > now:
                return int(blocked_until - now) + 1
            self._blocked_until.pop(key, None)
        attempts = self._attempts.get(key)
        if not attempts:
            return None
        cutoff = now - self.window_seconds
        while attempts and attempts[0] < cutoff:
            attempts.popleft()
        if len(attempts) >= self.max_attempts:
            self._blocked_until[key] = now + self.block_seconds
            self._attempts.pop(key, None)
            return self.block_seconds
        return None

    def fail(self, key: str) -> None:
        self._attempts[key].append(time.monotonic())

    def reset(self, key: str) -> None:
        self._attempts.pop(key, None)
        self._blocked_until.pop(key, None)
