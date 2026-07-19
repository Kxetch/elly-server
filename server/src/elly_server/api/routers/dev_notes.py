from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from elly_server.api.deps import get_db
from elly_server.api.schemas import DevNoteCreate
from elly_server.domain import dev_notes

router = APIRouter(prefix="/dev-notes", tags=["dev-notes"])


@router.post("", status_code=201)
def create_dev_note(payload: DevNoteCreate, session: Session = Depends(get_db)) -> dict[str, Any]:
    return dev_notes.create_dev_note(session, **payload.model_dump())


@router.get("")
def list_dev_notes(
    limit: int = 50, session: Session = Depends(get_db)
) -> list[dict[str, Any]]:
    return dev_notes.list_dev_notes(session, limit=limit)


@router.delete("/{note_id}")
def delete_dev_note(note_id: int, session: Session = Depends(get_db)) -> dict[str, bool]:
    return {"deleted": dev_notes.delete_dev_note(session, note_id=note_id)}
