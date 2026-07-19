"""Tasks/to-dos, including AI-assisted breakdown into subtasks."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Optional

from sqlalchemy import select, desc
from sqlalchemy.orm import Session

from elly_server.db.models import Task
from elly_server.db.serialize import model_to_dict
from elly_server.domain import reminders as reminders_domain
from elly_server.domain.validation import require_nonblank
from elly_server.timeutil import now, parse_datetime


def create_task(
    session: Session,
    title: str,
    due_at: Optional[str] = None,
    estimate_minutes: Optional[int] = None,
    priority: Optional[str] = None,
    parent_task_id: Optional[int] = None,
) -> dict[str, Any]:
    task = Task(
        title=require_nonblank(title, "title"),
        due_at=parse_datetime(due_at),
        estimate_minutes=estimate_minutes,
        priority=priority,
        parent_task_id=parent_task_id,
    )
    session.add(task)
    session.flush()
    return model_to_dict(task)


def complete_task(session: Session, task_id: int) -> dict[str, Any]:
    task = session.get(Task, task_id)
    if task is None:
        raise ValueError(f"Task {task_id} not found")
    task.status = "done"
    task.completed_at = now()
    session.flush()
    return model_to_dict(task)


def reopen_task(session: Session, task_id: int) -> dict[str, Any]:
    """Move a completed task back to open. Undoes `complete_task` -- a
    misclick or a "actually, not done yet" should always be
    recoverable, not a one-way trip."""
    task = session.get(Task, task_id)
    if task is None:
        raise ValueError(f"Task {task_id} not found")
    task.status = "open"
    task.completed_at = None
    session.flush()
    return model_to_dict(task)


def list_pending_tasks(
    session: Session, due_before: Optional[str] = None
) -> list[dict[str, Any]]:
    stmt = select(Task).where(Task.status == "open")
    before_dt = parse_datetime(due_before)
    if before_dt:
        stmt = stmt.where(Task.due_at <= before_dt)
    # Dated tasks first (soonest due first), undated tasks trail at the end.
    stmt = stmt.order_by(Task.due_at.is_(None), Task.due_at)
    return [model_to_dict(t) for t in session.scalars(stmt).all()]


def list_tasks_due_on(session: Session, date: str) -> list[dict[str, Any]]:
    """All tasks -- open *or* completed -- whose due_at falls on *date*
    (a bare ISO date, e.g. "2026-08-01"). Powers the Calendar page's
    day-detail view: unlike list_pending_tasks(), this deliberately
    includes completed tasks too (shown crossed out, same as
    TodayView's own completed-tasks section) so browsing a past day
    shows what was actually due that day, not just what's still open.
    """
    target = parse_datetime(date)
    if target is None:
        raise ValueError(f"Invalid date: {date!r}")
    day_start = datetime(target.year, target.month, target.day)
    day_end = day_start + timedelta(days=1)
    stmt = (
        select(Task)
        .where(Task.due_at >= day_start, Task.due_at < day_end)
        .order_by(Task.status, Task.due_at)
    )
    return [model_to_dict(t) for t in session.scalars(stmt).all()]


def duplicate_tasks(
    session: Session, task_ids: Optional[list[int]] = None
) -> list[dict[str, Any]]:
    """Copy open tasks to today. If task_ids given, only copy those;
    otherwise copy all currently open tasks."""
    stmt = select(Task).where(Task.status == "open")
    if task_ids:
        stmt = stmt.where(Task.id.in_(task_ids))
    tasks = session.scalars(stmt).all()
    new_tasks: list[dict[str, Any]] = []
    for t in tasks:
        new_task = Task(
            title=t.title,
            estimate_minutes=t.estimate_minutes,
            priority=t.priority,
        )
        session.add(new_task)
        session.flush()
        new_tasks.append(model_to_dict(new_task))
    return new_tasks


def list_completed_tasks(
    session: Session, since: Optional[str] = None, limit: int = 50
) -> list[dict[str, Any]]:
    """List recently completed tasks, newest first."""
    stmt = select(Task).where(Task.status == "done")
    since_dt = parse_datetime(since)
    if since_dt:
        stmt = stmt.where(Task.completed_at >= since_dt)
    stmt = stmt.order_by(desc(Task.completed_at)).limit(limit)
    return [model_to_dict(t) for t in session.scalars(stmt).all()]


def get_task_tree(session: Session) -> list[dict[str, Any]]:
    """All open tasks as a hierarchy: top-level tasks with their children nested.

    This preserves the ordering of `list_pending_tasks` (dated tasks first,
    then undated) but groups each parent's subtasks under it.

    An open task whose parent is no longer open (completed, or -- in
    principle, though delete_task already cascades -- deleted) surfaces
    at the top level instead of vanishing. Nothing in the UI stops
    someone from completing a parent task while its subtasks are still
    open (there's no "finish your steps first" gate), so this has to
    be handled here rather than assumed away: a still-open subtask must
    never become unreachable just because its parent was marked done.
    """
    all_open = select(Task).where(Task.status == "open").order_by(
        Task.due_at.is_(None), Task.due_at
    )
    open_tasks = session.scalars(all_open).all()
    by_id: dict[int, dict[str, Any]] = {}
    for task in open_tasks:
        t = model_to_dict(task)
        t["children"] = []
        by_id[task.id] = t

    roots: list[dict[str, Any]] = []
    for task in open_tasks:
        t = by_id[task.id]
        if task.parent_task_id is not None and task.parent_task_id in by_id:
            by_id[task.parent_task_id]["children"].append(t)
        else:
            roots.append(t)

    return roots


def delete_task(session: Session, task_id: int) -> bool:
    """Delete a task and its subtasks. Returns False if it didn't exist."""
    task = session.get(Task, task_id)
    if task is None:
        return False
    children = session.scalars(select(Task).where(Task.parent_task_id == task_id)).all()
    for child in children:
        reminders_domain.delete_reminder_for(session, "task", child.id)
        session.delete(child)
    reminders_domain.delete_reminder_for(session, "task", task_id)
    session.delete(task)
    return True


def update_task(
    session: Session,
    task_id: int,
    title: Optional[str] = None,
    due_at: Optional[str] = None,
    estimate_minutes: Optional[int] = None,
    priority: Optional[str] = None,
) -> dict[str, Any]:
    task = session.get(Task, task_id)
    if task is None:
        raise ValueError(f"Task {task_id} not found")
    if title is not None:
        task.title = require_nonblank(title, "title")
    if due_at is not None:
        task.due_at = parse_datetime(due_at)
    if estimate_minutes is not None:
        task.estimate_minutes = estimate_minutes
    if priority is not None:
        task.priority = priority
    session.flush()
    if due_at is not None:
        # A reminder's trigger_at was computed from the old due date --
        # must not silently keep firing at the old time once it changes.
        reminders_domain.recompute_reminder_for_target(session, "task", task_id)
    return model_to_dict(task)


def breakdown_task(
    session: Session, task_id: int, subtasks: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Persist an LLM-proposed breakdown of a task into smaller subtasks.

    This function does NOT do the thinking -- the caller (the model,
    via the MCP tool) should propose small, concrete, time-estimated
    steps. This just saves them, linked to the parent task, so the
    first one can be tiny enough to start in a few minutes.
    """
    parent = session.get(Task, task_id)
    if parent is None:
        raise ValueError(f"Task {task_id} not found")
    created: list[Task] = []
    for sub in subtasks:
        child = Task(
            title=require_nonblank(sub["title"], "title"),
            estimate_minutes=sub.get("estimate_minutes"),
            due_at=parse_datetime(sub.get("due_at")),
            priority=sub.get("priority"),
            parent_task_id=parent.id,
        )
        session.add(child)
        created.append(child)
    session.flush()
    return [model_to_dict(c) for c in created]
