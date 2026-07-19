"""Calendar events / time-blocks."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from elly_server.api.deps import get_db
from elly_server.api.schemas import EventCreate, EventReschedule
from elly_server.domain import calendar

router = APIRouter(prefix="/events", tags=["calendar"])


@router.post("", status_code=201)
def create_event(payload: EventCreate, session: Session = Depends(get_db)) -> dict[str, Any]:
    return calendar.create_event(session, **payload.model_dump())


@router.get("/today")
def list_today(session: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return calendar.list_today(session)


@router.get("/search")
def search_events(
    query: str,
    start: Optional[str] = None,
    end: Optional[str] = None,
    session: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    return calendar.search_events(session, query=query, start=start, end=end)


@router.get("")
def list_events_range(
    start: str, end: str, session: Session = Depends(get_db)
) -> list[dict[str, Any]]:
    return calendar.list_events_range(session, start=start, end=end)


@router.patch("/{event_id}")
def reschedule_event(
    event_id: int, payload: EventReschedule, session: Session = Depends(get_db)
) -> dict[str, Any]:
    return calendar.reschedule_event(session, event_id=event_id, **payload.model_dump())


@router.delete("/{event_id}")
def delete_event(event_id: int, session: Session = Depends(get_db)) -> dict[str, bool]:
    return {"deleted": calendar.delete_event(session, event_id=event_id)}
