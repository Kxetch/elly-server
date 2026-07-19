"""Tests for domain/calendar.py -- event CRUD, search, and the
scheduled-habit-event generation triggered by list_events_range."""

from __future__ import annotations

import pytest
from sqlalchemy import delete

from elly_server.db.base import get_session
from elly_server.db.models import Event, Habit, HabitLog
from elly_server.domain import calendar, habits


@pytest.fixture(autouse=True)
def _clean_tables() -> None:
    with get_session() as session:
        session.execute(delete(Event))
        session.execute(delete(HabitLog))
        session.execute(delete(Habit))


def test_create_event_basic() -> None:
    with get_session() as session:
        event = calendar.create_event(
            session, title="Dentist", start_at="2026-08-01T15:00:00", end_at="2026-08-01T15:45:00"
        )
    assert event["title"] == "Dentist"
    assert event["habit_id"] is None


def test_create_event_with_unknown_habit_id_raises() -> None:
    with get_session() as session:
        with pytest.raises(ValueError, match="not found"):
            calendar.create_event(session, title="X", start_at="2026-08-01T09:00:00", habit_id=999)


def test_create_event_rejects_blank_title() -> None:
    with get_session() as session:
        with pytest.raises(ValueError, match="can't be empty"):
            calendar.create_event(session, title="   ", start_at="2026-08-01T09:00:00")


def test_create_event_rejects_end_before_start() -> None:
    with get_session() as session:
        with pytest.raises(ValueError, match="after the start time"):
            calendar.create_event(
                session, title="Backwards", start_at="2026-08-01T10:00:00", end_at="2026-08-01T09:00:00"
            )


def test_create_event_rejects_end_equal_to_start() -> None:
    with get_session() as session:
        with pytest.raises(ValueError, match="after the start time"):
            calendar.create_event(
                session, title="Zero-length", start_at="2026-08-01T10:00:00", end_at="2026-08-01T10:00:00"
            )


def test_reschedule_event_rejects_end_before_new_start() -> None:
    with get_session() as session:
        event = calendar.create_event(
            session, title="Meeting", start_at="2026-08-01T10:00:00", end_at="2026-08-01T11:00:00"
        )
    with get_session() as session:
        # Only moving start_at forward past the existing (unchanged) end_at.
        with pytest.raises(ValueError, match="after the start time"):
            calendar.reschedule_event(session, event["id"], start_at="2026-08-01T12:00:00")


def test_create_event_with_valid_habit_id() -> None:
    with get_session() as session:
        habit = habits.create_habit(session, name="Drink water")
    with get_session() as session:
        event = calendar.create_event(
            session, title="Water", start_at="2026-08-01T09:00:00", habit_id=habit["id"]
        )
    assert event["habit_id"] == habit["id"]


def test_reschedule_event() -> None:
    with get_session() as session:
        event = calendar.create_event(session, title="Meeting", start_at="2026-08-01T10:00:00")
    with get_session() as session:
        updated = calendar.reschedule_event(session, event["id"], start_at="2026-08-01T14:00:00")
    assert updated["start_at"] == "2026-08-01T14:00:00"


def test_reschedule_unknown_event_raises() -> None:
    with get_session() as session:
        with pytest.raises(ValueError, match="not found"):
            calendar.reschedule_event(session, 999, start_at="2026-08-01T14:00:00")


def test_delete_event() -> None:
    with get_session() as session:
        event = calendar.create_event(session, title="Temp", start_at="2026-08-01T10:00:00")
    with get_session() as session:
        assert calendar.delete_event(session, event["id"]) is True
    with get_session() as session:
        assert calendar.delete_event(session, event["id"]) is False  # already gone


def test_search_events_by_title() -> None:
    with get_session() as session:
        calendar.create_event(session, title="Team Standup", start_at="2026-08-02T09:00:00")
        calendar.create_event(session, title="Dentist Appointment", start_at="2026-08-03T09:00:00")

    with get_session() as session:
        results = calendar.search_events(session, query="dentist", start="2026-08-01", end="2026-08-10")
    assert len(results) == 1
    assert results[0]["title"] == "Dentist Appointment"


def test_search_events_case_insensitive_and_scoped_by_date() -> None:
    with get_session() as session:
        calendar.create_event(session, title="Standup", start_at="2026-08-02T09:00:00")
        calendar.create_event(session, title="Standup", start_at="2026-09-15T09:00:00")

    with get_session() as session:
        results = calendar.search_events(session, query="STANDUP", start="2026-08-01", end="2026-08-31")
    assert len(results) == 1


def test_list_events_range_returns_events_in_window() -> None:
    with get_session() as session:
        calendar.create_event(session, title="In range", start_at="2026-08-05T09:00:00")
        calendar.create_event(session, title="Out of range", start_at="2026-09-05T09:00:00")

    with get_session() as session:
        results = calendar.list_events_range(session, "2026-08-01T00:00:00", "2026-08-31T23:59:59")
    titles = [e["title"] for e in results]
    assert "In range" in titles
    assert "Out of range" not in titles


def test_list_events_range_triggers_scheduled_habit_generation() -> None:
    """list_events_range pre-generates scheduled habit events so a
    routine habit's calendar block appears without a separate refresh
    step -- this is the documented, intentional side effect."""
    with get_session() as session:
        habits.create_habit(
            session,
            name="Work Hours",
            label="routine",
            scheduled_start="09:00",
            scheduled_end="17:00",
            scheduled_days="0,1,2,3,4",
        )

    with get_session() as session:
        results = calendar.list_events_range(session, "2026-08-01T00:00:00", "2026-08-07T23:59:59")

    assert any(e["title"] == "Work Hours" for e in results)


def test_list_events_range_is_idempotent_no_duplicate_habit_events() -> None:
    with get_session() as session:
        habits.create_habit(
            session,
            name="Work Hours",
            label="routine",
            scheduled_start="09:00",
            scheduled_end="17:00",
            scheduled_days="0,1,2,3,4",
        )

    with get_session() as session:
        calendar.list_events_range(session, "2026-08-01T00:00:00", "2026-08-07T23:59:59")
    with get_session() as session:
        results = calendar.list_events_range(session, "2026-08-01T00:00:00", "2026-08-07T23:59:59")

    # Exactly one event per matching weekday in range, not doubled by
    # the second call.
    work_events = [e for e in results if e["title"] == "Work Hours"]
    assert len(work_events) == len({e["start_at"] for e in work_events})


class TestMultiDayEventOverlap:
    """A multi-day event (e.g. a week-long vacation) must appear on
    every day/range it spans, not just the day it starts on -- the
    original query only ever matched an event's start_at against the
    window, so a 7-day event would silently vanish from every day but
    its first. Fixed via a real interval-overlap check in every "events
    in this window" query (list_events_range, list_today, search_events
    all share it)."""

    def test_multi_day_event_appears_on_a_middle_day_query(self) -> None:
        with get_session() as session:
            calendar.create_event(
                session, title="Vacation", start_at="2026-08-01T00:00:00", end_at="2026-08-07T23:59:59"
            )
        with get_session() as session:
            # Query just day 4 of the 7-day span -- the event started
            # 3 days before this window and ends 3 days after it.
            results = calendar.list_events_range(session, "2026-08-04T00:00:00", "2026-08-04T23:59:59")
        assert any(e["title"] == "Vacation" for e in results)

    def test_multi_day_event_appears_on_its_last_day(self) -> None:
        with get_session() as session:
            calendar.create_event(
                session, title="Vacation", start_at="2026-08-01T00:00:00", end_at="2026-08-07T23:59:59"
            )
        with get_session() as session:
            results = calendar.list_events_range(session, "2026-08-07T00:00:00", "2026-08-07T23:59:59")
        assert any(e["title"] == "Vacation" for e in results)

    def test_multi_day_event_does_not_appear_before_it_starts_or_after_it_ends(self) -> None:
        with get_session() as session:
            calendar.create_event(
                session, title="Vacation", start_at="2026-08-01T00:00:00", end_at="2026-08-07T23:59:59"
            )
        with get_session() as session:
            before = calendar.list_events_range(session, "2026-07-25T00:00:00", "2026-07-31T23:59:59")
            after = calendar.list_events_range(session, "2026-08-08T00:00:00", "2026-08-14T23:59:59")
        assert not any(e["title"] == "Vacation" for e in before)
        assert not any(e["title"] == "Vacation" for e in after)

    def test_multi_day_event_shows_in_todays_list_when_today_falls_within_its_span(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from datetime import datetime

        monkeypatch.setattr(calendar, "now", lambda: datetime(2026, 8, 4, 12, 0, 0))
        with get_session() as session:
            calendar.create_event(
                session, title="Vacation", start_at="2026-08-01T00:00:00", end_at="2026-08-07T23:59:59"
            )
        with get_session() as session:
            results = calendar.list_today(session)
        assert any(e["title"] == "Vacation" for e in results)

    def test_point_in_time_event_with_no_end_only_matches_its_own_day(self) -> None:
        """Regression guard: a no-end_at event must not become a
        "matches every future query forever" event just because it has
        no end date to bound it -- effective_end falls back to start_at
        itself in that case."""
        with get_session() as session:
            calendar.create_event(session, title="Instant", start_at="2026-08-01T09:00:00")
        with get_session() as session:
            same_day = calendar.list_events_range(session, "2026-08-01T00:00:00", "2026-08-01T23:59:59")
            later = calendar.list_events_range(session, "2026-08-02T00:00:00", "2026-08-31T23:59:59")
        assert any(e["title"] == "Instant" for e in same_day)
        assert not any(e["title"] == "Instant" for e in later)

    def test_search_events_finds_a_multi_day_event_from_a_middle_day_window(self) -> None:
        with get_session() as session:
            calendar.create_event(
                session, title="Summer Vacation", start_at="2026-08-01T00:00:00", end_at="2026-08-07T23:59:59"
            )
        with get_session() as session:
            results = calendar.search_events(
                session, query="vacation", start="2026-08-04", end="2026-08-04"
            )
        assert any(e["title"] == "Summer Vacation" for e in results)

    def test_overnight_event_crossing_midnight_still_only_matches_the_days_it_actually_touches(self) -> None:
        """Regression guard for the bug this exact overlap logic must
        NOT reintroduce: a short overnight event (e.g. a habit's evening
        shift block, 18:30-02:00) crosses two calendar dates but is NOT
        a multi-day event -- it must still show up on both the day it
        starts and the day it ends (correct, pre-existing behavior for
        any overnight event), but must NOT show up on a day two days
        later just because "crossing midnight" might naively look
        similar to a multi-day span to some heuristic."""
        with get_session() as session:
            calendar.create_event(
                session, title="Night Shift", start_at="2026-08-01T18:30:00", end_at="2026-08-02T02:00:00"
            )
        with get_session() as session:
            day_it_starts = calendar.list_events_range(session, "2026-08-01T00:00:00", "2026-08-01T23:59:59")
            day_it_ends = calendar.list_events_range(session, "2026-08-02T00:00:00", "2026-08-02T23:59:59")
            two_days_later = calendar.list_events_range(session, "2026-08-03T00:00:00", "2026-08-03T23:59:59")
        assert any(e["title"] == "Night Shift" for e in day_it_starts)
        assert any(e["title"] == "Night Shift" for e in day_it_ends)
        assert not any(e["title"] == "Night Shift" for e in two_days_later)
