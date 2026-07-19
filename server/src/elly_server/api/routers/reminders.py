"""Reminders & alarms for tasks, events, and habits (Sprint 5 -- the UI
half of the Sprint 4 engine in domain/reminders.py).

Exactly one reminder per target -- PUT always replaces any existing one
for that target rather than creating a second (see domain/reminders.py's
module docstring for the full rationale). No MCP/chat tools for this --
UI-only, confirmed 2026-07-15 (see PLAN.md section 0.2).
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from elly_server.api.deps import get_db
from elly_server.api.schemas import ReminderSet, ReminderTargetType
from elly_server.domain import reminders

router = APIRouter(prefix="/reminders", tags=["reminders"])


@router.get("")
def list_reminders(session: Session = Depends(get_db)) -> list[dict[str, Any]]:
    """All reminders, soonest-first, each with its target's title --
    powers a Settings management view."""
    return reminders.list_reminders(session)


@router.get("/{target_type}/{target_id}")
def get_reminder(
    target_type: ReminderTargetType, target_id: int, session: Session = Depends(get_db)
) -> Optional[dict[str, Any]]:
    return reminders.get_reminder_for(session, target_type, target_id)


@router.put("/{target_type}/{target_id}")
def set_reminder(
    target_type: ReminderTargetType, target_id: int, payload: ReminderSet, session: Session = Depends(get_db)
) -> dict[str, Any]:
    return reminders.set_reminder(
        session, target_type, target_id, payload.kind, payload.offset_minutes, payload.message
    )


@router.delete("/{target_type}/{target_id}")
def delete_reminder(
    target_type: ReminderTargetType, target_id: int, session: Session = Depends(get_db)
) -> dict[str, bool]:
    return {"deleted": reminders.delete_reminder_for(session, target_type, target_id)}
