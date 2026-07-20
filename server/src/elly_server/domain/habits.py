"""Habit tracking with deliberately forgiving streak logic.

Design rationale (see the research writeup): habit formation research
(Lally et al., 2010) found a single missed day barely affects the
long-term trend, and shame-based tracking reliably backfires for
ADHD/RSD. So a streak here tolerates exactly one non-today gap before
resetting, and "today" is never counted as a miss (the day isn't over
yet). This is intentionally simple to reason about and explain to the
user -- not a points/scoring system.
"""

from __future__ import annotations

from datetime import date, time, timedelta, datetime
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from elly_server.db.models import Habit, HabitLog, Event
from elly_server.db.serialize import model_to_dict
from elly_server.domain import reminders as reminders_domain
from elly_server.domain.validation import require_nonblank
from elly_server.timeutil import now

# Monday=0..Sunday=6, matching Habit.scheduled_days' convention (see
# generate_scheduled_events below, which already matches against
# str(date.weekday())) and date.weekday() itself.
_WEEKDAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _format_scheduled_days(scheduled_days: str) -> str:
    indices = sorted({int(d) for d in scheduled_days.split(",") if d.strip().isdigit()})
    return ", ".join(_WEEKDAY_NAMES[i] for i in indices if 0 <= i <= 6)


def _assert_loggable_now(habit: Habit) -> None:
    """Raise a ValueError if *habit* can't be logged right now.

    Two independent gates, both optional depending on what's actually
    configured on the habit:
    - `scheduled_days`, if set: only loggable on one of those weekdays.
    - `scheduled_start`, if set: only loggable at or after that time of
      day (never before -- "shouldn't be completable beforehand").
      Deliberately does NOT gate on `scheduled_end` -- there's no
      matching upper bound requested, just a lower one.

    A habit with neither set (the common case) is always loggable, same
    as before this check existed. Applies to both the REST and MCP
    paths since both call log_habit() below -- see AGENTS.md: domain/*
    is the only place this kind of logic should live.
    """
    current = now()

    if habit.scheduled_days:
        allowed = {d.strip() for d in habit.scheduled_days.split(",") if d.strip()}
        if str(current.weekday()) not in allowed:
            raise ValueError(
                f"{habit.name} is scheduled for {_format_scheduled_days(habit.scheduled_days)}, not today."
            )

    if habit.scheduled_start:
        h, m = (habit.scheduled_start.split(":") + ["00"])[:2]
        if current.time() < time(int(h), int(m)):
            raise ValueError(f"{habit.name} isn't scheduled to start until {habit.scheduled_start} today.")


def create_habit(
    session: Session,
    name: str,
    cadence: str = "daily",
    tiny_version: Optional[str] = None,
    label: Optional[str] = None,
    scheduled_start: Optional[str] = None,
    scheduled_end: Optional[str] = None,
    scheduled_days: Optional[str] = None,
    auto_event: bool = True,
    color: Optional[str] = None,
) -> dict[str, Any]:
    habit = Habit(
        name=require_nonblank(name, "name"),
        cadence=cadence,
        tiny_version=tiny_version,
        label=label,
        scheduled_start=scheduled_start,
        scheduled_end=scheduled_end,
        scheduled_days=scheduled_days,
        auto_event=auto_event,
        color=color,
    )
    session.add(habit)
    session.flush()
    # Generate calendar events immediately so they show up without a
    # page refresh.
    has_schedule = bool(scheduled_start and scheduled_days)
    if has_schedule:
        generate_scheduled_events(session)
    return model_to_dict(habit)


def _find_habit(session: Session, habit_id: Optional[int], name: Optional[str]) -> Habit:
    habit: Optional[Habit] = None
    if habit_id is not None:
        habit = session.get(Habit, habit_id)
    elif name:
        stmt = select(Habit).where(Habit.name.ilike(f"%{name}%"), Habit.is_active.is_(True))
        habit = session.scalars(stmt).first()
    if habit is None:
        raise ValueError(f"Habit not found (id={habit_id!r}, name={name!r})")
    return habit


def log_habit(
    session: Session,
    habit_id: Optional[int] = None,
    name: Optional[str] = None,
    note: Optional[str] = None,
) -> dict[str, Any]:
    habit = _find_habit(session, habit_id, name)
    _assert_loggable_now(habit)
    session.add(HabitLog(habit_id=habit.id, note=note))
    session.flush()
    return _status_for_habit(session, habit)


def _log_dates(session: Session, habit_id: int) -> set[date]:
    stmt = select(HabitLog.logged_at).where(HabitLog.habit_id == habit_id)
    return {logged_at.date() for (logged_at,) in session.execute(stmt).all()}


def _compute_streak(log_dates: set[date], today: date) -> dict[str, Any]:
    if not log_dates:
        return {"current_streak": 0, "grace_day_used": False}

    # Don't walk back further than the earliest-ever log -- there's no
    # "missed day" before the habit existed, so that must never be
    # counted as a forgiven gap (that would misleadingly tell a brand
    # new habit "you already used your grace day").
    floor = min(log_dates)

    streak = 0
    grace_used = False
    cursor = today
    while cursor >= floor:
        if cursor in log_dates:
            streak += 1
            cursor -= timedelta(days=1)
            continue
        if cursor == today:
            # Today isn't over yet -- not having logged it *yet* isn't a miss.
            cursor -= timedelta(days=1)
            continue
        if not grace_used:
            grace_used = True
            cursor -= timedelta(days=1)
            continue
        break
    return {"current_streak": streak, "grace_day_used": grace_used}


def _status_for_habit(session: Session, habit: Habit) -> dict[str, Any]:
    dates = _log_dates(session, habit.id)
    today = now().date()
    last_14 = [today - timedelta(days=i) for i in range(14)]
    return {
        **model_to_dict(habit),
        "total_completions": len(dates),
        "last_logged": max(dates).isoformat() if dates else None,
        "completions_last_14_days": sum(1 for d in last_14 if d in dates),
        **_compute_streak(dates, today),
    }


def get_habit_status(
    session: Session,
    habit_id: Optional[int] = None,
    name: Optional[str] = None,
) -> dict[str, Any]:
    """Status for one habit (by id or name), or a summary of all active
    habits if neither is given."""
    if habit_id is None and name is None:
        return {"habits": list_all_habit_statuses(session)}
    habit = _find_habit(session, habit_id, name)
    return _status_for_habit(session, habit)


def set_habit_active(session: Session, habit_id: int, is_active: bool) -> dict[str, Any]:
    """Archive (or un-archive) a habit. Archiving keeps all its history --
    it just stops showing up. Never framed as quitting or failing.

    Archiving also removes any *future* scheduled calendar events for
    this habit -- generate_scheduled_events() pre-fills events up to
    ~1.5 years ahead, so without this an archived habit (which
    explicitly promises "it just stops showing up here") would keep
    appearing on the calendar for a long time after being archived.
    Past events are left alone -- they're real history, same as
    HabitLog rows. Un-archiving needs no matching regeneration step:
    list_events_range() already calls generate_scheduled_events() on
    every calendar load, which naturally refills the schedule forward
    from today the next time the calendar is viewed.
    """
    habit = session.get(Habit, habit_id)
    if habit is None:
        raise ValueError(f"Habit {habit_id} not found")
    habit.is_active = is_active
    if not is_active:
        session.query(Event).filter(Event.habit_id == habit_id, Event.start_at >= now()).delete()
        # Same reasoning as the future-events cleanup above -- a
        # reminder that kept firing for an archived habit would
        # contradict "it just stops showing up here".
        reminders_domain.delete_reminder_for(session, "habit", habit_id)
    session.flush()
    return model_to_dict(habit)


def delete_habit(session: Session, habit_id: int) -> bool:
    """Permanently delete a habit and all its associated data (logs + calendar events)."""
    habit = session.get(Habit, habit_id)
    if habit is None:
        return False
    # Delete associated habit logs
    session.query(HabitLog).filter(HabitLog.habit_id == habit_id).delete()
    # Delete associated calendar events
    session.query(Event).filter(Event.habit_id == habit_id).delete()
    reminders_domain.delete_reminder_for(session, "habit", habit_id)
    # Delete the habit itself
    session.delete(habit)
    return True


def update_habit(
    session: Session,
    habit_id: int,
    name: Optional[str] = None,
    tiny_version: Optional[str] = None,
    cadence: Optional[str] = None,
    label: Optional[str] = None,
    scheduled_start: Optional[str] = None,
    scheduled_end: Optional[str] = None,
    scheduled_days: Optional[str] = None,
    auto_event: Optional[bool] = None,
    color: Optional[str] = None,
) -> dict[str, Any]:
    habit = session.get(Habit, habit_id)
    if habit is None:
        raise ValueError(f"Habit {habit_id} not found")
    if name is not None:
        habit.name = require_nonblank(name, "name")
    if tiny_version is not None:
        habit.tiny_version = tiny_version
    if cadence is not None:
        habit.cadence = cadence
    if label is not None:
        habit.label = label
    if scheduled_start is not None:
        habit.scheduled_start = scheduled_start
    if scheduled_end is not None:
        habit.scheduled_end = scheduled_end
    if scheduled_days is not None:
        habit.scheduled_days = scheduled_days
    if auto_event is not None:
        habit.auto_event = auto_event
    if color is not None:
        habit.color = color
    session.flush()
    return model_to_dict(habit)


def list_all_habit_statuses(session: Session) -> list[dict[str, Any]]:
    stmt = select(Habit).where(Habit.is_active.is_(True))
    return [_status_for_habit(session, h) for h in session.scalars(stmt).all()]


def list_all_habit_logs(session: Session) -> list[dict[str, Any]]:
    """Every individual habit completion, raw (id, habit_id, logged_at,
    note) -- list_all_habit_statuses()/list_archived_habits() above only
    ever return *aggregate* stats (current_streak, total_completions,
    etc.) computed at read time, never the underlying log rows. A
    backup built from just those aggregates couldn't actually restore
    habit history -- streaks would reset to zero and past completions
    would be gone -- so domain/export.py's import_all_data() needs this
    instead. See PLAN.md/ASSESSMENT.md batch 4 for why this was added."""
    stmt = select(HabitLog).order_by(HabitLog.id)
    return [model_to_dict(h) for h in session.scalars(stmt).all()]


def list_archived_habits(session: Session) -> list[dict[str, Any]]:
    """Archived (is_active=False) habits, most recently created first.

    Archiving is deliberately framed as reversible ("it just stops
    showing up"), which only means something if there's actually a way
    to see and restore what's been archived -- this powers that view.
    """
    stmt = select(Habit).where(Habit.is_active.is_(False)).order_by(Habit.created_at.desc())
    return [_status_for_habit(session, h) for h in session.scalars(stmt).all()]


def get_habit_calendar(session: Session, days: int = 14) -> list[dict[str, Any]]:
    """Per-habit daily completion, oldest day first, ending today.

    Powers a heatmap-style view (one row per habit, one cell per day) --
    REST-only (not an MCP tool), since this is a visualization concern
    rather than something a conversation needs. A missing day here is
    just an empty cell, never a "miss" -- rendering, like the streak
    logic above, should never single out gaps as failures.
    """
    today = now().date()
    day_list = [today - timedelta(days=i) for i in range(days - 1, -1, -1)]
    stmt = select(Habit).where(Habit.is_active.is_(True))
    result = []
    for habit in session.scalars(stmt).all():
        logged = _log_dates(session, habit.id)
        result.append(
            {
                "habit_id": habit.id,
                "name": habit.name,
                "tiny_version": habit.tiny_version,
                "label": habit.label,
                "days": [
                    {"date": d.isoformat(), "completed": d in logged} for d in day_list
                ],
            }
        )
    return result


def _event_for_habit_on_date(session: Session, habit: Habit, target_date: date) -> dict[str, Any] | None:
    """Create a calendar event for *habit* on *target_date*, unless one
    already exists (dedup by habit_id + date). Returns the event dict or
    None if skipped/existing."""
    day_start = datetime(target_date.year, target_date.month, target_date.day)
    day_end = day_start + timedelta(days=1)
    existing = (
        session.execute(
            select(Event).where(
                Event.habit_id == habit.id,
                Event.start_at >= day_start,
                Event.start_at < day_end,
            )
        )
        .scalars()
        .first()
    )
    if existing is not None:
        return None

    if habit.scheduled_start:
        h, m = (habit.scheduled_start.split(":") + ["00"])[:2]
        start_dt = day_start.replace(hour=int(h), minute=int(m))
    else:
        start_dt = day_start.replace(hour=9, minute=0)

    if habit.scheduled_end:
        h, m = (habit.scheduled_end.split(":") + ["00"])[:2]
        end_dt = day_start.replace(hour=int(h), minute=int(m))
        if end_dt <= start_dt:
            end_dt += timedelta(days=1)
    else:
        end_dt = start_dt.replace(hour=start_dt.hour + 1)

    event = Event(
        title=habit.name,
        start_at=start_dt,
        end_at=end_dt,
        description=f"Scheduled habit: {habit.name}",
        habit_id=habit.id,
    )
    session.add(event)
    session.flush()
    return model_to_dict(event)


def generate_scheduled_events(session: Session, days_ahead: Optional[int] = None) -> list[dict[str, Any]]:
    """Create calendar events for scheduled habits (time range + days of
    week), day by day, filling through end of next year (so the current
    year's months always have events).

    Was also once used for a "finance" label (monthly recurring
    bills/salary via scheduled_day_of_month) -- that whole concept has
    moved to the Budget page/BudgetEntry now (see domain/budget.py's
    generate_scheduled_budget_events, and the migration that moved any
    existing finance-labelled habits forward into that table), so this
    function is routine/fitness-only these days.

    When *days_ahead* is omitted (the default), the horizon is set to the
    end of next year — so every month of the current year is filled, and
    events reach well into the following year. Repeated calls only fill
    forward from the latest existing event (rolling window). Dedup by
    habit_id + date makes it safe to call as often as you like.
    Returns the list of newly created event dicts.
    """
    today = now().date()
    if days_ahead is None:
        end_of_next_year = date(today.year + 1, 12, 31)
        days_ahead = (end_of_next_year - today).days
    created: list[dict[str, Any]] = []

    stmt = select(Habit).where(Habit.is_active.is_(True), Habit.auto_event.is_(True))
    habits_all = list(session.scalars(stmt).all())

    for habit in habits_all:
        label = (habit.label or "").lower()
        if label not in ("routine", "fitness", "") or not habit.scheduled_start:
            continue

        latest_start = session.scalar(
            select(func.max(Event.start_at)).where(Event.habit_id == habit.id)
        )
        if latest_start:
            start_from = max(today, latest_start.date() + timedelta(days=1))
        else:
            start_from = today

        for offset in range((start_from - today).days, days_ahead):
            target_date = today + timedelta(days=offset)
            weekday = str(target_date.weekday())
            if habit.scheduled_days:
                allowed = [d.strip() for d in habit.scheduled_days.split(",")]
                if weekday not in allowed:
                    continue
            ev = _event_for_habit_on_date(session, habit, target_date)
            if ev:
                created.append(ev)

    return created
