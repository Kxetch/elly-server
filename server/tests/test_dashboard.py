"""Tests for domain/dashboard.py -- the composed "right now" snapshot."""

from __future__ import annotations

import pytest
from sqlalchemy import delete

from elly_server.db.base import get_session
from elly_server.db.models import Event, Habit, HabitLog, Task
from elly_server.domain import dashboard, habits, tasks


@pytest.fixture(autouse=True)
def _clean_tables() -> None:
    with get_session() as session:
        session.execute(delete(Event))
        session.execute(delete(HabitLog))
        session.execute(delete(Habit))
        session.execute(delete(Task))


def test_today_snapshot_shape() -> None:
    with get_session() as session:
        snapshot = dashboard.today_snapshot(session)
    assert set(snapshot.keys()) == {"events", "pending_tasks", "habits"}
    assert snapshot["pending_tasks"] == []


def test_today_snapshot_nests_broken_down_tasks_under_their_parent() -> None:
    """Regression coverage: pending_tasks used to be a flat list, which
    meant an AI-broken-down task's steps rendered as ordinary flat
    siblings on the Today page instead of nested under their parent
    like the dedicated Tasks page already shows them. today_snapshot()
    now reuses get_task_tree() -- same hierarchical shape everywhere."""
    with get_session() as session:
        parent = tasks.create_task(session, title="Plan the move")
        tasks.create_task(session, title="Standalone task")
        tasks.breakdown_task(session, parent["id"], [{"title": "Book the van"}, {"title": "Pack boxes"}])

    with get_session() as session:
        snapshot = dashboard.today_snapshot(session)

    by_title = {t["title"]: t for t in snapshot["pending_tasks"]}
    assert "Plan the move" in by_title
    assert "Standalone task" in by_title
    assert len(by_title["Plan the move"]["children"]) == 2
    assert by_title["Standalone task"]["children"] == []


def test_today_snapshot_includes_habits() -> None:
    with get_session() as session:
        habits.create_habit(session, name="Drink water")

    with get_session() as session:
        snapshot = dashboard.today_snapshot(session)

    assert any(h["name"] == "Drink water" for h in snapshot["habits"])
