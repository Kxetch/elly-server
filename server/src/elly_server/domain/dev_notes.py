from __future__ import annotations

from typing import Any, Optional

from sqlalchemy import desc
from sqlalchemy.orm import Session

from elly_server.db.models import DevNote
from elly_server.db.serialize import model_to_dict
from elly_server.domain.validation import require_nonblank


def create_dev_note(session: Session, body: str, title: Optional[str] = None) -> dict[str, Any]:
    note = DevNote(body=require_nonblank(body, "body"), title=title)
    session.add(note)
    session.flush()
    return model_to_dict(note)


def list_dev_notes(session: Session, limit: int = 50) -> list[dict[str, Any]]:
    notes = session.query(DevNote).order_by(desc(DevNote.created_at)).limit(limit).all()
    return [model_to_dict(n) for n in notes]


def delete_dev_note(session: Session, note_id: int) -> bool:
    note = session.get(DevNote, note_id)
    if note is None:
        raise ValueError(f"DevNote {note_id} not found")
    session.delete(note)
    return True
