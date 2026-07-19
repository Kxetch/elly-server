"""Habit tracking."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from elly_server.api.deps import get_db
from elly_server.api.schemas import HabitCreate, HabitLogRequest, HabitUpdate
from elly_server.domain import habits

router = APIRouter(prefix="/habits", tags=["habits"])


@router.post("", status_code=201)
def create_habit(payload: HabitCreate, session: Session = Depends(get_db)) -> dict[str, Any]:
    return habits.create_habit(session, **payload.model_dump())


@router.get("")
def list_habits(session: Session = Depends(get_db)) -> list[dict[str, Any]]:
    """Status (streak, last logged, etc.) for every active habit."""
    return habits.list_all_habit_statuses(session)


@router.get("/calendar")
def get_habit_calendar(
    days: int = 14, session: Session = Depends(get_db)
) -> list[dict[str, Any]]:
    """Per-habit daily completion for the last N days -- powers a
    heatmap view. Must be registered before /{habit_id} below."""
    return habits.get_habit_calendar(session, days=days)


@router.get("/archived")
def list_archived_habits(session: Session = Depends(get_db)) -> list[dict[str, Any]]:
    """Archived habits -- kept visible and restorable, never a dead
    end. Must be registered before /{habit_id} below."""
    return habits.list_archived_habits(session)


@router.get("/{habit_id}")
def get_habit_status(habit_id: int, session: Session = Depends(get_db)) -> dict[str, Any]:
    return habits.get_habit_status(session, habit_id=habit_id)


@router.patch("/{habit_id}")
def update_habit(
    habit_id: int, payload: HabitUpdate, session: Session = Depends(get_db)
) -> dict[str, Any]:
    data = payload.model_dump()
    is_active = data.pop("is_active", None)
    result = habits.update_habit(session, habit_id=habit_id, **data)
    if is_active is not None:
        result = habits.set_habit_active(session, habit_id=habit_id, is_active=is_active)
    return result


@router.post("/generate-events", status_code=201)
def generate_scheduled_events(session: Session = Depends(get_db)) -> list[dict[str, Any]]:
    """Auto-create today's calendar events for habits with scheduling
    configured and auto_event=True. Safe to call repeatedly -- it skips
    dates where an event already exists for that habit."""
    return habits.generate_scheduled_events(session)


@router.delete("/{habit_id}")
def delete_habit(habit_id: int, session: Session = Depends(get_db)) -> dict[str, bool]:
    """Permanently delete a habit and all its logs + calendar events."""
    return {"deleted": habits.delete_habit(session, habit_id=habit_id)}


@router.post("/{habit_id}/log")
def log_habit(
    habit_id: int,
    payload: HabitLogRequest = HabitLogRequest(),
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    """Log a completion for today. Body is optional -- omit it to just
    log with no note."""
    return habits.log_habit(session, habit_id=habit_id, note=payload.note)
