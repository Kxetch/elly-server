"""Integration tests: the Telegram pairing REST surface (dashboard side)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from elly_server.api.app import app
from elly_server.db.base import get_session
from elly_server.db.models import AppSettings, InboundTelegramMessage, TelegramLink
from elly_server.domain.auth import get_or_create_token

client = TestClient(app)


@pytest.fixture(autouse=True)
def _clean_telegram_tables() -> None:
    with get_session() as session:
        session.execute(delete(InboundTelegramMessage))
        session.execute(delete(TelegramLink))
        session.execute(delete(AppSettings))


def _auth_headers() -> dict[str, str]:
    token, _ = get_or_create_token()
    return {"Authorization": f"Bearer {token}"}


def test_telegram_status_requires_auth() -> None:
    resp = client.get("/api/telegram/status")
    assert resp.status_code == 401


def test_telegram_status_unpaired_by_default() -> None:
    resp = client.get("/api/telegram/status", headers=_auth_headers())
    assert resp.status_code == 200
    body = resp.json()
    assert body["paired"] is False
    assert body["pairing_code_active"] is False
    assert body["bot_configured"] is False
    assert body["bot_running"] is False


def test_generate_pairing_code_and_status_reflects_it() -> None:
    resp = client.post("/api/telegram/pairing-code", headers=_auth_headers())
    assert resp.status_code == 201
    assert len(resp.json()["code"]) == 6

    status = client.get("/api/telegram/status", headers=_auth_headers())
    assert status.json()["pairing_code_active"] is True


def test_unpair_when_not_paired_is_safe() -> None:
    resp = client.post("/api/telegram/unpair", headers=_auth_headers())
    assert resp.status_code == 200
    assert resp.json() == {"paired": False}


# ---- Bot token configuration (Settings UI) ----------------------------


def test_set_bot_token_requires_auth() -> None:
    resp = client.put("/api/telegram/bot-token", json={"token": "abc123"})
    assert resp.status_code == 401


def test_set_bot_token_marks_configured() -> None:
    resp = client.put(
        "/api/telegram/bot-token", json={"token": "abc123:real-token"}, headers=_auth_headers()
    )
    assert resp.status_code == 200
    assert resp.json() == {"configured": True}

    status = client.get("/api/telegram/status", headers=_auth_headers())
    assert status.json()["bot_configured"] is True


def test_set_bot_token_rejects_empty_string() -> None:
    resp = client.put("/api/telegram/bot-token", json={"token": ""}, headers=_auth_headers())
    assert resp.status_code == 422


def test_clear_bot_token() -> None:
    client.put("/api/telegram/bot-token", json={"token": "abc123"}, headers=_auth_headers())
    resp = client.delete("/api/telegram/bot-token", headers=_auth_headers())
    assert resp.status_code == 200
    assert resp.json() == {"configured": False}

    status = client.get("/api/telegram/status", headers=_auth_headers())
    assert status.json()["bot_configured"] is False


def test_status_never_leaks_raw_bot_token() -> None:
    client.put("/api/telegram/bot-token", json={"token": "super-secret-value"}, headers=_auth_headers())
    status = client.get("/api/telegram/status", headers=_auth_headers())
    assert "super-secret-value" not in status.text
    assert "bot_token" not in status.json()
