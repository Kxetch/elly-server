"""Calendar events / time-blocks."""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Optional

from sqlalchemy import case, select
from sqlalchemy.orm import Session

from elly_server.db.models import Event, Habit
from elly_server.db.serialize import model_to_dict
from elly_server.domain import reminders as reminders_domain
from elly_server.domain.habits import generate_scheduled_events
from elly_server.domain.validation import require_nonblank
from elly_server.timeutil import now, parse_datetime


def _check_time_order(start_at, end_at) -> None:
    if end_at is not None and end_at <= start_at:
        raise ValueError("End time must be after the start time.")


# A multi-day event (e.g. a week-long vacation) must show up on every day
# it spans, not just the day it starts -- a query filtering purely on
# "start_at falls within this window" (the original, simpler approach)
# would only ever match a multi-day event's first day. This expression
# treats end_at as start_at itself when unset (a point-in-time event with
# no explicit end), so the overlap check below still only matches a
# point event on its own single day, exactly as before -- only events
# that actually have a later end_at get the new multi-day-overlap
# behavior. This is backend-only and does not affect how any event
# (including habit-generated ones) renders in the UI -- Timeline.tsx's
# own multi-day/all-day detection is a separate, deliberately narrow
# duration-only heuristic (see its own comment) so short overnight
# events like a habit's "18:30-02:00" block are never affected by this.
_effective_end = case((Event.end_at.is_(None), Event.start_at), else_=Event.end_at)


def _overlaps_range(start_dt, end_dt):
    """SQLAlchemy filter: does an Event's [start_at, effective_end] span
    overlap the [start_dt, end_dt] query window at all? Standard interval
    overlap check, used by every "events in this window" query below so
    a multi-day event appears on each day/range it actually spans."""
    return (Event.start_at <= end_dt) & (_effective_end >= start_dt)


def create_event(
    session: Session,
    title: str,
    start_at: str,
    end_at: Optional[str] = None,
    description: Optional[str] = None,
    habit_id: Optional[int] = None,
    color: Optional[str] = None,
) -> dict[str, Any]:
    if habit_id is not None and session.get(Habit, habit_id) is None:
        raise ValueError("That habit was not found.")
    start_dt = parse_datetime(start_at)
    end_dt = parse_datetime(end_at)
    _check_time_order(start_dt, end_dt)
    event = Event(
        title=require_nonblank(title, "title"),
        start_at=start_dt,
        end_at=end_dt,
        description=description,
        habit_id=habit_id,
        color=color,
    )
    session.add(event)
    session.flush()
    return model_to_dict(event)


def list_events_range(session: Session, start: str, end: str) -> list[dict[str, Any]]:
    start_dt = parse_datetime(start)
    end_dt = parse_datetime(end)
    # Pre-generate scheduled habit events within this range so they
    # appear without requiring a separate refresh step.
    generate_scheduled_events(session)
    stmt = (
        select(Event)
        .where(_overlaps_range(start_dt, end_dt))
        .order_by(Event.start_at)
    )
    return [model_to_dict(e) for e in session.scalars(stmt).all()]


def list_today(session: Session) -> list[dict[str, Any]]:
    today = now()
    start = today.replace(hour=0, minute=0, second=0, microsecond=0)
    # _overlaps_range's upper bound is inclusive (matches
    # list_events_range's existing T23:59:59-style convention below) --
    # end here is today's last microsecond, not tomorrow's midnight, so
    # an event starting exactly at tomorrow's midnight still correctly
    # falls outside "today" as before.
    end = start + timedelta(days=1) - timedelta(microseconds=1)
    stmt = (
        select(Event)
        .where(_overlaps_range(start, end))
        .order_by(Event.start_at)
    )
    return [model_to_dict(e) for e in session.scalars(stmt).all()]


def reschedule_event(
    session: Session, event_id: int, start_at: str, end_at: Optional[str] = None
) -> dict[str, Any]:
    event = session.get(Event, event_id)
    if event is None:
        raise ValueError("That event was not found.")
    new_start = parse_datetime(start_at)
    new_end = parse_datetime(end_at) if end_at is not None else event.end_at
    _check_time_order(new_start, new_end)
    event.start_at = new_start
    if end_at is not None:
        event.end_at = new_end
    session.flush()
    # A reminder's trigger_at was computed from the old start time --
    # must not silently keep firing at the old time once it moves.
    reminders_domain.recompute_reminder_for_target(session, "event", event_id)
    return model_to_dict(event)


def search_events(
    session: Session,
    query: str,
    start: Optional[str] = None,
    end: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Search events whose title contains *query* (case-insensitive),
    optionally within an ISO date range. Omit start/end to search all
    future events (starting from today)."""
    today = now()
    start_dt = parse_datetime(start) if start else today.replace(hour=0, minute=0, second=0, microsecond=0)
    end_dt = parse_datetime(end) if end else start_dt + timedelta(days=365)
    stmt = (
        select(Event)
        .where(Event.title.ilike(f"%{query}%"), _overlaps_range(start_dt, end_dt))
        .order_by(Event.start_at)
    )
    return [model_to_dict(e) for e in session.scalars(stmt).all()]


def delete_event(session: Session, event_id: int) -> bool:
    event = session.get(Event, event_id)
    if event is None:
        return False
    reminders_domain.delete_reminder_for(session, "event", event_id)
    session.delete(event)
    return True
