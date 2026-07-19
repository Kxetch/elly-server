"""Regression tests for a real bug: the sync chat tool-calling loop used
to hold the SQLite write lock (an open, uncommitted transaction) across
every blocking LLM call in a multi-round exchange, since a FastAPI
request-scoped session spans the whole request by default. Reproduced
for real against a live Ollama instance -- a slow response left the
background notification scheduler's own unrelated write hitting
`database is locked` moments later.

These tests use a fake sync OpenAI-shaped client (no real network call)
that records whether the session had any uncommitted pending write at
the moment `.chat.completions.create()` was invoked -- i.e. any earlier
writes were actually committed, not just flushed, before this "blocking
call" was about to happen. This is the precise, direct way to verify
the fix without needing real concurrent threads/processes to reproduce
the race."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from sqlalchemy import delete

from elly_server.db.base import get_session
from elly_server.db.models import ChatMessage
from elly_server.domain.chat import _run_sync_tool_round, send_message  # noqa: SLF001


@pytest.fixture(autouse=True)
def _clean_chat_messages() -> None:
    with get_session() as session:
        session.execute(delete(ChatMessage))


def _text_response(content: str):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=None))]
    )


def _tool_call_response(call_id: str, name: str, args: dict):
    tc = SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(name=name, arguments=json.dumps(args)),
    )
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="", tool_calls=[tc]))]
    )


class _LockCheckingClient:
    """Records whether the session had any uncommitted pending write
    (session.new/session.dirty) at the moment each call happened -- the
    actual thing under test -- while behaving like a real OpenAI-shaped
    sync client otherwise.

    Deliberately checks session.new/session.dirty rather than
    session.in_transaction(): SQLAlchemy "autobegins" an implicit
    transaction on *any* statement, including a plain read (e.g.
    _build_oai_messages()'s SELECT), which would make in_transaction()
    True even with nothing actually written and no real SQLite write
    lock held -- WAL mode's whole point is that a read-only transaction
    never blocks a writer. session.new/session.dirty reflect the ORM's
    actual pending-write state, which is what could really collide with
    another writer."""

    def __init__(self, session, responses: list) -> None:
        self._session = session
        self._responses = list(responses)
        self.had_pending_write_at_call_time: list[bool] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **_kwargs):
        pending = bool(self._session.new) or bool(self._session.dirty)
        self.had_pending_write_at_call_time.append(pending)
        return self._responses.pop(0)


def test_single_round_reply_commits_before_the_llm_call() -> None:
    """send_message()'s own user-message write must be committed before
    _run_sync_tool_round() makes its first (only, in this case) call."""
    with get_session() as session:
        session.add(ChatMessage(conversation_id="conv-1", role="system", content="sys"))
        session.add(ChatMessage(conversation_id="conv-1", role="user", content="hi"))
        session.commit()

        oai_messages = [{"role": "user", "content": "hi"}]
        client = _LockCheckingClient(session, [_text_response("hello back")])
        result = _run_sync_tool_round(session, "conv-1", oai_messages, client, "fake-model", "ollama")

    assert result["content"] == "hello back"
    assert client.had_pending_write_at_call_time == [False]


def test_multi_round_tool_call_commits_before_each_subsequent_llm_call() -> None:
    """The critical case: round 1 returns a (non-destructive) tool call,
    which gets executed and its result persisted -- round 2's LLM call
    must see a committed session, not one still holding round 1's write
    lock. This is the exact shape of the reproduced real-world bug."""
    with get_session() as session:
        session.add(ChatMessage(conversation_id="conv-2", role="system", content="sys"))
        session.commit()

        oai_messages = [{"role": "user", "content": "log that I drank water"}]
        responses = [
            _tool_call_response("call_1", "log_habit", {"name": "water"}),
            _text_response("Logged it!"),
        ]
        client = _LockCheckingClient(session, responses)
        result = _run_sync_tool_round(session, "conv-2", oai_messages, client, "fake-model", "ollama")

    # Both calls -- the one before any writes, and the one after round 1's
    # tool-result write -- must see a session with nothing uncommitted.
    assert client.had_pending_write_at_call_time == [False, False]
    assert result["content"] == "Logged it!"


def test_send_message_end_to_end_never_leaves_an_open_transaction_mid_call() -> None:
    """Full send_message() entry point (not just the inner loop helper),
    monkeypatched only at the LLM-client boundary -- confirms the fix
    holds through the real public function real callers (Telegram, the
    REST route) actually use."""
    import elly_server.domain.chat as chat_module

    with get_session() as session:
        session.add(ChatMessage(conversation_id="conv-3", role="system", content="sys"))
        session.commit()

        client_holder: dict = {}

        def fake_get_llm_client(_session, *, async_mode=False):
            client = _LockCheckingClient(session, [_text_response("ok")])
            client_holder["client"] = client
            return client, "fake-model", "ollama"

        original = chat_module.get_llm_client
        chat_module.get_llm_client = fake_get_llm_client
        try:
            result = send_message(session, "conv-3", "hello")
        finally:
            chat_module.get_llm_client = original

    assert result["content"] == "ok"
    assert client_holder["client"].had_pending_write_at_call_time == [False]
