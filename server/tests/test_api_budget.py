"""Integration tests: the Budget (income/expense) REST surface."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from elly_server.api.app import app
from elly_server.db.base import get_session
from elly_server.db.models import BudgetEntry, Event, Habit, HabitLog
from elly_server.domain.auth import get_or_create_token

client = TestClient(app)


@pytest.fixture(autouse=True)
def _clean_tables() -> None:
    with get_session() as session:
        session.execute(delete(Event))
        session.execute(delete(HabitLog))
        session.execute(delete(Habit))
        session.execute(delete(BudgetEntry))


def _auth_headers() -> dict[str, str]:
    token, _ = get_or_create_token()
    return {"Authorization": f"Bearer {token}"}


def test_create_entry_requires_auth() -> None:
    resp = client.post("/api/budget/entries", json={"kind": "expense", "category": "Coffee", "amount": 4.5})
    assert resp.status_code == 401


def test_create_one_off_expense() -> None:
    resp = client.post(
        "/api/budget/entries",
        json={"kind": "expense", "category": "Coffee", "amount": 4.5},
        headers=_auth_headers(),
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["kind"] == "expense"
    assert body["amount"] == 4.5
    assert body["is_recurring"] is False
    assert body["quantity"] == 1
    assert body["unit_label"] is None


def test_create_entry_with_quantity_and_unit_label() -> None:
    resp = client.post(
        "/api/budget/entries",
        json={"kind": "expense", "category": "Coke Zero", "amount": 4.5, "quantity": 3, "unit_label": "bottle"},
        headers=_auth_headers(),
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["quantity"] == 3
    assert body["unit_label"] == "bottle"


def test_create_entry_rejects_quantity_below_one() -> None:
    resp = client.post(
        "/api/budget/entries",
        json={"kind": "expense", "category": "Coffee", "amount": 4.5, "quantity": 0},
        headers=_auth_headers(),
    )
    assert resp.status_code == 422


def test_create_recurring_income() -> None:
    resp = client.post(
        "/api/budget/entries",
        json={
            "kind": "income",
            "category": "Salary",
            "amount": 3000,
            "is_recurring": True,
            "recurrence_day_of_month": 25,
        },
        headers=_auth_headers(),
    )
    assert resp.status_code == 201
    assert resp.json()["recurrence_day_of_month"] == 25


def test_create_entry_rejects_zero_amount() -> None:
    resp = client.post(
        "/api/budget/entries",
        json={"kind": "expense", "category": "Coffee", "amount": 0},
        headers=_auth_headers(),
    )
    assert resp.status_code == 422  # Pydantic gt=0 constraint


def test_create_entry_rejects_invalid_kind() -> None:
    resp = client.post(
        "/api/budget/entries",
        json={"kind": "savings", "category": "Coffee", "amount": 4.5},
        headers=_auth_headers(),
    )
    assert resp.status_code == 422  # Pydantic Literal constraint


def test_list_entries() -> None:
    client.post(
        "/api/budget/entries",
        json={"kind": "expense", "category": "Coffee", "amount": 4.5},
        headers=_auth_headers(),
    )
    resp = client.get("/api/budget/entries", headers=_auth_headers())
    assert resp.status_code == 200
    assert len(resp.json()) == 1


def test_list_entries_filters_by_kind() -> None:
    client.post(
        "/api/budget/entries",
        json={"kind": "expense", "category": "Coffee", "amount": 4.5},
        headers=_auth_headers(),
    )
    client.post(
        "/api/budget/entries",
        json={"kind": "income", "category": "Salary", "amount": 3000},
        headers=_auth_headers(),
    )
    resp = client.get("/api/budget/entries?kind=income", headers=_auth_headers())
    assert resp.status_code == 200
    assert len(resp.json()) == 1
    assert resp.json()[0]["category"] == "Salary"


def test_get_entry() -> None:
    created = client.post(
        "/api/budget/entries",
        json={"kind": "expense", "category": "Coffee", "amount": 4.5},
        headers=_auth_headers(),
    ).json()
    resp = client.get(f"/api/budget/entries/{created['id']}", headers=_auth_headers())
    assert resp.status_code == 200
    assert resp.json()["category"] == "Coffee"


def test_get_entry_not_found() -> None:
    resp = client.get("/api/budget/entries/999", headers=_auth_headers())
    assert resp.status_code == 404


def test_update_entry() -> None:
    created = client.post(
        "/api/budget/entries",
        json={"kind": "expense", "category": "Coffee", "amount": 4.5},
        headers=_auth_headers(),
    ).json()
    resp = client.patch(
        f"/api/budget/entries/{created['id']}", json={"amount": 5.0}, headers=_auth_headers()
    )
    assert resp.status_code == 200
    assert resp.json()["amount"] == 5.0
    assert resp.json()["category"] == "Coffee"


def test_delete_entry() -> None:
    created = client.post(
        "/api/budget/entries",
        json={"kind": "expense", "category": "Coffee", "amount": 4.5},
        headers=_auth_headers(),
    ).json()
    resp = client.delete(f"/api/budget/entries/{created['id']}", headers=_auth_headers())
    assert resp.status_code == 200
    assert resp.json() == {"deleted": True}


def test_delete_entry_missing_returns_false() -> None:
    resp = client.delete("/api/budget/entries/999", headers=_auth_headers())
    assert resp.status_code == 200
    assert resp.json() == {"deleted": False}


def test_list_recent() -> None:
    for _ in range(3):
        client.post(
            "/api/budget/entries",
            json={"kind": "expense", "category": "Coffee", "amount": 4.5},
            headers=_auth_headers(),
        )
    resp = client.get("/api/budget/entries/recent", headers=_auth_headers())
    assert resp.status_code == 200
    assert len(resp.json()) == 1  # deduplicated


def test_list_categories() -> None:
    client.post(
        "/api/budget/entries",
        json={"kind": "expense", "category": "Coffee", "amount": 4.5},
        headers=_auth_headers(),
    )
    resp = client.get("/api/budget/categories", headers=_auth_headers())
    assert resp.status_code == 200
    assert resp.json() == ["Coffee"]


def test_get_summary() -> None:
    client.post(
        "/api/budget/entries",
        json={"kind": "income", "category": "Freelance", "amount": 500},
        headers=_auth_headers(),
    )
    client.post(
        "/api/budget/entries",
        json={"kind": "expense", "category": "Coffee", "amount": 4.5},
        headers=_auth_headers(),
    )
    resp = client.get("/api/budget/summary", headers=_auth_headers())
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_income"] == 500
    assert body["total_expenses"] == 4.5
    assert body["net"] == 495.5


def test_get_trend() -> None:
    resp = client.get("/api/budget/trend?months=3", headers=_auth_headers())
    assert resp.status_code == 200
    assert len(resp.json()["months"]) == 3


def test_get_upcoming() -> None:
    client.post(
        "/api/budget/entries",
        json={
            "kind": "income",
            "category": "Salary",
            "amount": 3000,
            "is_recurring": True,
            "recurrence_day_of_month": 25,
        },
        headers=_auth_headers(),
    )
    resp = client.get("/api/budget/upcoming?days=60", headers=_auth_headers())
    assert resp.status_code == 200
    assert len(resp.json()) >= 1


def test_generate_scheduled_budget_events_endpoint() -> None:
    resp = client.post("/api/budget/generate-events", headers=_auth_headers())
    assert resp.status_code == 201
    assert resp.json() == []  # nothing recurring configured yet
