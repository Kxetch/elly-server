"""Persistent memory: facts/goals/preferences the companion should
remember across conversations.

MVP recall is plain keyword search -- no vector store yet (unlike the
old `ely` project). That's an intentional simplification; it can be
swapped in later (e.g. sqlite-vec) without changing this module's
public functions or the MCP tools that call them.

`Memory.content` is encrypted at rest (see db/encrypted_types.py), so
`recall()`'s keyword match can't be pushed down as a SQL WHERE clause
against ciphertext -- it fetches all memories (ordered as before) and
filters by the already-decrypted content in Python instead. Fine at
personal-use data volumes (hundreds to low thousands of memories).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from elly_server.db.models import Memory
from elly_server.db.serialize import model_to_dict
from elly_server.domain.validation import require_nonblank
from elly_server.timeutil import now


def remember(
    session: Session,
    content: str,
    type: str = "general",
    importance: float | None = None,
) -> dict[str, Any]:
    memory = Memory(
        content=require_nonblank(content, "content"),
        type=type,
        importance=importance if importance is not None else 0.5,
    )
    session.add(memory)
    session.flush()
    return model_to_dict(memory)


def recall(session: Session, query: str, limit: int = 5) -> list[dict[str, Any]]:
    stmt = select(Memory).order_by(Memory.importance.desc(), Memory.access_count.desc())
    q = query.lower()
    matches = [m for m in session.scalars(stmt).all() if q in m.content.lower()]
    matches = matches[:limit]
    for memory in matches:
        memory.access_count += 1
        memory.last_accessed = now()
    session.flush()
    return [model_to_dict(m) for m in matches]


def list_all_memories(session: Session) -> list[dict[str, Any]]:
    """Every memory, raw (id, content, type, importance, timestamps,
    access_count) -- unlike get_profile_summary() below, which groups
    memories by type into plain content strings for display, this
    keeps everything needed to faithfully restore a memory later (see
    domain/export.py's import_all_data). get_profile_summary()'s
    grouped-strings shape loses importance/created_at/access_count,
    which is fine for a chat-facing summary but not for a backup."""
    stmt = select(Memory).order_by(Memory.id)
    return [model_to_dict(m) for m in session.scalars(stmt).all()]


def get_profile_summary(session: Session) -> dict[str, list[str]]:
    stmt = select(Memory).order_by(Memory.importance.desc())
    grouped: dict[str, list[str]] = {}
    for memory in session.scalars(stmt).all():
        grouped.setdefault(memory.type, []).append(memory.content)
    return grouped
