"""Telegram remote-access channel: pairing, allow-list, message durability.

Design rationale (see PLAN.md section 0.1 / SECURITY.md): Elly supports
exactly one paired Telegram chat per instance, matching its single-user
design. Pairing happens via an in-app, time-limited 6-digit code --
never a hardcoded chat ID -- so a stranger who finds the bot has no way
to command it even if they know it exists.

Telegram's own long-polling delivery already queues updates
server-side while this process is offline (bounded, not indefinite --
see SECURITY.md's honest caveat about this). The `InboundTelegramMessage`
table below is a second, local durability layer on top of that: every
message is persisted here the instant it's received, before being run
through the LLM tool-calling loop, so a crash mid-processing is
detectable and never silently drops a message.
"""

from __future__ import annotations

import secrets
from datetime import timedelta
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from elly_server.db.models import InboundTelegramMessage, TelegramLink
from elly_server.db.serialize import model_to_dict
from elly_server.domain import chat
from elly_server.timeutil import now

PAIRING_CODE_TTL_MINUTES = 10


def _ensure_link(session: Session) -> TelegramLink:
    row = session.scalars(select(TelegramLink).limit(1)).first()
    if row is None:
        row = TelegramLink()
        session.add(row)
        session.flush()
    return row


def get_link(session: Session) -> dict[str, Any]:
    return model_to_dict(_ensure_link(session))


def is_paired(session: Session) -> bool:
    return _ensure_link(session).chat_id is not None


def is_authorized_chat(session: Session, chat_id: int) -> bool:
    """True only for the single paired chat. Never reveals whether
    pairing exists at all to a mismatched chat_id -- callers should
    give unauthorized senders a generic, non-specific reply."""
    link = _ensure_link(session)
    return link.chat_id is not None and link.chat_id == chat_id


def generate_pairing_code(session: Session) -> dict[str, Any]:
    """Create a new one-time pairing code, replacing any previous
    unconsumed one. Returns {code, expires_at}. Does NOT unpair an
    already-paired chat -- generating a new code is safe to do at any
    time (e.g. to re-pair a new device), it just won't take effect
    until someone actually sends it to the bot."""
    link = _ensure_link(session)
    code = f"{secrets.randbelow(1_000_000):06d}"
    link.pairing_code = code
    link.pairing_code_expires_at = now() + timedelta(minutes=PAIRING_CODE_TTL_MINUTES)
    session.flush()
    return {"code": code, "expires_at": link.pairing_code_expires_at.isoformat()}


def verify_and_pair(session: Session, code: str, chat_id: int) -> bool:
    """Consume a pairing code if it matches and hasn't expired, linking
    this chat_id as the one authorized sender. Creates a fresh
    conversation (with the system prompt already seeded) for this
    channel the first time pairing succeeds. Returns whether pairing
    succeeded."""
    link = _ensure_link(session)
    if not link.pairing_code or not code:
        return False
    if not secrets.compare_digest(link.pairing_code, code):
        return False
    if link.pairing_code_expires_at is None or now() > link.pairing_code_expires_at:
        return False

    link.chat_id = chat_id
    link.paired_at = now()
    link.pairing_code = None
    link.pairing_code_expires_at = None
    if not link.conversation_id:
        link.conversation_id = chat.create_conversation(session)
    session.flush()
    return True


def unpair(session: Session) -> dict[str, Any]:
    """Disconnect the currently-paired chat (if any). Keeps the
    conversation history in the chat_messages table -- re-pairing
    later (even a different device) resumes the same thread rather
    than silently losing it, consistent with never deleting user data
    without an explicit, separate delete action."""
    link = _ensure_link(session)
    link.chat_id = None
    link.paired_at = None
    session.flush()
    return model_to_dict(link)


def get_conversation_id(session: Session) -> Optional[str]:
    return _ensure_link(session).conversation_id


def record_inbound_message(
    session: Session, chat_id: int, telegram_update_id: int, text: str
) -> dict[str, Any]:
    """Persist a message immediately, before any processing. Idempotent
    on telegram_update_id -- a redelivered update returns the existing
    row rather than creating a duplicate."""
    existing = session.scalars(
        select(InboundTelegramMessage).where(
            InboundTelegramMessage.telegram_update_id == telegram_update_id
        )
    ).first()
    if existing is not None:
        return model_to_dict(existing)

    row = InboundTelegramMessage(chat_id=chat_id, telegram_update_id=telegram_update_id, text=text)
    session.add(row)
    session.flush()
    return model_to_dict(row)


def mark_processed(session: Session, message_id: int) -> None:
    row = session.get(InboundTelegramMessage, message_id)
    if row is not None:
        row.status = "processed"
        row.processed_at = now()
        session.flush()


def mark_error(session: Session, message_id: int, error: str) -> None:
    row = session.get(InboundTelegramMessage, message_id)
    if row is not None:
        row.status = "error"
        row.processed_at = now()
        row.error_message = error
        session.flush()


def get_unprocessed_messages(session: Session) -> list[dict[str, Any]]:
    """Any message left in "pending" status -- normally this should be
    empty (messages are processed synchronously right after being
    recorded), but a crash between record and process would leave one
    here. Used on bot startup to resume rather than silently skip."""
    stmt = (
        select(InboundTelegramMessage)
        .where(InboundTelegramMessage.status == "pending")
        .order_by(InboundTelegramMessage.id)
    )
    return [model_to_dict(row) for row in session.scalars(stmt).all()]
