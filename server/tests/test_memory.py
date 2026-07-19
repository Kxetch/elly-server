"""Tests for domain/memory.py -- remembered facts/goals/preferences.

recall()'s content matching happens in Python (not a SQL WHERE clause)
since Memory.content is encrypted at rest -- see the module's own
docstring for why. These tests cover the *domain* behavior (ordering,
matching, access-count bookkeeping); test_encrypted_types.py covers the
encryption-at-rest guarantee itself.
"""

from __future__ import annotations

import pytest
from sqlalchemy import delete

from elly_server.db.base import get_session
from elly_server.db.models import Memory
from elly_server.domain import memory as memory_domain


@pytest.fixture(autouse=True)
def _clean_memories_table() -> None:
    with get_session() as session:
        session.execute(delete(Memory))


def test_remember_defaults() -> None:
    with get_session() as session:
        mem = memory_domain.remember(session, content="Prefers mornings for deep work.")
    assert mem["type"] == "general"
    assert mem["importance"] == 0.5
    assert mem["access_count"] == 0


def test_remember_with_explicit_type_and_importance() -> None:
    with get_session() as session:
        mem = memory_domain.remember(session, content="Wants to run a 10k", type="goal", importance=0.9)
    assert mem["type"] == "goal"
    assert mem["importance"] == 0.9


def test_remember_rejects_blank_content() -> None:
    with get_session() as session:
        with pytest.raises(ValueError, match="can't be empty"):
            memory_domain.remember(session, content="   ")


def test_recall_finds_matching_content_case_insensitively() -> None:
    with get_session() as session:
        memory_domain.remember(session, content="Prefers mornings for deep work.")
        memory_domain.remember(session, content="Dislikes loud open offices.")

    with get_session() as session:
        results = memory_domain.recall(session, query="MORNINGS")
    assert len(results) == 1
    assert "mornings" in results[0]["content"].lower()


def test_recall_orders_by_importance_then_access_count() -> None:
    with get_session() as session:
        memory_domain.remember(session, content="low importance note", importance=0.2)
        memory_domain.remember(session, content="high importance note", importance=0.9)

    with get_session() as session:
        results = memory_domain.recall(session, query="importance")
    assert results[0]["content"] == "high importance note"
    assert results[1]["content"] == "low importance note"


def test_recall_respects_limit() -> None:
    with get_session() as session:
        for i in range(5):
            memory_domain.remember(session, content=f"shared keyword item {i}")

    with get_session() as session:
        results = memory_domain.recall(session, query="shared keyword", limit=2)
    assert len(results) == 2


def test_recall_increments_access_count_and_updates_last_accessed() -> None:
    with get_session() as session:
        memory_domain.remember(session, content="track my access count")

    with get_session() as session:
        first = memory_domain.recall(session, query="access count")
    assert first[0]["access_count"] == 1

    with get_session() as session:
        second = memory_domain.recall(session, query="access count")
    assert second[0]["access_count"] == 2


def test_recall_no_match_returns_empty_list() -> None:
    with get_session() as session:
        memory_domain.remember(session, content="something unrelated")

    with get_session() as session:
        results = memory_domain.recall(session, query="totally different phrase")
    assert results == []


def test_get_profile_summary_groups_by_type() -> None:
    with get_session() as session:
        memory_domain.remember(session, content="a fact", type="fact")
        memory_domain.remember(session, content="a goal", type="goal")
        memory_domain.remember(session, content="another fact", type="fact")

    with get_session() as session:
        summary = memory_domain.get_profile_summary(session)
    assert set(summary["fact"]) == {"a fact", "another fact"}
    assert summary["goal"] == ["a goal"]
