"""Tests confirming the encrypted columns actually round-trip correctly
through a real DB session/engine (not just the crypto helpers in
isolation) -- and that the raw bytes on disk are genuinely not
plaintext, not just "trust the TypeDecorator did its job"."""

from __future__ import annotations

import sqlite3

import pytest
from sqlalchemy import delete

from elly_server.config import get_db_path
from elly_server.db.base import get_session
from elly_server.db.models import ChatMessage, HabitLog, Habit, InboundTelegramMessage, Memory, Note
from elly_server.domain import notes as notes_domain


@pytest.fixture(autouse=True)
def _clean_tables() -> None:
    with get_session() as session:
        session.execute(delete(ChatMessage))
        session.execute(delete(InboundTelegramMessage))
        session.execute(delete(HabitLog))
        session.execute(delete(Habit))
        session.execute(delete(Memory))
        session.execute(delete(Note))


def _raw_column_value(table: str, column: str, row_id: int) -> str:
    """Read a column's raw stored value directly via sqlite3, bypassing
    the ORM entirely -- this is the only way to confirm the bytes on
    disk are actually ciphertext, not just that the ORM decrypts them
    correctly on the way back out (which would pass even if encryption
    silently no-op'd)."""
    conn = sqlite3.connect(str(get_db_path()))
    try:
        cur = conn.execute(f"SELECT {column} FROM {table} WHERE id = ?", (row_id,))  # noqa: S608
        return cur.fetchone()[0]
    finally:
        conn.close()


def test_note_body_is_ciphertext_on_disk_but_plaintext_via_orm() -> None:
    plaintext = "Today I felt genuinely proud of finishing something small."
    with get_session() as session:
        note = notes_domain.create_note(session, body=plaintext, title="A private title")

    raw_body = _raw_column_value("notes", "body", note["id"])
    raw_title = _raw_column_value("notes", "title", note["id"])
    assert plaintext not in raw_body
    assert "A private title" not in raw_title

    with get_session() as session:
        fetched = notes_domain.get_note(session, note["id"])
    assert fetched["body"] == plaintext
    assert fetched["title"] == "A private title"


def test_memory_content_is_ciphertext_on_disk() -> None:
    from elly_server.domain import memory as memory_domain

    plaintext = "Prefers mornings for deep work."
    with get_session() as session:
        mem = memory_domain.remember(session, content=plaintext)

    raw = _raw_column_value("memories", "content", mem["id"])
    assert plaintext not in raw

    with get_session() as session:
        recalled = memory_domain.recall(session, query="mornings")
    assert any(m["content"] == plaintext for m in recalled)


def test_chat_message_content_and_tool_arguments_are_ciphertext() -> None:
    with get_session() as session:
        msg = ChatMessage(
            conversation_id="test-convo",
            role="assistant",
            content="Here's something about your diary entry.",
            tool_arguments=[{"name": "create_note", "arguments": '{"body": "secret diary text"}'}],
        )
        session.add(msg)
        session.flush()
        msg_id = msg.id

    raw_content = _raw_column_value("chat_messages", "content", msg_id)
    raw_args = _raw_column_value("chat_messages", "tool_arguments", msg_id)
    assert "diary entry" not in raw_content
    assert "secret diary text" not in raw_args

    with get_session() as session:
        fetched = session.get(ChatMessage, msg_id)
        assert fetched.content == "Here's something about your diary entry."
        assert fetched.tool_arguments[0]["arguments"] == '{"body": "secret diary text"}'


def test_habit_log_note_is_ciphertext() -> None:
    with get_session() as session:
        habit = Habit(name="Test habit")
        session.add(habit)
        session.flush()
        log = HabitLog(habit_id=habit.id, note="felt anxious today")
        session.add(log)
        session.flush()
        log_id = log.id

    raw = _raw_column_value("habit_logs", "note", log_id)
    assert "anxious" not in raw

    with get_session() as session:
        fetched = session.get(HabitLog, log_id)
        assert fetched.note == "felt anxious today"


def test_inbound_telegram_message_text_is_ciphertext() -> None:
    with get_session() as session:
        msg = InboundTelegramMessage(chat_id=123, telegram_update_id=999, text="log that I drank water")
        session.add(msg)
        session.flush()
        msg_id = msg.id

    raw = _raw_column_value("inbound_telegram_messages", "text", msg_id)
    assert "drank water" not in raw

    with get_session() as session:
        fetched = session.get(InboundTelegramMessage, msg_id)
        assert fetched.text == "log that I drank water"


def test_null_values_stay_null_not_encrypted_empty_string() -> None:
    with get_session() as session:
        note = notes_domain.create_note(session, body="No title here")
    assert note["title"] is None

    raw_title = _raw_column_value("notes", "title", note["id"])
    assert raw_title is None
