"""Tests for domain/habits.py's CRUD surface -- create/update
validation, archive/unarchive round-tripping. Streak math itself is
covered separately in test_habits_streak.py."""

from __future__ import annotations

import pytest
from sqlalchemy import delete

from elly_server.db.base import get_session
from elly_server.db.models import Event, Habit, HabitLog
from elly_server.domain import habits


@pytest.fixture(autouse=True)
def _clean_tables() -> None:
    with get_session() as session:
        session.execute(delete(Event))
        session.execute(delete(HabitLog))
        session.execute(delete(Habit))


def test_create_habit_rejects_blank_name() -> None:
    with get_session() as session:
        with pytest.raises(ValueError, match="can't be empty"):
            habits.create_habit(session, name="   ")


def test_create_habit_strips_name_whitespace() -> None:
    with get_session() as session:
        habit = habits.create_habit(session, name="  Drink water  ")
    assert habit["name"] == "Drink water"


def test_update_habit_rejects_blank_name() -> None:
    with get_session() as session:
        habit = habits.create_habit(session, name="Original")
    with get_session() as session:
        with pytest.raises(ValueError, match="can't be empty"):
            habits.update_habit(session, habit["id"], name="   ")


def test_create_habit_accepts_fitness_label() -> None:
    with get_session() as session:
        habit = habits.create_habit(session, name="Run", label="fitness")
    assert habit["label"] == "fitness"


def test_archive_then_unarchive_round_trip() -> None:
    with get_session() as session:
        habit = habits.create_habit(session, name="Meditate")

    with get_session() as session:
        archived = habits.set_habit_active(session, habit["id"], False)
    assert archived["is_active"] is False

    with get_session() as session:
        active_list = habits.list_all_habit_statuses(session)
    assert habit["id"] not in [h["id"] for h in active_list]

    with get_session() as session:
        archived_list = habits.list_archived_habits(session)
    assert habit["id"] in [h["id"] for h in archived_list]

    with get_session() as session:
        restored = habits.set_habit_active(session, habit["id"], True)
    assert restored["is_active"] is True

    with get_session() as session:
        active_list_after = habits.list_all_habit_statuses(session)
        archived_list_after = habits.list_archived_habits(session)
    assert habit["id"] in [h["id"] for h in active_list_after]
    assert habit["id"] not in [h["id"] for h in archived_list_after]


def test_list_archived_habits_empty_when_none_archived() -> None:
    with get_session() as session:
        habits.create_habit(session, name="Still active")
    with get_session() as session:
        assert habits.list_archived_habits(session) == []


def test_archiving_scheduled_habit_removes_future_events_but_keeps_past() -> None:
    """Archiving explicitly promises 'it just stops showing up here' --
    that has to include the calendar, not just the habit list."""
    from datetime import timedelta

    from elly_server.db.models import Event
    from elly_server.timeutil import now

    with get_session() as session:
        habit = habits.create_habit(
            session, name="Workout", label="fitness", scheduled_start="09:00", scheduled_days="0,1,2,3,4,5,6"
        )
        past_event = Event(
            title="Workout", start_at=now() - timedelta(days=2), habit_id=habit["id"]
        )
        future_event = Event(
            title="Workout", start_at=now() + timedelta(days=2), habit_id=habit["id"]
        )
        session.add_all([past_event, future_event])
        session.flush()
        past_id, future_id = past_event.id, future_event.id

    with get_session() as session:
        habits.set_habit_active(session, habit["id"], False)

    with get_session() as session:
        assert session.get(Event, past_id) is not None, "past events are history, must survive archiving"
        assert session.get(Event, future_id) is None, "future events must not outlive an archived habit"
