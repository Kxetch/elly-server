"""Composed dashboard snapshots (span more than one domain module)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from elly_server.api.deps import get_db
from elly_server.domain import dashboard

router = APIRouter(tags=["dashboard"])


@router.get("/today")
def today(session: Session = Depends(get_db)) -> dict[str, Any]:
    """Everything relevant to right now: today's events, open tasks,
    and habit status. Same shape as the MCP `elly://today` resource."""
    return dashboard.today_snapshot(session)
