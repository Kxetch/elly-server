"""Tests for domain/habits.py's log-eligibility gate (_assert_loggable_now,
exercised through log_habit): a habit scheduled only for certain days
of the week isn't loggable on other days, and a habit with a scheduled
start time isn't loggable before that time on a scheduled day. See
test_habits_crud.py for general CRUD tests and test_habits_streak.py
for streak math -- this file is specifically about *when* logging is
allowed at all.

2026-07-13 is a Monday (weekday()==0) -- used as the fixed "today"
throughout via monkeypatching domain/habits.py's `now` import directly
(it's imported by name into that module's namespace, not called
through an injectable clock).
"""

from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import delete

from elly_server.db.base import get_session
from elly_server.db.models import Event, Habit, HabitLog
from elly_server.domain import habits as habits_domain

MONDAY = datetime(2026, 7, 13, 12, 0)  # noon Monday, arbitrary but unambiguous
TUESDAY = datetime(2026, 7, 14, 12, 0)


@pytest.fixture(autouse=True)
def _clean_tables() -> None:
    with get_session() as session:
        session.execute(delete(Event))
        session.execute(delete(HabitLog))
        session.execute(delete(Habit))


def _freeze(monkeypatch: pytest.MonkeyPatch, at: datetime) -> None:
    monkeypatch.setattr(habits_domain, "now", lambda: at)


# ---- No schedule set: unrestricted, same as before this feature -----------


def test_logging_allowed_any_day_any_time_with_no_schedule(monkeypatch: pytest.MonkeyPatch) -> None:
    _freeze(monkeypatch, datetime(2026, 7, 13, 3, 0))  # 3am Monday
    with get_session() as session:
        habit = habits_domain.create_habit(session, name="Drink water")
    with get_session() as session:
        result = habits_domain.log_habit(session, habit_id=habit["id"])
    assert result["total_completions"] == 1


# ---- scheduled_days gate ----------------------------------------------


def test_logging_allowed_on_a_scheduled_day(monkeypatch: pytest.MonkeyPatch) -> None:
    with get_session() as session:
        habit = habits_domain.create_habit(
            session, name="Gym", label="fitness", scheduled_days="0,2,4"
        )  # Mon/Wed/Fri
    _freeze(monkeypatch, MONDAY)
    with get_session() as session:
        result = habits_domain.log_habit(session, habit_id=habit["id"])
    assert result["total_completions"] == 1


def test_logging_rejected_on_a_non_scheduled_day(monkeypatch: pytest.MonkeyPatch) -> None:
    with get_session() as session:
        habit = habits_domain.create_habit(
            session, name="Gym", label="fitness", scheduled_days="0,2,4"
        )  # Mon/Wed/Fri
    _freeze(monkeypatch, TUESDAY)
    with get_session() as session:
        with pytest.raises(ValueError, match="Mon, Wed, Fri"):
            habits_domain.log_habit(session, habit_id=habit["id"])


def test_rejected_log_does_not_create_a_habit_log_row(monkeypatch: pytest.MonkeyPatch) -> None:
    with get_session() as session:
        habit = habits_domain.create_habit(session, name="Gym", label="fitness", scheduled_days="0")
    _freeze(monkeypatch, TUESDAY)
    with get_session() as session:
        with pytest.raises(ValueError):
            habits_domain.log_habit(session, habit_id=habit["id"])
    with get_session() as session:
        status = habits_domain.get_habit_status(session, habit_id=habit["id"])
    assert status["total_completions"] == 0


# ---- scheduled_start gate ------------------------------------------------


def test_logging_rejected_before_scheduled_start_time(monkeypatch: pytest.MonkeyPatch) -> None:
    with get_session() as session:
        habit = habits_domain.create_habit(
            session, name="Evening walk", label="routine", scheduled_start="18:00"
        )
    _freeze(monkeypatch, datetime(2026, 7, 13, 8, 0))  # 8am, before 18:00
    with get_session() as session:
        with pytest.raises(ValueError, match="18:00"):
            habits_domain.log_habit(session, habit_id=habit["id"])


def test_logging_allowed_exactly_at_scheduled_start_time(monkeypatch: pytest.MonkeyPatch) -> None:
    with get_session() as session:
        habit = habits_domain.create_habit(
            session, name="Evening walk", label="routine", scheduled_start="18:00"
        )
    _freeze(monkeypatch, datetime(2026, 7, 13, 18, 0))  # exactly on time
    with get_session() as session:
        result = habits_domain.log_habit(session, habit_id=habit["id"])
    assert result["total_completions"] == 1


def test_logging_allowed_after_scheduled_start_time(monkeypatch: pytest.MonkeyPatch) -> None:
    with get_session() as session:
        habit = habits_domain.create_habit(
            session, name="Evening walk", label="routine", scheduled_start="18:00"
        )
    _freeze(monkeypatch, datetime(2026, 7, 13, 23, 59))
    with get_session() as session:
        result = habits_domain.log_habit(session, habit_id=habit["id"])
    assert result["total_completions"] == 1


def test_logging_not_gated_by_scheduled_end(monkeypatch: pytest.MonkeyPatch) -> None:
    """No upper bound -- only 'not before it starts' was asked for."""
    with get_session() as session:
        habit = habits_domain.create_habit(
            session,
            name="Evening walk",
            label="routine",
            scheduled_start="18:00",
            scheduled_end="19:00",
        )
    _freeze(monkeypatch, datetime(2026, 7, 13, 23, 0))  # well past scheduled_end
    with get_session() as session:
        result = habits_domain.log_habit(session, habit_id=habit["id"])
    assert result["total_completions"] == 1


# ---- Both gates combined ------------------------------------------------


def test_logging_allowed_when_both_day_and_time_match(monkeypatch: pytest.MonkeyPatch) -> None:
    with get_session() as session:
        habit = habits_domain.create_habit(
            session,
            name="Gym",
            label="fitness",
            scheduled_days="0",  # Monday only
            scheduled_start="06:00",
        )
    _freeze(monkeypatch, datetime(2026, 7, 13, 7, 0))  # Monday, after 06:00
    with get_session() as session:
        result = habits_domain.log_habit(session, habit_id=habit["id"])
    assert result["total_completions"] == 1


def test_logging_rejected_when_day_matches_but_too_early(monkeypatch: pytest.MonkeyPatch) -> None:
    with get_session() as session:
        habit = habits_domain.create_habit(
            session,
            name="Gym",
            label="fitness",
            scheduled_days="0",  # Monday only
            scheduled_start="06:00",
        )
    _freeze(monkeypatch, datetime(2026, 7, 13, 5, 0))  # Monday, before 06:00
    with get_session() as session:
        with pytest.raises(ValueError, match="06:00"):
            habits_domain.log_habit(session, habit_id=habit["id"])


def test_logging_rejected_when_time_passed_but_wrong_day(monkeypatch: pytest.MonkeyPatch) -> None:
    with get_session() as session:
        habit = habits_domain.create_habit(
            session,
            name="Gym",
            label="fitness",
            scheduled_days="0",  # Monday only
            scheduled_start="06:00",
        )
    _freeze(monkeypatch, TUESDAY)  # right time-of-day, wrong day
    with get_session() as session:
        with pytest.raises(ValueError, match="Mon"):
            habits_domain.log_habit(session, habit_id=habit["id"])


# ---- Message formatting ---------------------------------------------------


def test_format_scheduled_days_orders_and_dedupes() -> None:
    assert habits_domain._format_scheduled_days("4,0,2,0") == "Mon, Wed, Fri"  # noqa: SLF001


def test_format_scheduled_days_all_seven() -> None:
    assert (
        habits_domain._format_scheduled_days("0,1,2,3,4,5,6")  # noqa: SLF001
        == "Mon, Tue, Wed, Thu, Fri, Sat, Sun"
    )
