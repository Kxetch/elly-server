"""Tests for domain/tasks.py -- CRUD, hierarchy, and AI-breakdown persistence."""

from __future__ import annotations

import pytest
from sqlalchemy import delete

from elly_server.db.base import get_session
from elly_server.db.models import Task
from elly_server.domain import tasks


@pytest.fixture(autouse=True)
def _clean_tasks_table() -> None:
    with get_session() as session:
        session.execute(delete(Task))


def test_create_and_complete_task() -> None:
    with get_session() as session:
        task = tasks.create_task(session, title="Sort insurance")
    assert task["status"] == "open"
    assert task["completed_at"] is None

    with get_session() as session:
        completed = tasks.complete_task(session, task["id"])
    assert completed["status"] == "done"
    assert completed["completed_at"] is not None


def test_complete_unknown_task_raises() -> None:
    with get_session() as session:
        with pytest.raises(ValueError, match="not found"):
            tasks.complete_task(session, 999)


def test_reopen_task_undoes_completion() -> None:
    with get_session() as session:
        task = tasks.create_task(session, title="Oops, not done yet")
        tasks.complete_task(session, task["id"])
    with get_session() as session:
        reopened = tasks.reopen_task(session, task["id"])
    assert reopened["status"] == "open"
    assert reopened["completed_at"] is None


def test_reopen_unknown_task_raises() -> None:
    with get_session() as session:
        with pytest.raises(ValueError, match="not found"):
            tasks.reopen_task(session, 999)


def test_create_task_rejects_blank_title() -> None:
    with get_session() as session:
        with pytest.raises(ValueError, match="can't be empty"):
            tasks.create_task(session, title="   ")


def test_create_task_strips_title_whitespace() -> None:
    with get_session() as session:
        task = tasks.create_task(session, title="  Padded  ")
    assert task["title"] == "Padded"


def test_update_task_rejects_blank_title() -> None:
    with get_session() as session:
        task = tasks.create_task(session, title="Original")
    with get_session() as session:
        with pytest.raises(ValueError, match="can't be empty"):
            tasks.update_task(session, task["id"], title="   ")


def test_list_pending_tasks_excludes_completed() -> None:
    with get_session() as session:
        t1 = tasks.create_task(session, title="Open task")
        t2 = tasks.create_task(session, title="Will complete")
        tasks.complete_task(session, t2["id"])

    with get_session() as session:
        pending = tasks.list_pending_tasks(session)
    ids = [t["id"] for t in pending]
    assert t1["id"] in ids
    assert t2["id"] not in ids


def test_list_pending_tasks_dated_before_undated() -> None:
    with get_session() as session:
        tasks.create_task(session, title="No due date")
        tasks.create_task(session, title="Has due date", due_at="2026-08-01T00:00:00")

    with get_session() as session:
        pending = tasks.list_pending_tasks(session)
    assert pending[0]["title"] == "Has due date"
    assert pending[1]["title"] == "No due date"


def test_update_task_partial() -> None:
    with get_session() as session:
        task = tasks.create_task(session, title="Original", priority="low")
    with get_session() as session:
        updated = tasks.update_task(session, task["id"], priority="high")
    assert updated["title"] == "Original"  # untouched
    assert updated["priority"] == "high"


def test_update_unknown_task_raises() -> None:
    with get_session() as session:
        with pytest.raises(ValueError, match="not found"):
            tasks.update_task(session, 999, title="X")


def test_delete_task_and_its_subtasks() -> None:
    with get_session() as session:
        parent = tasks.create_task(session, title="Parent")
        tasks.breakdown_task(session, parent["id"], [{"title": "Step 1"}, {"title": "Step 2"}])

    with get_session() as session:
        assert tasks.delete_task(session, parent["id"]) is True

    with get_session() as session:
        remaining = tasks.list_pending_tasks(session)
    assert remaining == []  # parent + both children gone


def test_delete_nonexistent_task_returns_false() -> None:
    with get_session() as session:
        assert tasks.delete_task(session, 999) is False


def test_breakdown_task_creates_linked_subtasks() -> None:
    with get_session() as session:
        parent = tasks.create_task(session, title="Big vague task")
    with get_session() as session:
        subtasks = tasks.breakdown_task(
            session,
            parent["id"],
            [
                {"title": "Tiny first step", "estimate_minutes": 5},
                {"title": "Second step", "estimate_minutes": 20, "priority": "medium"},
            ],
        )
    assert len(subtasks) == 2
    assert all(s["parent_task_id"] == parent["id"] for s in subtasks)
    assert subtasks[0]["estimate_minutes"] == 5


def test_breakdown_unknown_task_raises() -> None:
    with get_session() as session:
        with pytest.raises(ValueError, match="not found"):
            tasks.breakdown_task(session, 999, [{"title": "X"}])


def test_get_task_tree_nests_children_under_parent() -> None:
    with get_session() as session:
        parent = tasks.create_task(session, title="Parent")
        tasks.create_task(session, title="Standalone")
        tasks.breakdown_task(session, parent["id"], [{"title": "Child A"}, {"title": "Child B"}])

    with get_session() as session:
        tree = tasks.get_task_tree(session)

    roots_by_title = {t["title"]: t for t in tree}
    assert "Parent" in roots_by_title
    assert "Standalone" in roots_by_title
    assert len(roots_by_title["Parent"]["children"]) == 2
    assert roots_by_title["Standalone"]["children"] == []


def test_get_task_tree_promotes_orphaned_children_when_parent_completed() -> None:
    """Nothing stops a user from completing a parent task before its
    subtasks -- an open subtask must never become invisible/unreachable
    just because its parent was marked done first."""
    with get_session() as session:
        parent = tasks.create_task(session, title="Parent")
        tasks.breakdown_task(session, parent["id"], [{"title": "Child A"}, {"title": "Child B"}])

    with get_session() as session:
        tasks.complete_task(session, parent["id"])

    with get_session() as session:
        tree = tasks.get_task_tree(session)

    titles = {t["title"] for t in tree}
    assert "Parent" not in titles, "completed parent correctly no longer appears"
    assert {"Child A", "Child B"} <= titles, "still-open children must surface at the top level, not vanish"


def test_list_tasks_due_on_returns_only_tasks_due_that_day() -> None:
    with get_session() as session:
        tasks.create_task(session, title="Due on target day", due_at="2026-08-15")
        tasks.create_task(session, title="Due the day before", due_at="2026-08-14")
        tasks.create_task(session, title="Due the day after", due_at="2026-08-16")
        tasks.create_task(session, title="No due date at all")

    with get_session() as session:
        due = tasks.list_tasks_due_on(session, "2026-08-15")

    titles = {t["title"] for t in due}
    assert titles == {"Due on target day"}


def test_list_tasks_due_on_includes_completed_tasks_too() -> None:
    """Unlike list_pending_tasks(), the calendar day-detail view this
    powers should show what was due that day even if already done --
    matching TodayView's own completed-but-shown-crossed-out pattern."""
    with get_session() as session:
        done = tasks.create_task(session, title="Done on time", due_at="2026-08-15")
        tasks.create_task(session, title="Still open", due_at="2026-08-15")
        tasks.complete_task(session, done["id"])

    with get_session() as session:
        due = tasks.list_tasks_due_on(session, "2026-08-15")

    by_title = {t["title"]: t for t in due}
    assert by_title["Done on time"]["status"] == "done"
    assert by_title["Still open"]["status"] == "open"


def test_list_tasks_due_on_rejects_unparseable_date() -> None:
    with pytest.raises(ValueError, match="Could not parse"):
        with get_session() as session:
            tasks.list_tasks_due_on(session, "not-a-date")


def test_duplicate_tasks_copies_open_tasks_only() -> None:
    with get_session() as session:
        t1 = tasks.create_task(session, title="Keep me open")
        t2 = tasks.create_task(session, title="Will be done")
        tasks.complete_task(session, t2["id"])

    with get_session() as session:
        duplicated = tasks.duplicate_tasks(session)
    titles = [d["title"] for d in duplicated]
    assert "Keep me open" in titles
    assert "Will be done" not in titles
    assert t1["id"] not in [d["id"] for d in duplicated]  # genuinely new rows


def test_duplicate_tasks_with_explicit_ids() -> None:
    with get_session() as session:
        t1 = tasks.create_task(session, title="A")
        tasks.create_task(session, title="B")

    with get_session() as session:
        duplicated = tasks.duplicate_tasks(session, task_ids=[t1["id"]])
    assert len(duplicated) == 1
    assert duplicated[0]["title"] == "A"


def test_list_completed_tasks_ordered_newest_first() -> None:
    with get_session() as session:
        t1 = tasks.create_task(session, title="First done")
        t2 = tasks.create_task(session, title="Second done")
        tasks.complete_task(session, t1["id"])
        tasks.complete_task(session, t2["id"])

    with get_session() as session:
        completed = tasks.list_completed_tasks(session)
    assert completed[0]["title"] == "Second done"
    assert completed[1]["title"] == "First done"
