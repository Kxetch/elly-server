"""FastAPI dependencies."""

from __future__ import annotations

from typing import Iterator, Optional

from fastapi import Header, HTTPException
from sqlalchemy.orm import Session

from elly_server.db.base import get_session
from elly_server.domain.auth import verify_token


def get_db() -> Iterator[Session]:
    """Request-scoped session: commits if the route handler succeeds,
    rolls back if it raises, always closes. Same transaction-per-call
    semantics as the MCP server's `get_session()` usage."""
    with get_session() as session:
        yield session


def require_auth(authorization: Optional[str] = Header(default=None)) -> None:
    """Require a valid `Authorization: Bearer <token>` header.

    Applied to every protected router at include_router() time (see
    api/app.py) -- not as a global app-level dependency, so the
    unauthenticated setup/health routes can stay explicitly excluded
    rather than relying on a fragile path-based bypass list.
    """
    token: Optional[str] = None
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:]
    if not verify_token(token):
        raise HTTPException(status_code=401, detail="Invalid or missing access token")
