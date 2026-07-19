"""Integration tests: the Reminders & Alarms REST surface (Sprint 5)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from elly_server.api.app import app
from elly_server.db.base import get_session
from elly_server.db.models import Event, Habit, HabitLog, Reminder, Task
from elly_server.domain import calendar as calendar_domain
from elly_server.domain import tasks as tasks_domain
from elly_server.domain.auth import get_or_create_token

client = TestClient(app)


@pytest.fixture(autouse=True)
def _clean_tables() -> None:
    with get_session() as session:
        session.execute(delete(Reminder))
        session.execute(delete(Event))
        session.execute(delete(HabitLog))
        session.execute(delete(Habit))
        session.execute(delete(Task))


def _auth_headers() -> dict[str, str]:
    token, _ = get_or_create_token()
    return {"Authorization": f"Bearer {token}"}


def test_reminders_routes_require_auth() -> None:
    assert client.get("/api/reminders").status_code == 401
    assert client.get("/api/reminders/task/1").status_code == 401
    assert client.put("/api/reminders/task/1", json={"offset_minutes": 0}).status_code == 401
    assert client.delete("/api/reminders/task/1").status_code == 401


def test_set_and_get_reminder_for_a_task() -> None:
    with get_session() as session:
        task = tasks_domain.create_task(session, title="X", due_at="2026-08-01T10:00:00")

    resp = client.put(
        f"/api/reminders/task/{task['id']}",
        json={"kind": "alarm", "offset_minutes": -15},
        headers=_auth_headers(),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["kind"] == "alarm"
    assert body["trigger_at"] == "2026-08-01T09:45:00"

    get_resp = client.get(f"/api/reminders/task/{task['id']}", headers=_auth_headers())
    assert get_resp.status_code == 200
    assert get_resp.json()["kind"] == "alarm"


def test_get_reminder_returns_null_when_none_set() -> None:
    with get_session() as session:
        task = tasks_domain.create_task(session, title="X", due_at="2026-08-01T10:00:00")

    resp = client.get(f"/api/reminders/task/{task['id']}", headers=_auth_headers())
    assert resp.status_code == 200
    assert resp.json() is None


def test_put_replaces_any_existing_reminder() -> None:
    with get_session() as session:
        task = tasks_domain.create_task(session, title="X", due_at="2026-08-01T10:00:00")

    client.put(f"/api/reminders/task/{task['id']}", json={"kind": "notification", "offset_minutes": -15}, headers=_auth_headers())
    client.put(f"/api/reminders/task/{task['id']}", json={"kind": "alarm", "offset_minutes": -30}, headers=_auth_headers())

    with get_session() as session:
        rows = session.query(Reminder).filter(Reminder.target_type == "task", Reminder.target_id == task["id"]).all()
    assert len(rows) == 1
    assert rows[0].kind == "alarm"


def test_delete_reminder() -> None:
    with get_session() as session:
        task = tasks_domain.create_task(session, title="X", due_at="2026-08-01T10:00:00")
    client.put(f"/api/reminders/task/{task['id']}", json={"offset_minutes": 0}, headers=_auth_headers())

    resp = client.delete(f"/api/reminders/task/{task['id']}", headers=_auth_headers())
    assert resp.status_code == 200
    assert resp.json() == {"deleted": True}

    get_resp = client.get(f"/api/reminders/task/{task['id']}", headers=_auth_headers())
    assert get_resp.json() is None


def test_delete_reminder_returns_false_when_none_existed() -> None:
    resp = client.delete("/api/reminders/task/99999", headers=_auth_headers())
    assert resp.status_code == 200
    assert resp.json() == {"deleted": False}


def test_set_reminder_rejects_invalid_target_type() -> None:
    resp = client.put("/api/reminders/budget_entry/1", json={"offset_minutes": 0}, headers=_auth_headers())
    assert resp.status_code == 422  # Literal constraint on the path param


def test_set_reminder_rejects_invalid_kind() -> None:
    with get_session() as session:
        task = tasks_domain.create_task(session, title="X", due_at="2026-08-01T10:00:00")
    resp = client.put(
        f"/api/reminders/task/{task['id']}", json={"kind": "siren", "offset_minutes": 0}, headers=_auth_headers()
    )
    assert resp.status_code == 422


def test_set_reminder_for_task_with_no_due_date_returns_a_friendly_400() -> None:
    with get_session() as session:
        task = tasks_domain.create_task(session, title="No due date")
    resp = client.put(f"/api/reminders/task/{task['id']}", json={"offset_minutes": 0}, headers=_auth_headers())
    assert resp.status_code == 400
    assert "due date" in resp.json()["detail"]


def test_list_reminders_includes_events_and_tasks_soonest_first() -> None:
    with get_session() as session:
        task = tasks_domain.create_task(session, title="Task reminder", due_at="2026-09-01T10:00:00")
        event = calendar_domain.create_event(session, title="Event reminder", start_at="2026-08-01T10:00:00")

    client.put(f"/api/reminders/task/{task['id']}", json={"offset_minutes": 0}, headers=_auth_headers())
    client.put(f"/api/reminders/event/{event['id']}", json={"offset_minutes": 0}, headers=_auth_headers())

    resp = client.get("/api/reminders", headers=_auth_headers())
    assert resp.status_code == 200
    titles = [r["target_title"] for r in resp.json()]
    assert titles == ["Event reminder", "Task reminder"]
