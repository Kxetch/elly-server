"""Dashboard-side Telegram setup: configure the bot token, pair, check
status, unpair.

The bot process itself (elly-telegram, now normally spawned/managed
automatically by elly-api -- see telegram_bot/process_manager.py) calls
domain/telegram.py directly for pairing -- it shares the same domain
layer/DB, so it never needs to go through this REST surface. This
router exists purely for the Settings UI.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from elly_server.api.deps import get_db
from elly_server.api.schemas import TelegramTokenUpdate
from elly_server.domain import settings as settings_domain
from elly_server.domain import telegram as telegram_domain
from elly_server.telegram_bot.process_manager import telegram_process_manager

router = APIRouter(prefix="/telegram", tags=["telegram"])


@router.get("/status")
def get_status(session: Session = Depends(get_db)) -> dict[str, Any]:
    link = telegram_domain.get_link(session)
    return {
        "paired": link["chat_id"] is not None,
        "paired_at": link["paired_at"],
        "pairing_code_active": link["pairing_code"] is not None,
        "bot_configured": settings_domain.get_effective_telegram_bot_token(session) is not None,
        # Whether the managed subprocess is actually alive right now --
        # distinct from bot_configured (a token can be saved but not
        # yet applied until a restart; see api/routers/system.py).
        "bot_running": telegram_process_manager.is_running(),
    }


@router.put("/bot-token")
def set_bot_token(
    payload: TelegramTokenUpdate, session: Session = Depends(get_db)
) -> dict[str, bool]:
    """Save the Telegram bot token (from @BotFather). Doesn't start the
    bot itself -- that needs a restart (POST /api/system/restart) so
    the managed subprocess picks up the new token from a clean process
    start, same as any other Telegram-bot-affecting change."""
    settings_domain.set_telegram_bot_token(session, payload.token.strip())
    return {"configured": True}


@router.delete("/bot-token")
def clear_bot_token(session: Session = Depends(get_db)) -> dict[str, bool]:
    """Remove the Telegram bot token entirely. Also needs a restart to
    actually stop the managed subprocess."""
    settings_domain.set_telegram_bot_token(session, None)
    return {"configured": False}


@router.post("/pairing-code", status_code=201)
def create_pairing_code(session: Session = Depends(get_db)) -> dict[str, Any]:
    return telegram_domain.generate_pairing_code(session)


@router.post("/unpair")
def unpair(session: Session = Depends(get_db)) -> dict[str, Any]:
    link = telegram_domain.unpair(session)
    return {"paired": link["chat_id"] is not None}
