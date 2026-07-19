"""Datetime helpers.

Design decision: every datetime in Elly is naive local wall-clock time
(no timezone awareness, no UTC conversion). This is a single-user,
single-machine, single-timezone MVP -- ADHD time-blindness is about
making time *visible and concrete* for one person's actual day, not
about distributed-systems correctness. Revisit if/when this needs to
sync across timezones (e.g. traveling, or a future mobile client in a
different zone).
"""

from __future__ import annotations

import calendar
from datetime import date, datetime


def now() -> datetime:
    return datetime.now()


def months_ahead(today: date, months: int) -> list[date]:
    """First-of-month dates for the next *months* months, starting with
    *today*'s own month. Shared by domain/habits.py and domain/budget.py
    -- both generate monthly-recurring calendar events (bills/salary,
    now via BudgetEntry; previously "finance"-labelled habits)."""
    result: list[date] = []
    for m in range(months):
        month = today.month + m
        year = today.year + (month - 1) // 12
        month = ((month - 1) % 12) + 1
        result.append(date(year, month, 1))
    return result


def last_day_of_month(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]


def parse_datetime(value: str | datetime | None) -> datetime | None:
    """Best-effort parse of a datetime produced by an LLM or a human.

    Accepts full ISO-8601 ("2026-07-06T09:30:00"), a bare date
    ("2026-07-06", assumed midnight), a date+space+time
    ("2026-07-06 09:30"), or the literal "now"/"today".
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    text = value.strip()
    if not text:
        return None
    if text.lower() in {"now", "today"}:
        return now()
    text = text.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    raise ValueError(f"Could not parse datetime: {value!r}")
