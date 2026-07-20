"""Full data export -- a self-hosted app's users need a way to back up
their own data without touching the SQLite file directly (and touching
it directly wouldn't even work well here, since sensitive fields are
encrypted at rest -- see domain/crypto.py). This reuses the same
domain functions everything else in the app already calls, so the
export is always in sync with what search/get_recent_notes/etc. would
return -- there's no separate, divergent query path to keep correct.

Deliberately NOT included: the local access token, the encryption key,
Telegram chat linkage, and chat conversation history -- an export is
meant to move/backup a user's *content*, not the security material
that protects it or their AI conversation transcripts.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from elly_server.db.models import BudgetEntry, Event, Habit, HabitLog, Memory, Note, Task
from elly_server.domain import budget, calendar, habits, memory, notes, tasks
from elly_server.domain.budget import _to_cents
from elly_server.timeutil import now, parse_datetime

_FIVE_YEARS = timedelta(days=365 * 5)


def export_all_data(session: Session) -> dict[str, Any]:
    """Everything a user would reasonably want in a personal backup,
    as one JSON-serializable dict."""
    today = now()
    # A wide-enough window to capture essentially all real usage
    # without needing a true "no bound" query path in calendar.py.
    range_start = (today - _FIVE_YEARS).isoformat()
    range_end = (today + _FIVE_YEARS).isoformat()

    return {
        "exported_at": today.isoformat(),
        "notes": notes.search_notes(session, limit=100_000),
        "tasks": {
            "open": tasks.list_pending_tasks(session),
            "completed": tasks.list_completed_tasks(session, limit=100_000),
        },
        "habits": {
            "active": habits.list_all_habit_statuses(session),
            "archived": habits.list_archived_habits(session),
        },
        # Raw completion log rows, not just the aggregate streak stats
        # in "habits" above -- see list_all_habit_logs()'s docstring.
        # Needed to actually restore habit history on import, not just
        # display it once at export time.
        "habit_logs": habits.list_all_habit_logs(session),
        "events": calendar.list_events_range(session, range_start, range_end),
        # Grouped-by-type content strings -- convenient to skim in the
        # raw JSON, but lossy (no importance/timestamps). "memories"
        # below is the raw, restorable version.
        "memory": memory.get_profile_summary(session),
        "memories": memory.list_all_memories(session),
        # Added after the Budget page shipped (income/expense tracking) --
        # was missing here for a while, meaning "Export my data" silently
        # left out everything on the Budget page despite claiming to be
        # a full backup. list_entries() with no `kind` filter returns
        # both income and expenses, one-off and recurring.
        "budget_entries": budget.list_entries(session, limit=100_000),
    }


# ---- Import (restore) ------------------------------------------------------
#
# Deliberately v1-simple, per ASSESSMENT.md section 4.1: only ever
# *replaces into an empty database*, never merges. The real
# disaster-recovery flow this exists for -- new machine, fresh install,
# "get my data back" -- always starts from empty anyway, and a merge
# path risks silent duplicates/conflicts that are far worse than just
# refusing to run. If a genuine merge-into-existing-data need shows up
# later, that's a deliberate v2, not a corner cut here.
#
# Original ids and timestamps are preserved (not reassigned) rather
# than recreated through create_note()/create_task()/etc.:
#   - ids: an Event's habit_id/budget_entry_id and a subtask's
#     parent_task_id are plain FK references by id. Preserving the
#     original ids means those references are already correct with no
#     remapping pass; SQLite happily accepts an explicit INTEGER
#     PRIMARY KEY value and continues autoincrementing from the
#     highest one seen afterward.
#   - timestamps: a restored diary entry from six months ago should
#     still read as six months old, not as if it just happened -- mood/
#     energy history, habit streaks, and budget totals-by-month would
#     all be silently wrong otherwise.
# This does mean import_all_data() constructs ORM rows directly
# instead of going through the usual create_*() domain functions (which
# have no "preserve this exact id/timestamp" mode, by design -- that's
# correctly not something a normal create call should ever do).


def _parse_dt(value: Any):
    return parse_datetime(value) if value is not None else None


def _database_is_empty(session: Session) -> bool:
    for model in (Note, Task, Habit, HabitLog, Event, Memory, BudgetEntry):
        if session.execute(select(model.id).limit(1)).first() is not None:
            return False
    return True


def _import_notes(session: Session, rows: list[dict[str, Any]]) -> int:
    for row in rows:
        session.add(Note(
            id=row["id"],
            type=row.get("type", "note"),
            title=row.get("title"),
            body=row["body"],
            mood=row.get("mood"),
            energy=row.get("energy"),
            tags=row.get("tags") or [],
            created_at=_parse_dt(row.get("created_at")) or now(),
            updated_at=_parse_dt(row.get("updated_at")) or now(),
        ))
    return len(rows)


def _import_habits(session: Session, habits_payload: dict[str, Any]) -> int:
    rows = list(habits_payload.get("active") or []) + list(habits_payload.get("archived") or [])
    for row in rows:
        session.add(Habit(
            id=row["id"],
            name=row["name"],
            cadence=row.get("cadence", "daily"),
            tiny_version=row.get("tiny_version"),
            label=row.get("label"),
            scheduled_start=row.get("scheduled_start"),
            scheduled_end=row.get("scheduled_end"),
            scheduled_days=row.get("scheduled_days"),
            scheduled_day_of_month=row.get("scheduled_day_of_month"),
            color=row.get("color"),
            auto_event=row.get("auto_event", True),
            is_active=row.get("is_active", True),
            created_at=_parse_dt(row.get("created_at")) or now(),
        ))
    return len(rows)


def _import_habit_logs(session: Session, rows: list[dict[str, Any]]) -> int:
    for row in rows:
        session.add(HabitLog(
            id=row["id"],
            habit_id=row["habit_id"],
            logged_at=_parse_dt(row.get("logged_at")) or now(),
            note=row.get("note"),
        ))
    return len(rows)


def _import_budget_entries(session: Session, rows: list[dict[str, Any]]) -> int:
    for row in rows:
        session.add(BudgetEntry(
            id=row["id"],
            kind=row["kind"],
            category=row["category"],
            color=row.get("color"),
            amount_cents=_to_cents(row["amount"]),
            quantity=row.get("quantity", 1),
            unit_label=row.get("unit_label"),
            note=row.get("note"),
            is_recurring=row.get("is_recurring", False),
            recurrence_day_of_month=row.get("recurrence_day_of_month"),
            auto_event=row.get("auto_event", True),
            created_at=_parse_dt(row.get("created_at")) or now(),
        ))
    return len(rows)


def _import_tasks(session: Session, tasks_payload: dict[str, Any]) -> int:
    rows = list(tasks_payload.get("open") or []) + list(tasks_payload.get("completed") or [])
    # A subtask must be inserted after its parent -- foreign_keys=ON
    # (db/base.py) means SQLite checks parent_task_id per-statement,
    # not just at commit. The export doesn't guarantee parent-before-
    # child ordering (it's two flat open/completed lists), so insert in
    # waves: whatever's insertable right now, repeat until nothing's
    # left.
    remaining = {row["id"]: row for row in rows}
    inserted_ids: set[int] = set()
    while remaining:
        ready = [
            row for row in remaining.values()
            if row.get("parent_task_id") is None or row.get("parent_task_id") in inserted_ids
        ]
        if not ready:
            # A parent id genuinely missing from the export -- shouldn't
            # happen from our own export_all_data(), but don't infinite-
            # loop on a malformed/hand-edited file. Import what's left
            # as root tasks rather than silently dropping them.
            ready = list(remaining.values())
        for row in ready:
            session.add(Task(
                id=row["id"],
                title=row["title"],
                due_at=_parse_dt(row.get("due_at")),
                estimate_minutes=row.get("estimate_minutes"),
                priority=row.get("priority"),
                status=row.get("status", "open"),
                parent_task_id=row.get("parent_task_id"),
                created_at=_parse_dt(row.get("created_at")) or now(),
                completed_at=_parse_dt(row.get("completed_at")),
            ))
            inserted_ids.add(row["id"])
            del remaining[row["id"]]
        session.flush()
    return len(rows)


def _import_events(session: Session, rows: list[dict[str, Any]]) -> int:
    for row in rows:
        session.add(Event(
            id=row["id"],
            title=row["title"],
            start_at=_parse_dt(row["start_at"]),
            end_at=_parse_dt(row.get("end_at")),
            description=row.get("description"),
            habit_id=row.get("habit_id"),
            budget_entry_id=row.get("budget_entry_id"),
            color=row.get("color"),
            created_at=_parse_dt(row.get("created_at")) or now(),
        ))
    return len(rows)


def _import_memories(session: Session, rows: list[dict[str, Any]]) -> int:
    for row in rows:
        session.add(Memory(
            id=row["id"],
            content=row["content"],
            type=row.get("type", "general"),
            importance=row.get("importance", 0.5),
            created_at=_parse_dt(row.get("created_at")) or now(),
            last_accessed=_parse_dt(row.get("last_accessed")) or now(),
            access_count=row.get("access_count", 0),
        ))
    return len(rows)


def import_all_data(session: Session, data: dict[str, Any]) -> dict[str, int]:
    """Restore a backup produced by export_all_data(). Refuses to run
    unless the database is completely empty (see module note above for
    why this is v1's "replace-into-empty-database" choice, not a
    merge).

    Returns how many rows of each kind were imported, so the caller
    can show a concrete confirmation ("47 notes, 12 habits...") instead
    of a bare "done".
    """
    if not _database_is_empty(session):
        raise ValueError(
            "Import only works into an empty elly database (a fresh install or "
            "reinstall). This database already has content, so importing on top "
            "of it is refused rather than risking silent duplicates."
        )

    counts: dict[str, int] = {}
    try:
        counts["notes"] = _import_notes(session, data.get("notes") or [])
        counts["habits"] = _import_habits(session, data.get("habits") or {})
        counts["budget_entries"] = _import_budget_entries(session, data.get("budget_entries") or [])
        # Habits and budget entries need to actually exist before an
        # event/habit_log that references one of them by id can be
        # inserted -- see the foreign_keys=ON note above.
        session.flush()

        counts["tasks"] = _import_tasks(session, data.get("tasks") or {})
        counts["habit_logs"] = _import_habit_logs(session, data.get("habit_logs") or [])
        counts["memories"] = _import_memories(session, data.get("memories") or [])
        session.flush()

        counts["events"] = _import_events(session, data.get("events") or [])
        session.flush()
    except (KeyError, TypeError) as exc:
        raise ValueError(f"That file doesn't look like a valid elly export: {exc}") from exc

    return counts
