"""Tests for the forgiving-streak algorithm in domain/habits.py.

Pure logic over a set of dates -- no DB/session needed. Run after ANY
change to the streak logic in elly_server/domain/habits.py (per
AGENTS.md).
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from elly_server.domain.habits import _compute_streak  # noqa: SLF001 (intentional, testing internals)

TODAY = date(2026, 7, 6)


def d(days_ago: int) -> date:
    return TODAY - timedelta(days=days_ago)


@pytest.mark.parametrize(
    ("label", "log_dates", "expected_streak", "expected_grace"),
    [
        ("first log ever (today only)", {d(0)}, 1, False),
        ("logged yesterday, not yet today", {d(1)}, 1, False),
        ("perfect streak, today pending", {d(1), d(2), d(3), d(4), d(5)}, 5, False),
        ("one real gap forgiven", {d(0), d(2), d(3)}, 3, True),
        ("two consecutive misses stop the streak", {d(0), d(3), d(4)}, 1, True),
        ("never logged", set(), 0, False),
    ],
)
def test_compute_streak(
    label: str, log_dates: set[date], expected_streak: int, expected_grace: bool
) -> None:
    result = _compute_streak(log_dates, TODAY)
    assert result["current_streak"] == expected_streak, label
    assert result["grace_day_used"] == expected_grace, label
