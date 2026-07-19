"""Full personal data export -- a self-hosted backup affordance."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from elly_server.api.deps import get_db
from elly_server.domain import export as export_domain

router = APIRouter(prefix="/export", tags=["export"])


@router.get("")
def export_all_data(session: Session = Depends(get_db)) -> dict[str, Any]:
    """Everything a user would want in a personal backup: notes/diary,
    tasks, habits (active + archived), events, and remembered facts/
    goals/preferences. Deliberately excludes the access token,
    encryption key, and chat history -- see domain/export.py's
    docstring for the full reasoning."""
    return export_domain.export_all_data(session)
