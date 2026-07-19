"""Tasks/to-dos, including AI-assisted breakdown into subtasks."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from elly_server.api.deps import get_db
from elly_server.api.schemas import DuplicateTasksRequest, TaskBreakdown, TaskCreate, TaskUpdate
from elly_server.domain import tasks

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.post("", status_code=201)
def create_task(payload: TaskCreate, session: Session = Depends(get_db)) -> dict[str, Any]:
    return tasks.create_task(session, **payload.model_dump())


@router.get("")
def list_pending_tasks(
    due_before: Optional[str] = None, session: Session = Depends(get_db)
) -> list[dict[str, Any]]:
    return tasks.list_pending_tasks(session, due_before=due_before)


@router.get("/hierarchy")
def get_task_hierarchy(session: Session = Depends(get_db)) -> list[dict[str, Any]]:
    """Return open tasks as a tree: parent tasks with their children nested
    under a `children` key. Useful for visualizing AI task breakdowns."""
    return tasks.get_task_tree(session)


@router.get("/due-on")
def list_tasks_due_on(date: str, session: Session = Depends(get_db)) -> list[dict[str, Any]]:
    """Tasks (open or completed) due on a specific date -- powers the
    Calendar page's day-detail view."""
    return tasks.list_tasks_due_on(session, date=date)


@router.get("/completed")
def list_completed(
    since: Optional[str] = None, limit: int = 50, session: Session = Depends(get_db)
) -> list[dict[str, Any]]:
    return tasks.list_completed_tasks(session, since=since, limit=limit)


@router.post("/duplicate", status_code=201)
def duplicate_tasks(
    payload: DuplicateTasksRequest, session: Session = Depends(get_db)
) -> list[dict[str, Any]]:
    return tasks.duplicate_tasks(session, task_ids=payload.task_ids)


@router.post("/{task_id}/complete")
def complete_task(task_id: int, session: Session = Depends(get_db)) -> dict[str, Any]:
    return tasks.complete_task(session, task_id=task_id)


@router.post("/{task_id}/reopen")
def reopen_task(task_id: int, session: Session = Depends(get_db)) -> dict[str, Any]:
    """Undo a completion -- moves the task back to open. A misclick
    should always be recoverable."""
    return tasks.reopen_task(session, task_id=task_id)


@router.post("/{task_id}/breakdown")
def breakdown_task(
    task_id: int, payload: TaskBreakdown, session: Session = Depends(get_db)
) -> list[dict[str, Any]]:
    return tasks.breakdown_task(session, task_id=task_id, subtasks=payload.subtasks)


@router.patch("/{task_id}")
def update_task(
    task_id: int, payload: TaskUpdate, session: Session = Depends(get_db)
) -> dict[str, Any]:
    return tasks.update_task(session, task_id=task_id, **payload.model_dump())


@router.delete("/{task_id}")
def delete_task(task_id: int, session: Session = Depends(get_db)) -> dict[str, bool]:
    return {"deleted": tasks.delete_task(session, task_id=task_id)}
