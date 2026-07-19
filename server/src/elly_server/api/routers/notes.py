"""Notebook notes + diary entries."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from elly_server.api.deps import get_db
from elly_server.api.schemas import NoteCreate, NoteUpdate
from elly_server.domain import notes

router = APIRouter(prefix="/notes", tags=["notes"])


@router.post("", status_code=201)
def create_note(payload: NoteCreate, session: Session = Depends(get_db)) -> dict[str, Any]:
    return notes.create_note(session, **payload.model_dump())


@router.get("")
def search_notes(
    query: Optional[str] = None,
    type: Optional[str] = None,
    tag: Optional[str] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
    limit: int = 20,
    session: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    return notes.search_notes(
        session, query=query, type=type, tag=tag, since=since, until=until, limit=limit
    )


@router.get("/recent")
def get_recent_notes(
    type: Optional[str] = None, limit: int = 10, session: Session = Depends(get_db)
) -> list[dict[str, Any]]:
    return notes.get_recent_notes(session, type=type, limit=limit)


@router.get("/{note_id}")
def get_note(note_id: int, session: Session = Depends(get_db)) -> dict[str, Any]:
    note = notes.get_note(session, note_id)
    if note is None:
        raise HTTPException(status_code=404, detail=f"Note {note_id} not found")
    return note


@router.patch("/{note_id}")
def update_note(
    note_id: int, payload: NoteUpdate, session: Session = Depends(get_db)
) -> dict[str, Any]:
    return notes.update_note(session, note_id, **payload.model_dump())


@router.delete("/{note_id}")
def delete_note(note_id: int, session: Session = Depends(get_db)) -> dict[str, bool]:
    return {"deleted": notes.delete_note(session, note_id=note_id)}
