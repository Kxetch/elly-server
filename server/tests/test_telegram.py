"""Tests for domain/telegram.py -- pairing, allow-list, message durability."""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import delete

from elly_server.db.base import get_session
from elly_server.db.models import InboundTelegramMessage, TelegramLink
from elly_server.domain import telegram as telegram_domain
from elly_server.timeutil import now


@pytest.fixture(autouse=True)
def _clean_telegram_tables() -> None:
    with get_session() as session:
        session.execute(delete(InboundTelegramMessage))
        session.execute(delete(TelegramLink))


def test_starts_unpaired() -> None:
    with get_session() as session:
        assert telegram_domain.is_paired(session) is False
        assert telegram_domain.is_authorized_chat(session, 12345) is False


def test_generate_pairing_code_returns_six_digit_code() -> None:
    with get_session() as session:
        result = telegram_domain.generate_pairing_code(session)
    assert len(result["code"]) == 6
    assert result["code"].isdigit()
    assert "expires_at" in result


def test_verify_and_pair_success() -> None:
    with get_session() as session:
        code_result = telegram_domain.generate_pairing_code(session)
    with get_session() as session:
        ok = telegram_domain.verify_and_pair(session, code_result["code"], chat_id=999)
    assert ok is True
    with get_session() as session:
        assert telegram_domain.is_paired(session) is True
        assert telegram_domain.is_authorized_chat(session, 999) is True
        assert telegram_domain.is_authorized_chat(session, 111) is False


def test_verify_and_pair_wrong_code_fails() -> None:
    with get_session() as session:
        telegram_domain.generate_pairing_code(session)
    with get_session() as session:
        ok = telegram_domain.verify_and_pair(session, "000000", chat_id=999)
    assert ok is False
    with get_session() as session:
        assert telegram_domain.is_paired(session) is False


def test_verify_and_pair_expired_code_fails() -> None:
    with get_session() as session:
        code_result = telegram_domain.generate_pairing_code(session)
        # Force expiry into the past.
        link = session.query(TelegramLink).first()
        link.pairing_code_expires_at = now() - timedelta(minutes=1)
        session.flush()

    with get_session() as session:
        ok = telegram_domain.verify_and_pair(session, code_result["code"], chat_id=999)
    assert ok is False


def test_pairing_creates_a_conversation() -> None:
    with get_session() as session:
        code_result = telegram_domain.generate_pairing_code(session)
        telegram_domain.verify_and_pair(session, code_result["code"], chat_id=999)
    with get_session() as session:
        conv_id = telegram_domain.get_conversation_id(session)
    assert conv_id is not None
    assert len(conv_id) == 36  # uuid4 string


def test_unpair_clears_chat_id_but_keeps_conversation() -> None:
    with get_session() as session:
        code_result = telegram_domain.generate_pairing_code(session)
        telegram_domain.verify_and_pair(session, code_result["code"], chat_id=999)
    with get_session() as session:
        conv_id_before = telegram_domain.get_conversation_id(session)

    with get_session() as session:
        telegram_domain.unpair(session)
    with get_session() as session:
        assert telegram_domain.is_paired(session) is False
        # Conversation history is preserved for a future re-pair.
        assert telegram_domain.get_conversation_id(session) == conv_id_before


def test_record_inbound_message_is_idempotent_on_update_id() -> None:
    with get_session() as session:
        first = telegram_domain.record_inbound_message(session, chat_id=1, telegram_update_id=42, text="hello")
    with get_session() as session:
        second = telegram_domain.record_inbound_message(session, chat_id=1, telegram_update_id=42, text="hello again")

    assert first["id"] == second["id"]
    assert second["text"] == "hello"  # unchanged -- returns the existing row, doesn't overwrite


def test_mark_processed_and_mark_error() -> None:
    with get_session() as session:
        msg = telegram_domain.record_inbound_message(session, chat_id=1, telegram_update_id=1, text="a")
    with get_session() as session:
        telegram_domain.mark_processed(session, msg["id"])
    with get_session() as session:
        pending = telegram_domain.get_unprocessed_messages(session)
    assert pending == []

    with get_session() as session:
        msg2 = telegram_domain.record_inbound_message(session, chat_id=1, telegram_update_id=2, text="b")
        telegram_domain.mark_error(session, msg2["id"], "boom")


def test_get_unprocessed_messages_ordered_by_id() -> None:
    with get_session() as session:
        telegram_domain.record_inbound_message(session, chat_id=1, telegram_update_id=10, text="first")
        telegram_domain.record_inbound_message(session, chat_id=1, telegram_update_id=11, text="second")
        telegram_domain.record_inbound_message(session, chat_id=1, telegram_update_id=12, text="third")

    with get_session() as session:
        pending = telegram_domain.get_unprocessed_messages(session)
    assert [p["text"] for p in pending] == ["first", "second", "third"]
