"""Integration tests: the export/import REST surface."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import delete

from elly_server.api.app import app
from elly_server.api.rate_limit import limiter
from elly_server.db.base import get_session
from elly_server.db.models import BudgetEntry, Event, Habit, HabitLog, Memory, Note, Task
from elly_server.domain.auth import get_or_create_token

client = TestClient(app)


def _clean_tables() -> None:
    with get_session() as session:
        session.execute(delete(Event))
        session.execute(delete(HabitLog))
        session.execute(delete(Habit))
        session.execute(delete(Task))
        session.execute(delete(Note))
        session.execute(delete(Memory))
        session.execute(delete(BudgetEntry))


def _auth_headers() -> dict[str, str]:
    token, _ = get_or_create_token()
    return {"Authorization": f"Bearer {token}"}


def setup_function() -> None:
    _clean_tables()
    # Every route this file hits shares slowapi's process-wide limiter
    # state -- reset it before each test so one test's requests never
    # count toward another's rate-limit window.
    limiter.reset()


def test_export_requires_auth() -> None:
    resp = client.get("/api/export")
    assert resp.status_code == 401


def test_export_returns_real_data() -> None:
    resp = client.post("/api/notes", json={"body": "hi"}, headers=_auth_headers())
    assert resp.status_code == 201

    resp = client.get("/api/export", headers=_auth_headers())
    assert resp.status_code == 200
    assert len(resp.json()["notes"]) == 1


def test_export_is_rate_limited_to_3_per_minute() -> None:
    headers = _auth_headers()
    for _ in range(3):
        resp = client.get("/api/export", headers=headers)
        assert resp.status_code == 200
    resp = client.get("/api/export", headers=headers)
    assert resp.status_code == 429


def test_import_requires_auth() -> None:
    resp = client.post("/api/export/import", json={"notes": []})
    assert resp.status_code == 401


def test_import_round_trips_through_the_real_api() -> None:
    headers = _auth_headers()
    resp = client.post("/api/notes", json={"body": "backed up note"}, headers=headers)
    assert resp.status_code == 201

    exported = client.get("/api/export", headers=headers).json()

    _clean_tables()

    resp = client.post("/api/export/import", json=exported, headers=headers)
    assert resp.status_code == 200
    assert resp.json()["notes"] == 1

    reimported = client.get("/api/export", headers=headers).json()
    assert reimported["notes"][0]["body"] == "backed up note"


def test_import_refuses_a_non_empty_database_with_a_clean_400() -> None:
    headers = _auth_headers()
    client.post("/api/notes", json={"body": "already here"}, headers=headers)

    resp = client.post("/api/export/import", json={"notes": []}, headers=headers)
    assert resp.status_code == 400
    assert "empty" in resp.json()["detail"].lower()
