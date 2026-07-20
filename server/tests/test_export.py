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
    assert result["habit_logs"] == []
    assert result["events"] == []
    assert result["memory"] == {}
    assert result["memories"] == []
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
        habits.log_habit(session, habit_id=h["id"])
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

    # Raw, restorable versions -- see list_all_habit_logs()/
    # list_all_memories()'s docstrings for why these exist alongside
    # the aggregate/grouped "habits"/"memory" views above.
    assert len(result["habit_logs"]) == 1
    assert result["habit_logs"][0]["habit_id"] == h["id"]
    assert any(m["content"] == "Prefers mornings" for m in result["memories"])


def test_export_never_leaks_auth_or_encryption_material() -> None:
    with get_session() as session:
        result = export.export_all_data(session)
    dumped = str(result)
    assert "token" not in dumped.lower()
    assert "dbkey" not in dumped.lower()


# ---- import_all_data() ------------------------------------------------------


def _wipe_all_tables() -> None:
    with get_session() as session:
        session.execute(delete(Event))
        session.execute(delete(HabitLog))
        session.execute(delete(Habit))
        session.execute(delete(Task))
        session.execute(delete(Note))
        session.execute(delete(Memory))
        session.execute(delete(BudgetEntry))


def test_import_refuses_when_the_database_already_has_content() -> None:
    with get_session() as session:
        notes.create_note(session, body="Already here")

    with get_session() as session:
        with pytest.raises(ValueError, match="empty"):
            export.import_all_data(session, {"notes": []})


def test_import_round_trips_a_full_export_into_an_empty_database() -> None:
    with get_session() as session:
        notes.create_note(session, body="A diary entry", type="diary", mood=6, tags=["x", "y"])
        parent = tasks.create_task(session, title="Parent task")
        tasks.create_task(session, title="Child task", parent_task_id=parent["id"])
        habit = habits.create_habit(session, name="Drink water", tiny_version="one sip")
        habits.log_habit(session, habit_id=habit["id"], note="felt good")
        entry = budget.create_entry(session, kind="expense", category="Coffee", amount=4.5)
        calendar.create_event(session, title="Standalone event", start_at="2026-08-01T09:00:00")
        calendar.create_event(
            session, title="Habit event", start_at="2026-08-02T09:00:00", habit_id=habit["id"],
        )
        mem.remember(session, content="Likes tea", type="preference", importance=0.8)

    with get_session() as session:
        exported = export.export_all_data(session)

    _wipe_all_tables()

    with get_session() as session:
        counts = export.import_all_data(session, exported)

    assert counts == {
        "notes": 1,
        "habits": 1,
        "budget_entries": 1,
        "tasks": 2,
        "habit_logs": 1,
        "memories": 1,
        "events": 2,
    }

    with get_session() as session:
        reimported = export.export_all_data(session)

    # Same content, same ids, same original timestamps -- a restore
    # should read exactly like the original, not like everything just
    # happened at restore time.
    assert reimported["notes"][0]["id"] == exported["notes"][0]["id"]
    assert reimported["notes"][0]["body"] == "A diary entry"
    assert reimported["notes"][0]["tags"] == ["x", "y"]
    assert reimported["notes"][0]["created_at"] == exported["notes"][0]["created_at"]

    child = next(t for t in reimported["tasks"]["open"] if t["title"] == "Child task")
    assert child["parent_task_id"] == parent["id"]

    assert reimported["habits"]["active"][0]["name"] == "Drink water"
    assert reimported["habits"]["active"][0]["tiny_version"] == "one sip"
    assert len(reimported["habit_logs"]) == 1
    assert reimported["habit_logs"][0]["note"] == "felt good"
    assert reimported["habit_logs"][0]["habit_id"] == habit["id"]

    assert reimported["budget_entries"][0]["category"] == "Coffee"
    assert reimported["budget_entries"][0]["amount"] == 4.5
    assert reimported["budget_entries"][0]["id"] == entry["id"]

    events_by_title = {e["title"]: e for e in reimported["events"]}
    assert events_by_title["Habit event"]["habit_id"] == habit["id"]

    assert reimported["memories"][0]["content"] == "Likes tea"
    assert reimported["memories"][0]["importance"] == 0.8


def test_import_reconstructs_subtasks_regardless_of_list_order() -> None:
    """The export is two flat open/completed lists -- nothing guarantees
    a parent appears before its child. import_all_data() must insert in
    dependency order regardless (see its "waves" comment)."""
    with get_session() as session:
        parent = tasks.create_task(session, title="Parent")
        child = tasks.create_task(session, title="Child", parent_task_id=parent["id"])
        grandchild = tasks.create_task(session, title="Grandchild", parent_task_id=child["id"])

    _wipe_all_tables()

    # Deliberately reversed: grandchild, child, parent.
    payload = {
        "tasks": {
            "open": [
                {**grandchild, "status": "open"},
                {**child, "status": "open"},
                {**parent, "status": "open"},
            ],
            "completed": [],
        },
    }

    with get_session() as session:
        counts = export.import_all_data(session, payload)
    assert counts["tasks"] == 3

    with get_session() as session:
        result = export.export_all_data(session)
    by_title = {t["title"]: t for t in result["tasks"]["open"]}
    assert by_title["Child"]["parent_task_id"] == by_title["Parent"]["id"]
    assert by_title["Grandchild"]["parent_task_id"] == by_title["Child"]["id"]


def test_import_raises_a_clean_error_for_malformed_data_instead_of_crashing() -> None:
    with get_session() as session:
        with pytest.raises(ValueError, match="doesn't look like a valid elly export"):
            export.import_all_data(session, {"notes": [{"id": 1}]})  # missing required "body"
