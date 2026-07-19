"""Shared validation helpers for domain functions.

Kept intentionally tiny and dependency-free. The domain layer is the
single source of truth for business rules (see AGENTS.md's "one
service layer, two doors in" principle) -- the REST API's Pydantic
schemas only validate requests coming through HTTP, but MCP tool calls
from the LLM bypass those schemas entirely and call these functions
directly with raw values. A check that only lived in `api/schemas.py`
would silently let the chat/MCP path create blank-titled tasks, habits,
events, and notes even though the manual UI's own client-side guards
(disabled Save buttons on empty input) would prevent the same thing.
Putting the guard here means both callers get it for free.
"""

from __future__ import annotations


def require_nonblank(value: str, field_name: str) -> str:
    """Strip surrounding whitespace and reject an empty/whitespace-only
    required text field.

    Returns the stripped value (so callers store the trimmed form,
    never leading/trailing whitespace) or raises `ValueError` with a
    plain, non-judgmental message -- never phrased as the user having
    done something wrong, just what's needed to proceed.
    """
    stripped = value.strip()
    if not stripped:
        raise ValueError(f"{field_name} can't be empty.")
    return stripped
