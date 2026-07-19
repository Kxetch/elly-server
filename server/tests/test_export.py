"""Tests for domain/export.py -- the full personal-data-backup export."""

from __future__ import annotations

import pytest
from sqlalchemy import delete

from elly_server.db.base import get_session
from elly_server.db.models import BudgetEntry, Event, Habit, HabitLog, Memory, Note, Task
from elly_server.domain import budget, calendar, export, habits, memory as mem, notes, tasks


@pytest.fixture(autouse=True)
def _clean_tables() -> None:
    with get_session() as session:
        session.execute(delete(Event))
        session.execute(delete(HabitLog))
        session.execute(delete(Habit))
        session.execute(delete(Task))
        session.execute(delete(Note))
        session.execute(delete(Memory))
        session.execute(delete(BudgetEntry))


def test_export_shape_is_present_even_with_no_data() -> None:
    with get_session() as session:
        result = export.export_all_data(session)
    assert "exported_at" in result
    assert result["notes"] == []
    assert result["tasks"] == {"open": [], "completed": []}
    assert result["habits"] == {"active": [], "archived": []}
    assert result["events"] == []
    assert result["memory"] == {}
    assert result["budget_entries"] == []


def test_export_includes_budget_entries() -> None:
    """Regression coverage: budget_entries was missing from
    export_all_data() for a while after the Budget page shipped --
    "Export my data" silently left out all income/expense history
    despite claiming to be a full backup."""
    with get_session() as session:
        budget.create_entry(session, kind="expense", category="Groceries", amount=42.5)
        budget.create_entry(
            session, kind="income", category="Salary", amount=3000,
            is_recurring=True, recurrence_day_of_month=25,
        )

    with get_session() as session:
        result = export.export_all_data(session)

    assert len(result["budget_entries"]) == 2
    categories = {e["category"] for e in result["budget_entries"]}
    assert categories == {"Groceries", "Salary"}


def test_export_includes_everything_a_user_has_created() -> None:
    with get_session() as session:
        notes.create_note(session, body="A diary entry", type="diary", mood=6)
        t = tasks.create_task(session, title="Open task")
        tasks.create_task(session, title="Done task")
        h = habits.create_habit(session, name="Active habit")
        archived = habits.create_habit(session, name="Archived habit")
        calendar.create_event(session, title="An event", start_at="2026-08-01T10:00:00")
        mem.remember(session, content="Prefers mornings", type="preference")

    with get_session() as session:
        tasks.complete_task(session, tasks.create_task(session, title="Will complete")["id"])
        habits.set_habit_active(session, archived["id"], False)

    with get_session() as session:
        result = export.export_all_data(session)

    assert len(result["notes"]) == 1
    assert result["notes"][0]["body"] == "A diary entry"
    assert any(x["title"] == "Open task" for x in result["tasks"]["open"])
    assert any(x["title"] == "Will complete" for x in result["tasks"]["completed"])
    assert any(x["name"] == "Active habit" for x in result["habits"]["active"])
    assert any(x["name"] == "Archived habit" for x in result["habits"]["archived"])
    assert any(e["title"] == "An event" for e in result["events"])
    assert "Prefers mornings" in result["memory"]["preference"]
    assert t["id"] is not None and h["id"] is not None  # sanity: fixtures created fine


def test_export_never_leaks_auth_or_encryption_material() -> None:
    with get_session() as session:
        result = export.export_all_data(session)
    dumped = str(result)
    assert "token" not in dumped.lower()
    assert "dbkey" not in dumped.lower()
