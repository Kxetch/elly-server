"""Notification preferences and test endpoint."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from elly_server.api.deps import get_db
from elly_server.api.schemas import NotificationPrefUpdate
from elly_server.domain import notifications

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("/prefs")
def get_prefs(session: Session = Depends(get_db)) -> dict[str, Any]:
    return notifications.get_prefs(session)


@router.put("/prefs")
def update_prefs(
    payload: NotificationPrefUpdate,
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    return notifications.update_prefs(
        session,
        enabled=payload.enabled,
        morning_time=payload.morning_time,
        evening_time=payload.evening_time,
    )


@router.post("/test")
def test_notification(session: Session = Depends(get_db)) -> dict[str, str]:
    notifications.send_test(session)
    return {"status": "sent"}
