"""Tests for telegram_bot/rate_limiter.py's sliding-window limiter."""

from __future__ import annotations

from elly_server.telegram_bot.rate_limiter import SlidingWindowRateLimiter


def test_allows_up_to_max_calls() -> None:
    limiter = SlidingWindowRateLimiter(max_calls=3, window_seconds=60)
    assert limiter.allow() is True
    assert limiter.allow() is True
    assert limiter.allow() is True
    assert limiter.allow() is False  # 4th call within the window is rejected


def test_window_expiry_allows_more_calls(monkeypatch) -> None:
    limiter = SlidingWindowRateLimiter(max_calls=1, window_seconds=10)
    fake_time = [1000.0]
    monkeypatch.setattr(
        "elly_server.telegram_bot.rate_limiter.monotonic", lambda: fake_time[0]
    )

    assert limiter.allow() is True
    assert limiter.allow() is False  # still within the window

    fake_time[0] += 11  # advance past the window
    assert limiter.allow() is True
