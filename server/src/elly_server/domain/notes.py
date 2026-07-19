"""Notebook notes and diary/journal entries (same underlying table)."""

from __future__ import annotations

from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from elly_server.db.models import Note
from elly_server.db.serialize import model_to_dict
from elly_server.domain.validation import require_nonblank
from elly_server.timeutil import parse_datetime


def create_note(
    session: Session,
    body: str,
    type: str = "note",
    title: Optional[str] = None,
    mood: Optional[int] = None,
    energy: Optional[int] = None,
    tags: Optional[list[str]] = None,
) -> dict[str, Any]:
    note = Note(
        body=require_nonblank(body, "body"),
        type=type,
        title=title,
        mood=mood,
        energy=energy,
        tags=tags or [],
    )
    session.add(note)
    session.flush()
    return model_to_dict(note)


def update_note(
    session: Session,
    note_id: int,
    body: Optional[str] = None,
    title: Optional[str] = None,
    mood: Optional[int] = None,
    energy: Optional[int] = None,
    tags: Optional[list[str]] = None,
) -> dict[str, Any]:
    note = session.get(Note, note_id)
    if note is None:
        raise ValueError(f"Note {note_id} not found")
    if body is not None:
        note.body = require_nonblank(body, "body")
    if title is not None:
        note.title = title
    if mood is not None:
        note.mood = mood
    if energy is not None:
        note.energy = energy
    if tags is not None:
        note.tags = tags
    session.flush()
    return model_to_dict(note)


def get_note(session: Session, note_id: int) -> Optional[dict[str, Any]]:
    note = session.get(Note, note_id)
    return model_to_dict(note) if note else None


def delete_note(session: Session, note_id: int) -> bool:
    """Delete a note. Returns False if it didn't exist."""
    note = session.get(Note, note_id)
    if note is None:
        return False
    session.delete(note)
    return True


def search_notes(
    session: Session,
    query: Optional[str] = None,
    type: Optional[str] = None,
    tag: Optional[str] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Search notes/diary entries.

    `body`/`title` are encrypted at rest (see db/encrypted_types.py),
    so a free-text `query` can't be pushed down as a SQL WHERE clause
    against ciphertext -- type/date filters still run in SQL (fast,
    indexed), but the free-text match happens in Python against the
    already-decrypted values after fetching. Fine at personal-use data
    volumes; not an approach that scales to a large multi-user corpus,
    which isn't this app's use case anyway.
    """
    stmt = select(Note)
    if type:
        stmt = stmt.where(Note.type == type)
    since_dt = parse_datetime(since)
    until_dt = parse_datetime(until)
    if since_dt:
        stmt = stmt.where(Note.created_at >= since_dt)
    if until_dt:
        stmt = stmt.where(Note.created_at <= until_dt)
    stmt = stmt.order_by(Note.created_at.desc())
    # Only push the SQL-level limit down when there's no additional
    # Python-side filtering left to do (free-text query OR tag) --
    # otherwise we'd risk truncating the candidate set (to the most
    # recent `limit` rows) before that filter runs, silently missing
    # real matches that happen to be older than the cutoff.
    if not query and not tag:
        stmt = stmt.limit(limit)
    results = [model_to_dict(n) for n in session.scalars(stmt).all()]
    if query:
        q = query.lower()
        results = [
            n for n in results
            if q in (n.get("body") or "").lower() or q in (n.get("title") or "").lower()
        ]
    if tag:
        results = [n for n in results if tag in (n.get("tags") or [])]
    if query or tag:
        results = results[:limit]
    return results


def get_recent_notes(
    session: Session, type: Optional[str] = None, limit: int = 10
) -> list[dict[str, Any]]:
    return search_notes(session, type=type, limit=limit)
