"""Reminders & alarms for tasks, events, and habits (Sprint 4 of the
dev-note-driven roadmap -- see PLAN.md section 0.2 for the full design
rationale and the 2026-07-15 decisions this implements).

Scope, as confirmed with the user before this was built:
- Exactly one reminder per target (task/event/habit) -- set_reminder()
  replaces any existing one rather than creating a second. The table
  itself doesn't enforce this (no unique constraint), so it could
  support more than one per target later without a migration.
- "alarm" is a one-shot notification + a distinct sound -- never a
  repeating/snoozing alarm-clock that keeps ringing until dismissed.
- Two delivery channels, both best-effort (a failure in one must never
  block or crash the other): Telegram (reliable regardless of host
  headlessness) and native macOS notifications (only meaningful when
  elly-api runs directly on a Mac with a display/speakers -- see
  domain/notifications.py::send_native_notification). Windows/Linux
  native delivery is a documented future follow-up, not built here.
- No MCP/chat tools yet -- UI-only for this pass (see api routers).

Tasks and events are genuinely one-shot: `trigger_at` is computed once
(from the target's own due_at/start_at plus a signed offset) and
`fired_at` being set means "done forever". Habits are the one
exception -- a habit recurs daily (or on specific scheduled days), so
there's no single upcoming occurrence to compute a fixed trigger_at
from. For habit targets, `trigger_at` is recomputed fresh against
*today* on every scheduler check, and "already fired" means "already
fired today" (comparing dates, not exact timestamps) -- see
check_and_send_reminders() below. Habit reminders also skip firing
(without erroring) if the habit's already been logged today -- an
already-done habit getting reminded about would be exactly the kind of
nag AGENTS.md's no-shame principle rules out.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from elly_server.db.models import Event, Habit, HabitLog, Reminder, Task
from elly_server.db.serialize import model_to_dict
from elly_server.domain import settings as settings_domain
from elly_server.timeutil import now

logger = logging.getLogger("elly_server.domain.reminders")

TARGET_TYPES = ("task", "event", "habit")
KINDS = ("notification", "alarm")


def _validate_target_type(target_type: str) -> None:
    if target_type not in TARGET_TYPES:
        raise ValueError(f"target_type must be one of {TARGET_TYPES}, got {target_type!r}")


def _validate_kind(kind: str) -> None:
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}, got {kind!r}")


def _compute_trigger_at(
    session: Session, target_type: str, target_id: int, offset_minutes: int, on_date: Optional[date] = None
) -> datetime:
    """The absolute datetime a reminder for this target should fire at.

    Task/event targets: based on the target's own actual date
    (due_at/start_at) -- a fixed point in time.
    Habit targets: always relative to *on_date* (today, unless given)
    combined with the habit's scheduled_start time-of-day, since a
    habit recurs daily rather than having one single upcoming
    occurrence. Raises ValueError if the target doesn't exist or has
    no relevant date/time to anchor to (no due date, no scheduled
    time).
    """
    if target_type == "task":
        task = session.get(Task, target_id)
        if task is None:
            raise ValueError(f"Task {target_id} not found")
        if task.due_at is None:
            raise ValueError("This task has no due date to base a reminder on")
        base = task.due_at
    elif target_type == "event":
        event = session.get(Event, target_id)
        if event is None:
            raise ValueError(f"Event {target_id} not found")
        base = event.start_at
    elif target_type == "habit":
        habit = session.get(Habit, target_id)
        if habit is None:
            raise ValueError(f"Habit {target_id} not found")
        if not habit.scheduled_start:
            raise ValueError("This habit has no scheduled time block to base a reminder on")
        day = on_date or now().date()
        h, m = (habit.scheduled_start.split(":") + ["00"])[:2]
        base = datetime(day.year, day.month, day.day, int(h), int(m))
    else:
        _validate_target_type(target_type)
        raise AssertionError("unreachable")  # pragma: no cover

    return base + timedelta(minutes=offset_minutes)


def set_reminder(
    session: Session,
    target_type: str,
    target_id: int,
    kind: str,
    offset_minutes: int,
    message: Optional[str] = None,
) -> dict[str, Any]:
    """Create a reminder for a task/event/habit, replacing any existing
    one for that same target (exactly one reminder per target -- see
    module docstring)."""
    _validate_target_type(target_type)
    _validate_kind(kind)
    trigger_at = _compute_trigger_at(session, target_type, target_id, offset_minutes)

    existing = session.scalars(
        select(Reminder).where(Reminder.target_type == target_type, Reminder.target_id == target_id)
    ).first()
    if existing is not None:
        session.delete(existing)
        session.flush()

    reminder = Reminder(
        target_type=target_type,
        target_id=target_id,
        kind=kind,
        offset_minutes=offset_minutes,
        trigger_at=trigger_at,
        message=message,
    )
    session.add(reminder)
    session.flush()
    return model_to_dict(reminder)


def get_reminder_for(session: Session, target_type: str, target_id: int) -> Optional[dict[str, Any]]:
    row = session.scalars(
        select(Reminder).where(Reminder.target_type == target_type, Reminder.target_id == target_id)
    ).first()
    return model_to_dict(row) if row is not None else None


def list_reminders(session: Session) -> list[dict[str, Any]]:
    """All reminders, each enriched with its target's title -- powers a
    Settings management view so a reminder set from deep inside a task/
    event/habit form has somewhere to be reviewed/cancelled later
    without re-opening that exact item. Ordered soonest-first; a stale
    reminder whose target has vanished (shouldn't normally happen --
    cascade-delete should already have removed it) is silently skipped
    rather than shown with a broken title.
    """
    today = now().date()
    result: list[dict[str, Any]] = []
    for reminder in session.scalars(select(Reminder).order_by(Reminder.trigger_at)).all():
        info = _target_info(session, reminder.target_type, reminder.target_id, today)
        if info is None:
            continue
        row = model_to_dict(reminder)
        row["target_title"] = info["title"]
        result.append(row)
    return result


def delete_reminder_for(session: Session, target_type: str, target_id: int) -> bool:
    """Delete the reminder for a target, if any. Called both directly
    (user removes a reminder) and as a cascade-delete hook from
    delete_task/delete_event/delete_habit -- a deleted task/event/habit
    must never leave a dangling reminder that fires for something that
    no longer exists."""
    row = session.scalars(
        select(Reminder).where(Reminder.target_type == target_type, Reminder.target_id == target_id)
    ).first()
    if row is None:
        return False
    session.delete(row)
    session.flush()
    return True


def recompute_reminder_for_target(session: Session, target_type: str, target_id: int) -> Optional[dict[str, Any]]:
    """Recompute a reminder's trigger_at from its stored offset against
    the target's *current* date -- called after rescheduling a task's
    due date or an event's start time. A reminder for "15 min before"
    an event that then gets moved to a different day must not silently
    keep firing at the old time. Also un-fires it (clears fired_at) so
    a reminder that already fired for the old time but got moved back
    into the future fires again for the new time. No-op (returns None)
    if there's no reminder for this target at all."""
    row = session.scalars(
        select(Reminder).where(Reminder.target_type == target_type, Reminder.target_id == target_id)
    ).first()
    if row is None:
        return None
    try:
        row.trigger_at = _compute_trigger_at(session, target_type, target_id, row.offset_minutes)
    except ValueError:
        # The target no longer has a date to anchor to (e.g. due_at was
        # cleared entirely) -- the reminder can't mean anything now.
        session.delete(row)
        session.flush()
        return None
    row.fired_at = None
    session.flush()
    return model_to_dict(row)


def _habit_logged_on(session: Session, habit_id: int, day: date) -> bool:
    day_start = datetime(day.year, day.month, day.day)
    day_end = day_start + timedelta(days=1)
    return (
        session.scalars(
            select(HabitLog).where(
                HabitLog.habit_id == habit_id, HabitLog.logged_at >= day_start, HabitLog.logged_at < day_end
            )
        ).first()
        is not None
    )


def _target_info(session: Session, target_type: str, target_id: int, today: date) -> Optional[dict[str, Any]]:
    """Human-readable title + whether this target is already "done"
    (in which case a reminder about it would just be a nag -- skip
    delivering, but still mark it handled). Returns None if the target
    no longer exists (shouldn't normally happen -- cascade-delete
    should have removed the reminder already -- but never crash the
    whole scheduler tick over one stale row)."""
    if target_type == "task":
        task = session.get(Task, target_id)
        if task is None:
            return None
        return {"title": task.title, "already_done": task.status == "done"}
    if target_type == "event":
        event = session.get(Event, target_id)
        if event is None:
            return None
        return {"title": event.title, "already_done": False}
    if target_type == "habit":
        habit = session.get(Habit, target_id)
        if habit is None:
            return None
        return {"title": habit.name, "already_done": _habit_logged_on(session, habit.id, today)}
    return None  # pragma: no cover -- target_type already validated at creation


async def _send_telegram_async(token: str, chat_id: int, text: str) -> None:
    from telegram import Bot

    bot = Bot(token=token)
    await bot.send_message(chat_id=chat_id, text=text)


def _deliver_telegram(session: Session, title: str, kind: str) -> None:
    """Best-effort -- any failure here (not paired, network error, bad
    token) is logged and swallowed, never raised, so it can't block
    native delivery for the same reminder.

    Imports domain.telegram lazily (function-local) -- telegram.py
    imports domain.chat, which imports domain.tasks/calendar/habits,
    which import domain.reminders (for cascade-delete/reschedule
    hooks) -- a module-level import here would be a second circular
    import, same shape as deliver_reminder()'s notifications import
    above: tasks -> reminders -> telegram -> chat -> tasks.
    """
    from elly_server.domain import telegram as telegram_domain

    try:
        token = settings_domain.get_effective_telegram_bot_token(session)
        if not token:
            return
        link = telegram_domain.get_link(session)
        chat_id = link.get("chat_id")
        if chat_id is None:
            return
        prefix = "\u23f0 Alarm" if kind == "alarm" else "\U0001f514 Reminder"
        text = f"{prefix}: {title}"
        asyncio.run(_send_telegram_async(token, chat_id, text))
    except Exception:
        logger.exception("Failed to deliver Telegram reminder")


def deliver_reminder(session: Session, title: str, kind: str) -> None:
    """Send a reminder/alarm through every available channel. Each
    channel is independently best-effort (see module docstring) --
    this function itself never raises, and one channel failing can
    never prevent the other from being attempted (also matters for
    check_and_send_reminders' loop over multiple reminders: an
    unhandled exception here would otherwise abandon every remaining
    reminder in that same scheduler tick, not just this one).

    Imports domain.notifications lazily (function-local, not at module
    level) -- notifications.py itself imports domain.tasks/calendar/
    habits (for its morning/evening check-in summaries), and
    domain.tasks imports domain.reminders (for cascade-delete/reschedule
    hooks), so a module-level import here would be a circular import:
    tasks -> reminders -> notifications -> tasks.
    """
    from elly_server.domain import notifications as notifications_domain

    _deliver_telegram(session, title, kind)
    label = "Alarm" if kind == "alarm" else "Reminder"
    try:
        notifications_domain.send_native_notification(
            "Elly", label, title, play_sound=(kind == "alarm")
        )
    except Exception:
        logger.exception("Failed to deliver native notification for reminder")


def check_and_send_reminders(session: Session) -> int:
    """Scheduler entry point -- called every ~60s alongside the
    existing morning/evening notification check (same polling
    granularity; "alarm" is confirmed one-shot/gentle, not a precision
    timer). Returns how many reminders were actually delivered (an
    already-done target that gets skipped still counts as "handled",
    not "sent" -- see _target_info's already_done)."""
    now_ = now()
    today = now_.date()
    sent = 0

    for reminder in session.scalars(select(Reminder)).all():
        try:
            if reminder.target_type == "habit":
                try:
                    reminder.trigger_at = _compute_trigger_at(
                        session, reminder.target_type, reminder.target_id, reminder.offset_minutes, on_date=today
                    )
                except ValueError:
                    # Habit's schedule was cleared -- nothing to
                    # recompute against; drop the now-meaningless
                    # reminder entirely.
                    session.delete(reminder)
                    continue
                already_fired_today = reminder.fired_at is not None and reminder.fired_at.date() == today
                eligible = not already_fired_today and now_ >= reminder.trigger_at
            else:
                eligible = reminder.fired_at is None and now_ >= reminder.trigger_at

            if not eligible:
                continue

            info = _target_info(session, reminder.target_type, reminder.target_id, today)
            if info is None:
                # Stale reminder for a deleted target that somehow
                # wasn't cascade-cleaned -- remove it rather than
                # firing forever.
                session.delete(reminder)
                continue

            reminder.fired_at = now_
            if info["already_done"]:
                continue  # handled, but not a nag about something already done

            deliver_reminder(session, info["title"], reminder.kind)
            sent += 1
        except Exception:
            # One bad reminder (an unexpected bug, not one of the
            # already-handled ValueError cases above) must never
            # abandon every other reminder still waiting in this same
            # scheduler tick -- log and move on, matching the module's
            # "never crash the scheduler loop" design goal.
            logger.exception(
                "Unexpected error processing reminder %s (target_type=%s, target_id=%s)",
                reminder.id, reminder.target_type, reminder.target_id,
            )

    session.flush()
    return sent
