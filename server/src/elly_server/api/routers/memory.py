"""Persistent facts/goals/preferences."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from elly_server.api.deps import get_db
from elly_server.api.schemas import MemoryCreate
from elly_server.domain import memory

router = APIRouter(prefix="/memory", tags=["memory"])


@router.post("", status_code=201)
def remember(payload: MemoryCreate, session: Session = Depends(get_db)) -> dict[str, Any]:
    return memory.remember(session, **payload.model_dump())


@router.get("/profile")
def get_profile_summary(session: Session = Depends(get_db)) -> dict[str, list[str]]:
    return memory.get_profile_summary(session)


@router.get("")
def recall(query: str, limit: int = 5, session: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return memory.recall(session, query=query, limit=limit)
