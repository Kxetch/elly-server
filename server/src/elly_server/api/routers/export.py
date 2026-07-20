"""Full personal data export/import -- a self-hosted backup affordance."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from elly_server.api.deps import get_db
from elly_server.api.rate_limit import limiter
from elly_server.domain import export as export_domain

router = APIRouter(prefix="/export", tags=["export"])


@router.get("")
@limiter.limit("3/minute")
def export_all_data(request: Request, session: Session = Depends(get_db)) -> dict[str, Any]:
    """Everything a user would want in a personal backup: notes/diary,
    tasks, habits (active + archived, plus raw completion logs), events,
    remembered facts/goals/preferences, and budget entries. Deliberately
    excludes the access token, encryption key, and chat history -- see
    domain/export.py's docstring for the full reasoning.

    Rate-limited (unlike most read routes): this is the single
    highest-value target route in the app -- everything, in one
    authenticated GET. Pure defense-in-depth against "token leaked,
    fast silent bulk exfiltration" -- a rate-limited attacker is more
    likely to be noticed. Zero cost to the one legitimate caller (a
    human clicking "Export my data" in Settings occasionally)."""
    return export_domain.export_all_data(session)


@router.post("/import")
def import_all_data(payload: dict[str, Any], session: Session = Depends(get_db)) -> dict[str, int]:
    """Restore a backup produced by GET /export above -- pass the
    exact JSON that endpoint returned as the request body. See
    domain/export.py::import_all_data()'s docstring for why this only
    ever works into an empty database (v1's deliberate, honest scope --
    not a merge)."""
    return export_domain.import_all_data(session, payload)
