"""App-wide settings: LLM provider choice + onboarding state.

Authenticated (protected by require_auth in api/app.py) -- unlike
/api/setup, this reveals real configuration state, not just a
true/false match.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from elly_server.api.deps import get_db
from elly_server.api.schemas import OpenAiKeyUpdate, SettingsUpdate
from elly_server.domain import settings as settings_domain

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("")
def get_settings(session: Session = Depends(get_db)) -> dict[str, Any]:
    return settings_domain.get_settings(session)


@router.put("")
def update_settings(
    payload: SettingsUpdate, session: Session = Depends(get_db)
) -> dict[str, Any]:
    return settings_domain.update_settings(session, **payload.model_dump(exclude_unset=True))


@router.put("/openai-key")
def set_openai_key(
    payload: OpenAiKeyUpdate, session: Session = Depends(get_db)
) -> dict[str, bool]:
    """Save the OpenAI API key from the Settings UI. Unlike the
    Telegram bot token, this takes effect immediately -- no restart
    needed, since get_llm_client() reads settings fresh on every call."""
    settings_domain.set_openai_api_key(session, payload.key.strip())
    return {"configured": True}


@router.delete("/openai-key")
def clear_openai_key(session: Session = Depends(get_db)) -> dict[str, bool]:
    """Remove the OpenAI API key from Settings. Falls back to
    OPENAI_API_KEY in server/.env, if set."""
    settings_domain.set_openai_api_key(session, None)
    return {"configured": False}
