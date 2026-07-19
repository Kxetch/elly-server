"""Tests for the destructive-tool-call confirmation gate in
domain/chat.py -- _pending_tool_calls() and _auto_decline_stale_pending().

These exercise the pure state-detection logic directly against
ChatMessage rows (no real LLM call involved -- the actual agentic loop
and resolve_pending_tool() streaming behavior is verified live against
a running server, the same way the rest of this session's work was
verified, since mocking a full OpenAI-compatible streaming client is
disproportionate to what these DB-state-machine functions need).
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import delete

from elly_server.db.base import get_session
from elly_server.db.models import ChatMessage
from elly_server.domain.chat import (
    DESTRUCTIVE_TOOLS,
    _auto_decline_stale_pending,
    _classify_yes_no,
    _describe_destructive_action,
    _pending_tool_calls,
    send_message,
)


@pytest.fixture(autouse=True)
def _clean_chat_messages() -> None:
    with get_session() as session:
        session.execute(delete(ChatMessage))


def _tool_call(call_id: str, name: str, args: dict) -> dict:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args)},
    }


def test_destructive_tools_set_is_exactly_the_five_deletes() -> None:
    assert DESTRUCTIVE_TOOLS == {
        "delete_note", "delete_event", "delete_task", "delete_habit", "delete_budget_entry"
    }


def test_no_pending_when_conversation_is_empty() -> None:
    with get_session() as session:
        assert _pending_tool_calls(session, "conv-1") is None


def test_no_pending_after_a_fully_resolved_round() -> None:
    with get_session() as session:
        session.add(ChatMessage(conversation_id="conv-1", role="user", content="hi"))
        session.add(ChatMessage(
            conversation_id="conv-1", role="assistant", content="",
            tool_arguments=[_tool_call("call_1", "create_task", {"title": "X"})],
        ))
        session.add(ChatMessage(
            conversation_id="conv-1", role="tool", content='{"id": 1}',
            tool_name="create_task", tool_call_id="call_1",
        ))
    with get_session() as session:
        assert _pending_tool_calls(session, "conv-1") is None


def test_pending_when_a_destructive_call_was_never_resolved() -> None:
    with get_session() as session:
        session.add(ChatMessage(conversation_id="conv-1", role="user", content="delete it"))
        session.add(ChatMessage(
            conversation_id="conv-1", role="assistant", content="",
            tool_arguments=[_tool_call("call_1", "delete_task", {"task_id": 5})],
        ))
    with get_session() as session:
        pending = _pending_tool_calls(session, "conv-1")
    assert pending is not None
    tool_calls, resolved_ids = pending
    assert len(tool_calls) == 1
    assert resolved_ids == set()


def test_pending_detects_partially_resolved_round() -> None:
    """A round with two calls where the first (safe) one already
    executed and the second (destructive) one is still awaiting
    confirmation -- only the second should show up as unresolved."""
    with get_session() as session:
        session.add(ChatMessage(conversation_id="conv-1", role="user", content="do both"))
        session.add(ChatMessage(
            conversation_id="conv-1", role="assistant", content="",
            tool_arguments=[
                _tool_call("call_1", "create_task", {"title": "safe"}),
                _tool_call("call_2", "delete_habit", {"habit_id": 3}),
            ],
        ))
        session.add(ChatMessage(
            conversation_id="conv-1", role="tool", content='{"id": 1}',
            tool_name="create_task", tool_call_id="call_1",
        ))
    with get_session() as session:
        pending = _pending_tool_calls(session, "conv-1")
    assert pending is not None
    tool_calls, resolved_ids = pending
    assert resolved_ids == {"call_1"}
    unresolved = [tc for tc in tool_calls if tc["id"] not in resolved_ids]
    assert len(unresolved) == 1
    assert unresolved[0]["function"]["name"] == "delete_habit"


def test_pending_resets_after_a_new_user_message() -> None:
    """If a new user turn started after an unresolved round (shouldn't
    normally happen since send_message_stream auto-declines first, but
    the helper itself should still be correct standalone)."""
    with get_session() as session:
        session.add(ChatMessage(conversation_id="conv-1", role="user", content="delete it"))
        session.add(ChatMessage(
            conversation_id="conv-1", role="assistant", content="",
            tool_arguments=[_tool_call("call_1", "delete_task", {"task_id": 5})],
        ))
        session.add(ChatMessage(conversation_id="conv-1", role="user", content="never mind"))
    with get_session() as session:
        assert _pending_tool_calls(session, "conv-1") is None


def test_auto_decline_stale_pending_records_a_declined_tool_result() -> None:
    with get_session() as session:
        session.add(ChatMessage(conversation_id="conv-1", role="user", content="delete it"))
        session.add(ChatMessage(
            conversation_id="conv-1", role="assistant", content="",
            tool_arguments=[_tool_call("call_1", "delete_task", {"task_id": 5})],
        ))

    with get_session() as session:
        _auto_decline_stale_pending(session, "conv-1")

    with get_session() as session:
        assert _pending_tool_calls(session, "conv-1") is None
        msgs = session.query(ChatMessage).filter_by(conversation_id="conv-1", role="tool").all()
        assert len(msgs) == 1
        result = json.loads(msgs[0].content)
        assert result["declined"] is True
        assert msgs[0].tool_call_id == "call_1"


def test_auto_decline_stale_pending_is_a_noop_when_nothing_pending() -> None:
    with get_session() as session:
        session.add(ChatMessage(conversation_id="conv-1", role="user", content="hi"))

    with get_session() as session:
        _auto_decline_stale_pending(session, "conv-1")  # should not raise

    with get_session() as session:
        msgs = session.query(ChatMessage).filter_by(conversation_id="conv-1").all()
    assert len(msgs) == 1  # just the original user message


# --- send_message()'s own confirmation gate (the Telegram path -- no dialog
# UI, so a destructive tool call must pause and be resolved by a plain-text
# yes/no reply instead of POST /chat/messages/resolve-tool). Found missing
# entirely during a risk-based pre-release testing pass: send_message() used
# to execute delete_note/delete_event/delete_task/delete_habit/
# delete_budget_entry immediately with no confirmation of any kind, even
# though the streaming path (used by the web UI) always paused. Since
# Telegram's bot.py calls send_message() directly (not the streaming path),
# this meant a destructive request over Telegram could delete data with zero
# confirmation, contradicting the documented safety guarantee. ------------

@pytest.mark.parametrize("name,args,expected_fragment", [
    ("delete_task", {"task_id": 5}, "Task #5"),
    ("delete_habit", {"habit_id": 3}, "Habit #3"),
    ("delete_event", {"event_id": 9}, "Event #9"),
    ("delete_note", {"note_id": 1}, "Note #1"),
    ("delete_budget_entry", {"entry_id": 2}, "Entry #2"),
])
def test_describe_destructive_action_names_the_target_and_asks_yes_no(name, args, expected_fragment) -> None:
    description = _describe_destructive_action(name, args)
    assert expected_fragment in description
    assert '"yes"' in description
    assert '"no"' in description


def test_describe_destructive_action_falls_back_for_unknown_tool_name() -> None:
    description = _describe_destructive_action("delete_something_new", {"id": 1})
    assert "delete_something_new" in description
    assert "can't be undone" in description


@pytest.mark.parametrize("reply", ["yes", "Yes", "YES!", "y", "confirm", "go ahead", "sure", "okay."])
def test_classify_yes_no_recognizes_affirmatives(reply: str) -> None:
    assert _classify_yes_no(reply) == "confirm"


@pytest.mark.parametrize("reply", ["no", "No", "nope", "cancel", "don't", "never mind", "stop!"])
def test_classify_yes_no_recognizes_negatives(reply: str) -> None:
    assert _classify_yes_no(reply) == "decline"


@pytest.mark.parametrize("reply", ["maybe", "delete the other one instead", "what do you mean", ""])
def test_classify_yes_no_is_ambiguous_for_anything_else(reply: str) -> None:
    assert _classify_yes_no(reply) is None


def test_send_message_pauses_and_asks_again_on_ambiguous_reply_to_pending_delete() -> None:
    """The core regression: a destructive call is mid-round awaiting
    confirmation, and the caller (Telegram) sends something that isn't a
    clear yes/no -- send_message() must ask again, not silently execute
    (and not silently drop it either). Deliberately requires no LLM
    client/API key: the ambiguous-reply branch returns before ever
    calling get_llm_client(), by design, so this stays a fast, real unit
    test rather than needing a mocked OpenAI client."""
    with get_session() as session:
        session.add(ChatMessage(conversation_id="conv-1", role="user", content="delete my running habit"))
        session.add(ChatMessage(
            conversation_id="conv-1", role="assistant", content="",
            tool_arguments=[_tool_call("call_1", "delete_habit", {"habit_id": 7})],
        ))

    with get_session() as session:
        result = send_message(session, "conv-1", "what do you mean")

    assert result["role"] == "assistant"
    assert "Habit #7" in result["content"]
    assert '"yes"' in result["content"]

    # Still pending afterward -- the ambiguous exchange must not have been
    # persisted (that would corrupt the tool_calls/tool-response invariant
    # OpenAI's API requires, and would silently drop the pending action).
    with get_session() as session:
        pending = _pending_tool_calls(session, "conv-1")
        msgs = session.query(ChatMessage).filter_by(conversation_id="conv-1").all()
    assert pending is not None
    tool_calls, resolved_ids = pending
    assert resolved_ids == set()
    assert len(msgs) == 2  # only the original user + assistant tool-call rows


def test_send_message_declines_a_pending_delete_without_executing_it() -> None:
    """A clear "no" to a pending destructive call must resolve it as
    declined and must NOT call the underlying delete function -- proven
    here by pointing the pending call at a habit id that doesn't exist
    (delete_habit would raise if it were actually invoked) and confirming
    no exception propagates and the round resolves cleanly instead."""
    with get_session() as session:
        session.add(ChatMessage(conversation_id="conv-1", role="user", content="delete habit 999"))
        session.add(ChatMessage(
            conversation_id="conv-1", role="assistant", content="",
            tool_arguments=[_tool_call("call_1", "delete_habit", {"habit_id": 999})],
        ))

    with get_session() as session:
        pending_before = _pending_tool_calls(session, "conv-1")
    assert pending_before is not None

    # No OPENAI_API_KEY is configured in the test environment (conftest
    # redirects ELLY_DATA_DIR to a throwaway dir with no settings), so the
    # follow-up completion after resolving will hit LlmNotConfiguredError
    # -- exercised here as a real, honest error path, not mocked away.
    with get_session() as session:
        result = send_message(session, "conv-1", "no")

    assert result["role"] == "assistant"

    with get_session() as session:
        assert _pending_tool_calls(session, "conv-1") is None
        tool_msgs = session.query(ChatMessage).filter_by(conversation_id="conv-1", role="tool").all()
    assert len(tool_msgs) == 1
    outcome = json.loads(tool_msgs[0].content)
    assert outcome["declined"] is True
