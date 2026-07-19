"""Integration tests: the Calendar (events) REST surface's request schema.

Currently just the ColorName regression below -- domain-level event
CRUD is already covered thoroughly in test_calendar.py.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from elly_server.api.app import app
from elly_server.db.base import get_session
from elly_server.db.models import Event
from elly_server.domain.auth import get_or_create_token

client = TestClient(app)


@pytest.fixture(autouse=True)
def _clean_tables() -> None:
    with get_session() as session:
        session.execute(delete(Event))


def _auth_headers() -> dict[str, str]:
    token, _ = get_or_create_token()
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.parametrize(
    "color",
    [
        "blue", "emerald", "amber", "violet", "rose", "cyan",
        "lime", "pink", "indigo", "teal", "orange", "sky",
        "red", "yellow", "green", "purple", "fuchsia", "slate",
    ],
)
def test_create_event_accepts_every_color_the_frontend_offers(color: str) -> None:
    """Regression test: the frontend's color palette (lib/colors.ts)
    was expanded from 12 to 18 colors, but the backend's ColorName
    schema validator was missed -- any of the 6 new colors (red,
    yellow, green, purple, fuchsia, slate) got silently rejected with a
    422 on save. Every color the picker offers must actually be
    accepted."""
    resp = client.post(
        "/api/events",
        json={"title": "Color test", "start_at": "2026-08-01T10:00:00", "color": color},
        headers=_auth_headers(),
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["color"] == color
