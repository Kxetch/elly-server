"""Ollama connectivity + model management for the Settings UI.

Talks to Ollama's own REST API (never runs host-level install
commands) -- see domain/ollama_admin.py for the full rationale.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from elly_server.api.deps import get_db
from elly_server.api.schemas import OllamaPullModelRequest, OllamaTestConnectionRequest
from elly_server.config import get_ollama_base_url
from elly_server.domain import ollama_admin
from elly_server.domain import settings as settings_domain

router = APIRouter(prefix="/ollama", tags=["ollama"])


def _resolve_base_url(session: Session, override: Optional[str]) -> str:
    if override and override.strip():
        return override.strip()
    prefs = settings_domain.get_settings(session)
    return prefs.get("ollama_base_url") or get_ollama_base_url()


@router.post("/test-connection")
async def test_connection(
    payload: OllamaTestConnectionRequest, session: Session = Depends(get_db)
) -> dict[str, Any]:
    base_url = _resolve_base_url(session, payload.base_url)
    return await ollama_admin.test_connection(base_url)


@router.post("/pull-model")
async def pull_model(
    payload: OllamaPullModelRequest, session: Session = Depends(get_db)
) -> StreamingResponse:
    base_url = _resolve_base_url(session, payload.base_url)
    model = payload.model.strip()

    async def event_generator():
        async for event in ollama_admin.pull_model(base_url, model):
            yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
