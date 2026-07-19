"""Income/expense tracking (the Budget page)."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from elly_server.api.deps import get_db
from elly_server.api.schemas import BudgetEntryCreate, BudgetEntryUpdate
from elly_server.domain import budget

router = APIRouter(prefix="/budget", tags=["budget"])


@router.post("/entries", status_code=201)
def create_entry(payload: BudgetEntryCreate, session: Session = Depends(get_db)) -> dict[str, Any]:
    return budget.create_entry(session, **payload.model_dump())


@router.get("/entries")
def list_entries(
    kind: Optional[str] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
    limit: int = 50,
    session: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    return budget.list_entries(session, kind=kind, since=since, until=until, limit=limit)


@router.get("/entries/recent")
def list_recent(
    kind: str = "expense", limit: int = 5, session: Session = Depends(get_db)
) -> list[dict[str, Any]]:
    """Recent, deduplicated one-off entries -- powers "tap to repeat"
    quick-log chips in the UI. Must be registered before
    /entries/{entry_id} below."""
    return budget.list_recent(session, kind=kind, limit=limit)


@router.get("/categories")
def list_categories(
    kind: Optional[str] = None, session: Session = Depends(get_db)
) -> list[str]:
    return budget.list_categories(session, kind=kind)


@router.get("/summary")
def get_summary(
    since: Optional[str] = None, until: Optional[str] = None, session: Session = Depends(get_db)
) -> dict[str, Any]:
    """Totals + expense-by-category breakdown -- defaults to the
    current calendar month."""
    return budget.get_summary(session, since=since, until=until)


@router.get("/trend")
def get_monthly_trend(months: int = 6, session: Session = Depends(get_db)) -> dict[str, Any]:
    return budget.get_monthly_trend(session, months=months)


@router.get("/upcoming")
def list_upcoming(days: int = 30, session: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return budget.list_upcoming(session, days=days)


@router.post("/generate-events", status_code=201)
def generate_scheduled_budget_events(session: Session = Depends(get_db)) -> list[dict[str, Any]]:
    """Auto-create upcoming calendar events for recurring budget entries
    with auto_event=True. Safe to call repeatedly."""
    return budget.generate_scheduled_budget_events(session)


@router.get("/entries/{entry_id}")
def get_entry(entry_id: int, session: Session = Depends(get_db)) -> dict[str, Any]:
    return budget.get_entry(session, entry_id=entry_id)


@router.patch("/entries/{entry_id}")
def update_entry(
    entry_id: int, payload: BudgetEntryUpdate, session: Session = Depends(get_db)
) -> dict[str, Any]:
    return budget.update_entry(session, entry_id=entry_id, **payload.model_dump(exclude_unset=True))


@router.delete("/entries/{entry_id}")
def delete_entry(entry_id: int, session: Session = Depends(get_db)) -> dict[str, bool]:
    return {"deleted": budget.delete_entry(session, entry_id=entry_id)}
