"""Simple in-process sliding-window rate limiter.

Not `slowapi` (that's FastAPI/ASGI middleware -- the Telegram bot is a
separate process that calls `domain.chat.send_message()` directly, the
same "separate process, shared domain layer" pattern as the MCP server
and REST API, so it never goes through FastAPI at all). Single-process,
single-user, so a plain in-memory deque is all that's needed here --
no Redis or other external store.
"""

from __future__ import annotations

from collections import deque
from time import monotonic


class SlidingWindowRateLimiter:
    def __init__(self, max_calls: int, window_seconds: float) -> None:
        self._max_calls = max_calls
        self._window_seconds = window_seconds
        self._calls: deque[float] = deque()

    def allow(self) -> bool:
        """Record an attempt now and return whether it's within the limit."""
        t = monotonic()
        while self._calls and t - self._calls[0] > self._window_seconds:
            self._calls.popleft()
        if len(self._calls) >= self._max_calls:
            return False
        self._calls.append(t)
        return True
