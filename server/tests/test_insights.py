"""Tests for domain/insights.py -- weekly_review's per-category counts."""

from __future__ import annotations

import pytest
from sqlalchemy import delete

from elly_server.db.base import get_session
from elly_server.db.models import Note, Task
from elly_server.domain import insights, notes as notes_domain, tasks


@pytest.fixture(autouse=True)
def _clean_tables() -> None:
    with get_session() as session:
        session.execute(delete(Note))
        session.execute(delete(Task))


def test_weekly_review_notes_written_excludes_diary_entries() -> None:
    """notes_written and diary_entries are shown as two separate stat
    tiles side by side -- they must be non-overlapping counts, or the
    numbers silently imply more total activity than actually happened."""
    with get_session() as session:
        notes_domain.create_note(session, body="Plain note 1", type="note")
        notes_domain.create_note(session, body="Plain note 2", type="note")
        notes_domain.create_note(session, body="Diary entry 1", type="diary", mood=5, energy=5)

    with get_session() as session:
        review = insights.weekly_review(session)

    assert review["notes_written"] == 2
    assert review["diary_entries"] == 1


def test_weekly_review_task_counts() -> None:
    with get_session() as session:
        done = tasks.create_task(session, title="Done one")
        tasks.create_task(session, title="Still open")
        tasks.complete_task(session, done["id"])

    with get_session() as session:
        review = insights.weekly_review(session)

    assert review["tasks_completed"] == 1
    assert review["tasks_still_pending"] == 1
