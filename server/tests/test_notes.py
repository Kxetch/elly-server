"""Tests for domain/notes.py -- notebook notes + diary entries (shared table)."""

from __future__ import annotations

import pytest
from sqlalchemy import delete

from elly_server.db.base import get_session
from elly_server.db.models import Note
from elly_server.domain import notes


@pytest.fixture(autouse=True)
def _clean_notes_table() -> None:
    with get_session() as session:
        session.execute(delete(Note))


def test_create_note_default_type() -> None:
    with get_session() as session:
        note = notes.create_note(session, body="Just a thought")
    assert note["type"] == "note"
    assert note["mood"] is None
    assert note["tags"] == []


def test_create_diary_entry_with_mood_energy() -> None:
    with get_session() as session:
        entry = notes.create_note(session, body="Good day", type="diary", mood=7, energy=6)
    assert entry["type"] == "diary"
    assert entry["mood"] == 7
    assert entry["energy"] == 6


def test_update_note_partial() -> None:
    with get_session() as session:
        note = notes.create_note(session, body="Original", title="Old title")
    with get_session() as session:
        updated = notes.update_note(session, note["id"], title="New title")
    assert updated["title"] == "New title"
    assert updated["body"] == "Original"  # untouched


def test_update_unknown_note_raises() -> None:
    with get_session() as session:
        with pytest.raises(ValueError, match="not found"):
            notes.update_note(session, 999, body="X")


def test_create_note_rejects_blank_body() -> None:
    with get_session() as session:
        with pytest.raises(ValueError, match="can't be empty"):
            notes.create_note(session, body="   ")


def test_update_note_rejects_blank_body() -> None:
    with get_session() as session:
        note = notes.create_note(session, body="Original")
    with get_session() as session:
        with pytest.raises(ValueError, match="can't be empty"):
            notes.update_note(session, note["id"], body="   ")


def test_get_note() -> None:
    with get_session() as session:
        note = notes.create_note(session, body="Findable")
    with get_session() as session:
        found = notes.get_note(session, note["id"])
    assert found is not None
    assert found["body"] == "Findable"


def test_get_note_returns_none_when_missing() -> None:
    with get_session() as session:
        assert notes.get_note(session, 999) is None


def test_delete_note() -> None:
    with get_session() as session:
        note = notes.create_note(session, body="Temp")
    with get_session() as session:
        assert notes.delete_note(session, note["id"]) is True
    with get_session() as session:
        assert notes.delete_note(session, note["id"]) is False


def test_search_notes_by_type() -> None:
    with get_session() as session:
        notes.create_note(session, body="A note", type="note")
        notes.create_note(session, body="A diary entry", type="diary")

    with get_session() as session:
        diary_only = notes.search_notes(session, type="diary")
    assert len(diary_only) == 1
    assert diary_only[0]["type"] == "diary"


def test_search_notes_by_query_text() -> None:
    with get_session() as session:
        notes.create_note(session, body="Something about dentists")
        notes.create_note(session, body="Unrelated content")

    with get_session() as session:
        results = notes.search_notes(session, query="dentist")
    assert len(results) == 1


def test_search_notes_by_tag() -> None:
    with get_session() as session:
        notes.create_note(session, body="Tagged", tags=["weekly-reflection"])
        notes.create_note(session, body="Untagged")

    with get_session() as session:
        results = notes.search_notes(session, tag="weekly-reflection")
    assert len(results) == 1
    assert results[0]["body"] == "Tagged"


def test_search_notes_by_tag_alone_is_not_truncated_before_filtering() -> None:
    """Same bug class as the query-vs-limit ordering above, but for
    `tag`: the SQL-level limit must not apply before the tag filter
    runs, or a tag search can silently miss real matches once there
    are more than `limit` more-recent, untagged notes in between."""
    with get_session() as session:
        notes.create_note(session, body="The one tagged note", tags=["health"])
        # More than the default limit (20) of newer, untagged notes --
        # without the fix, these push the tagged note out of the
        # SQL-level LIMIT window before the tag filter ever runs.
        for i in range(25):
            notes.create_note(session, body=f"Untagged note {i}")

    with get_session() as session:
        results = notes.search_notes(session, tag="health")

    assert len(results) == 1
    assert results[0]["body"] == "The one tagged note"


def test_get_recent_notes_respects_limit() -> None:
    with get_session() as session:
        for i in range(5):
            notes.create_note(session, body=f"Note {i}")

    with get_session() as session:
        recent = notes.get_recent_notes(session, limit=3)
    assert len(recent) == 3


def test_search_notes_query_matches_title_too() -> None:
    with get_session() as session:
        notes.create_note(session, body="unrelated body", title="Dentist follow-up")
        notes.create_note(session, body="unrelated body", title="Something else")

    with get_session() as session:
        results = notes.search_notes(session, query="dentist")
    assert len(results) == 1
    assert results[0]["title"] == "Dentist follow-up"


def test_search_notes_query_and_tag_combined() -> None:
    with get_session() as session:
        notes.create_note(session, body="dentist visit went fine", tags=["health"])
        notes.create_note(session, body="dentist visit went fine", tags=["work"])

    with get_session() as session:
        results = notes.search_notes(session, query="dentist", tag="health")
    assert len(results) == 1
    assert "health" in results[0]["tags"]


def test_search_notes_query_respects_limit() -> None:
    """Regression coverage: body/title are encrypted, so the free-text
    match happens in Python after fetching -- confirm limit is still
    applied correctly to the *filtered* result, not the raw fetch."""
    with get_session() as session:
        for i in range(5):
            notes.create_note(session, body=f"shared keyword note {i}")
        notes.create_note(session, body="does not match at all")

    with get_session() as session:
        results = notes.search_notes(session, query="shared keyword", limit=2)
    assert len(results) == 2


def test_search_notes_query_no_match_returns_empty() -> None:
    with get_session() as session:
        notes.create_note(session, body="something else entirely")

    with get_session() as session:
        results = notes.search_notes(session, query="totally unrelated phrase")
    assert results == []
