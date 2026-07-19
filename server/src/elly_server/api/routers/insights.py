"""Structured numeric analytics -- narration happens in the LLM/UI
layer, never here (see `elly_server.domain.insights` docstring)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from elly_server.api.deps import get_db
from elly_server.domain import insights

router = APIRouter(prefix="/insights", tags=["insights"])


@router.get("/mood-trend")
def mood_trend(days: int = 14, session: Session = Depends(get_db)) -> dict[str, Any]:
    return insights.mood_trend(session, days=days)


@router.get("/correlate")
def correlate(
    metric_a: str, metric_b: str, days: int = 30, session: Session = Depends(get_db)
) -> dict[str, Any]:
    return insights.correlate(session, metric_a=metric_a, metric_b=metric_b, days=days)


@router.get("/weekly-review")
def weekly_review(session: Session = Depends(get_db)) -> dict[str, Any]:
    return insights.weekly_review(session)
